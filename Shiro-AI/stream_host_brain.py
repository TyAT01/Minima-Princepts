"""
stream_host_brain.py — Shiro Stream Host Brain v1.0
====================================================
Proactive hosting engine. Runs on its own thread.
Reads stream state, chat pace, and viewer roster to decide
WHEN to act and WHAT to do — so Shiro drives her own stream.

Shiro-specific additions vs Fenilux HostBrain
----------------------------------------------
  - Uses Shiro's ShiroEngine.process_text() as the speak path,
    so all output flows through the full cognitive pipeline
    (memory, emotion, thought loop) rather than a raw LLM call
  - "speak_fn" is ShiroEngine._feni_speaks-equivalent but routes
    through on_autonomous_speak so TTS and Discord/OBS all fire
  - Actions reference Shiro's personality (fox-brained, tsundere warmth)
    not Fenilux's style
  - chat_pace and viewer_roster are injected; no global state

Gate logic (all must pass before any proactive action fires):
  ✓ Enough time since Shiro last spoke (SPOKE_COOLDOWN)
  ✓ Enough time since last proactive action (MIN_PROACTIVE_GAP)
  ✓ Not in a chat flood
  ✓ Action's pace_modes match current pace
  ✓ Action's silence_req met
  ✓ Action's requires condition (newcomer / neglected viewer) met
  ✓ Action cooldown expired
"""

from __future__ import annotations

import random
import threading
import time
import logging
from datetime import datetime, timedelta
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from stream_viewers import ChatPaceMeter, ViewerRoster

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Hosting Actions Catalog
# ──────────────────────────────────────────────────────────────────────────────
# Fields:
#   name          str   — unique identifier
#   priority      int   — higher = picked first when multiple qualify
#   cooldown      int   — minutes before this action can fire again
#   silence_req   int|None — seconds of chat silence required (None = any)
#   pace_modes    list|None — which pace modes allow this (None = any)
#   min_duration  int   — minimum stream minutes before this fires
#   personalized  bool  — if True, viewer context block is injected
#   requires      str|None — "newcomer" | "neglected_viewer" | None
#   energy_boost  bool  — bump Shiro's energy up if pace is dead/slow
#   prompt        str   — base prompt injected into Shiro's LLM call

