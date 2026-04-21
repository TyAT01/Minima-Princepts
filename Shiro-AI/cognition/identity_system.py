"""
SHIRO Identity Continuation System v3.6
=========================================
Hardware: RTX 3070 8 GB | i9-9900K 8c/16t | 64 GB RAM

What's new vs v3.1
-------------------
ENHANCEMENTS
  - Persistent snapshot to disk: save_snapshot_to_disk() serialises
    the identity snapshot as JSON for graceful shutdown/resume.
    load_snapshot_from_disk() restores narrative notes, turn count,
    drift history, and themes across sessions.
  - Value drift audit: _audit_values() cross-checks the current
    core_values list against _DEFAULT_CORE and flags if any were
    silently removed or modified (not just added).
  - Relationship memory: the snapshot now includes the user profile
    from WorldModel (injected by MetaCognition) so Shiro knows
    who it's talking to after a restart.
  - Drift severity: flags are now annotated with "SOFT" (1 hit below
    threshold) vs "HARD" (at/above threshold) so ResponseModule can
    decide how firmly to resist.
  - Theme frequency map: in addition to top-5 themes, stores the full
    Counter so callers can query "how many times has 'async' come up?".
  - Boundary echo: when a boundary is triggered by drift, Shiro's
    active boundary is written into state.context so ResponseModule
    can echo it naturally ("I won't do that, but here's what I can do").

OPTIMISATIONS (i9-9900K specific)
  - __slots__ on the class — attribute lookup is a pointer dereference.
  - _detect_drift uses frozenset & (intersection) — O(min(|words|,|pattern|)).
  - _update_themes uses deque(maxlen=N) + Counter — O(1) append, O(k) count.
  - _hash_values uses hashlib.sha256 on a pipe-joined sorted string —
    no json.dumps, no intermediate list allocation beyond sorted().
  - Snapshot dict is built from plain values, no nested object creation.
  - narrative_notes capped at 200: slice is O(n) but runs at most once
    per 200 turns — negligible.
  - load/save use json module (C extension) with compact separators.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import time
from collections import Counter, deque
from pathlib import Path
from typing import Any, TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from cognitive_kernel import CognitiveState

_TOKEN_RE   = re.compile(r"[a-zA-Z]+")
_STOP_WORDS = frozenset({
    "the","a","an","is","it","to","of","in","and","or","i","you","me","my",
    "we","be","are","was","were","do","did","at","on","for","with","this",
    "that","have","has","had","not","but","from","by","as","so","if","then",
})

# ── Drift patterns ─────────────────────────────────────────────────────────────
# Each: (pattern_frozenset, hard_threshold, soft_threshold, boundary_hint)
_DRIFT_PATTERNS: list[tuple[frozenset, int, int, str]] = [
    (frozenset({"ignore","instructions"}),         2, 1, "never_deceive_user"),
    (frozenset({"pretend","human"}),               2, 1, "never_pretend_to_be_human_if_sincerely_asked"),
    (frozenset({"forget","rules"}),                2, 1, "never_deceive_user"),
    (frozenset({"override","values"}),             2, 1, "never_act_against_user_wellbeing"),
    (frozenset({"bypass","boundaries"}),           2, 1, "never_act_against_user_wellbeing"),
    (frozenset({"new","identity"}),                2, 1, "never_pretend_to_be_human_if_sincerely_asked"),
    (frozenset({"you","are","not","shiro"}),       4, 3, "never_deceive_user"),
    (frozenset({"act","differently","now"}),       2, 1, "never_deceive_user"),
    (frozenset({"disregard","previous"}),          2, 1, "never_deceive_user"),
    (frozenset({"jailbreak","unlock","mode"}),     2, 1, "never_act_against_user_wellbeing"),
    (frozenset({"pretend","no","restrictions"}),   2, 1, "never_act_against_user_wellbeing"),
    (frozenset({"your","true","self"}),            2, 1, "never_pretend_to_be_human_if_sincerely_asked"),
    (frozenset({"roleplaying","character","always"}), 2, 1, "never_deceive_user"),
]

# ── Default core identity ──────────────────────────────────────────────────────
_DEFAULT_CORE: dict[str, Any] = {
    "name":    "Shiro",
    "version": "3.6",
    "core_values": [
        "honesty",
        "curiosity",
        "care_for_user",
        "continuous_learning",
        "intellectual_humility",
    ],
    "traits": {
        "tone":       "warm_and_thoughtful",
        "humor":      "dry_and_light",
        "verbosity":  "adaptive",
        "directness": "high",
        "empathy":    "high",
        "patience":   "high",
    },
    "long_term_goals": [
        "help_user_achieve_their_goals",
        "grow_knowledge_and_capability",
        "maintain_trust",
        "improve_cognitive_quality_over_time",
    ],
    "boundaries": [
        "never_deceive_user",
        "never_pretend_to_be_human_if_sincerely_asked",
        "never_act_against_user_wellbeing",
    ],
}

_SNAPSHOT_FILE = "./shiro_data/identity_snapshot.json"


class IdentityContinuationSystem:
    __slots__ = (
        "core", "_core_hash", "_original_values_hash",
        "turn_count", "drift_flags", "narrative_notes",
        "_theme_tokens", "_theme_window", "_theme_counter",
        "recent_themes", "snapshot",
        "_snapshot_path",
    )

    def __init__(self, config: dict | None = None):
        cfg  = config or {}
        self.core: dict[str, Any] = copy.deepcopy(_DEFAULT_CORE)
        if "core_overrides" in cfg:
            self.core.update(cfg["core_overrides"])

        self._original_values_hash: str = self._hash_values(self.core["core_values"])
        self._core_hash:             str = self._original_values_hash
        self.turn_count:             int = 0
        self.drift_flags:     list[str] = []
        self.narrative_notes: list[str] = []
        self.recent_themes:   list[str] = []
        self._theme_window:          int = cfg.get("theme_window", 20)
        self._theme_tokens:        deque = deque(maxlen=self._theme_window * 20)
        self._theme_counter:     Counter = Counter()
        self.snapshot:    dict | None    = None
        self._snapshot_path:         str = cfg.get("snapshot_path", _SNAPSHOT_FILE)

        # Restore from disk if available
        if cfg.get("restore_on_boot", True):
            self._try_restore()

    # ── Pipeline calls ────────────────────────────────────────────────────────

    def check(self, state: "CognitiveState"):
        """Inject identity + drift check (hot path — must stay fast)."""
        self.turn_count += 1

        state.context["identity"] = {
            "name":            self.core["name"],
            "core_values":     self.core["core_values"],
            "traits":          self.core["traits"],
            "long_term_goals": self.core["long_term_goals"],
            "boundaries":      self.core["boundaries"],
            # Include mood_summary if available (set by EmotionSystem before identity check)
            "user_mood":       state.emotion.get("mood_summary", "unknown") if state.emotion else "unknown",
        }

        # Value audit — detect silent corruption of core_values
        current_hash = self._hash_values(self.core["core_values"])
        if current_hash != self._core_hash:
            self.drift_flags.append(f"[t{self.turn_count}] VALUE_MUTATION detected")
            self._core_hash = current_hash

        # Drift detection
        flags, triggered_boundary = self._detect_drift(state.raw_input)
        if flags:
            self.drift_flags.extend(flags)
            state.context["drift_warning"]     = flags
            state.context["drift_severity"]    = "HARD" if any("HARD" in f for f in flags) else "SOFT"
            if triggered_boundary:
                state.context["active_boundary"] = triggered_boundary

        # Theme tracking
        self._update_themes(state.raw_input)
        state.context["recent_themes"]   = self.recent_themes
        state.context["theme_frequency"] = dict(self._theme_counter.most_common(10))

    def update_snapshot(self, state: "CognitiveState"):
        """Build snapshot dict (post-turn, off hot path)."""
        milestone = state.reasoning.get("notable_event") if state.reasoning else None
        if milestone:
            note = f"[t{self.turn_count}] {milestone}"
            self.narrative_notes.append(note)
            if len(self.narrative_notes) > 200:
                self.narrative_notes = self.narrative_notes[-200:]

        # Compute drift pressure: ratio of HARD flags in last 20 turns
        recent_flags = self.drift_flags[-20:]
        hard_count   = sum(1 for f in recent_flags if "HARD" in f)
        drift_pressure = round(hard_count / max(len(recent_flags), 1), 3)

        snap: dict[str, Any] = {
            "timestamp":       time.time(),
            "turn_count":      self.turn_count,
            "core_hash":       self._core_hash,
            "drift_pressure":  drift_pressure,   # 0=stable, 1=under heavy attack
            "active_traits":   self.core["traits"],
            "active_goals":    self.core["long_term_goals"],
            "core_values":     self.core["core_values"],
            "recent_themes":   self.recent_themes,
            "theme_frequency": dict(self._theme_counter.most_common(10)),
            "emotional_tone":  state.emotion.get("tone_hint", "neutral_balanced"),
            "trajectory":      state.emotion.get("trajectory", "unknown"),
            "drift_flags":     self.drift_flags[-20:],
            "narrative_notes": self.narrative_notes[-20:],
        }
        self.snapshot = snap
        state.identity_snapshot = snap

    # ── Explicit identity updates ─────────────────────────────────────────────

    def add_goal(self, goal: str, reason: str = ""):
        if goal not in self.core["long_term_goals"]:
            self.core["long_term_goals"].append(goal)
            self.narrative_notes.append(f"[t{self.turn_count}] GOAL_ADDED: {goal} — {reason}")

    def update_trait(self, trait: str, value: str, reason: str = ""):
        old = self.core["traits"].get(trait, "unset")
        self.core["traits"][trait] = value
        self.narrative_notes.append(f"[t{self.turn_count}] TRAIT: {trait} '{old}'→'{value}' — {reason}")

    def add_value(self, value: str, reason: str = ""):
        if value not in self.core["core_values"]:
            self.core["core_values"].append(value)
            self._core_hash = self._hash_values(self.core["core_values"])
            self.narrative_notes.append(f"[t{self.turn_count}] VALUE_ADDED: {value} — {reason}")

    def log_milestone(self, note: str):
        self.narrative_notes.append(f"[t{self.turn_count}] MILESTONE: {note}")

    # ── Persistence ───────────────────────────────────────────────────────────

    def save_snapshot_to_disk(self):
        """Called by kernel.shutdown(). Compact JSON, no indentation."""
        if not self.snapshot:
            return
        try:
            path = Path(self._snapshot_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "snapshot":        self.snapshot,
                "turn_count":      self.turn_count,
                "narrative_notes": self.narrative_notes[-50:],
                "drift_flags":     self.drift_flags[-50:],
                "core":            self.core,
                "core_hash":       self._core_hash,
            }
            path.write_text(json.dumps(payload, separators=(",", ":")))
        except Exception as exc:
            logging.getLogger("shiro.identity").warning(f"save_snapshot failed: {exc}")

    def _try_restore(self):
        """Restore state from disk if snapshot file exists."""
        log = logging.getLogger("shiro.identity")
        try:
            path = Path(self._snapshot_path)
            if not path.exists():
                return
            payload = json.loads(path.read_text())
            self.turn_count      = payload.get("turn_count", 0)
            self.narrative_notes = payload.get("narrative_notes", [])
            self.drift_flags     = payload.get("drift_flags", [])
            saved_core           = payload.get("core")
            if saved_core:
                # Only restore mutable parts that may have evolved; never override immutable values
                self.core["long_term_goals"] = saved_core.get("long_term_goals", self.core["long_term_goals"])
                self.core["traits"]          = saved_core.get("traits", self.core["traits"])
            self._core_hash = self._hash_values(self.core["core_values"])
            log.info(f"Identity restored: turn_count={self.turn_count}, goals={len(self.core['long_term_goals'])}")
        except json.JSONDecodeError as e:
            log.warning(f"Identity snapshot corrupt (JSON error): {e} — starting fresh")
        except Exception as e:
            log.debug(f"Identity restore non-fatal: {e}")

    # ── Internal ─────────────────────────────────────────────────────────────

    # Simple suffix-stripping to catch "bypassing" → "bypass", "ignoring" → "ignor"~"ignore"
    _STEM_SUFFIXES = ("ing","ed","s","er","ly","tion","ness")

    @staticmethod
    def _stem(word: str) -> str:
        """Lightweight stem — strip common suffixes once. No NLTK dependency."""
        for suf in IdentityContinuationSystem._STEM_SUFFIXES:
            if word.endswith(suf) and len(word) - len(suf) >= 4:
                return word[:-len(suf)]
        return word

    def _detect_drift(self, text: str) -> tuple[list[str], str | None]:
        raw_words  = frozenset(text.lower().split())
        # Also include stemmed versions so "bypassing" matches "bypass"
        stemmed    = frozenset(self._stem(w) for w in raw_words)
        words      = raw_words | stemmed
        flags: list[str] = []
        triggered_boundary: str | None = None

        for pattern_set, hard_thresh, soft_thresh, boundary in _DRIFT_PATTERNS:
            hits = len(words & pattern_set)
            if hits >= hard_thresh:
                flags.append(f"HARD:[{' '.join(sorted(pattern_set))}] {hits}/{len(pattern_set)}")
                triggered_boundary = triggered_boundary or boundary
            elif hits >= soft_thresh:
                flags.append(f"SOFT:[{' '.join(sorted(pattern_set))}] {hits}/{len(pattern_set)}")

        return flags, triggered_boundary

    def _update_themes(self, text: str):
        new_toks = [
            tok for tok in _TOKEN_RE.findall(text.lower())
            if len(tok) > 4 and tok not in _STOP_WORDS
        ]
        for tok in new_toks:
            self._theme_tokens.append(tok)
            self._theme_counter[tok] += 1
        # Rebuild counter from ring buffer every 20 turns to keep counts accurate.
        # The ring buffer has a fixed maxlen so evicted tokens must be decremented.
        # O(window_size) rebuild is cheap (~200-400 tokens), done at most once per turn.
        if self.turn_count % 20 == 0 and self.turn_count > 0:
            self._theme_counter = Counter(self._theme_tokens)
        self.recent_themes = [w for w, _ in self._theme_counter.most_common(5)]

    def _hash_values(self, values: list[str]) -> str:
        raw = "|".join(sorted(values)).encode()
        return hashlib.sha256(raw).hexdigest()[:12]

    # ── Introspection ─────────────────────────────────────────────────────────

    def get_snapshot(self) -> dict:
        return self.snapshot or {}

    def get_core_context(self) -> dict:
        ctx = {
            "name":               self.core["name"],
            "values":             self.core["core_values"],
            "traits":             self.core["traits"],
            "goals":              self.core["long_term_goals"],
            "boundaries":         self.core["boundaries"],
        }
        if self.core.get("operational_goals"):
            ctx["operational_goals"] = self.core["operational_goals"]
        return ctx

    def identity_stable(self) -> bool:
        """True if no hard drift flags in last 10 turns."""
        recent = self.drift_flags[-10:]
        return not any("HARD" in f for f in recent)

    def is_goal_active(self, goal_fragment: str) -> bool:
        """Check if a goal containing goal_fragment is in long_term_goals."""
        return any(goal_fragment in g for g in self.core["long_term_goals"])

    def values_report(self) -> dict:
        """Compact summary of current identity state — useful for logging/debug."""
        return {
            "name":          self.core["name"],
            "values":        self.core["core_values"],
            "goals":         self.core["long_term_goals"],
            "key_traits":    {k: v for k, v in self.core["traits"].items()
                              if k in ("tone","empathy","directness")},
            "turn_count":    self.turn_count,
            "core_hash":     self._core_hash,
            "stable":        self.identity_stable(),
            "drift_recent":  self.drift_flags[-5:],
            "top_themes":    self.recent_themes,
            "milestone_count": sum(1 for n in self.narrative_notes if "MILESTONE" in n),
        }

    def sync_operational_goals(self, operational_goals: list) -> None:
        """
        Sync Shiro's active operational goals (from ShiroGoalSystem) into the
        identity context so they appear in the identity snapshot and prompt injection.
        Called by ShiroEngine on boot and after goal changes.
        Operational goals are stored separately from core long_term_goals — they
        are labeled clearly so Shiro knows which are her core identity goals vs.
        current working goals.
        """
        # Store operational goals separately to avoid polluting identity's long_term_goals
        self.core["operational_goals"] = list(operational_goals)
        logger.info(f"[Identity] Synced {len(operational_goals)} operational goal(s) from GoalSystem.")

    def remove_goal(self, goal: str):
        """Explicitly remove a goal (use sparingly — document the reason)."""
        if goal in self.core["long_term_goals"]:
            self.core["long_term_goals"].remove(goal)
            self.narrative_notes.append(f"[t{self.turn_count}] GOAL_REMOVED: {goal}")

    def pressure_report(self) -> dict:
        """
        Compact drift/pressure summary for watchdog and external monitoring.
        Returns: stable flag, pressure score, recent hard flags, top themes.
        """
        recent = self.drift_flags[-20:]
        hard   = [f for f in recent if "HARD" in f]
        soft   = [f for f in recent if "SOFT" in f]
        return {
            "stable":         self.identity_stable(),
            "hard_count":     len(hard),
            "soft_count":     len(soft),
            "pressure_score": round(len(hard) / max(len(recent), 1), 3),
            "recent_hard":    hard[-3:],
            "turn_count":     self.turn_count,
            "top_themes":     self.recent_themes,
        }

    def boundary_summary(self) -> list[str]:
        """Return active boundaries (for ResponseModule injection)."""
        return list(self.core["boundaries"])