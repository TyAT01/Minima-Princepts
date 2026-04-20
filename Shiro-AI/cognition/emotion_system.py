"""
SHIRO Emotion Weighting System v3.6
=====================================
Hardware: RTX 3070 8 GB | i9-9900K 8c/16t | 64 GB RAM

What's new vs v3.1
-------------------
ENHANCEMENTS
  - Emotion trajectory: detects whether the user's emotional state is
    IMPROVING, STABLE, or DETERIORATING across the last 4 turns.
    Feeds into ReasoningModule so Shiro responds to trends not just snapshots.
  - Intensity thresholds: MILD (0.2–0.4), MODERATE (0.4–0.65), HIGH (0.65+).
    Each maps to a different urgency hint used by the scheduler.
  - Negation modifier: "not happy", "don't feel great" inverts the detected
    cue polarity before blending — previously Shiro would detect "happy"
    and ignore "not".
  - Ambivalence detection: when positive and negative cues co-occur at similar
    intensity (within 0.15 VAD distance) the state is tagged as "ambivalent",
    which feeds a special tone hint "gently_curious".
  - Emotional memory: stores which labels appeared in the last 10 turns for
    pattern-matching (e.g. "user feels lonely repeatedly").

OPTIMISATIONS (i9-9900K specific)
  - VAD stored as three plain floats in __slots__ — no dict, no dataclass.
  - _CUE_VAD pre-computed at import: zero dict-in-dict lookup per token.
  - Cue scan is a single regex findall + frozenset lookup, no per-token branch.
  - history is deque(maxlen=50) — O(1) append, no slice, fixed memory.
  - _top_labels uses a hand-unrolled nearest-neighbour over 17 entries —
    faster than sorted() on a list of tuples for N=17.
  - _trajectory computed from a 4-element window of float intensities — O(4).
  - All derived scalars computed from plain float arithmetic, no objects.
"""

from __future__ import annotations

import heapq
import math
import re
import time
from collections import Counter, deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cognitive_kernel import CognitiveState

# ── Compiled pattern — once at import ─────────────────────────────────────────
_TOKEN_RE = re.compile(r"[a-zA-Z']+")

# Negation window: if any of these precede a cue word, invert its polarity
_NEGATION_WORDS = frozenset({
    "not", "no", "never", "don't", "doesn't", "didn't", "can't",
    "won't", "isn't", "aren't", "wasn't", "weren't", "hardly",
    "barely", "scarcely",
})

# ── VAD coordinates: (valence, arousal, dominance) — plain tuples ─────────────
_EMOTION_VAD: dict[str, tuple[float, float, float]] = {
    "joy":           ( 0.82,  0.62,  0.42),
    "curiosity":     ( 0.52,  0.42,  0.32),
    "satisfaction":  ( 0.72,  0.10,  0.52),
    "enthusiasm":    ( 0.78,  0.82,  0.52),
    "affection":     ( 0.78,  0.32,  0.22),
    "pride":         ( 0.62,  0.40,  0.72),
    "hope":          ( 0.60,  0.32,  0.22),
    "calm":          ( 0.40,  -0.30,  0.50),
    "sadness":       (-0.62, -0.42, -0.32),
    "frustration":   (-0.52,  0.52, -0.22),
    "anxiety":       (-0.58,  0.62, -0.42),
    "anger":         (-0.72,  0.82,  0.52),
    "boredom":       (-0.22, -0.62, -0.22),
    "shame":         (-0.72, -0.22, -0.62),
    "loneliness":    (-0.68, -0.32, -0.42),
    "surprise":      ( 0.12,  0.72,  0.02),
    "confusion":     (-0.12,  0.22, -0.32),
    "exhaustion":    (-0.40, -0.70, -0.40),
    "neutral":       ( 0.00,  0.00,  0.00),
}

