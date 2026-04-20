"""
CognitionBridge v1.0
=====================
Integrates the CognitiveKernel (v3.6 pipeline) into ShiroEngine without
replacing it. ShiroEngine keeps ownership of: LLM calls, TTS, streaming,
Discord, voice room, Gradio UI, SpeechCadence, AutonomousVoice.

The bridge:
  1. Runs CognitiveKernel.process() for every user turn
  2. Extracts the structured context packet from the kernel output
  3. Returns a formatted string block that ShiroEngine injects into
     the system prompt (the "top_bun") before every LLM call
  4. Feeds emotion/attention/world-model data back into ShiroEngine's
     existing SentimentTrajectory and SelfAwareness so both systems stay
     in sync — no duplication, only enrichment

Thread safety:
  ShiroEngine calls process_text() from a threading.Lock context.
  The bridge holds its own asyncio event loop (run_in_executor pattern)
  that is started once at boot and never torn down until shutdown().
  All calls from the sync process_text() path go through
  _safe_run(coro) which submits to that dedicated loop.

New capabilities injected into every LLM turn:
  - ATTENTION: top salient tokens, urgency, novelty spike flag
  - EMOTION:   VAD trajectory, ambivalence, valence velocity, mood band
  - IDENTITY:  drift flags (SOFT/HARD), active boundary echo
  - WORLD:     relationship stage, expertise, communication style, interests
  - REASONING: selected hypothesis, debate winner, 3-step plan
  - MEMORY:    top retrieved cognitive memories (separate from conv history)
  - METACOG:   reflection issues/insights, curiosity signals
  - TELEMETRY: pipeline stage timing (debug logging only)
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("shiro.cognition")

# ── Default config ────────────────────────────────────────────────────────────
_DEFAULT_COGNITION_CONFIG: dict = {
    "conversation_window_turns": 12,
    "memory": {
        "base_dir":               "./shiro_data/cognition/memory",
        "top_k_semantic":         8,
        "top_k_keyword":          5,
        "top_k_recent":           3,
        "top_n_final":           10,
        "importance_floor":       0.12,
        "episodic_ttl_days":      45,
        "auto_promote_threshold": 4,
    },
    "attention": {
        "max_signals":   12,
        "recency_decay": 0.92,
    },
    "emotion": {
        "inertia":            0.82,
        "decay_rate":         0.04,
        "baseline_valence":   0.15,
        "baseline_arousal":  -0.10,
        "baseline_dominance": 0.20,
        "history_max":        50,
    },
    "identity": {
        "drift_threshold":  2,
        "theme_window":    20,
        "restore_on_boot": True,
        "snapshot_path":   "./shiro_data/cognition/identity_snapshot.json",
    },
    "persona": "shiro",
}


class CognitionBridge:
    """
    Thin async wrapper around CognitiveKernel that is safe to call from
    ShiroEngine's synchronous process_text() path.

    Usage:
        bridge = CognitionBridge(config_override={...})
        bridge.boot()                          # called once at startup
        ctx_block = bridge.enrich(user_input, user_name)
        # inject ctx_block into ShiroEngine's system prompt
        bridge.shutdown()                      # called at graceful exit
    """

    def __init__(self, config_override: dict | None = None):
        cfg = dict(_DEFAULT_COGNITION_CONFIG)
        if config_override:
            # Deep-merge top-level keys
            for k, v in config_override.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k] = {**cfg[k], **v}
                else:
                    cfg[k] = v
        self._config = cfg
        self._kernel: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = False
        self._last_state: dict = {}   # last enriched state for cross-module sync
        self._last_context_block: str = ""

    # ── Boot ──────────────────────────────────────────────────────────────────

    def boot(self):
        """
        Start a dedicated event loop in a daemon thread, then boot the kernel.
        Blocks until the kernel has finished booting (including warmup).
        Safe to call from the main thread.
        """
        if self._ready:
            return

        # Ensure data dirs exist
        mem_dir = Path(self._config["memory"]["base_dir"])
        mem_dir.mkdir(parents=True, exist_ok=True)
        id_dir  = Path(self._config["identity"]["snapshot_path"]).parent
        id_dir.mkdir(parents=True, exist_ok=True)

        ready_event = threading.Event()
        error_holder: list = []

        def _run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._boot_kernel(ready_event, error_holder))
                self._loop.run_forever()
            finally:
                self._loop.close()

        self._thread = threading.Thread(target=_run_loop, daemon=True, name="shiro_cognition")
        self._thread.start()
        ready_event.wait(timeout=60.0)

        if error_holder:
            log.error(f"CognitionBridge boot failed: {error_holder[0]}")
            self._ready = False
        else:
            self._ready = True
            log.info("CognitionBridge ready — cognitive pipeline active")

    async def _boot_kernel(self, ready_event: threading.Event, error_holder: list):
        try:
            try:
                from .cognitive_kernel import CognitiveKernel
            except ImportError:
                from cognitive_kernel import CognitiveKernel
            self._kernel = CognitiveKernel(self._config)
            await self._kernel.boot(warmup=True)
            ready_event.set()
        except Exception as exc:
            error_holder.append(exc)
            log.error(f"Kernel boot error: {exc}")
            ready_event.set()

    # ── Enrich — called from ShiroEngine per turn ─────────────────────────────

    def enrich(
        self,
        user_input: str,
        user_name: str = "user",
        context_override: dict | None = None,
    ) -> str:
        """
        Run the full cognitive pipeline for this turn.
        Returns a formatted context block string for injection into the LLM prompt.
        Falls back to empty string if the kernel is not ready (never crashes).
        """
        if not self._ready or not self._kernel:
            return ""
        try:
            ctx = context_override or {}
            ctx["user_name"] = user_name

            # Wrap in an async function so the coroutine — and any tasks it
            # creates internally (e.g. asyncio.shield post-turn) — are born
            # entirely inside the kernel's dedicated event loop, never leaking
            # a Future object back to run_coroutine_threadsafe.
            async def _run():
                return await self._kernel.process(user_input, context_override=ctx)

            result = self._safe_run(_run())
            # result is the ResponseModule's output packet (instruction block for LLM)
            self._last_context_block = result or ""
            # Cache last kernel state for cross-module sync
            self._sync_state()
            return self._last_context_block
        except Exception as exc:
            log.warning(f"CognitionBridge.enrich error: {exc}")
            return ""

    def _sync_state(self):
        """Extract last-turn data from kernel for ShiroEngine cross-sync."""
        try:
            hist = self._kernel.get_turn_history(1)
            if hist:
                self._last_state = hist[0]
        except Exception:
            pass

    # ── Cross-module sync helpers ─────────────────────────────────────────────

    def get_emotion_state(self) -> dict:
        """
        Returns the current emotion VAD + trajectory from the cognitive kernel.
        ShiroEngine can feed this into SentimentTrajectory to keep them in sync.
        """
        try:
            return self._kernel.emotion_mod.current_state() if self._kernel and self._kernel.emotion_mod else {}
        except Exception:
            return {}

    def get_world_profile(self) -> dict:
        """Returns the WorldModel's user profile for SelfAwareness sync."""
        try:
            return self._kernel.world_mod.get_user_profile() if self._kernel and self._kernel.world_mod else {}
        except Exception:
            return {}

    def get_attention_report(self) -> dict:
        """Returns AttentionSystem salience report."""
        try:
            return self._kernel.attention_mod.report() if self._kernel and self._kernel.attention_mod else {}
        except Exception:
            return {}

    def get_identity_snapshot(self) -> dict:
        """Returns IdentitySystem snapshot for drift monitoring."""
        try:
            return self._kernel.identity_mod.get_snapshot() if self._kernel and self._kernel.identity_mod else {}
        except Exception:
            return {}

    def get_memory_summary(self, n: int = 5) -> str:
        """Returns top-n cognitive memories as injection-ready text."""
        try:
            return self._kernel.memory_mod.summarise(n) if self._kernel and self._kernel.memory_mod else ""
        except Exception:
            return ""

    def get_mood_summary(self) -> str:
        """One-line mood string from EmotionWeightingSystem."""
        try:
            return self._kernel.emotion_mod.mood_summary() if self._kernel and self._kernel.emotion_mod else ""
        except Exception:
            return ""

    def get_last_turn(self) -> dict:
        """Last TurnRecord as dict (intent, tone, relationship, etc.)."""
        return dict(self._last_state)

    def get_kernel_status(self) -> dict:
        """Full kernel status dict — for /status endpoints or debug UI."""
        try:
            return self._kernel.status() if self._kernel else {"booted": False}
        except Exception:
            return {"booted": False, "error": "status unavailable"}

    def new_session(self, user_name: str = ""):
        """
        Reset per-session cognitive state (emotion, attention, world phase).
        Identity and long-term memory are preserved.
        """
        if self._kernel:
            try:
                self._kernel.new_session()
                log.info(f"CognitionBridge: new session for '{user_name}'")
            except Exception as exc:
                log.warning(f"new_session error: {exc}")

    def set_context(self, key: str, value):
        """Inject a persistent key-value into every kernel turn."""
        if self._kernel:
            self._kernel.set_context(key, value)

    def clear_context(self, key: str):
        if self._kernel:
            self._kernel.clear_context(key)

    # ── Shutdown ──────────────────────────────────────────────────────────────

    def shutdown(self, timeout: float = 10.0):
        """Gracefully shut down the kernel and stop the event loop."""
        if not self._ready or not self._kernel:
            return
        try:
            async def _shutdown():
                await self._kernel.shutdown(timeout=timeout)
            self._safe_run(_shutdown())
        except Exception as exc:
            log.warning(f"CognitionBridge shutdown error: {exc}")
        finally:
            self._ready = False
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._loop.stop)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _safe_run(self, coro) -> Any:
        """
        Submit a coroutine to the bridge's dedicated event loop from any thread.
        Blocks until done. Never raises — returns None on timeout/error.
        """
        if not self._loop or not self._loop.is_running():
            return None
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=45.0)  # FIX: raised from 30s — 8B under TTS load can take 15s+
        except Exception as exc:
            log.warning(f"_safe_run error: {exc}")
            return None


