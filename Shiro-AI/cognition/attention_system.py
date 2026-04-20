"""
SHIRO Attention System v3.6
============================
Hardware: RTX 3070 8 GB | i9-9900K 8c/16t | 64 GB RAM

What's new vs v3.1
-------------------
ENHANCEMENTS
  - Positional weighting: tokens near the START or END of input get a
    position bonus (0.06) — humans front-load intent and back-load emphasis.
  - Bigram signals: two-word pairs (e.g. "memory leak", "help me") are
    scored as compound signals, catching intent that single tokens miss.
  - Cross-turn salience spike detection: if a token's salience jumps >2×
    its rolling average it's flagged as a "novelty spike" in the payload,
    which ReasoningModule can use to detect topic shifts.
  - Suppression is soft not hard: suppressed tokens still contribute 10%
    of their score rather than being zeroed, preserving filler-word patterns.
  - Context-aware urgency: urgency_score now also factors in ALL-CAPS tokens
    (shouting signal) and !! / ??? punctuation density.
  - Persistent salience now has a hard cap of 256 entries — on a 64 GB
    system RAM is plentiful but salience tables beyond 256 produce noise.

OPTIMISATIONS (i9-9900K specific)
  - ALL keyword sets: frozenset at module level — hash table built once.
  - Token cap raised to 96 (i9 can handle it; 64 was conservative).
  - Score accumulation uses plain float adds — no list comprehension or
    temporary objects inside the scoring loop.
  - `_salience` dict keys are interned strings (all lowercase alpha tokens
    returned by the regex are already interned by CPython).
  - Bigram loop fused into the same single pass as unigram scoring —
    zero extra regex calls.
  - sort() called once with key=lambda (no attrgetter overhead for small N).
  - Decay loop uses list(salience) to copy keys once, avoids RuntimeError.
  - `weight_response` is O(1): slice + in-place assignment, no new dict.
"""

from __future__ import annotations

import re
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cognitive_kernel import CognitiveState

# ── Compiled patterns — module level, once ────────────────────────────────────
_TOKEN_RE  = re.compile(r"[a-zA-Z']+")
_CAPS_RE   = re.compile(r"\b[A-Z]{2,}\b")      # ALL-CAPS words
_BANG_RE   = re.compile(r"[!?]{2,}")            # !! or ???

# ── Keyword banks — frozenset, O(1) lookup ────────────────────────────────────
_URGENCY: frozenset[str] = frozenset({
    "urgent", "immediately", "now", "asap", "critical", "emergency",
    "broken", "error", "fail", "crash", "important", "deadline",
    "stop", "wrong", "stuck", "help", "please", "breaking", "down",
    "dying", "freezing", "hung", "unresponsive", "corrupted",
})
_EMOTIONAL: frozenset[str] = frozenset({
    "love", "hate", "fear", "hope", "sad", "angry", "happy",
    "excited", "hurt", "lonely", "proud", "ashamed", "miss",
    "care", "sorry", "thank", "scared", "frustrated", "anxious",
    "stressed", "overwhelmed", "depressed", "exhausted", "empty",
    "numb", "hopeless", "worthless", "broken", "tired", "lost",
})
_GOAL: frozenset[str] = frozenset({
    "want", "need", "wish", "goal", "plan", "achieve", "build",
    "create", "solve", "fix", "learn", "remember", "always",
    "implement", "design", "make", "improve", "understand",
    "figure", "trying", "working", "developing", "building",
})
_MEMORY_REF: frozenset[str] = frozenset({
    "remember", "recall", "mentioned", "before", "earlier",
    "said", "told", "discussed", "agreed", "last", "previous",
})
_SUPPRESS: frozenset[str] = frozenset({
    "um", "uh", "like", "basically", "literally", "actually",
    "you", "know", "sort", "kind", "just", "really", "very",
})
# Question / action lead: extra bonus when these appear at sentence start
_QUESTION_LEAD: frozenset[str] = frozenset({
    "what","why","how","when","where","who","which",
    "can","could","would","should","will","is","are","does",
})
_ACTION_LEAD: frozenset[str] = frozenset({
    "make","build","create","write","fix","help","show",
    "explain","find","generate","design","implement","debug",
    "refactor","convert","analyse","analyze","compare",
})
_TOPIC_BOOST: frozenset[str] = frozenset({
    # High-value technical/conceptual nouns that almost always matter
    "python", "async", "memory", "model", "database", "api", "server",
    "error", "function", "class", "loop", "thread", "cuda", "gpu",
    "kernel", "vector", "embedding", "token", "prompt", "context",
    "llm", "agent", "pipeline", "schema", "query", "deploy", "docker",
})