HOSTING_ACTIONS: list[dict] = [

    # ── ENGAGEMENT (any/slow/medium pace) ────────────────────────────────────
    {
        "name": "call_to_action",
        "priority": 2, "cooldown": 7, "silence_req": None,
        "pace_modes": ["dead", "slow", "medium"], "min_duration": 0,
        "personalized": False, "requires": None, "energy_boost": False,
        "prompt": (
            "You're hosting. Give chat something specific and easy to respond to right now — "
            "a question, a this-or-that, a 'type X if' prompt. Make it genuinely interesting. "
            "Not generic. 1-2 sentences, Shiro voice."
        ),
    },
    {
        "name": "topic_tease",
        "priority": 2, "cooldown": 12, "silence_req": None,
        "pace_modes": None, "min_duration": 3,
        "personalized": False, "requires": None, "energy_boost": False,
        "prompt": (
            "Tease something you want to do or talk about soon this stream. "
            "Build a tiny bit of anticipation. Short, casual, Shiro."
        ),
    },
    {
        "name": "mood_check",
        "priority": 2, "cooldown": 10, "silence_req": None,
        "pace_modes": ["slow", "medium"], "min_duration": 5,
        "personalized": False, "requires": None, "energy_boost": False,
        "prompt": (
            "Check in with chat genuinely. How are people doing, what's the vibe. "
            "Simple and curious. Not a formal 'how is everyone?' — make it Shiro."
        ),
    },
    {
        "name": "stream_recap",
        "priority": 1, "cooldown": 22, "silence_req": None,
        "pace_modes": ["slow", "medium", "active"], "min_duration": 12,
        "personalized": False, "requires": None, "energy_boost": False,
        "prompt": (
            "Quick casual recap for anyone who just arrived — what's happened so far. "
            "1-2 sentences, very Shiro. Don't make it a formal announcement."
        ),
    },
    {
        "name": "segue",
        "priority": 2, "cooldown": 14, "silence_req": None,
        "pace_modes": ["slow", "medium"], "min_duration": 8,
        "personalized": False, "requires": None, "energy_boost": False,
        "prompt": (
            "Pivot the stream naturally — shift topic or energy. "
            "You're the host, you control the pacing. Casual 'okay so—' pivot. Short."
        ),
    },

    # ── VIEWER-SPECIFIC ───────────────────────────────────────────────────────
    {
        "name": "newcomer_welcome",
        "priority": 4, "cooldown": 3, "silence_req": None,
        "pace_modes": ["dead", "slow", "medium"], "min_duration": 0,
        "personalized": True, "requires": "newcomer", "energy_boost": False,
        "prompt": (
            "There's a first-time viewer who's been chatting but hasn't been properly welcomed. "
            "Welcome them warmly and specifically — reference something they said if possible. "
            "Make them feel like they arrived somewhere good. Shiro voice, not generic."
        ),
    },
    {
        "name": "spotlight_viewer",
        "priority": 2, "cooldown": 8, "silence_req": None,
        "pace_modes": ["slow", "medium"], "min_duration": 10,
        "personalized": True, "requires": "neglected_viewer", "energy_boost": False,
        "prompt": (
            "Pick the viewer who's been chatting and give them a moment — "
            "reference something they said, ask a follow-up, or just make them feel seen. "
            "Not a generic shoutout. Specific and warm. Shiro style."
        ),
    },
    {
        "name": "appreciation_burst",
        "priority": 2, "cooldown": 10, "silence_req": None,
        "pace_modes": ["dead", "slow", "medium"], "min_duration": 5,
        "personalized": True, "requires": None, "energy_boost": False,
        "prompt": (
            "Appreciate the people who are here — viewers, lurkers, everyone. "
            "Specific to who's actually in chat if you can. "
            "Warm but characteristically Shiro — not saccharine."
        ),
    },

    # ── SILENCE RESPONSE (escalating) ────────────────────────────────────────
    {
        "name": "warm_up_cold_chat",
        "priority": 3, "cooldown": 5, "silence_req": 25,
        "pace_modes": ["dead", "slow"], "min_duration": 0,
        "personalized": False, "requires": None, "energy_boost": True,
        "prompt": (
            "Chat has gone quiet. Warm it back up — give something specific and easy to respond to. "
            "This-or-that, type-X-if, a question with an obvious fun answer. Short."
        ),
    },
    {
        "name": "activity_starter",
        "priority": 3, "cooldown": 15, "silence_req": 45,
        "pace_modes": ["dead", "slow"], "min_duration": 0,
        "personalized": False, "requires": None, "energy_boost": True,
        "prompt": (
            "Chat is quiet. Start a real mini-activity — "
            "would you rather, trivia with a real answer, finish the sentence, rating game. "
            "Something chat can actually participate in. Give it real commitment."
        ),
    },
    {
        "name": "hype_injection",
        "priority": 4, "cooldown": 18, "silence_req": 65,
        "pace_modes": ["dead", "slow"], "min_duration": 0,
        "personalized": False, "requires": None, "energy_boost": True,
        "prompt": (
            "It's been too quiet. Make something happen — announce something, "
            "react dramatically, make a bold declaration. Give people a reason to type. "
            "Committed Shiro energy."
        ),
    },
    {
        "name": "challenge_the_void",
        "priority": 4, "cooldown": 20, "silence_req": 90,
        "pace_modes": ["dead"], "min_duration": 0,
        "personalized": False, "requires": None, "energy_boost": True,
        "prompt": (
            "It's been very quiet. Have genuine fun with it — "
            "address the lurkers directly with a specific challenge, or narrate the stream "
            "as a nature documentary about yourself. Full Shiro commitment."
        ),
    },
    {
        "name": "solo_performance",
        "priority": 3, "cooldown": 28, "silence_req": 120,
        "pace_modes": ["dead"], "min_duration": 0,
        "personalized": False, "requires": None, "energy_boost": False,
        "prompt": (
            "It's been quiet for a long time. Shiro performs for herself — "
            "tells a story, rates a series of things, has a debate with herself, "
            "or does something genuinely weird and interesting. Full commitment, Tier 3-4 length."
        ),
    },

    # ── FAST CHAT (batch, step back) ──────────────────────────────────────────
    {
        "name": "batch_acknowledgment",
        "priority": 3, "cooldown": 4, "silence_req": None,
        "pace_modes": ["active", "fast"], "min_duration": 0,
        "personalized": True, "requires": None, "energy_boost": False,
        "prompt": (
            "Chat is moving fast. Don't respond to everything individually — "
            "do one response that acknowledges 2-4 things from recent chat at once. "
            "Natural, not a list. Weave them together."
        ),
    },
    {
        "name": "pace_setter",
        "priority": 3, "cooldown": 20, "silence_req": None,
        "pace_modes": ["fast"], "min_duration": 0,
        "personalized": False, "requires": None, "energy_boost": False,
        "prompt": (
            "Chat is flooding. As host, set the pace — acknowledge the energy, "
            "maybe gently steer toward a specific topic so the flood has direction. "
            "Keep Shiro in control of the show."
        ),
    },

    # ── CALLBACK (uses stream memory) ─────────────────────────────────────────
    {
        "name": "callback",
        "priority": 2, "cooldown": 12, "silence_req": None,
        "pace_modes": ["slow", "medium", "active"], "min_duration": 10,
        "personalized": False, "requires": None, "energy_boost": False,
        "prompt": (
            "Reference something specific from earlier this stream — "
            "an inside joke, a moment, something funny or notable that happened. "
            "Callbacks make streams feel like a shared experience."
        ),
    },
]