# ── Context block formatter ────────────────────────────────────────────────────

def format_cognition_block(kernel_output: str, last_turn: dict) -> str:
    """
    Wraps the kernel's output packet in a clearly-delimited block for injection
    into ShiroEngine's system prompt. The LLM sees this as enriched context,
    not as something to echo verbatim.

    The block is clearly marked so ShiroEngine can strip or replace it if needed.
    """
    if not kernel_output:
        return ""
    # Trim the kernel output to avoid ballooning the context window
    # (ResponseModule output is already compact; cap at 3000 chars)
    trimmed = kernel_output[:2000]  # FIX: was 3000 — tightened to keep block under ~500 tokens
    tone   = last_turn.get("tone", "")
    rel    = last_turn.get("relationship", "")
    intent = last_turn.get("intent", "")
    hot    = last_turn.get("hot_ms", 0)
    # FIX: metadata moved to plain comment line — bracket form was echoed by 8B model.
    # The structured data is still present for the LLM to read, just not in [bracket] form.
    meta = ""
    if any([intent, tone, rel]):
        meta = f"# intent={intent} tone={tone} rel={rel} pipeline={hot:.0f}ms\n"
    return f"\n[COGNITIVE CONTEXT]\n{meta}{trimmed}\n[/COGNITIVE CONTEXT]\n"