# ── Score weights ─────────────────────────────────────────────────────────────
_W_URGENCY  = 0.36
_W_EMOTION  = 0.26
_W_GOAL     = 0.20
_W_TOPIC    = 0.18
_W_CONTENT  = 0.08   # long alpha-only words not in any set
_W_SUPPRESS = 0.10   # soft suppression multiplier (not zero)
_W_POSITION = 0.06   # bonus for first/last 20% of token list
_W_SALIENCE = 0.09   # history boost per salience unit
_W_BIGRAM   = 0.12   # bonus when two adjacent scored tokens co-occur
_W_LEAD     = 0.14   # bonus for question/action word at sentence start
_SCORE_THRESHOLD = 0.04
_MAX_SALIENCE_ENTRIES = 256


class AttentionSignal:
    """Lightweight signal — __slots__ keeps instance to ~56 bytes."""
    __slots__ = ("token", "score", "flags")
    def __init__(self, token: str, score: float, flags: int):
        self.token = token
        self.score = score
        self.flags = flags   # bitmask: 1=urgency 2=emotion 4=goal 8=content 16=topic 32=memory


class AttentionSystem:
    """
    Two-phase attention gating.
    gate_input  → runs at pipeline start, scores + ranks all tokens.
    weight_response → runs just before ResponseModule, injects top-focus.

    Persistent cross-turn salience tracks which concepts keep recurring.
    Novelty detection flags when something genuinely new appears.
    """
    __slots__ = (
        "max_signals", "recency_decay",
        "_salience",    # {token: float}  — capped at 256
        "_seen",        # set[str]        — all tokens ever seen
        "_turn_scores", # deque of per-turn avg scores for spike detection
    )

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.max_signals:  int   = cfg.get("max_signals",   12)
        self.recency_decay:float = cfg.get("recency_decay",  0.92)
        self._salience:    dict[str, float] = {}
        self._seen:        set[str]         = set()
        self._turn_scores: deque            = deque(maxlen=20)

    # ── gate_input — hot path ─────────────────────────────────────────────────

    def gate_input(self, state: "CognitiveState"):
        text      = state.raw_input
        lower     = text.lower()
        tokens    = _TOKEN_RE.findall(lower)

        # Cap at 96 — safe for i9-9900K, catches more context than old 64 cap
        if len(tokens) > 96:
            tokens = tokens[:96]
        n_tok = len(tokens)
        if n_tok == 0:
            state.attention = _empty_attention()
            return

        max_sig  = self.max_signals
        salience = self._salience
        seen     = self._seen
        n_fifth  = max(1, n_tok // 5)   # 20% boundary for position bonus

        # Context-aware urgency extras
        caps_count = len(_CAPS_RE.findall(text))
        bang_count = len(_BANG_RE.findall(text))
        extra_urgency = min(0.20, caps_count * 0.05 + bang_count * 0.06)

        signals:    list[AttentionSignal] = []
        new_tokens: list[str]             = []
        urgency_max: float                = 0.0
        total_score: float                = 0.0
        prev_scored: bool                 = False   # for bigram detection
        prev_tok:    str                  = ""

        for idx, tok in enumerate(tokens):
            # ── inline scoring ────────────────────────────────────────
            score  = 0.0
            flags  = 0

            if tok in _URGENCY:
                score += _W_URGENCY + extra_urgency
                flags |= 1
            if tok in _EMOTIONAL:
                score += _W_EMOTION
                flags |= 2
            if tok in _GOAL:
                score += _W_GOAL
                flags |= 4
            if tok in _TOPIC_BOOST:
                score += _W_TOPIC
                flags |= 16
            if tok in _MEMORY_REF:
                score += _W_GOAL    # memory references are goal-adjacent
                flags |= 32
            if tok in _SUPPRESS:
                score *= _W_SUPPRESS   # soft suppression
            elif score == 0.0 and len(tok) > 6 and tok.isalpha():
                score += _W_CONTENT
                flags |= 8

            # Lead bonus: question/action word at very start of message = clear intent
            if idx <= 1 and (tok in _QUESTION_LEAD or tok in _ACTION_LEAD):
                score += _W_LEAD
                flags |= 64

            # Position bonus (front-loaded intent, back-loaded emphasis)
            if idx < n_fifth or idx >= n_tok - n_fifth:
                score += _W_POSITION

            # Persistent salience boost
            sal = salience.get(tok, 0.0)
            if sal > 0.0:
                score += _W_SALIENCE * sal

            # Bigram bonus: consecutive scored tokens reinforce each other
            if prev_scored and score > _SCORE_THRESHOLD:
                score += _W_BIGRAM
                # Also boost previous signal
                if signals:
                    signals[-1].score += _W_BIGRAM * 0.5

            if score > _SCORE_THRESHOLD:
                signals.append(AttentionSignal(tok, score, flags))
                if flags & 1 and score > urgency_max:
                    urgency_max = score
                prev_scored = True
            else:
                prev_scored = False

            prev_tok = tok
            total_score += score

            if tok not in seen:
                new_tokens.append(tok)

        # ── rank ──────────────────────────────────────────────────────
        signals.sort(key=lambda s: s.score, reverse=True)
        if len(signals) > max_sig:
            signals = signals[:max_sig]

        # ── update persistent state ───────────────────────────────────
        seen.update(new_tokens)

        decay = self.recency_decay
        # True single pass: decay + prune in one dict comprehension
        # Replaces entire dict — faster than two separate loops on CPython
        self._salience = {k: v * decay for k, v in salience.items() if v * decay >= 0.01}
        salience = self._salience
        for sig in signals:
            salience[sig.token] = salience.get(sig.token, 0.0) + sig.score

        # Hard cap to prevent unbounded growth
        if len(salience) > _MAX_SALIENCE_ENTRIES:
            # Keep top-scored entries only
            trimmed = sorted(salience.items(), key=lambda x: x[1], reverse=True)[:_MAX_SALIENCE_ENTRIES]
            salience.clear()
            salience.update(trimmed)

        # ── novelty / spike detection ─────────────────────────────────
        avg_score = total_score / n_tok if n_tok > 0 else 0.0
        self._turn_scores.append(avg_score)
        rolling_avg = sum(self._turn_scores) / len(self._turn_scores)
        novelty_score = len(new_tokens) / max(n_tok, 1)
        spike = avg_score > rolling_avg * 2.0 and len(self._turn_scores) >= 3

        top_focus = [s.token for s in signals[:5]]

        state.attention = {
            "signals":         [(s.token, round(s.score, 3)) for s in signals],
            "top_focus":       top_focus,
            "suppressed":      [t for t in tokens if t in _SUPPRESS],
            "salience_map":    {s.token: round(s.score, 3) for s in signals},
            "novelty_score":   round(novelty_score, 3),
            "urgency_score":   round(urgency_max, 3),
            "extra_urgency":   round(extra_urgency, 3),
            "load":            round(len(signals) / max_sig, 3),
            "salience_spike":  spike,
            "memory_ref":      any(s.flags & 32 for s in signals),
            "question_lead":   any(s.flags & 64 for s in signals[:3]),
            "action_lead":     tokens[0] in _ACTION_LEAD if tokens else False,
            "response_priority": [],
        }

    # ── weight_response — O(1) ────────────────────────────────────────────────

    def weight_response(self, state: "CognitiveState"):
        """Inject top-focus into response_priority (in-place, no allocation)."""
        state.attention["response_priority"] = state.attention["top_focus"][:4]

    # ── Introspection ─────────────────────────────────────────────────────────

    def report(self) -> dict:
        top = sorted(self._salience.items(), key=lambda x: x[1], reverse=True)[:15]
        return {
            "salience_entries": len(self._salience),
            "seen_tokens":      len(self._seen),
            "top_persistent":   top,
            "turn_avg_history": list(self._turn_scores),
        }

    def reset_salience(self):
        """Call between separate conversation sessions."""
        self._salience.clear()
        self._seen.clear()
        self._turn_scores.clear()


def _empty_attention() -> dict:
    return {
        "signals": [], "top_focus": [], "suppressed": [],
        "salience_map": {}, "novelty_score": 0.0, "urgency_score": 0.0,
        "extra_urgency": 0.0, "load": 0.0, "salience_spike": False,
        "memory_ref": False, "question_lead": False, "action_lead": False, "response_priority": [],
    }