"""
prompt_governor.py — Shiro AI Prompt Governor v2.1
====================================================
Upgrade: Smart Escalation + Coherence Filter + Intent Lock

WHAT'S NEW IN v2.1 (on top of v2.0)
--------------------------------------
  1. Smart escalation — draft failure is now diagnosed before acting:
       • relevance HIGH + clarity LOW  → reasoning failure → regenerate SAME tier once
       • relevance LOW or both LOW     → context failure  → escalate tier
     Prevents unnecessary tier jumps from pure reasoning/phrasing failures.

  2. Module coherence filter — micro-gates now produce a global priority score.
     If too many heavy modules activate simultaneously, only the top N are kept.
     Prevents dilution from cognitively contradictory or redundant contexts.
       priority = {
           "cognition":     complexity + intent,
           "memory":        memory_relevance + emotional_weight,
           "emotion_layer": emotional_weight + ambiguity,
           ...
       }

  3. Intent Lock — the dominant intent category is injected into the prompt
     as a focused directive BEFORE the LLM generates. Prevents rambling,
     personality drift, and over-explanation on clear-intent messages.
       "Primary goal: answer a technical question. Stay direct. Do not over-explain."

UNCHANGED FROM v2.0
--------------------
  • Same assemble() signature (drop-in replacement for v1 and v2.0)
  • Same GovernedPrompt dataclass (new fields added but are optional)
  • Semantic TierScores (intent/emotional/ambiguity scoring)
  • Soft tier blending
  • Core personality always-on
  • Top-k memory (max 5 lines)
  • Selective Tier 3 (compressed cognition, per-module micro-gates)

INTEGRATION
-----------
  Drop-in replacement — see v2.0 module docstring for full integration notes.
  New optional fields on GovernedPrompt:
    .escalation_mode   — "reasoning" | "context" | None
    .intent_lock_text  — the injected intent directive (for debug logging)
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Optional, Callable

logger = logging.getLogger("shiro.governor")


# ══════════════════════════════════════════════════════════════════════════════
# TOKEN ESTIMATOR
# ══════════════════════════════════════════════════════════════════════════════

def _tok(text: str) -> int:
    """~1 token per 3.8 chars (matches text_utils.py heuristic)."""
    return max(0, int(len(text) / 3.8)) if text else 0


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL LEXICONS
# ══════════════════════════════════════════════════════════════════════════════

_EMOTION_WORDS = frozenset({
    "sad", "angry", "hurt", "scared", "lonely", "anxious", "stressed",
    "depressed", "frustrated", "crying", "struggling", "upset", "worried",
    "excited", "happy", "proud", "grateful", "love", "hate", "miss",
    "broken", "lost", "hopeless", "afraid", "exhausted", "overwhelmed",
    "numb", "empty", "confused", "ashamed", "guilty", "regret", "joy",
    "content", "relieved", "jealous", "envious", "disgusted", "nervous",
    "missing", "longing", "ache", "yearn",
})

_MEMORY_WORDS = frozenset({
    "remember", "recall", "told", "said", "mentioned", "before", "earlier",
    "last time", "last week", "you said", "we talked", "you know", "my name",
    "i told you", "you told me", "used to", "back then", "previously",
})

_TASK_WORDS = frozenset({
    "help", "how", "explain", "write", "make", "build", "fix", "debug",
    "create", "solve", "show", "give me", "can you", "could you", "would you",
    "what is", "what are", "why", "difference", "compare", "analyze",
    "summarize", "list", "describe", "find", "search", "tell me",
})

_DEEP_WORDS = frozenset({
    "carefully", "thorough", "everything", "complete", "in-depth", "all",
    "comprehensive", "detail", "step by step", "fully", "entire",
    "exhaustive", "breakdown", "walkthrough", "everything about",
})

_AMBIGUITY_PHRASES = (
    "i'm fine", "i am fine", "it's fine", "doesn't matter",
    "never mind", "forget it", "nothing", "whatever", "i don't know",
    "not sure", "maybe", "i guess", "sort of", "kind of", "it's okay",
    "i'm okay", "i'm good", "all good",
)

_NEGATION_WORDS = frozenset({
    "not", "never", "barely", "hardly", "don't", "cant", "can't", "won't", "wouldn't"
})

_DISTRESS_PHRASES = (
    "broken", "hopeless", "don't know what to do", "i don't know",
    "cant cope", "can't cope", "give up", "no point", "worthless",
    "want to disappear", "can't go on", "end it", "not worth it",
    "too much", "falling apart",
)

_COMPARATIVE_RE = re.compile(
    r'\b(pros|cons|versus|vs\.?|difference|compare|better|worse|between)\b',
    re.IGNORECASE
)

# ── Intent pattern registry ───────────────────────────────────────────────────
# Each entry: (regex, label, intent_lock_directive)
# label     = short machine-readable name used in micro-gating
# directive = what gets injected into the prompt as the Intent Lock
_INTENT_REGISTRY = [
    (
        re.compile(r'\b(help|fix|debug|build|write|create|code|implement|make)\b', re.I),
        "task_request",
        "Primary goal: complete a concrete task or request. Be direct and useful. "
        "Skip preamble. Do not over-explain. Deliver the thing asked for.",
    ),
    (
        re.compile(r'\b(what|why|how|when|where|who|which|explain|tell me about)\b', re.I),
        "question",
        "Primary goal: answer a question clearly and concisely. "
        "Lead with the answer. Do not ramble or hedge unnecessarily.",
    ),
    (
        re.compile(r'\b(feel|feeling|felt|i am|i\'m|been|going through|struggling|scared|hurt|sad|anxious|lost)\b', re.I),
        "emotional_share",
        "Primary goal: be present with this person. "
        "Acknowledge what they said before anything else. Do not pivot to advice unless asked.",
    ),
    (
        re.compile(r'\b(remember|recall|earlier|before|last time|you said|told you|back when)\b', re.I),
        "memory_ref",
        "Primary goal: draw on shared history. "
        "Reference what you actually know. If you're uncertain, say so naturally.",
    ),
    (
        re.compile(r'^(hey|hi|hello|yo|sup|what\'?s up|how are you|how\'?s it going)\W*$', re.I),
        "casual",
        "Primary goal: be natural and present. One or two sentences. Do not over-answer.",
    ),
]

# Fallback directive when no specific intent matches
_INTENT_LOCK_FALLBACK = (
    "Primary goal: respond naturally to what was said. Stay on topic. Be concise."
)


# ══════════════════════════════════════════════════════════════════════════════
# SEMANTIC SCORING
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class TierScores:
    intent_score:     float = 0.0
    emotional_weight: float = 0.0
    ambiguity_score:  float = 0.0
    complexity_score: float = 0.0
    memory_relevance: float = 0.0
    primary_intent:   str   = "unknown"   # NEW v2.1 — dominant intent label
    intent_directive: str   = ""          # NEW v2.1 — the lock text to inject
    raw_tier:         int   = 0
    final_tier:       int   = 0


def _score_message(message: str, session_turns: int = 0) -> TierScores:
    msg_lower = message.lower().strip()
    words     = msg_lower.split()
    n         = len(words)
    word_set  = frozenset(words)

    # Complexity
    len_score  = min(n / 80.0, 1.0)
    code_score = 0.3 if ("```" in message or message.count("`") >= 4) else 0.0
    q_score    = min(message.count("?") / 4.0, 0.4)
    deep_score = 0.25 if (word_set & _DEEP_WORDS) else 0.0
    complexity = min(len_score + code_score + q_score + deep_score, 1.0)

    # Emotional weight
    emotion_hits  = len(word_set & _EMOTION_WORDS)
    distress_hit  = any(d in msg_lower for d in _DISTRESS_PHRASES)
    negation      = bool(word_set & _NEGATION_WORDS)
    emotional_raw = min(emotion_hits / 3.0, 1.0)
    if distress_hit:
        emotional_raw = max(emotional_raw, 0.85)
    # "feel" / emotional-share phrasing adds weight
    if re.search(r'\b(feel|feeling|felt|going through|struggling)\b', msg_lower):
        emotional_raw = min(emotional_raw + 0.15, 1.0)
    emotional = emotional_raw

    # Ambiguity
    ambiguity = 0.0
    if any(ph in msg_lower for ph in _AMBIGUITY_PHRASES):
        ambiguity += 0.5
    if n <= 5 and not re.match(
        r'^(hey|hi|hello|lol|haha|ok|okay|yeah|yep|nope|nice|cool|sure|thanks|ty|np)\W*$',
        msg_lower
    ):
        ambiguity += 0.3
    if "fine" in word_set and negation:
        ambiguity += 0.35
    if session_turns >= 3:
        ambiguity = min(ambiguity + 0.1, 1.0)
    ambiguity = min(ambiguity, 1.0)

    # Intent score (numeric)
    task_hits = len(word_set & _TASK_WORDS)
    intent    = 0.0
    if re.search(r'\b(help|fix|write|build|create|debug|solve|make|code)\b', msg_lower):
        intent += 0.4
    if re.search(r'\b(what|why|how|when|where|who|which|is it|are they|does|do you)\b', msg_lower):
        intent += 0.3
    if task_hits >= 2:
        intent += 0.2
    if _COMPARATIVE_RE.search(msg_lower):
        intent += 0.2
    intent = min(intent, 1.0)

    # Memory relevance
    mem_hits  = sum(1 for mw in _MEMORY_WORDS if mw in msg_lower)
    mem_pat   = bool(re.search(r'\b(remember|recall|earlier|before|last time|you said|told you)\b', msg_lower))
    memory_rel = min((mem_hits / 3.0) + (0.3 if mem_pat else 0.0), 1.0)

    # ── Primary intent (NEW v2.1) ─────────────────────────────────────────
    primary_intent   = "unknown"
    intent_directive = _INTENT_LOCK_FALLBACK
    best_score       = 0

    for pattern, label, directive in _INTENT_REGISTRY:
        hits = len(pattern.findall(msg_lower))
        if hits > best_score:
            best_score       = hits
            primary_intent   = label
            intent_directive = directive

    # Emotional share always wins over task/question when emotion is dominant
    if emotional >= 0.5:
        for pattern, label, directive in _INTENT_REGISTRY:
            if label == "emotional_share":
                primary_intent   = label
                intent_directive = directive
                break

    return TierScores(
        intent_score     = round(intent, 3),
        emotional_weight = round(emotional, 3),
        ambiguity_score  = round(ambiguity, 3),
        complexity_score = round(complexity, 3),
        memory_relevance = round(memory_rel, 3),
        primary_intent   = primary_intent,
        intent_directive = intent_directive,
    )


def classify_tier_v2(message: str, session_turns: int = 0) -> TierScores:
    """Full semantic tier classifier with soft blending."""
    scores    = _score_message(message, session_turns)
    msg_lower = message.lower().strip()
    words     = msg_lower.split()
    n         = len(words)
    word_set  = frozenset(words)

    # Hard tier-3 triggers
    raw = 0
    if n > 60:
        raw = 3
    elif "```" in message or message.count("`") >= 4:
        raw = 3
    elif message.count("?") >= 3:
        raw = 3
    elif word_set & _DEEP_WORDS:
        raw = 3
    elif scores.emotional_weight >= 0.85:
        raw = 3
    # Tier 2
    elif (n > 20
          or scores.memory_relevance >= 0.3
          or scores.emotional_weight >= 0.30
          or message.count("?") >= 2
          or (len(word_set & _TASK_WORDS) >= 2)
          or _COMPARATIVE_RE.search(msg_lower)
          or (session_turns >= 3 and "?" in message and n > 8)
          or (len(word_set & _TASK_WORDS) >= 1 and "?" in message and n > 10)):
        raw = 2
    # Tier 1
    elif ((n > 6 and not re.fullmatch(r'what(?:\'s| is) (?:the )?time\??', msg_lower))
          or ("?" in message and scores.intent_score >= 0.3)
          or scores.intent_score >= 0.4):
        raw = 1
    else:
        raw = 0

    scores.raw_tier = raw

    # Soft blending
    final = raw
    if scores.ambiguity_score >= 0.5 and final < 1:
        final = 1
    if scores.ambiguity_score >= 0.7 and final < 2:
        final = 2
    if scores.emotional_weight >= 0.2 and final == 0:
        final = 1

    scores.final_tier = final
    return scores


def classify_tier(message: str, session_turns: int = 0) -> int:
    """Thin shim — maintains compatibility with v1 callers."""
    return classify_tier_v2(message, session_turns).final_tier


# ══════════════════════════════════════════════════════════════════════════════
# TIER BUDGETS & GATES
# ══════════════════════════════════════════════════════════════════════════════

_TIER_CONTEXT_BUDGET = {0: 500, 1: 900, 2: 1_600, 3: 2_800}

# (tier0, tier1, tier2, tier3)
_TIER_GATES: dict[str, tuple[bool, bool, bool, bool]] = {
    "speaker_line":  (True,  True,  True,  True),
    "anti_leak":     (True,  True,  True,  True),
    "task_block":    (False, True,  True,  True),
    "goal_block":    (False, True,  True,  True),
    "cog_report":    (False, True,  True,  True),
    "memory":        (False, True,  True,  True),
    "cognition":     (False, False, True,  True),
    "consciousness": (False, False, True,  True),
    "inner_context": (False, False, True,  True),
    "kg_block":      (False, False, True,  True),
}

# Maximum heavy modules active at the same tier to avoid coherence dilution
_MAX_HEAVY_MODULES = 4
_HEAVY_MODULES     = {"cognition", "consciousness", "inner_context", "memory", "kg_block"}


# ══════════════════════════════════════════════════════════════════════════════
# MICRO-GATES WITH COHERENCE FILTER  (NEW v2.1)
# ══════════════════════════════════════════════════════════════════════════════

def _micro_gates_with_coherence(scores: TierScores) -> dict[str, bool]:
    """
    Compute per-module activation gates AND apply a coherence filter.

    Step 1: Score each heavy module by signal strength.
    Step 2: Rank modules by their combined signal score.
    Step 3: Keep only the top-N heavy modules; disable the rest.

    This prevents the "everything fires" coherence dilution where memory,
    cognition, consciousness, and inner_context all activate simultaneously
    but contradict or drown each other out.
    """
    e  = scores.emotional_weight
    a  = scores.ambiguity_score
    i  = scores.intent_score
    c  = scores.complexity_score
    mr = scores.memory_relevance

    # Raw activation booleans (unchanged from v2.0 logic)
    raw_gates = {
        "memory":        mr > 0.25 or e > 0.3,
        "emotion_layer": e > 0.4 or a > 0.5,
        "planning":      i > 0.5 and c > 0.3,
        "self_reflect":  a > 0.5 or (e > 0.3 and a > 0.3),
        "cognition":     c > 0.35 or i > 0.55,
        "consciousness": e > 0.35 or a > 0.45 or c > 0.5,
        "inner_context": e > 0.3 or a > 0.4,
        "kg_block":      i > 0.4 and mr < 0.6,
        "cog_report":    i > 0.25 or c > 0.25,
    }

    # ── Coherence filter ──────────────────────────────────────────────────
    # Priority scores for heavy modules — higher score = more signal value
    priority_scores: dict[str, float] = {
        "cognition":     c + i,                      # most useful for task/complex
        "memory":        mr + e,                     # most useful for emotional/personal
        "consciousness": e + a + (c * 0.5),          # emotional state + ambiguity awareness
        "inner_context": e + a,                      # pure emotional depth
        "kg_block":      i * 0.8 + (1.0 - mr) * 0.3,  # knowledge tasks, not memory-heavy turns
    }

    # Count how many heavy modules raw-gated ON
    active_heavy = [m for m in _HEAVY_MODULES if raw_gates.get(m, False)]

    if len(active_heavy) > _MAX_HEAVY_MODULES:
        # Sort by priority score descending, keep top N
        ranked    = sorted(active_heavy, key=lambda m: priority_scores.get(m, 0.0), reverse=True)
        keep      = set(ranked[:_MAX_HEAVY_MODULES])
        disabled  = set(active_heavy) - keep
        for m in disabled:
            raw_gates[m] = False
        logger.debug(
            "[Governor v2.1] coherence filter: disabled %s (kept %s)",
            disabled, keep
        )

    return raw_gates


# ══════════════════════════════════════════════════════════════════════════════
# LAYER COMPRESSORS
# ══════════════════════════════════════════════════════════════════════════════

def _compress_anti_leak(text: str, tier: int) -> str:
    if tier >= 2 or not text:
        return text
    lines = [l for l in text.split("\n") if l.strip()]
    essential = [l for l in lines if any(
        kw in l.lower() for kw in ("markup", "plain", "match", "brief", "spoken", "word")
    )]
    return "\n".join((essential if essential else lines)[:3])


def _compress_consciousness(text: str, tier: int) -> str:
    if tier >= 3 or not text:
        return text
    _skip = {"[PAST SESSIONS]", "[RELATIONSHIP RECALL]", "[ROOM]"}
    lines = [l for l in text.split("\n") if not any(sk in l for sk in _skip)]
    compressed = "\n".join(lines)
    return compressed[:600] + "…" if len(compressed) > 600 else compressed


def _compress_cognition(text: str, tier: int) -> str:
    """Tier ≤2: 800 chars. Tier 3: 1600 chars. Always capped."""
    if not text:
        return text
    cap = 800 if tier <= 2 else 1_600
    return (text[:cap].rsplit("\n", 1)[0] + "…") if len(text) > cap else text


def _compress_memory(text: str, tier: int, scores: Optional[TierScores] = None) -> str:
    """
    Tier 1: top-1, or top-3 when emotional_weight elevated (tier-2 bleed).
    Tier 2+: top-5.
    """
    if not text:
        return ""
    lines = [l.strip() for l in text.split("\n") if l.strip() and len(l.strip()) > 10]
    if tier == 1:
        k = 3 if (scores and scores.emotional_weight > 0.4) else 1
    else:
        k = 5
    return "\n".join(lines[:k])


# ══════════════════════════════════════════════════════════════════════════════
# CORE PERSONALITY SLICE  (always injected, never gated)
# ══════════════════════════════════════════════════════════════════════════════

def _extract_core_personality(system_prompt: str) -> str:
    if not system_prompt:
        return ""
    for marker in ("[CORE]", "[PERSONALITY CORE]", "[PERSONA CORE]"):
        idx = system_prompt.find(marker)
        if idx != -1:
            chunk = system_prompt[idx: idx + 400]
            return chunk.split("\n\n")[0].strip()
    lines = [l for l in system_prompt.split("\n") if l.strip()]
    return "\n".join(lines[:3])


# ══════════════════════════════════════════════════════════════════════════════
# INTENT LOCK INJECTION  (NEW v2.1)
# ══════════════════════════════════════════════════════════════════════════════

def _build_intent_lock(scores: TierScores, tier: int) -> str:
    """
    Build the intent lock directive string to inject into the prompt.

    Only injected at tier ≥ 1 — casual tier-0 turns don't need locking.
    At tier 0 the message is so simple that a directive adds noise.
    """
    if tier == 0:
        return ""
    directive = scores.intent_directive or _INTENT_LOCK_FALLBACK
    return f"\n[INTENT LOCK] {directive}"


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL QUALITY SCORER
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SignalScores:
    clarity:    float = 0.0
    relevance:  float = 0.0
    directness: float = 0.0

    @property
    def overall(self) -> float:
        return (self.clarity + self.relevance + self.directness) / 3.0

    @property
    def is_low(self) -> bool:
        return self.overall < 0.55 or self.clarity < 0.35

    @property
    def failure_mode(self) -> str:
        """
        Diagnose WHY the signal is low.
        Returns: "reasoning" | "context" | "ok"

        reasoning = relevance is high but clarity/directness is low
                    → the model understood the topic but expressed it badly
                    → fix: regenerate same tier
        context   = relevance is low (model didn't engage with the right topic)
                    → fix: escalate tier for more context
        """
        if not self.is_low:
            return "ok"
        if self.relevance >= 0.55 and self.clarity < 0.45:
            return "reasoning"
        return "context"


def _score_draft_response(draft: str, message: str) -> SignalScores:
    """Lightweight heuristic signal scorer. No LLM call — structure-based only."""
    if not draft:
        return SignalScores()

    d_lower = draft.lower()
    d_words = set(d_lower.split())
    m_words = set(message.lower().split())

    # Clarity — penalize hedging
    hedge_phrases = (
        "i think", "i believe", "i'm not sure", "it depends",
        "it could be", "perhaps", "maybe", "possibly",
        "i cannot", "i can't", "i don't know", "as an ai",
    )
    hedge_hits = sum(1 for h in hedge_phrases if h in d_lower)
    clarity    = max(0.0, 1.0 - (hedge_hits * 0.18))

    # Relevance — keyword overlap with user message
    overlap   = len(d_words & m_words) / max(len(m_words), 1)
    relevance = min(overlap * 2.5, 1.0)

    # Directness — penalize filler preambles
    preamble = draft[:80].lower()
    filler_starts = (
        "of course", "certainly", "sure", "great", "absolutely",
        "that's a great", "good question", "i'd be happy",
    )
    direct_penalty = 0.3 if any(f in preamble for f in filler_starts) else 0.0
    directness     = max(0.0, 1.0 - direct_penalty)

    return SignalScores(
        clarity    = round(clarity, 3),
        relevance  = round(relevance, 3),
        directness = round(directness, 3),
    )


# ══════════════════════════════════════════════════════════════════════════════
# OUTPUT DATACLASS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class GovernedPrompt:
    top_bun:         str
    tier:            int
    tokens_used:     int
    budget:          int
    layers_included: list[str]              = field(default_factory=list)
    layers_dropped:  list[str]              = field(default_factory=list)
    tier_scores:     Optional[TierScores]   = None
    signal_scores:   Optional[SignalScores] = None
    escalated:       bool                   = False
    escalation_mode: Optional[str]          = None   # NEW v2.1: "reasoning"|"context"|None
    intent_lock_text: str                   = ""     # NEW v2.1: injected directive (for debug)


# ══════════════════════════════════════════════════════════════════════════════
# PROMPT GOVERNOR  v2.1
# ══════════════════════════════════════════════════════════════════════════════

class PromptGovernor:
    """
    Adaptive Intelligence Routing for Shiro AI — v2.1.

    New in v2.1:
    - Smart escalation: diagnoses reasoning vs context failures before acting
    - Module coherence filter: prevents cognitive dilution from too many active modules
    - Intent Lock: injects a focused primary-goal directive before LLM generation
    """

    def __init__(self, enabled: bool = True):
        self.enabled              = enabled
        self._tier_counts         = {0: 0, 1: 0, 2: 0, 3: 0}
        self._escalations_context  = 0
        self._escalations_reasoning = 0

    # ──────────────────────────────────────────────────────────────────────
    # PRIMARY ASSEMBLY
    # ──────────────────────────────────────────────────────────────────────

    def assemble(
        self,
        message:          str,
        system_prompt:    str,
        shiro_context:    str = "",
        cognition_block:  str = "",
        cog_report_block: str = "",
        consciousness:    str = "",
        inner_context:    str = "",
        task_block:       str = "",
        goal_block:       str = "",
        memory_block:     str = "",
        kg_block:         str = "",
        user_name:        str = "user",
        session_turns:    int = 0,
        force_tier:       Optional[int] = None,
    ) -> GovernedPrompt:
        """
        Assemble top_bun with adaptive tier-appropriate context.
        Drop-in replacement for v1/v2.0 assemble().
        """
        scores = classify_tier_v2(message, session_turns)
        tier   = force_tier if force_tier is not None else scores.final_tier
        budget = _TIER_CONTEXT_BUDGET[tier]
        self._tier_counts[tier] += 1

        if not self.enabled:
            top_bun = (system_prompt + shiro_context + cognition_block
                       + cog_report_block + consciousness)
            return GovernedPrompt(
                top_bun=top_bun, tier=tier,
                tokens_used=_tok(top_bun), budget=budget,
                layers_included=["all (governor disabled)"],
                tier_scores=scores,
            )

        micro  = _micro_gates_with_coherence(scores)
        gates  = _TIER_GATES

        included:      list[str] = []
        dropped:       list[str] = []
        context_parts: list[str] = []
        tokens_used    = 0

        # ── Always-on: core personality slice ─────────────────────────────
        core = _extract_core_personality(system_prompt)
        if core:
            context_parts.append(core)
            tokens_used += _tok(core)
            included.append("core_personality")

        def _add(
            key:         str,
            text:        str,
            compress_fn: Optional[Callable] = None,
            micro_key:   Optional[str]      = None,
        ):
            nonlocal tokens_used
            if not text or not text.strip():
                return
            if not gates[key][tier]:
                dropped.append(key)
                return
            if micro_key and not micro.get(micro_key, True):
                dropped.append(f"{key}(micro_gated)")
                return
            if compress_fn:
                processed = (
                    compress_fn(text, tier, scores)
                    if compress_fn is _compress_memory
                    else compress_fn(text, tier)
                )
            else:
                processed = text
            if not processed or not processed.strip():
                dropped.append(key)
                return
            cost = _tok(processed)
            if tokens_used + cost > budget:
                dropped.append(f"{key}(over_budget)")
                return
            context_parts.append(processed)
            tokens_used += cost
            included.append(key)

        # Priority order — highest signal first
        _add("speaker_line",  shiro_context)
        _add("anti_leak",     shiro_context,    _compress_anti_leak)
        _add("task_block",    task_block,        None,                    "cog_report")
        _add("goal_block",    goal_block,        None,                    "cog_report")
        _add("cog_report",    cog_report_block,  None,                    "cog_report")
        _add("memory",        memory_block,      _compress_memory,        "memory")
        _add("cognition",     cognition_block,   _compress_cognition,     "cognition")
        _add("consciousness", consciousness,     _compress_consciousness, "consciousness")
        _add("inner_context", inner_context,     None,                    "inner_context")
        _add("kg_block",      kg_block,          None,                    "kg_block")

        # ── Intent Lock injection (NEW v2.1) ──────────────────────────────
        intent_lock = _build_intent_lock(scores, tier)
        if intent_lock:
            context_parts.append(intent_lock)
            tokens_used += _tok(intent_lock)
            included.append("intent_lock")

        context_block = "\n".join(p for p in context_parts if p)
        top_bun       = system_prompt + "\n" + context_block

        logger.debug(
            "[Governor v2.1] tier=%d (raw=%d) tokens=%d/%d "
            "E=%.2f A=%.2f I=%.2f C=%.2f intent=%s "
            "included=%s dropped=%s",
            tier, scores.raw_tier, tokens_used, budget,
            scores.emotional_weight, scores.ambiguity_score,
            scores.intent_score, scores.complexity_score,
            scores.primary_intent, included, dropped,
        )

        return GovernedPrompt(
            top_bun          = top_bun,
            tier             = tier,
            tokens_used      = tokens_used,
            budget           = budget,
            layers_included  = included,
            layers_dropped   = dropped,
            tier_scores      = scores,
            intent_lock_text = intent_lock.strip(),
        )

    # ──────────────────────────────────────────────────────────────────────
    # SMART ESCALATION  (NEW v2.1 — replaces v2.0 assemble_with_escalation)
    # ──────────────────────────────────────────────────────────────────────

    def assemble_with_escalation(
        self,
        message:          str,
        draft_response:   str,
        system_prompt:    str,
        **kwargs,
    ) -> GovernedPrompt:
        """
        Response-first pipeline with smart failure diagnosis.

        1. Caller generates a fast low-context draft
        2. Score the draft signal
        3. Diagnose failure mode:
             "reasoning" — relevance OK, clarity low → regenerate SAME tier
             "context"   — relevance low             → escalate tier
        4. Return the appropriate prompt

        Usage in shiro_engine.py:
            # Step 1: fast draft
            fast = self.prompt_governor.assemble(message, system_prompt, ...).top_bun
            draft = llm.generate(fast + message)

            # Step 2: smart escalation check
            governed = self.prompt_governor.assemble_with_escalation(
                message=message,
                draft_response=draft,
                system_prompt=system_prompt,
                **all_layer_kwargs,
            )

            if governed.escalation_mode == "reasoning":
                # Same tier, just regenerate once — don't change context
                final = llm.generate(governed.top_bun + message)
            elif governed.escalation_mode == "context":
                # Escalated tier — run with richer context
                final = llm.generate(governed.top_bun + message)
            else:
                # Draft was good enough
                final = draft
        """
        signal       = _score_draft_response(draft_response, message)
        failure_mode = signal.failure_mode

        if failure_mode == "reasoning":
            # Regenerate at SAME tier — reasoning issue, not context starvation
            logger.info(
                "[Governor v2.1] draft low (%.2f) — REASONING failure "
                "(rel=%.2f clf=%.2f) → same tier regenerate",
                signal.overall, signal.relevance, signal.clarity,
            )
            self._escalations_reasoning += 1
            result = self.assemble(
                message=message,
                system_prompt=system_prompt,
                **kwargs,
            )
            result.escalated       = True
            result.escalation_mode = "reasoning"
            result.signal_scores   = signal

        elif failure_mode == "context":
            # Escalate tier — context starvation
            current_tier   = classify_tier_v2(message).final_tier
            escalated_tier = min(max(current_tier + 1, 2), 3)
            logger.info(
                "[Governor v2.1] draft low (%.2f) — CONTEXT failure "
                "(rel=%.2f clf=%.2f) → escalate to tier %d",
                signal.overall, signal.relevance, signal.clarity, escalated_tier,
            )
            self._escalations_context += 1
            result = self.assemble(
                message=message,
                system_prompt=system_prompt,
                force_tier=escalated_tier,
                **kwargs,
            )
            result.escalated       = True
            result.escalation_mode = "context"
            result.signal_scores   = signal

        else:
            # Draft is fine — return the normal governed prompt (for intent lock etc.)
            result = self.assemble(
                message=message,
                system_prompt=system_prompt,
                **kwargs,
            )
            result.signal_scores = signal

        return result

    # ──────────────────────────────────────────────────────────────────────
    # STATS
    # ──────────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        total = sum(self._tier_counts.values())
        total_esc = self._escalations_context + self._escalations_reasoning
        return {
            "total_turns":            total,
            "tier_counts":            dict(self._tier_counts),
            "escalations_context":    self._escalations_context,
            "escalations_reasoning":  self._escalations_reasoning,
            "escalation_rate":        f"{total_esc / total * 100:.1f}%" if total else "0%",
            "tier_pct": {
                t: f"{c / total * 100:.1f}%" if total else "0%"
                for t, c in self._tier_counts.items()
            },
        }


# ══════════════════════════════════════════════════════════════════════════════
# SELF-TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import json

    print("=" * 70)
    print("TIER CLASSIFICATION TESTS")
    print("=" * 70)
    tests = [
        ("hey",                                                            0),
        ("lol ok",                                                         0),
        ("how are you?",                                                   1),
        ("what's your favourite thing to talk about",                      1),
        ("remember when we talked about that game last week?",             2),
        ("I've been feeling really stressed and anxious lately",           2),
        ("can you help me understand how attention mechanisms work?",      2),
        ("what are the pros and cons of python vs rust?",                  2),
        ("can you help me write a function that parses JSON?",             2),
        ("i feel broken and hopeless and i dont know what to do anymore",  3),
        ("write me a comprehensive guide to building a neural network from scratch with code examples", 3),
        ("that makes sense",                                               0),
        ("okay",                                                           0),
        ("what time is it",                                                0),
        ("why do you think that?",                                         1),
        ("i really miss talking to you when you're not around",            2),
    ]
    all_pass = True
    for msg, expected in tests:
        s  = classify_tier_v2(msg)
        ok = s.final_tier == expected
        if not ok:
            all_pass = False
        print(
            f"  [{'✓' if ok else '✗'}] tier={s.final_tier} (raw={s.raw_tier}) "
            f"E={s.emotional_weight:.2f} A={s.ambiguity_score:.2f} "
            f"I={s.intent_score:.2f}  intent={s.primary_intent:<16} '{msg[:50]}'"
        )
    print()
    print("All tests passed ✓" if all_pass else "⚠ Some tests FAILED")

    print()
    print("=" * 70)
    print("INTENT LOCK TEST")
    print("=" * 70)
    intent_cases = [
        "can you help me fix this bug in my python code",
        "i've been feeling really overwhelmed lately",
        "what are the pros and cons of rust vs python",
        "remember when we talked about this last week",
        "hey",
        "explain how transformers work step by step",
    ]
    for msg in intent_cases:
        s = classify_tier_v2(msg, session_turns=2)
        lock = _build_intent_lock(s, s.final_tier)
        print(f"  intent={s.primary_intent:<16} tier={s.final_tier}  '{msg[:50]}'")
        print(f"    lock: {lock.strip()[:90]}")

    print()
    print("=" * 70)
    print("SMART ESCALATION DIAGNOSIS")
    print("=" * 70)
    escalation_cases = [
        # (message, draft, expected_mode)
        (
            "explain recursion",
            "I think recursion is perhaps a concept where maybe a function calls itself, "
            "I'm not sure exactly how it bottoms out.",
            "reasoning",   # relevance high (recursion/function overlap), clarity low (hedging)
        ),
        (
            "what does ontological mean",
            "Great question! I'd be happy to help. Basically, it's a word that means "
            "something about existence but it really depends on the context.",
            "reasoning",   # topic words present, but hedgy/unclear
        ),
        (
            "explain the difference between tcp and udp",
            "Sure! I definitely know about this. It's a great networking topic.",
            "context",     # relevance LOW (tcp/udp not in draft), context starvation
        ),
        (
            "how are you",
            "I'm doing well, thanks for asking! Ready to chat.",
            "ok",          # good enough signal
        ),
    ]
    for msg, draft, expected in escalation_cases:
        sig = _score_draft_response(draft, msg)
        got = sig.failure_mode
        ok  = got == expected
        print(
            f"  [{'✓' if ok else '✗'}] mode={got:<10} overall={sig.overall:.2f} "
            f"rel={sig.relevance:.2f} clf={sig.clarity:.2f} "
            f"  '{msg[:40]}'"
        )

    print()
    print("=" * 70)
    print("COHERENCE FILTER TEST")
    print("=" * 70)
    # High-complexity emotional message — all modules would naively activate
    complex_msg = (
        "i've been feeling really anxious and lost, can you help me understand "
        "what i should do, i remember you said something about this before"
    )
    s   = classify_tier_v2(complex_msg, session_turns=5)
    mg  = _micro_gates_with_coherence(s)
    print(f"  message  : '{complex_msg[:70]}...'")
    print(f"  tier     : {s.final_tier}  E={s.emotional_weight:.2f} A={s.ambiguity_score:.2f} "
          f"I={s.intent_score:.2f} C={s.complexity_score:.2f}")
    print(f"  micro    : {json.dumps({k: v for k, v in mg.items()}, indent=4)}")
    heavy_on = [m for m in _HEAVY_MODULES if mg.get(m)]
    print(f"  heavy ON : {heavy_on} ({len(heavy_on)} ≤ {_MAX_HEAVY_MODULES} limit)")
