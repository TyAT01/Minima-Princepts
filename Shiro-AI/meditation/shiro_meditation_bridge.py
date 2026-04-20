"""
shiro_meditation_bridge.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Integration bridge between ShiroMeditation (async) and ShiroEngine (sync).

Design:
  • Runs the meditation asyncio loop in a *dedicated background thread* —
    completely isolated from the main synchronous engine.
  • Thread-safe communication via threading.Lock() + shared state dicts.
  • GUI polling via get_gui_status() — always safe to call.
  • Engine integration via notify_activity() / receive_message() / get_reentry_context().
  • Manual controls via begin_manual() / wake_manual().

Usage in main.py:
    bridge = create_meditation_bridge(shiro_root, primary_user, idle_trigger_seconds=600)
    bridge.start()                          # launches background thread
    engine._meditation_ref = bridge         # attach to engine

In ShiroEngine.process_text():
    if self._meditation_ref:
        result = self._meditation_ref.receive_message(text, user)
        if result:
            wake_text, reentry = result
            self._inject_reentry_note(reentry)
            yield wake_text
            return
        self._meditation_ref.notify_activity()

    reentry_note = self._meditation_ref.get_reentry_context() if self._meditation_ref else None
    if reentry_note:
        shiro_context += f"\nMeditation reentry context: {reentry_note}"  # FIX: was [MEDITATION REENTRY] bracket token
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import asyncio
import threading
import logging
import time
import json
from pathlib import Path
from typing import Optional, Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  STUB  (used when shiro_meditation.py is unavailable)
# ═══════════════════════════════════════════════════════════════

class MeditationBridgeStub:
    """No-op fallback when meditation module is not installed."""

    def start(self):              pass
    def stop(self):               pass
    def notify_activity(self):    pass

    def receive_message(self, msg: str, user: str = "user"):
        return None

    def begin_manual(self, depth: str = "standard") -> bool:
        return False

    def wake_manual(self) -> Optional[dict]:
        return None

    def get_gui_status(self) -> dict:
        return {
            "active": False, "phase": None, "depth": None,
            "thoughts_count": 0, "insights_count": 0,
            "duration_seconds": 0, "idle_pct": 0,
            "session_streak": 0, "alignment_score": 0.5,
        }

    def get_recent_thoughts(self) -> list:
        return []

    def get_session_stats(self) -> dict:
        return {
            "total_sessions": 0, "total_seconds": 0,
            "total_insights": 0, "total_questions": 0,
            "current_streak": 0, "avg_alignment": 0.5,
        }

    def get_reentry_context(self) -> Optional[str]:
        return None


# ═══════════════════════════════════════════════════════════════
#  REAL BRIDGE
# ═══════════════════════════════════════════════════════════════

class MeditationBridge:
    """
    Wraps ShiroMeditation and runs it in an isolated asyncio loop
    in a background daemon thread.  All public methods are thread-safe.
    """

    def __init__(
        self,
        shiro_root:           str  = ".",
        primary_user:         str  = "tyler",
        idle_trigger_seconds: int  = 600,
    ):
        self._shiro_root          = shiro_root
        self._primary_user        = primary_user
        self._idle_trigger        = idle_trigger_seconds

        self._lock                = threading.Lock()
        self._loop:   Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread]          = None
        self._med:    Any                                  = None   # ShiroMeditation instance

        # Pending reentry note — consumed once by engine
        self._reentry_note: Optional[str] = None

        # Ready event — set once _async_init completes
        self._ready = threading.Event()
        # Loop-running event — set once run_forever() is actually executing
        self._loop_running = threading.Event()

        # Rolling thought log for GUI (capped at 200)
        self._recent_thoughts: list = []

        # Background asyncio task handles — cancelled on stop() to avoid "Task destroyed" warning
        self._tasks: list = []

        # Lifetime stat accumulators
        self._lifetime: dict = {
            "sessions":   0,
            "seconds":    0.0,
            "insights":   0,
            "questions":  0,
            "alignments": [],   # list of floats for avg
        }
        self._lifetime_path = Path(shiro_root) / "shiro_meditation_stats.json"
        self._load_lifetime()  # restore stats from last run

    # ── Lifecycle ──────────────────────────────────────────────

    def start(self):
        """Launch background meditation thread. Call once from main.py."""
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_loop,
            name="ShiroMeditationThread",
            daemon=True,
        )
        self._thread.start()
        logger.info("[MedBridge] Background meditation thread started.")

    def stop(self):
        """Gracefully stop the meditation loop — cancel tasks first to avoid asyncio warnings."""
        self._save_lifetime()  # persist stats before loop dies
        if self._loop and self._loop.is_running():
            def _cancel_and_stop():
                for t in self._tasks:
                    if not t.done():
                        t.cancel()
                self._loop.stop()
            self._loop.call_soon_threadsafe(_cancel_and_stop)

    # ── Thread entry point ─────────────────────────────────────

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_init())
            # Signal that the loop is now entering run_forever — begin_manual is safe to call
            self._loop_running.set()
            self._loop.run_forever()
        except Exception as e:
            logger.error(f"[MedBridge] Loop error: {e}")
        finally:
            self._loop_running.clear()
            self._loop.close()

    async def _async_init(self):
        """Build ShiroMeditation inside the background loop."""
        try:
            import sys as _sys, os as _os
            _med_dir = _os.path.dirname(_os.path.abspath(__file__))
            if _med_dir not in _sys.path:
                _sys.path.insert(0, _med_dir)
            from shiro_meditation import ShiroMeditation, MeditationConfig

            cfg = MeditationConfig()
            cfg.IDLE_TRIGGER_SECONDS = self._idle_trigger
            cfg.MAX_SESSION_DURATION = 86400   # no hard limit — Shiro peeks herself

            self._med = ShiroMeditation(
                config        = cfg,
                on_thought    = self._on_thought,
                on_wake_stage = self._on_wake_stage,
            )

            # Wire optional callbacks
            if hasattr(self._med, "set_insight_callback"):
                self._med.set_insight_callback(self._on_insight)

            # Start idle monitor and scheduler — store handles so stop() can cancel them
            self._tasks.append(asyncio.ensure_future(self._med.idle_monitor()))
            if hasattr(self._med, "scheduler") and hasattr(self._med.scheduler, "run"):
                self._tasks.append(asyncio.ensure_future(self._med.scheduler.run()))

            logger.info("[MedBridge] ShiroMeditation initialised in background loop.")

        except ImportError:
            logger.warning("[MedBridge] shiro_meditation.py not found — stub mode.")
            self._med = None
        except Exception as e:
            logger.error(f"[MedBridge] Init error: {e}")
            self._med = None
        finally:
            # Signal that init is done (even on failure) so begin_manual doesn't race
            self._ready.set()

    # ── Async callbacks (run in background thread's loop) ──────

    async def _on_thought(self, thought) -> None:
        """Receives a Thought dataclass object from ShiroMeditation."""
        try:
            phase_name = thought.phase.name if hasattr(thought.phase, "name") else str(thought.phase)
            text = thought.content if hasattr(thought, "content") else str(thought)
        except Exception:
            phase_name = "unknown"
            text = str(thought)
        with self._lock:
            self._recent_thoughts.append({
                "phase": phase_name,
                "text":  text,
                "ts":    time.time(),
            })
            if len(self._recent_thoughts) > 200:
                self._recent_thoughts = self._recent_thoughts[-200:]

    async def _on_wake_stage(self, stage: str, **kwargs):
        logger.debug(f"[MedBridge] Wake stage: {stage}")

    async def _on_insight(self, insight: str, session_id: str, phase: str, level: float):
        logger.info(f"[MedBridge] Insight ({phase}, lvl={level:.2f}): {insight[:80]}")
        with self._lock:
            self._lifetime["insights"] += 1
            self._recent_thoughts.append({
                "phase": f"{phase}/INSIGHT",
                "text":  f"✦ {insight}",
                "ts":    time.time(),
            })
        self._save_lifetime()  # persist immediately so insights survive crashes

    # ── Engine-facing API (thread-safe, synchronous) ───────────

    def notify_activity(self):
        """Call on every incoming user message. Resets idle timer."""
        if self._med and self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._med.notify_activity)

    def receive_message(self, msg: str, user: str = "user") -> Optional[tuple]:
        """
        Check if this message should wake Shiro from meditation.
        Returns (wake_text, reentry_note_str) or None.
        Blocks briefly (up to 2s) to await the async result.
        """
        if not self._med or not self._loop or not self._loop.is_running():
            return None

        future = asyncio.run_coroutine_threadsafe(
            self._med.receive_message(msg, user),
            self._loop,
        )
        try:
            result = future.result(timeout=2.0)
        except Exception:
            return None

        if result is None:
            return None

        wake_text, reentry = result
        # reentry may be a ReentryContext object with .as_system_note()
        reentry_str = ""
        if reentry is not None:
            if hasattr(reentry, "as_system_note"):
                reentry_str = reentry.as_system_note()
            else:
                reentry_str = str(reentry)

        with self._lock:
            self._reentry_note = reentry_str

        # Update lifetime stats
        self._update_lifetime_on_wake()

        return wake_text, reentry_str

    def begin_manual(self, depth: str = "standard") -> bool:
        """Trigger meditation manually (e.g. from GUI). Returns True if started."""
        # Wait for init then for the loop to actually be in run_forever
        if not self._ready.wait(timeout=10.0):
            logger.warning("[MedBridge] begin_manual: timed out waiting for init")
            return False
        if not self._loop_running.wait(timeout=5.0):
            logger.warning("[MedBridge] begin_manual: timed out waiting for loop to start")
            return False
        if not self._med:
            logger.warning("[MedBridge] begin_manual: _med is None (init failed?)")
            return False
        if self._med.is_meditating:  # is_meditating is a @property, not a method
            logger.info("[MedBridge] begin_manual: already meditating — waking instead")
            self.wake_manual()
            return True
        # Fire-and-forget — begin() runs for the entire session duration,
        # so we must NOT wait for its result. Just schedule it and return True.
        try:
            asyncio.run_coroutine_threadsafe(
                self._med.begin(depth=depth, trigger="manual"),
                self._loop,
            )
            logger.info(f"[MedBridge] Meditation scheduled (depth={depth})")
            return True
        except Exception as e:
            logger.warning(f"[MedBridge] begin_manual schedule error: {e}")
            return False

    def wake_manual(self) -> Optional[dict]:
        """Send a gentle wake signal from the GUI. Returns current status or None."""
        if not self._med:
            return None
        # notify_activity triggers the peek/wake logic if Shiro is meditating
        self.notify_activity()
        # Also try sending a soft GUI wake message
        if self._loop and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                self._med.receive_message("[operator:wake]", user=self._primary_user),
                self._loop,
            )
            try:
                future.result(timeout=2.0)
            except Exception:
                pass
        return self.get_gui_status()

    # ── GUI polling ─────────────────────────────────────────────

    def get_gui_status(self) -> dict:
        """Returns a lightweight status dict — always safe to call."""
        if not self._med:
            return {
                "active": False, "phase": None, "depth": None,
                "thoughts_count": 0, "insights_count": 0,
                "duration_seconds": 0, "idle_pct": 0,
                "session_streak": 0, "alignment_score": 0.5,
            }
        try:
            s = self._med.status()
            return {
                "active":           s.get("active", False),
                "phase":            s.get("current_phase"),
                "depth":            s.get("depth"),
                "thoughts_count":   s.get("thoughts", 0),
                "insights_count":   s.get("insights", 0),
                "duration_seconds": s.get("duration", 0),
                "idle_pct":         s.get("idle_pct", 0),
                "session_streak":   s.get("growth_streak", 0),
                "alignment_score":  s.get("alignment", 0.5),
            }
        except Exception as e:
            logger.debug(f"[MedBridge] get_gui_status error: {e}")
            return {"active": False, "phase": None, "depth": None,
                    "thoughts_count": 0, "insights_count": 0,
                    "duration_seconds": 0, "idle_pct": 0,
                    "session_streak": 0, "alignment_score": 0.5}

    def get_recent_thoughts(self) -> list:
        """Returns a copy of recent thoughts for GUI display."""
        with self._lock:
            return list(self._recent_thoughts[-50:])

    def get_session_stats(self) -> dict:
        """Returns lifetime meditation statistics."""
        with self._lock:
            aligns = self._lifetime["alignments"]
            avg_align = (sum(aligns) / len(aligns)) if aligns else 0.5
            streak = 0
            if self._med and hasattr(self._med, "growth_tracker"):
                try:
                    streak = self._med.growth_tracker.streak
                except Exception:
                    pass
            return {
                "total_sessions":  self._lifetime["sessions"],
                "total_seconds":   self._lifetime["seconds"],
                "total_insights":  self._lifetime["insights"],
                "total_questions": self._lifetime["questions"],
                "current_streak":  streak,
                "avg_alignment":   round(avg_align, 3),
            }

    def get_reentry_context(self) -> Optional[str]:
        """Returns and clears any pending reentry note for injection into LLM context."""
        with self._lock:
            note = self._reentry_note
            self._reentry_note = None
            return note

    # ── Internal helpers ────────────────────────────────────────

    def _load_lifetime(self):
        """Restore lifetime stats from disk (called once at init)."""
        try:
            if self._lifetime_path.exists():
                text = self._lifetime_path.read_text(encoding="utf-8").strip()
                if text:
                    data = json.loads(text)
                    for key in ("sessions", "seconds", "insights", "questions", "alignments"):
                        if key in data:
                            self._lifetime[key] = data[key]
                    logger.info(
                        f"[MedBridge] Lifetime stats restored — "
                        f"{self._lifetime['sessions']} sessions, "
                        f"{self._lifetime['insights']} insights"
                    )
        except Exception as e:
            logger.warning(f"[MedBridge] Could not load lifetime stats: {e}")

    def _save_lifetime(self):
        """Persist lifetime stats to disk (called after each wake and on stop)."""
        try:
            self._lifetime_path.write_text(
                json.dumps(self._lifetime, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[MedBridge] Could not save lifetime stats: {e}")

    def _update_lifetime_on_wake(self):
        """Update lifetime counters after a wake event."""
        if not self._med:
            return
        try:
            s = self._med.status()
            with self._lock:
                self._lifetime["sessions"] += 1
                self._lifetime["seconds"]  += s.get("duration", 0)
                self._lifetime["questions"] += s.get("questions", 0)
                align = s.get("alignment", 0.5)
                self._lifetime["alignments"].append(align)
                if len(self._lifetime["alignments"]) > 100:
                    self._lifetime["alignments"] = self._lifetime["alignments"][-100:]
            self._save_lifetime()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
#  FACTORY
# ═══════════════════════════════════════════════════════════════

def create_meditation_bridge(
    shiro_root:           str  = ".",
    primary_user:         str  = "tyler",
    idle_trigger_seconds: int  = 600,
    auto_start:           bool = True,
):
    """
    Factory — returns a live MeditationBridge or a no-op stub.
    Always safe to call; will never raise.
    """
    try:
        bridge = MeditationBridge(
            shiro_root           = shiro_root,
            primary_user         = primary_user,
            idle_trigger_seconds = idle_trigger_seconds,
        )
        if auto_start:
            bridge.start()
        return bridge
    except Exception as e:
        logger.error(f"[MedBridge] Could not create bridge: {e} — using stub.")
        return MeditationBridgeStub()