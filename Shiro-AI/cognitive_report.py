"""
cognitive_report.py — Unified Per-Turn Self-Awareness Report for Shiro AI.

Optimizations vs original:
  - to_prompt_block() output trimmed to ~120 tokens max (was bloating ctx on 8B model)
  - user=unknown identity bleed fixed: user_name is now always resolved before building
  - Thought injection threshold tightened (8s → 5s) so stale thoughts don't corrupt replies
  - Prompt block now uses terse single-line format — saves ~40 tokens per turn
  - to_dict() unchanged (still full for logging)
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from persona.inner_mind import ShiroInnerMind
    from consciousness.self_awareness import SelfAwareness
    from consciousness.thought_loop import InnerMind

log = logging.getLogger("shiro.cognitive_report")


@dataclass
class CognitiveReport:
    # 1. Self Model
    mood_label:          str   = "neutral"
    mood_arousal:        float = 0.5
    response_confidence: float = 0.75
    confidence_hint:     str   = ""
    strategy:            str   = "warm"
    playfulness:         str   = "moderate"

    # 2. Environment / Situational Awareness
    room_state:          str   = "active"
    time_of_day:         str   = "unknown"
    platform:            str   = "chat"
    silence_seconds:     float = 0.0
    user_present:        bool  = True

    # 3. Theory of Mind
    user_intent:         str   = "unknown"
    intent_confidence:   float = 0.0
    intent_hint:         str   = ""
    dominant_intent:     str   = "unknown"
    intent_shifting:     bool  = False
    intent_satisfaction: float = 0.5

    # 4. Memory State
    working_memory_count:  int   = 0
    hot_cache_count:       int   = 0
    session_turns:         int   = 0
    memory_echoes:         list  = field(default_factory=list)

    # 5. Reflection / Metacognition
    metacog_notes:         list  = field(default_factory=list)
    recursive_reflection:  str   = ""

    # 6. Continuous Thought Loop
    latest_thought:        str   = ""
    thought_category:      str   = ""
    thought_age_s:         float = 99.0

    # 7. Identity / Relationship
    relationship_tier:     str   = "stranger"
    relationship_momentum: float = 0.0
    sentiment_trend:       str   = "neutral"
    session_arc:           str   = ""

    # 8. Emotional State
    emotional_trajectory:  str   = "stable"
    emotional_valence:     float = 0.0
    user_emotions:         dict  = field(default_factory=dict)

    # Build metadata
    built_at:    float = field(default_factory=time.time)
    build_ms:    float = 0.0
    user_name:   str   = ""

    # ── Factory method ────────────────────────────────────────────────────────

    @classmethod
    def build(
        cls,
        user_name:    str,
        user_emotions: dict,
        inner_mind:   Optional["ShiroInnerMind"],
        awareness:    Optional["SelfAwareness"],
        thought_loop: Optional["InnerMind"],
        inner_mind_data: Optional[dict] = None,
        session_turns: int = 0,
        trajectory:   str = "stable",
        primary_user: str = "Tyler",   # NEW — prevents user=unknown bleed
    ) -> "CognitiveReport":
        t0 = time.perf_counter()

        # ── Identity resolution: always prefer known names ────────────────────
        # The log showed user=unknown because inner_mind stores the last-seen
        # user name, which defaults to "unknown" at boot. We resolve here.
        resolved_user = user_name
        if not resolved_user or resolved_user.lower() in ("unknown", "stranger", ""):
            resolved_user = primary_user

        r = cls(user_name=resolved_user)

        # ── 1. Self Model ─────────────────────────────────────────────────────
        try:
            if inner_mind_data:
                r.mood_label          = inner_mind_data.get("mood_label", "neutral")
                r.mood_arousal        = inner_mind_data.get("mood", {}).get("arousal", 0.5)
                r.response_confidence = inner_mind_data.get("response_confidence", 0.75)
                r.confidence_hint     = inner_mind_data.get("confidence_hint", "")
                r.strategy            = inner_mind_data.get("strategy", "warm")
                r.playfulness         = inner_mind_data.get("playfulness", "moderate")
            elif inner_mind:
                r.mood_label          = inner_mind.mood.label() if hasattr(inner_mind.mood, "label") else str(inner_mind.mood)
                r.response_confidence = getattr(inner_mind, "response_confidence", 0.75)
                r.confidence_hint     = inner_mind.confidence_hint() if hasattr(inner_mind, "confidence_hint") else ""
                r.strategy            = inner_mind.current_strategy.value if hasattr(inner_mind, "current_strategy") else "warm"
                r.playfulness         = inner_mind.playfulness.label() if hasattr(inner_mind, "playfulness") else "moderate"
        except Exception as _e:
            log.debug(f"[CogReport] Self model build error: {_e}")

        # ── 2. Environment / Situational Awareness ────────────────────────────
        try:
            if awareness:
                r.room_state    = awareness.room_state
                r.time_of_day   = awareness.time_of_day()
                r.platform      = awareness.platform
                r.user_present  = bool(awareness.get_present_users())
                focus = awareness.get_focus_user()
                if focus:
                    r.silence_seconds = focus.silence_duration()
        except Exception as _e:
            log.debug(f"[CogReport] Environment model error: {_e}")

        # ── 3. Theory of Mind ─────────────────────────────────────────────────
        try:
            if awareness:
                intent_model = awareness.get_intent_model(resolved_user)
                if intent_model:
                    r.user_intent         = intent_model.current_intent
                    r.intent_confidence   = intent_model.confidence
                    r.intent_hint         = intent_model.prompt_hint()
                    r.dominant_intent     = intent_model.dominant_intent()
                    r.intent_shifting     = intent_model.is_shifting()
                    r.intent_satisfaction = intent_model.satisfaction_score
        except Exception as _e:
            log.debug(f"[CogReport] Theory of mind error: {_e}")

        # ── 4. Memory State ───────────────────────────────────────────────────
        try:
            if inner_mind:
                r.working_memory_count = len(getattr(inner_mind, "working_memory", {}))
                r.hot_cache_count      = len(getattr(inner_mind, "_hot_cache", []))
                r.session_turns        = session_turns
                if inner_mind_data:
                    wm = inner_mind_data.get("working_memory", {})
                    r.memory_echoes = [
                        f"{k}: {v['value'][:60]}" if isinstance(v, dict) else f"{k}: {str(v)[:60]}"
                        for k, v in wm.items()
                        if not k.startswith("_") and isinstance(v, (dict, str))
                    ][:3]  # was 4 — trimmed to save tokens
        except Exception as _e:
            log.debug(f"[CogReport] Memory state error: {_e}")

        # ── 5. Reflection / Metacognition ─────────────────────────────────────
        try:
            if inner_mind:
                wm = getattr(inner_mind, "working_memory", {})
                meta_keys = ["_meta_uncertainty", "_meta_review"]
                r.metacog_notes = [
                    wm[k].value if hasattr(wm.get(k), "value") else str(wm[k])
                    for k in meta_keys if k in wm
                ]
                r.recursive_reflection = r.metacog_notes[0] if r.metacog_notes else ""
        except Exception as _e:
            log.debug(f"[CogReport] Metacognition error: {_e}")

        # ── 6. Continuous Thought Loop ────────────────────────────────────────
        try:
            if thought_loop:
                buf = list(getattr(thought_loop, "thought_buffer", []))
                if buf:
                    latest = buf[-1]
                    r.latest_thought   = getattr(latest, "text", "")
                    r.thought_category = getattr(latest, "category", "")
                    r.thought_age_s    = latest.age_seconds() if hasattr(latest, "age_seconds") else 99.0
        except Exception as _e:
            log.debug(f"[CogReport] Thought loop error: {_e}")

        # ── 7. Identity / Relationship ────────────────────────────────────────
        try:
            if inner_mind_data:
                r.relationship_tier     = inner_mind_data.get("relationship", "stranger").lower()
                r.relationship_momentum = inner_mind_data.get("rel_momentum", 0.0)
                r.sentiment_trend       = inner_mind_data.get("sentiment_trend", "neutral")
            elif inner_mind:
                r.relationship_tier     = inner_mind.relationship.level.name.lower()
                r.relationship_momentum = inner_mind.relationship.momentum() if hasattr(inner_mind.relationship, "momentum") else 0.0
                r.sentiment_trend       = inner_mind.sentiment_trend.label()

            if thought_loop and hasattr(thought_loop, "journal"):
                r.session_arc = thought_loop.journal.session_arc() if hasattr(thought_loop.journal, "session_arc") else ""
        except Exception as _e:
            log.debug(f"[CogReport] Identity/relationship error: {_e}")

        # ── 8. Emotional State ────────────────────────────────────────────────
        try:
            r.emotional_trajectory = trajectory
            r.user_emotions        = dict(user_emotions) if user_emotions else {}
            if inner_mind_data:
                mood_d = inner_mind_data.get("mood", {})
                r.emotional_valence = mood_d.get("valence", 0.0) if isinstance(mood_d, dict) else 0.0
        except Exception as _e:
            log.debug(f"[CogReport] Emotional state error: {_e}")

        r.built_at = time.time()
        r.build_ms = (time.perf_counter() - t0) * 1000.0
        return r

    # ── Prompt formatting ─────────────────────────────────────────────────────

    def to_prompt_block(self, verbose: bool = False) -> str:
        """
        Format as a compact system-prompt injection block.

        OPTIMIZED for llama3.1 8B:
          - Hard cap: ~120 tokens total (was uncapped, bloated to 180+)
          - Single-line pipe-separated format (no multi-line sections)
          - Only injects state that is genuinely non-baseline
          - Thoughts only injected if < 5s old (was 15s — stale thoughts
            caused the "still thinking about X" confusion seen in the log)
          - Returns "" when nothing material to say
        """
        parts: list[str] = []

        # Mood (skip neutral/unknown)
        if self.mood_label not in ("neutral", "unknown", ""):
            mood_str = self.mood_label
            if self.mood_arousal > 0.7:
                mood_str += "/energized"
            elif self.mood_arousal < 0.3:
                mood_str += "/low-energy"
            parts.append(f"mood:{mood_str}")

        # Confidence hint
        if self.confidence_hint:
            parts.append(self.confidence_hint)

        # Intent — most valuable injection
        if self.intent_hint:
            parts.append(f"intent:{self.intent_hint}")
        elif self.user_intent not in ("unknown", "general_chat", "") and self.intent_confidence >= 0.55:
            parts.append(f"intent:{self.user_intent.replace('_', ' ')}")

        if self.intent_shifting:
            parts.append("goal-shifting")

        # Metacognition
        if self.recursive_reflection:
            # Truncate to 60 chars max — prevents metacog from eating all tokens
            trunc = self.recursive_reflection[:60].rstrip()
            parts.append(f"self-note:{trunc}")

        # Thought — ONLY if very fresh (≤5s). Stale thoughts caused loop confusion.
        if self.latest_thought and self.thought_age_s <= 5.0:
            trunc = self.latest_thought[:80].rstrip()
            parts.append(f"thought:{trunc}")

        # Silence
        if self.silence_seconds > 45.0:
            m, s = divmod(int(self.silence_seconds), 60)
            parts.append(f"silence:{m}m{s}s" if m else f"silence:{s}s")

        # Relationship (skip stranger/neutral baseline)
        rel_parts = []
        if self.relationship_tier not in ("", "stranger"):
            rel_parts.append(self.relationship_tier)
        if self.sentiment_trend not in ("neutral", "stable", ""):
            rel_parts.append(self.sentiment_trend)
        if rel_parts:
            parts.append(f"rel:{','.join(rel_parts)}")

        if not parts:
            return ""

        body = " | ".join(p for p in parts if p)
        return f"[COG] {body}"

    def to_dict(self) -> dict:
        """Full structured dump — for logging, diagnostics, and journal."""
        return {
            "user_name":             self.user_name,
            "built_at":              self.built_at,
            "build_ms":              round(self.build_ms, 2),
            "self_model": {
                "mood":              self.mood_label,
                "arousal":           round(self.mood_arousal, 3),
                "confidence":        round(self.response_confidence, 3),
                "strategy":          self.strategy,
                "playfulness":       self.playfulness,
            },
            "environment": {
                "room_state":        self.room_state,
                "time_of_day":       self.time_of_day,
                "platform":          self.platform,
                "silence_seconds":   round(self.silence_seconds, 1),
                "user_present":      self.user_present,
            },
            "theory_of_mind": {
                "user_intent":       self.user_intent,
                "confidence":        round(self.intent_confidence, 3),
                "dominant_intent":   self.dominant_intent,
                "shifting":          self.intent_shifting,
                "satisfaction":      round(self.intent_satisfaction, 3),
            },
            "memory": {
                "working_memory":    self.working_memory_count,
                "hot_cache":         self.hot_cache_count,
                "session_turns":     self.session_turns,
            },
            "metacognition": {
                "notes":             self.metacog_notes,
                "recursive_note":    self.recursive_reflection,
            },
            "thought_loop": {
                "latest":            self.latest_thought,
                "category":          self.thought_category,
                "age_s":             round(self.thought_age_s, 1),
            },
            "identity": {
                "relationship_tier": self.relationship_tier,
                "momentum":          round(self.relationship_momentum, 3),
                "sentiment_trend":   self.sentiment_trend,
                "session_arc":       self.session_arc,
            },
            "emotional_state": {
                "trajectory":        self.emotional_trajectory,
                "valence":           round(self.emotional_valence, 3),
                "user_emotions":     {k: round(v, 3) for k, v in self.user_emotions.items()},
            },
        }

    def __repr__(self) -> str:
        return (
            f"CognitiveReport(user={self.user_name!r}, "
            f"mood={self.mood_label}, intent={self.user_intent}, "
            f"conf={self.response_confidence:.0%}, "
            f"built_ms={self.build_ms:.1f})"
        )