# ── Lexical cue → (emotion_label, intensity) ──────────────────────────────────
_CUE_MAP: dict[str, tuple[str, float]] = {
    "love":        ("affection",    0.82),
    "happy":       ("joy",          0.76),
    "excited":     ("enthusiasm",   0.82),
    "glad":        ("joy",          0.62),
    "proud":       ("pride",        0.72),
    "great":       ("satisfaction", 0.56),
    "wonderful":   ("joy",          0.72),
    "curious":     ("curiosity",    0.66),
    "hope":        ("hope",         0.62),
    "thank":       ("affection",    0.52),
    "please":      ("affection",    0.36),
    "calm":        ("calm",         0.55),
    "relaxed":     ("calm",         0.52),
    "peaceful":    ("calm",         0.58),
    "sad":         ("sadness",      0.72),
    "angry":       ("anger",        0.76),
    "hate":        ("anger",        0.82),
    "fear":        ("anxiety",      0.72),
    "scared":      ("anxiety",      0.76),
    "hurt":        ("sadness",      0.66),
    "lonely":      ("loneliness",   0.72),
    "miss":        ("loneliness",   0.56),
    "sorry":       ("shame",        0.46),
    "ashamed":     ("shame",        0.76),
    "boring":      ("boredom",      0.56),
    "bored":       ("boredom",      0.60),
    "frustrated":  ("frustration",  0.72),
    "frustrating": ("frustration",  0.66),
    "broken":      ("frustration",  0.56),
    "fail":        ("frustration",  0.56),
    "failing":     ("frustration",  0.60),
    "error":       ("frustration",  0.42),
    "wow":         ("surprise",     0.76),
    "weird":       ("confusion",    0.56),
    "strange":     ("confusion",    0.52),
    "confused":    ("confusion",    0.66),
    "anxious":     ("anxiety",      0.72),
    "stressed":    ("anxiety",      0.66),
    "overwhelmed": ("anxiety",      0.78),
    "depressed":   ("sadness",      0.82),
    "tired":       ("exhaustion",   0.68),
    "exhausted":   ("exhaustion",   0.82),
    "empty":       ("sadness",      0.76),
    "numb":        ("sadness",      0.70),
    "hopeless":    ("sadness",      0.84),
    "worthless":   ("shame",        0.80),
    "lost":        ("confusion",    0.62),
}

# Pre-compute cue → weighted VAD at import time (zero per-turn cost)
# Format: cue → (dv, da, dd, weight, label)
_CUE_VAD: dict[str, tuple[float, float, float, float, str]] = {}
for _cue, (_lbl, _inten) in _CUE_MAP.items():
    _v, _a, _d = _EMOTION_VAD[_lbl]
    _CUE_VAD[_cue] = (_v * _inten, _a * _inten, _d * _inten, _inten, _lbl)

# Pre-build VAD list for label resolution (17 + 2 entries — tiny)
_VAD_LIST: list[tuple[str, float, float, float]] = [
    (lbl, v, a, d) for lbl, (v, a, d) in _EMOTION_VAD.items()
]

# Intensity thresholds
_INTENSITY_MILD     = 0.20
_INTENSITY_MODERATE = 0.42
_INTENSITY_HIGH     = 0.65