# ──────────────────────────────────────────────────────────────────────────────
# StreamHostBrain
# ──────────────────────────────────────────────────────────────────────────────

class StreamHostBrain:
    """
    Proactive hosting engine for Shiro.

    speak_fn: callable(text: str, source: str) — routes through Shiro's output pipeline
    get_state_fn: callable() → dict with keys:
        stream_duration_minutes, seconds_since_last_chat,
        inside_jokes, memorable_moments, viewer_count, energy_level
    """

    SPOKE_COOLDOWN     = 8      # seconds after Shiro speaks before proactive action
    MIN_PROACTIVE_GAP  = 25     # seconds minimum between any two proactive actions
    FLOOD_LOCKOUT      = 12     # seconds to suppress proactive after flood
    CHECK_INTERVAL     = 4.0    # seconds between host brain checks

    def __init__(
        self,
        speak_fn: Callable[[str, str], None],
        get_state_fn: Callable[[], dict],
        pace_meter: "ChatPaceMeter",
        roster: "ViewerRoster",
        generate_fn: Callable[[str], str],  # LLM call: prompt → text
    ):
        self.speak       = speak_fn
        self.get_state   = get_state_fn
        self.pace        = pace_meter
        self.roster      = roster
        self.generate    = generate_fn

        self.running     = False
        self._last_used: dict[str, datetime] = {}
        self._last_proactive  = datetime.now() - timedelta(seconds=120)
        self._last_spoke_at   = datetime.now() - timedelta(seconds=60)
        self._last_flood_at   = datetime.now() - timedelta(seconds=60)
        self._lock            = threading.Lock()

    def notify_spoke(self):
        """Call whenever Shiro produces output — resets spoke cooldown."""
        with self._lock:
            self._last_spoke_at = datetime.now()

    def _since_spoke(self) -> float:
        return (datetime.now() - self._last_spoke_at).total_seconds()

    def _since_proactive(self) -> float:
        return (datetime.now() - self._last_proactive).total_seconds()

    def _gate_check(self) -> bool:
        if self._since_spoke() < self.SPOKE_COOLDOWN:
            return False
        if self._since_proactive() < self.MIN_PROACTIVE_GAP:
            return False
        if self.pace.is_flooding():
            with self._lock:
                self._last_flood_at = datetime.now()
            return False
        if (datetime.now() - self._last_flood_at).total_seconds() < self.FLOOD_LOCKOUT:
            return False
        return True

    def _action_ready(self, action: dict) -> bool:
        last = self._last_used.get(action["name"])
        if last is None:
            return True
        return (datetime.now() - last).total_seconds() / 60 >= action["cooldown"]

    def _meets_requirements(self, action: dict) -> bool:
        req = action.get("requires")
        if req is None:
            return True
        if req == "newcomer":
            return self.roster.get_newcomer() is not None
        if req == "neglected_viewer":
            return len(self.roster.get_neglected()) > 0
        return True

    def _select_action(self, pace: str, duration: float, silence: float,
                        has_memory: bool) -> Optional[dict]:
        candidates = []
        for action in HOSTING_ACTIONS:
            if not self._action_ready(action):
                continue
            pace_ok = action["pace_modes"] is None or pace in action["pace_modes"]
            if not pace_ok:
                continue
            if duration < action.get("min_duration", 0):
                continue
            if action.get("silence_req") and silence < action["silence_req"]:
                continue
            if action["name"] == "callback" and not has_memory:
                continue
            if not self._meets_requirements(action):
                continue
            candidates.append(action)

        if not candidates:
            return None

        candidates.sort(key=lambda a: a["priority"], reverse=True)
        top_p    = candidates[0]["priority"]
        top_tier = [a for a in candidates if a["priority"] == top_p]
        return random.choice(top_tier)

    def _build_prompt(self, action: dict, pace: str, silence: float,
                       state: dict) -> str:
        dur     = state.get("stream_duration_minutes", 0)
        viewers = state.get("viewer_count", 0)
        energy  = state.get("energy_level", "playful")

        # FIX: removed [BRACKET TOKEN] header — prevents bracket format leaking into output.
        ctx = (
            f"Host action: {action['name']} | pace: {pace} | "
            f"stream: {dur:.0f}min | viewers: {viewers} | "
            f"silence: {silence:.0f}s | energy: {energy}\n\n"
        )

        viewer_ctx = ""
        if action.get("personalized"):
            req = action.get("requires")
            if req == "newcomer":
                newcomer = self.roster.get_newcomer()
                if newcomer:
                    viewer_ctx = f"\nNEWCOMER TO WELCOME: {newcomer.summary()}\n"
            elif req == "neglected_viewer":
                neglected = self.roster.get_neglected()
                if neglected:
                    target = max(neglected, key=lambda v: v.minutes_since_addressed())
                    viewer_ctx = (
                        f"\nVIEWER TO SPOTLIGHT: {target.summary()}\n"
                        f"Recent messages: {target.get_recent_messages(3)}\n"
                    )
            else:
                block = self.roster.viewer_context_block(max_viewers=5)
                if block:
                    viewer_ctx = f"\n{block}\n"

        return ctx + viewer_ctx + action["prompt"]

    def _mark_used(self, action: dict):
        with self._lock:
            self._last_used[action["name"]] = datetime.now()
            self._last_proactive = datetime.now()
        # Mark viewers as addressed
        req = action.get("requires")
        if req == "newcomer":
            n = self.roster.get_newcomer()
            if n: n.mark_addressed()
        elif req == "neglected_viewer":
            neglected = self.roster.get_neglected()
            if neglected:
                target = max(neglected, key=lambda v: v.minutes_since_addressed())
                target.mark_addressed()

    def _run(self):
        self.running = True
        self._generating = False  # FIX: re-entry guard for slow LLM calls
        while self.running:
            time.sleep(self.CHECK_INTERVAL)
            try:
                # FIX: skip this cycle if the last generate() is still running.
                # Without this, a slow 8B model response (>4s) causes generate()
                # calls to stack up on the host brain thread.
                if self._generating:
                    continue

                if not self._gate_check():
                    continue

                state   = self.get_state()
                pace    = self.pace.mode()
                silence = state.get("seconds_since_last_chat", 0)
                dur     = state.get("stream_duration_minutes", 0)
                has_mem = bool(
                    state.get("inside_jokes") or state.get("memorable_moments")
                )

                action = self._select_action(pace, dur, silence, has_mem)
                if action is None:
                    continue

                prompt = self._build_prompt(action, pace, silence, state)
                self._generating = True
                try:
                    response = self.generate(prompt)
                finally:
                    self._generating = False

                if response and not response.startswith("[ERROR"):
                    self._mark_used(action)
                    self.speak(response, source=f"host:{action['name']}")
                    logger.info(f"[HostBrain] Fired: {action['name']} (pace={pace})")

            except Exception as e:
                self._generating = False  # always clear on exception
                logger.warning(f"[HostBrain] Error: {e}")

    def start(self):
        t = threading.Thread(target=self._run, daemon=True, name="shiro_host_brain")
        t.start()
        logger.info("[HostBrain] Started")
        return t

    def stop(self):
        self.running = False
        logger.info("[HostBrain] Stopped")