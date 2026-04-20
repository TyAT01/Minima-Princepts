"""
SHIRO Cognitive Kernel v3.6
============================
Hardware: RTX 3070 8 GB | i9-9900K 8c/16t | 64 GB RAM

What's new vs v3.2
-------------------
ENHANCEMENTS
  - GRACEFUL SHUTDOWN: kernel.shutdown() drains in-flight post-turn tasks,
    flushes identity snapshot to disk, cancels watchdog.
  - PIPELINE TELEMETRY: every stage is timed with perf_counter_ns();
    slowest stages surface in status() so you can see where time goes.
  - BOOT WARMUP: fires a silent dummy turn after loading all modules to
    warm Python caches, pre-fault deque/dict structures, open SQLite WAL.
  - PERSISTENT CONTEXT: set_context(key, value) injects key/value pairs
    that appear in every turn's state.context — for system date, TTS voice
    ID, session metadata, etc.
  - HEALTH WATCHDOG: background task every 60 s checks memory availability,
    identity drift accumulation, and error-log growth. Logs warnings only,
    never raises. Prevents silent degradation going unnoticed.
  - TURN TELEMETRY on TurnRecord: hot_ms stored per turn; get_turn_history()
    exposes it. Lets you spot slow turns without a profiler.
  - INTERRUPT SUPPORT: asyncio.shield wraps _post_turn so if process()
    is cancelled mid-flight, post-turn tasks survive and complete.
  - SALIENCE SPIKE PROPAGATION: if attention reports a salience_spike (topic
    shift), kernel escalates scheduler complexity by one level, ensuring
    memory and reasoning adapt to the new topic faster.

OPTIMISATIONS (i9-9900K)
  - __slots__ on CognitiveState, SchedulerConfig, TurnRecord, _StatePool,
    CognitiveKernel — attribute access is a pointer dereference, not a
    dict lookup.
  - StatePool of size 3: warmup + active turn + one post-turn overlap.
  - CognitiveState.reset() reuses existing list/dict objects where safe,
    avoiding heap allocation on every turn.
  - conversation_window snapshot is a tuple (immutable) — `tuple(deque)`
    is O(n) but avoids double-allocation that `list(deque)` does.
  - error_log is deque(maxlen=200) not a list — O(1) append, no realloc.
  - TurnRecord stores only scalars (no nested dicts), keeping 500 of them
    in RAM costs ~200 KB total.
  - post_turn_tasks is a set() with add_done_callback discard — O(1) ops.
  - Warmup fires synchronously within boot() before watchdog starts,
    ensuring first real turn sees warm caches without races.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from enum import IntEnum
from typing import Any

log = logging.getLogger("shiro.kernel")


# ──────────────────────────────────────────────────────────────────────────────
# Complexity
# ──────────────────────────────────────────────────────────────────────────────

class Complexity(IntEnum):
    LOW    = 1
    MEDIUM = 2
    HIGH   = 3
    DEEP   = 4


# ──────────────────────────────────────────────────────────────────────────────
# SchedulerConfig
# ──────────────────────────────────────────────────────────────────────────────

class SchedulerConfig:
    __slots__ = (
        "complexity", "enable_debate", "enable_planning",
        "enable_reflection", "enable_curiosity", "multi_agent_mode",
    )

    def __init__(self):
        self.complexity:        int  = Complexity.LOW
        self.enable_debate:     bool = False
        self.enable_planning:   bool = False
        self.enable_reflection: bool = False
        self.enable_curiosity:  bool = False
        self.multi_agent_mode:  bool = False   # sticky

    def reset(self):
        self.complexity        = Complexity.LOW
        self.enable_debate     = False
        self.enable_planning   = False
        self.enable_reflection = False
        self.enable_curiosity  = False


# ──────────────────────────────────────────────────────────────────────────────
# CognitiveState
# ──────────────────────────────────────────────────────────────────────────────

class CognitiveState:
    """
    Single mutable object threaded through the entire pipeline each turn.
    Recycled via _StatePool — no heap allocation on the hot path.

    conversation_window is a tuple snapshot (immutable, zero-copy view of
    the kernel's deque taken before the turn begins).

    telemetry is a plain dict of stage→ns elapsed, populated by mark().
    """
    __slots__ = (
        "turn_id", "timestamp_ns", "agent_id",
        "raw_input", "interpretation", "intent", "context",
        "memory", "world_model", "hypotheses", "debate",
        "reasoning", "strategy", "plan",
        "response", "styled_response", "output",
        "reflection", "goals", "learning_update", "confidence",
        "attention", "emotion", "identity_snapshot",
        "conversation_window", "telemetry",
        "scheduler", "errors",
    )

    def __init__(self):
        self.scheduler           = SchedulerConfig()
        self.conversation_window = ()
        self.telemetry: dict[str, int] = {}
        # Initialise container slots to None before _clear() checks them
        self.interpretation    = None
        self.intent            = None
        self.context           = None
        self.memory            = None
        self.world_model       = None
        self.hypotheses        = None
        self.debate            = None
        self.reasoning         = None
        self.strategy          = None
        self.plan              = None
        self.reflection        = None
        self.goals             = None
        self.errors            = None
        self.attention         = None
        self.emotion           = None
        self.identity_snapshot = None
        self._clear("")

    def reset(self, raw_input: str, window: tuple):
        self.scheduler.reset()
        self.conversation_window = window
        self.telemetry           = {}
        self._clear(raw_input)

    def _clear(self, raw_input: str):
        self.turn_id           = uuid.uuid4().hex
        self.timestamp_ns      = time.perf_counter_ns()
        self.agent_id          = "shiro"
        self.raw_input         = raw_input
        self.response          = ""
        self.styled_response   = ""
        self.output            = ""
        self.learning_update   = False
        self.confidence        = 0.0
        # Reuse existing containers — .clear() avoids malloc on the hot path.
        # StatePool recycles state objects so these dicts/lists already exist.
        if self.interpretation is not None:
            self.interpretation.clear()
        else:
            self.interpretation = {}
        if self.intent is not None:
            self.intent.clear()
        else:
            self.intent = {}
        if self.context is not None:
            self.context.clear()
        else:
            self.context = {}
        if self.memory is not None:
            self.memory.clear()
        else:
            self.memory = {}
        if self.world_model is not None:
            self.world_model.clear()
        else:
            self.world_model = {}
        if self.debate is not None:
            self.debate.clear()
        else:
            self.debate = {}
        if self.reasoning is not None:
            self.reasoning.clear()
        else:
            self.reasoning = {}
        if self.strategy is not None:
            self.strategy.clear()
        else:
            self.strategy = {}
        if self.reflection is not None:
            self.reflection.clear()
        else:
            self.reflection = {}
        if self.attention is not None:
            self.attention.clear()
        else:
            self.attention = {}
        if self.emotion is not None:
            self.emotion.clear()
        else:
            self.emotion = {}
        if self.identity_snapshot is not None:
            self.identity_snapshot.clear()
        else:
            self.identity_snapshot = {}
        # Lists — clear in place
        if self.hypotheses is not None:
            self.hypotheses.clear()
        else:
            self.hypotheses = []
        if self.plan is not None:
            self.plan.clear()
        else:
            self.plan = []
        if self.goals is not None:
            self.goals.clear()
        else:
            self.goals = []
        if self.errors is not None:
            self.errors.clear()
        else:
            self.errors = []

    def mark(self, stage: str):
        """Record elapsed ns for a pipeline stage. O(1)."""
        self.telemetry[stage] = time.perf_counter_ns() - self.timestamp_ns

    def flag_error(self, module: str, exc: Exception):
        self.errors.append({"module": module, "error": str(exc)})
        log.warning(f"[{module}] {exc}")


# ──────────────────────────────────────────────────────────────────────────────
# _StatePool
# ──────────────────────────────────────────────────────────────────────────────

class _StatePool:
    """
    Free-list of 3 recycled CognitiveState objects.
    Warmup consumes one; active turn consumes one; post-turn overlap uses one.
    Falls back to fresh allocation if pool is empty — never crashes.
    """
    __slots__ = ("_free",)

    def __init__(self, size: int = 3):
        self._free: list[CognitiveState] = [CognitiveState() for _ in range(size)]

    def acquire(self, raw_input: str, window: tuple) -> CognitiveState:
        s = self._free.pop() if self._free else CognitiveState()
        s.reset(raw_input, window)
        return s

    def release(self, state: CognitiveState):
        if len(self._free) < 4:
            self._free.append(state)


# ──────────────────────────────────────────────────────────────────────────────
# TurnRecord  — scalars only, cheap to hold 500 of them
# ──────────────────────────────────────────────────────────────────────────────

class TurnRecord:
    __slots__ = (
        "turn_id", "wall_time", "user_input", "output",
        "intent", "emotion_tone", "emotion_intensity", "emotion_trajectory",
        "valence_velocity",                          # v3.5: rate of mood change
        "confidence", "complexity", "hot_ms",
        "issues_count", "insights_count", "relationship_stage",
        "top_stage_ns",                              # v3.5: slowest pipeline stage name+ns
    )

    def __init__(self, state: "CognitiveState", hot_ns: int):
        self.turn_id            = state.turn_id
        self.wall_time          = time.time()
        self.user_input         = state.raw_input
        self.output             = state.output
        self.intent             = state.intent.get("primary", "unknown")
        self.emotion_tone       = state.emotion.get("tone_hint", "neutral_balanced")
        self.emotion_intensity  = state.emotion.get("intensity", 0.0)
        self.emotion_trajectory = state.emotion.get("trajectory", "unknown")
        self.confidence         = state.confidence
        self.complexity         = int(state.scheduler.complexity)
        self.hot_ms             = hot_ns / 1_000_000
        self.issues_count       = len(state.reflection.get("issues", []))
        self.insights_count     = len(state.reflection.get("insights", []))
        self.relationship_stage = state.world_model.get("relationship_stage", "transactional")
        self.valence_velocity   = state.emotion.get("valence_velocity", 0.0)
        # Identify the single slowest pipeline stage for latency debugging
        tel = state.telemetry
        if tel:
            top_stage = max(tel.items(), key=lambda x: x[1])
            self.top_stage_ns = f"{top_stage[0]}={top_stage[1]//1_000}µs"
        else:
            self.top_stage_ns = ""

    def to_window_entry(self) -> tuple[dict, dict]:
        return (
            {"role": "user",      "content": self.user_input},
            {"role": "assistant", "content": self.output},
        )


# ──────────────────────────────────────────────────────────────────────────────
# CognitiveKernel
# ──────────────────────────────────────────────────────────────────────────────

class CognitiveKernel:
    """
    Shiro's central orchestrator.

    Lifecycle:
        kernel = CognitiveKernel(SHIRO_CONFIG)
        await kernel.boot()
        output = await kernel.process("hello")
        ...
        await kernel.shutdown()

    Thread safety: designed for a single asyncio event loop. All module
    calls are synchronous except memory (which uses run_in_executor for
    ChromaDB) — everything runs on one event loop thread.

    Persistent context:
        kernel.set_context("date", "Monday")   # survives every turn
        kernel.clear_context("date")
    """
    __slots__ = (
        "config", "_pool", "_booted", "_shutting_down",
        "scheduler_mod", "perception_mod", "memory_mod",
        "world_mod", "reasoning_mod", "response_mod",
        "metacog_mod", "attention_mod", "emotion_mod",
        "identity_mod", "_multi_agent_hub",
        "_turn_history",       # deque[TurnRecord] maxlen=500
        "_conv_window",        # deque[dict] user/assistant pairs
        "_window_size",
        "_persistent_ctx",     # dict[str, Any]
        "error_log",           # deque[dict] maxlen=200
        "_post_turn_tasks",    # set[asyncio.Task]
        "_watchdog_task",
    )

    def __init__(self, config: dict | None = None):
        self.config             = config or {}
        self._pool              = _StatePool()
        self._booted            = False
        self._shutting_down     = False
        # Module slots — populated in boot()
        self.scheduler_mod      = None
        self.perception_mod     = None
        self.memory_mod         = None
        self.world_mod          = None
        self.reasoning_mod      = None
        self.response_mod       = None
        self.metacog_mod        = None
        self.attention_mod      = None
        self.emotion_mod        = None
        self.identity_mod       = None
        self._multi_agent_hub: Any = None
        # State
        self._window_size       = self.config.get("conversation_window_turns", 12)
        self._conv_window       = deque(maxlen=self._window_size * 2)
        self._turn_history      = deque(maxlen=500)
        self._persistent_ctx: dict[str, Any] = {}
        self.error_log          = deque(maxlen=200)
        self._post_turn_tasks: set[asyncio.Task] = set()
        self._watchdog_task: asyncio.Task | None = None

    # ── Boot ──────────────────────────────────────────────────────────────────

    async def boot(self, warmup: bool = True):
        t0 = time.perf_counter()

        # Support both package import (cognition.X) and flat import (X)
        try:
            from .attention_system  import AttentionSystem
            from .emotion_system    import EmotionWeightingSystem
            from .identity_system   import IdentityContinuationSystem
            from .memory_system     import MemorySystem
            from .cognitive_modules import (
                SchedulerModule, PerceptionModule, WorldModelModule,
                ReasoningModule, ResponseModule, MetaCognitionModule,
            )
        except ImportError:
            from attention_system  import AttentionSystem
            from emotion_system    import EmotionWeightingSystem
            from identity_system   import IdentityContinuationSystem
            from memory_system     import MemorySystem
            from cognitive_modules import (
                SchedulerModule, PerceptionModule, WorldModelModule,
                ReasoningModule, ResponseModule, MetaCognitionModule,
            )

        self.memory_mod     = MemorySystem(self.config.get("memory", {}))
        await self.memory_mod.init()

        self.attention_mod  = AttentionSystem(self.config.get("attention", {}))
        self.emotion_mod    = EmotionWeightingSystem(self.config.get("emotion", {}))
        self.identity_mod   = IdentityContinuationSystem(self.config.get("identity", {}))
        self.scheduler_mod  = SchedulerModule()
        self.perception_mod = PerceptionModule()
        self.world_mod      = WorldModelModule(self.memory_mod)
        self.reasoning_mod  = ReasoningModule()
        self.response_mod   = ResponseModule(self.config.get("persona", "shiro"))
        self.metacog_mod    = MetaCognitionModule(
            self.memory_mod, self.identity_mod, self.reasoning_mod
        )
        self._booted = True

        if warmup:
            await self._warmup()

        self._watchdog_task = asyncio.create_task(self._watchdog())
        elapsed_ms = (time.perf_counter() - t0) * 1000
        log.info(f"Shiro v3.6 booted in {elapsed_ms:.1f} ms (warmup={'on' if warmup else 'off'})")

    # ── Warmup ────────────────────────────────────────────────────────────────

    async def _warmup(self):
        try:
            dummy = self._pool.acquire("warmup ping", ())
            dummy.context["_warmup"] = True
            await self._hot_pipeline(dummy)
            self._pool.release(dummy)
            log.debug("Warmup complete")
        except Exception as exc:
            log.debug(f"Warmup non-fatal: {exc}")

    # ── Main entry ────────────────────────────────────────────────────────────

    async def process(
        self,
        user_input: str,
        context_override: dict | None = None,
    ) -> str:
        if not self._booted:
            await self.boot()
        if self._shutting_down:
            return "[Shiro is shutting down — please try again shortly.]"

        # Snapshot window as immutable tuple before acquiring state
        window: tuple = tuple(self._conv_window)
        s = self._pool.acquire(user_input, window)

        if self._persistent_ctx:
            s.context.update(self._persistent_ctx)
        if context_override:
            s.context.update(context_override)

        hot_start = time.perf_counter_ns()
        try:
            await self._hot_pipeline(s)
        except Exception as exc:
            s.flag_error("kernel", exc)
            s.output = f"[Shiro — something went wrong ({type(exc).__name__}). Logged.]"

        hot_ns = time.perf_counter_ns() - hot_start
        output = s.output

        record = TurnRecord(s, hot_ns)
        self._turn_history.append(record)
        user_e, asst_e = record.to_window_entry()
        self._conv_window.append(user_e)
        self._conv_window.append(asst_e)

        # asyncio.shield wraps the coroutine in a Future that survives
        # cancellation of process(). Use ensure_future (accepts both coroutines
        # and Futures) instead of create_task (only accepts coroutines) to avoid
        # "a coroutine was expected, got Future" errors.
        shielded = asyncio.shield(self._post_turn(s, record))
        task = asyncio.ensure_future(shielded)
        self._post_turn_tasks.add(task)
        task.add_done_callback(self._post_turn_tasks.discard)

        return output

    # ── Hot pipeline ──────────────────────────────────────────────────────────

    async def _hot_pipeline(self, s: CognitiveState):
        sch = s.scheduler
        mk  = s.mark   # local alias: cuts one attribute lookup per call

        self.scheduler_mod.run(s);        mk("scheduler")
        self.attention_mod.gate_input(s); mk("attention_gate")

        # Salience spike → escalate complexity
        if s.attention.get("salience_spike") and sch.complexity < Complexity.DEEP:
            sch.complexity = Complexity(sch.complexity + 1)
            sch.enable_reflection = True

        self.perception_mod.run(s);       mk("perception")
        self.perception_mod.expand_context(s); mk("expand_context")  # derive complexity_hint, likely_format
        await self.memory_mod.retrieve(s);mk("memory")     # memory before emotion: emotional records inform weighting
        self.emotion_mod.weight_state(s); mk("emotion")    # emotion sees retrieved emotional memories
        self.world_mod.update(s);         mk("world_model")
        self.perception_mod.model_intent(s); mk("model_intent")  # refine intent using world context
        self.identity_mod.check(s);       mk("identity")
        self.reasoning_mod.generate_hypotheses(s); mk("hypotheses")

        if sch.enable_debate:
            self.reasoning_mod.internal_debate(s); mk("debate")

        self.reasoning_mod.reason(s);          mk("reason")
        self.reasoning_mod.select_strategy(s); mk("strategy")

        if sch.enable_planning:
            self.reasoning_mod.plan(s);         mk("plan")

        self.attention_mod.weight_response(s); mk("attention_weight")
        self.response_mod.run(s);              mk("response")
        self.response_mod.apply_style(s);      mk("apply_style")   # format hints by register/expertise
        self.response_mod.validate(s);         mk("validate")      # coherence check before returning

        if sch.multi_agent_mode and self._multi_agent_hub:
            await self._multi_agent_hub.dispatch(s); mk("multi_agent")

    # ── Post-turn ─────────────────────────────────────────────────────────────

    async def _post_turn(self, s: CognitiveState, record: TurnRecord):
        try:
            if s.scheduler.enable_reflection:
                await self.metacog_mod.reflect(s)
            self.metacog_mod.align_goals(s)
            await self.metacog_mod.learning_update(s, record, list(self._turn_history))
            await self.memory_mod.store(s)
            self.identity_mod.update_snapshot(s)
            if s.scheduler.enable_curiosity:
                self.metacog_mod.curiosity_pass(s)
        except Exception as exc:
            self.error_log.append({
                "turn_id": s.turn_id[:8],
                "error":   f"{type(exc).__name__}: {exc}",
                "ts":      time.time(),
            })
            log.warning(f"Post-turn [{s.turn_id[:8]}]: {exc}")
        finally:
            self._pool.release(s)

    # ── Watchdog ──────────────────────────────────────────────────────────────

    async def _watchdog(self):
        _sem_warn_count = 0  # FIX: suppress repetitive offline warnings
        while not self._shutting_down:
            await asyncio.sleep(60)
            try:
                if not self.memory_mod.semantic.available:
                    _sem_warn_count += 1
                    if _sem_warn_count <= 3:  # warn max 3 times then go quiet
                        log.warning("WATCHDOG: semantic memory offline")
                drift = self.identity_mod.drift_flags[-20:]
                hard_drifts = sum(1 for f in drift if "HARD" in f)
                if hard_drifts >= 3:
                    log.warning(f"WATCHDOG: {hard_drifts} HARD identity drift flags")
                err_count = len(self.error_log)
                if err_count >= 10:
                    log.warning(f"WATCHDOG: {err_count} post-turn errors accumulated")
                # Check memory DB health
                stats = self.memory_mod.stats()
                if stats["episodic"]["total"] > 50_000:
                    log.warning("WATCHDOG: episodic DB over 50k records — consider pruning")
                # Latency spike: avg hot_ms > 500 ms is a sign of blocking I/O
                recent_hot = [t.hot_ms for t in list(self._turn_history)[-5:]]
                if recent_hot and (sum(recent_hot)/len(recent_hot)) > 500:
                    log.warning(f"WATCHDOG: avg hot_ms={sum(recent_hot)/len(recent_hot):.0f} — possible I/O block")
            except Exception as exc:
                log.debug(f"Watchdog: {exc}")

    # ── Shutdown ──────────────────────────────────────────────────────────────

    async def shutdown(self, timeout: float = 10.0):
        self._shutting_down = True
        log.info(f"Shiro shutting down ({len(self._post_turn_tasks)} tasks in-flight)")

        if self._post_turn_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._post_turn_tasks, return_exceptions=True),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                log.warning("Shutdown: post-turn tasks timed out")
                for t in self._post_turn_tasks:
                    t.cancel()

        if self._watchdog_task:
            self._watchdog_task.cancel()

        if self.identity_mod:
            self.identity_mod.save_snapshot_to_disk()

        if self.memory_mod:
            self.memory_mod.cancel_bg_tasks()

        if self._multi_agent_hub:
            await self._multi_agent_hub.shutdown()

        log.info("Shiro shutdown complete.")

    def new_session(self):
        """
        Reset per-session state without full shutdown.
        Clears conversation window, emotional drift, and attention salience.
        Identity, memory, and learned profile are preserved.
        """
        self._conv_window.clear()
        if self.emotion_mod:
            self.emotion_mod.reset_session()
        if self.attention_mod:
            self.attention_mod.reset_salience()
        if self.world_mod:
            self.world_mod.reset_session_state()
        log.info("Shiro: new session started (memory/identity preserved)")

    # ── Persistent context ────────────────────────────────────────────────────

    def set_context(self, key: str, value: Any):
        self._persistent_ctx[key] = value

    def clear_context(self, key: str):
        self._persistent_ctx.pop(key, None)

    # ── Multi-agent ───────────────────────────────────────────────────────────

    def activate_multi_agent(self, agent_configs: list[dict]):
        from multi_agent_hub import MultiAgentHub
        self._multi_agent_hub = MultiAgentHub(agent_configs)

    # ── Introspection ─────────────────────────────────────────────────────────

    def status(self) -> dict:
        recent_hot = [t.hot_ms for t in list(self._turn_history)[-10:]]
        avg_hot = sum(recent_hot) / len(recent_hot) if recent_hot else 0.0
        last = list(self._turn_history)[-1] if self._turn_history else None

        return {
            "booted":            self._booted,
            "shutting_down":     self._shutting_down,
            "turns_completed":   len(self._turn_history),
            "window_entries":    len(self._conv_window),
            "error_count":       len(self.error_log),
            "last_errors":       list(self.error_log)[-3:],
            "avg_hot_ms":        round(avg_hot, 2),
            "last_turn": {
                "intent":          last.intent if last else None,
                "tone":            last.emotion_tone if last else None,
                "trajectory":      last.emotion_trajectory if last else None,
                "valence_velocity":round(last.valence_velocity, 3) if last else None,
                "relationship":    last.relationship_stage if last else None,
                "confidence":      round(last.confidence, 2) if last else None,
                "hot_ms":          round(last.hot_ms, 2) if last else None,
                "complexity":      last.complexity if last else None,
                "slowest_stage":   last.top_stage_ns if last else None,
            },
            "emotion":           self.emotion_mod.current_state() if self.emotion_mod else {},
            "identity_turns":    self.identity_mod.turn_count if self.identity_mod else 0,
            "identity_stable":   self.identity_mod.identity_stable() if self.identity_mod else True,
            "memory":            self.memory_mod.stats() if self.memory_mod else {},
            "memory_online":     self.memory_mod.semantic.available if self.memory_mod else False,
            "post_turn_active":  len(self._post_turn_tasks),
            "persistent_ctx":    list(self._persistent_ctx.keys()),
            "recurring_emotions":self.emotion_mod.recurring_emotions() if self.emotion_mod else [],
        }

    def get_conversation_window(self) -> list[dict]:
        return list(self._conv_window)

    def pipeline_telemetry(self, n: int = 5) -> list[dict]:
        """
        Return per-stage timing for the last n turns.
        Each entry: {turn_id, hot_ms, slowest_stage, stages: {name: µs}}.
        Use this to find exactly which module is slow on your i9.
        """
        out = []
        for t in list(self._turn_history)[-n:]:
            out.append({
                "turn_id":      t.turn_id[:8],
                "hot_ms":       round(t.hot_ms, 2),
                "slowest_stage":t.top_stage_ns,
            })
        return out

    def get_turn_history(self, n: int = 20) -> list[dict]:
        return [
            {
                "turn_id":          t.turn_id[:8],
                "intent":           t.intent,
                "tone":             t.emotion_tone,
                "trajectory":       t.emotion_trajectory,
                "velocity":         round(t.valence_velocity, 3),
                "intensity":        round(t.emotion_intensity, 2),
                "relationship":     t.relationship_stage,
                "confidence":       round(t.confidence, 2),
                "complexity":       t.complexity,
                "hot_ms":           round(t.hot_ms, 2),
                "slowest_stage":    t.top_stage_ns,
                "issues":           t.issues_count,
                "insights":         t.insights_count,
            }
            for t in list(self._turn_history)[-n:]
        ]