class EmotionWeightingSystem:
    """
    Tracks Shiro's running emotional context using the VAD model.
    State stored as three plain floats (cv, ca, cd) — minimal overhead.
    Adds trajectory, ambivalence, and emotion pattern memory.
    """
    __slots__ = (
        "inertia", "decay_rate",
        "bv", "ba", "bd",           # baseline VAD
        "cv", "ca", "cd",           # current VAD
        "_history",                 # deque of lightweight dicts
        "_intensity_history",       # deque[float] for trajectory
        "_valence_history",         # deque[float] for velocity/trajectory (v3.4)
        "_label_counter",           # Counter[str] — label frequency
        "_session_bv",              # session baseline drift (v3.4)
        "_session_ba",
        "_session_bd",
    )

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.inertia     = cfg.get("inertia",            0.82)  # how sticky the current state is
        self.decay_rate  = cfg.get("decay_rate",         0.04)
        self.bv          = cfg.get("baseline_valence",   0.15)
        self.ba          = cfg.get("baseline_arousal",  -0.10)
        self.bd          = cfg.get("baseline_dominance", 0.20)
        self.cv, self.ca, self.cd = self.bv, self.ba, self.bd
        self._history:           deque = deque(maxlen=cfg.get("history_max", 50))
        self._intensity_history: deque = deque(maxlen=10)
        self._valence_history:   deque = deque(maxlen=8)   # for velocity detection
        self._label_counter: Counter   = Counter()
        # Session emotional baseline: drifts slowly toward repeated emotions
        self._session_bv: float = self.bv
        self._session_ba: float = self.ba
        self._session_bd: float = self.bd

    # ── weight_state — hot path ───────────────────────────────────────────────

    def weight_state(self, state: "CognitiveState"):
        """
        Detect emotion cues in input, blend into running VAD, write to state.
        Handles negation inversion and ambivalence detection.
        All arithmetic on plain floats — no object allocation.
        """
        text   = state.raw_input.lower()
        focus  = state.attention.get("top_focus", ())
        tokens = _TOKEN_RE.findall(text)

        # Build negation positions: set of indices where a negation appears
        neg_positions: set[int] = set()
        for i, tok in enumerate(tokens):
            if tok in _NEGATION_WORDS:
                # Mark the next 1-2 tokens as negated
                neg_positions.add(i + 1)
                neg_positions.add(i + 2)

        # ── scan cues with negation awareness ────────────────────────────
        tv = ta = td = tw = 0.0
        pos_weight = neg_weight = 0.0
        cues: list[tuple[str, float, bool]] = []   # (tok, weight, negated)

        for i, tok in enumerate(tokens):
            entry = _CUE_VAD.get(tok)
            if not entry:
                continue
            dv, da, dd, w, lbl = entry
            negated = i in neg_positions
            if negated:
                # Invert valence and dominance, keep arousal (still activated)
                dv, da, dd = -dv * 0.7, da * 0.5, -dd * 0.7
                neg_weight += w
                cues.append((tok, w, True))
            else:
                pos_weight += w
                cues.append((tok, w, False))
            tv += dv; ta += da; td += dd; tw += w

        # Focus-token boost (un-negated only)
        for tok in focus:
            entry = _CUE_VAD.get(tok)
            if entry:
                dv, da, dd, w, _ = entry
                boost = 0.10
                tv += dv * boost; ta += da * boost; td += dd * boost
                tw += w * boost

        # Implicit distress fallback: heavy negation without explicit cues
        # e.g. "I just can't figure this out" — no cue word but frustrated
        if tw == 0.0:
            neg_density = len(state.attention.get("suppressed", [])) / max(
                state.interpretation.get("token_count", 1) if state.interpretation else 1, 1
            )
            if neg_density > 0.15 and state.context.get("has_negation"):
                # Mild frustration signal
                tv, ta, td = -0.25, 0.35, -0.15
                tw = 0.3

        # ── blend toward target ───────────────────────────────────────
        if tw > 0.0:
            inv = 1.0 / tw
            tv *= inv; ta *= inv; td *= inv
        else:
            tv, ta, td = self.bv, self.ba, self.bd

        shift = 1.0 - self.inertia
        self.cv += (tv - self.cv) * shift
        self.ca += (ta - self.ca) * shift
        self.cd += (td - self.cd) * shift

        # ── decay toward baseline ─────────────────────────────────────
        dr = self.decay_rate
        self.cv += (self.bv - self.cv) * dr
        self.ca += (self.ba - self.ca) * dr
        self.cd += (self.bd - self.cd) * dr

        # ── session baseline drift (emotional contagion) ─────────────
        # If Shiro keeps responding to the same emotional tone, the session
        # baseline drifts 1% toward it — keeps response appropriate over time
        if tw > 0.0:
            self._session_bv += (tv - self._session_bv) * 0.01
            self._session_ba += (ta - self._session_ba) * 0.01
            self._session_bd += (td - self._session_bd) * 0.01

        # ── derived scalars ───────────────────────────────────────────
        cv, ca, cd = self.cv, self.ca, self.cd
        intensity = math.sqrt(cv*cv + ca*ca + cd*cd)
        if intensity > 1.0:
            intensity = 1.0

        # Valence velocity: rate of change in valence over last 4 turns
        self._valence_history.append(cv)
        valence_velocity = 0.0
        vhist = list(self._valence_history)
        if len(vhist) >= 3:
            valence_velocity = round(vhist[-1] - vhist[-3], 3)  # change over 2 turns

        labels = self._top_labels(3)
        tone   = self._tone(cv, ca, cd)

        # ── intensity band ────────────────────────────────────────────
        if   intensity >= _INTENSITY_HIGH:     band = "high"
        elif intensity >= _INTENSITY_MODERATE: band = "moderate"
        elif intensity >= _INTENSITY_MILD:     band = "mild"
        else:                                  band = "baseline"

        # ── trajectory ────────────────────────────────────────────────
        self._intensity_history.append(intensity)
        trajectory = self._trajectory()

        # ── ambivalence ───────────────────────────────────────────────
        ambivalent = (
            pos_weight > 0.20 and neg_weight > 0.20
            and abs(pos_weight - neg_weight) < 0.25
        )
        if ambivalent:
            tone = "gently_curious"

        # ── label frequency memory ────────────────────────────────────
        for lbl in labels:
            self._label_counter[lbl] += 1

        mem_bias = min(1.0, intensity * 0.82)

        mood_str = (
            f"{'high' if intensity>=0.65 else 'moderate' if intensity>=0.42 else 'mild' if intensity>=0.20 else 'baseline'}"
            f" {labels[0] if labels else 'neutral'}, {trajectory}, v={valence_velocity:+.3f}"
        )
        state.emotion = {
            "vad":             {"valence": round(cv,3), "arousal": round(ca,3), "dominance": round(cd,3)},
            "labels":          labels,
            "primary_label":   labels[0] if labels else "neutral",
            "mood_summary":    mood_str,   # one-line for LLM injection
            "detected_cues":   [(tok, w) for tok, w, _ in cues],
            "negated_cues":    [(tok, w) for tok, w, neg in cues if neg],
            "intensity":       round(intensity, 3),
            "intensity_band":  band,
            "tone_hint":       tone,
            "trajectory":      trajectory,
            "ambivalent":      ambivalent,
            "valence_velocity":valence_velocity,   # +ve = improving, -ve = declining
            "memory_bias":     round(mem_bias, 3),
        }

        self._history.append({
            "tone":      tone,
            "intensity": round(intensity, 3),
            "labels":    labels[:2],
        })

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _top_labels(self, n: int) -> list[str]:
        """Nearest-neighbour in VAD space. heapq.nsmallest is O(N log n) vs O(N log N) sort."""
        cv, ca, cd = self.cv, self.ca, self.cd
        dists = (
            (math.sqrt((cv-v)**2 + (ca-a)**2 + (cd-d)**2), lbl)
            for lbl, v, a, d in _VAD_LIST
        )
        return [lbl for _, lbl in heapq.nsmallest(n, dists)]

    def _tone(self, v: float, a: float, d: float) -> str:
        if   v >  0.50 and a >  0.40: return "warm_energetic"
        elif v >  0.40 and a < -0.05: return "warm_calm"
        elif v >  0.20:               return "gentle_positive"
        elif v < -0.50 and a >  0.50: return "intense_concern"
        elif v < -0.40:               return "gentle_support"
        elif a >  0.60:               return "focused_alert"
        elif a < -0.50:               return "subdued"
        return "neutral_balanced"

    def _trajectory(self) -> str:
        """
        Trend over last 4 turns using VALENCE (not intensity).
        Valence is directional: +ve = positive, -ve = negative.
        Intensity alone can't distinguish joy from distress.
        """
        hist = list(self._valence_history)[-4:]
        if len(hist) < 3:
            return "unknown"
        delta = hist[-1] - hist[0]
        if   delta < -0.12: return "deteriorating"  # valence falling = worse
        elif delta >  0.12: return "improving"       # valence rising = better
        else:               return "stable"

    def current_state(self) -> dict:
        labels = self._top_labels(3)
        vh = list(self._valence_history)
        vel = round(vh[-1] - vh[-3], 3) if len(vh) >= 3 else 0.0
        return {
            "vad":              {"valence": round(self.cv,3), "arousal": round(self.ca,3), "dominance": round(self.cd,3)},
            "session_baseline": {"valence": round(self._session_bv,3), "arousal": round(self._session_ba,3)},
            "labels":           labels,
            "tone":             self._tone(self.cv, self.ca, self.cd),
            "trajectory":       self._trajectory(),
            "valence_velocity": vel,
        }

    def mood_summary(self) -> str:
        """
        One-line mood description for direct LLM prompt injection.
        e.g. "moderate anxiety, deteriorating, velocity=-0.18"
        """
        labels = self._top_labels(2)
        primary = labels[0] if labels else "neutral"
        intensity = math.sqrt(self.cv**2 + self.ca**2 + self.cd**2)
        vh = list(self._valence_history)
        vel = round(vh[-1] - vh[-3], 3) if len(vh) >= 3 else 0.0
        band = (
            "high"     if intensity >= 0.65 else
            "moderate" if intensity >= 0.42 else
            "mild"     if intensity >= 0.20 else
            "baseline"
        )
        traj = self._trajectory()
        return f"{band} {primary}, {traj}, velocity={vel:+.3f}"

    def reset_session(self):
        """Call between conversation sessions to clear emotional drift."""
        self.cv, self.ca, self.cd = self.bv, self.ba, self.bd
        self._session_bv, self._session_ba, self._session_bd = self.bv, self.ba, self.bd
        self._history.clear()
        self._intensity_history.clear()
        self._valence_history.clear()
        self._label_counter.clear()

    def recurring_emotions(self, top_n: int = 3) -> list[tuple[str, int]]:
        """Which emotions have appeared most across turns."""
        return self._label_counter.most_common(top_n)

    def emotional_history(self, n: int = 10) -> list:
        return list(self._history)[-n:]

    def reset_baseline(self, valence: float = 0.15, arousal: float = -0.10, dominance: float = 0.20):
        """Adjust Shiro's emotional resting state."""
        self.bv, self.ba, self.bd = valence, arousal, dominance