"""
SHIRO Cognitive Modules v3.6
=============================
Hardware: RTX 3070 8 GB | i9-9900K 8c/16t | 64 GB RAM

What's new vs v3.2
-------------------
SCHEDULER
  - Negation escalation: "can't", "nothing works", "not sure" on short
    inputs escalates to HIGH — user is frustrated, not asking a quick q.
  - Emotional trajectory escalation: if WorldModel reports "deteriorating"
    trajectory, complexity floor rises to HIGH.
  - Salience spike propagation: AttentionSystem flags topic shifts;
    Kernel reads this and bumps complexity — Scheduler just sets the flags.
  - Memory-reference detection: referencing prior turns escalates to MEDIUM
    so memory retrieval always gets the full query pipeline.

PERCEPTION
  - Language register: formal / informal / technical / emotional, written
    into state.context and state.intent — ResponseModule matches it.
  - Negation detection: "don't", "never", "nothing" → avoidance signals.
  - Topic bigrams: two-word noun-phrase extraction for richer WorldModel.
  - Debug intent: error/bug/crash/exception combo triggers "debug" primary.

WORLD MODEL
  - Relationship stage: transactional → acquainted → friendly → trusted,
    driven by warmth score accumulated from emotional arc.
  - Interest map: weighted counter of topics the user engages with most.
  - Task history: rolling deque of last 5 tasks Shiro was asked to do.
  - Auto-resolve: open questions auto-close when current turn overlaps
    with prior question's vocabulary.
  - Role + project context: extracted and surfaced to ResponseModule.

REASONING
  - Hypothesis 7 (blended): "empathetic_structured" when emotion AND task
    co-occur — best of both worlds, not a forced choice.
  - Confidence calibration: compares against rolling quality history.
  - Notable event detection: relationship milestones, identity pressure,
    emotional spikes, quality streaks — all documented.

RESPONSE MODULE
  - Relationship-aware preamble: "you know this person well" vs "new".
  - Preferences block: stored user preferences injected as explicit
    instructions for the LLM.
  - Structured response dict alongside flat string, for callers that
    want data not text.
  - Conversation window uses last 8 entries (4 turns) — rich context,
    not overloading the prompt.

METACOGNITION
  - 9 reflection checks including relationship regression and complexity
    spike detection.
  - Quality feedback to ReasoningModule for confidence calibration.
  - Vocabulary growth tracking: new technical terms the user introduces
    are stored as facts.
  - History-based insight: 5 consecutive high-confidence turns logged.
  - 6 curiosity signal types including interest-map deep-dive suggestions
    and missing project context detection.

OPTIMISATIONS (i9-9900K)
  - ALL keyword sets and intent sets: frozenset at module level.
  - _PREF_PATTERNS: compiled once at import (no re.compile in hot path).
  - WorldModel emotion arc uses deque(maxlen=50) — O(1) append.
  - SchedulerModule: single regex findall + one frozenset build.
  - ReasoningModule: list.append() chain, not string concatenation.
  - MetaCognitionModule._intent_patterns: plain dict (faster than Counter
    for < 20 keys).
  - _extract_preferences uses pre-compiled patterns, no per-call compile.
  - All heavy string operations deferred to post-turn (off hot path).
"""

from __future__ import annotations

import logging
import re
import time
from collections import Counter, deque
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cognitive_kernel import CognitiveState, TurnRecord, Complexity
    from memory_system    import MemorySystem
    from identity_system  import IdentityContinuationSystem

# ── Module-level compiled regex ───────────────────────────────────────────────
_TOKEN_RE    = re.compile(r"[a-zA-Z']+")
_SENT_SPLIT  = re.compile(r"[.!?]+")
_LIST_RE     = re.compile(r"\n\s*[-*\d]")
_DIGIT_RE    = re.compile(r"\d")
_CODE_RE     = re.compile(r"```|`[^`]+`")
_NEGATION_RE = re.compile(
    r"\b(?:can't|cannot|won't|don't|doesn't|didn't|never|nothing|"
    r"no one|nobody|nowhere|isn't|aren't|wasn't|weren't|hardly|barely)\b", re.I
)
_BIGRAM_RE   = re.compile(r"\b([a-z]{4,})\s+([a-z]{4,})\b")
_NAME_RE     = re.compile(r"(?:i'm|i am|my name is|call me)\s+([A-Z][a-z]{1,20})", re.I)

_NAME_STOPLIST = frozenset({
    "here","there","not","just","still","also","fine","good","okay",
    "sorry","happy","ready","sure","late","early","back","home",
    "tired","done","trying","going","coming","working",
})

# Pre-compiled preference patterns (module-level, never re-compiled)
_PREF_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"i (?:really |always |)?(?:like|love|enjoy|prefer)\s+(.{4,50}?)(?:[.,;!]|$)", re.I), "likes"),
    (re.compile(r"i (?:really |always |)?(?:hate|dislike|don't like|can't stand)\s+(.{4,50}?)(?:[.,;!]|$)", re.I), "dislikes"),
    (re.compile(r"(?:please |always |)?(?:keep it|make it|be|stay)\s+(short|brief|concise|detailed|thorough|casual|formal|direct|friendly|simple|technical)", re.I), "style"),
    (re.compile(r"(?:don't|do not|please no)\s+(?:use|add|include|give me)\s+(.{4,40}?)(?:[.,;!]|$)", re.I), "avoid"),
    (re.compile(r"i(?:'m| am) (?:a |an )?(.{3,30}?)\s+(?:developer|engineer|student|designer|writer|researcher|scientist|architect|analyst|manager)", re.I), "role"),
    (re.compile(r"(?:my|our)\s+(?:project|app|system|product|codebase|startup|tool)\s+(?:is|uses?|needs?|does)\s+(.{4,80}?)(?:[.,;!]|$)", re.I), "project"),
]


# ══════════════════════════════════════════════════════════════════════════════
# SCHEDULER
# ══════════════════════════════════════════════════════════════════════════════

_PLAN_WORDS = frozenset({
    "plan","step","steps","how","build","create","design","implement",
    "strategy","approach","solve","fix","improve","outline","roadmap",
    "make","write","generate","code","develop","structure","architecture",
    "walk","through","tutorial","guide","example","show","demonstrate",
})
_REASON_WORDS = frozenset({
    "why","because","analyse","analyze","compare","evaluate","examine",
    "think","explain","reason","understand","consider","review","assess",
    "difference","between","versus","vs","pros","cons","tradeoffs",
    "impact","effect","benefit","drawback","advantage","disadvantage",
})
_EMOTION_WORDS = frozenset({
    "feel","feeling","felt","hurt","sad","scared","angry","lonely",
    "depressed","anxious","stressed","overwhelmed","crisis","upset",
    "afraid","happy","excited","love","miss","proud","ashamed",
    "frustrated","tired","exhausted","hopeless","worthless","empty",
    "numb","broken","lost","crying","crying","struggling","suffering",
})
_DEEP_WORDS = frozenset({
    "carefully","thoroughly","comprehensive","detailed","everything",
    "complete","in-depth","exhaustive","fully","all","deep","thorough",
    "extensive","rigorous","every","each","entire","whole","full",
})
_MEMORY_WORDS = frozenset({
    "remember","recall","told","said","mentioned","before","earlier",
    "last","previous","we","talked","discussed","agreed","suggested",
    "you","thought","decided","planned",
})
_DEBUG_WORDS = frozenset({
    "error","bug","broken","crash","fail","exception","traceback",
    "issue","problem","wrong","fix","not","working","returns","gives",
    "unexpected","undefined","null","none","type","attribute","key",
})

try:
    from .cognitive_kernel import Complexity
except ImportError:
    from cognitive_kernel import Complexity

class SchedulerModule:
    __slots__ = ()

    def run(self, state: "CognitiveState"):
        text  = state.raw_input.lower()
        n     = len(text.split())
        words = frozenset(_TOKEN_RE.findall(text))

        if   n > 80: c = Complexity.DEEP
        elif n > 40: c = Complexity.HIGH
        elif n > 10: c = Complexity.MEDIUM
        else:        c = Complexity.LOW

        if words & _DEEP_WORDS:            c = Complexity.DEEP
        elif words & _EMOTION_WORDS:       c = max(c, Complexity.HIGH)
        elif words & _REASON_WORDS:        c = max(c, Complexity.MEDIUM)

        if words & _PLAN_WORDS:            c = max(c, Complexity.MEDIUM)
        if words & _DEBUG_WORDS:           c = max(c, Complexity.MEDIUM)

        # Follow-up detection: if last intent was debug/task and this is short, keep at MEDIUM
        # (user likely asking a follow-up, needs memory continuity)
        last_intent = state.world_model.get("last_intent") if state.world_model else None
        if last_intent in ("debug","task_request","planning") and n <= 8:
            c = max(c, Complexity.MEDIUM)
        if words & _MEMORY_WORDS:          c = max(c, Complexity.MEDIUM)

        # Short frustrated message
        if n <= 10 and _NEGATION_RE.search(state.raw_input):
            c = max(c, Complexity.HIGH)

        # Interpretation-based escalation (set by PerceptionModule which runs before us... 
        # wait — Scheduler runs BEFORE Perception. Use raw heuristics instead.)
        # Multi-sentence input with code → always at least HIGH
        if "```" in state.raw_input or state.raw_input.count("`") >= 4:
            c = max(c, Complexity.HIGH)
        # Multiple question marks = multi-question = needs careful handling
        if state.raw_input.count("?") >= 2:
            c = max(c, Complexity.MEDIUM)

        # Deteriorating emotional trajectory → needs more care
        arc = state.world_model.get("session_emotion_arc", []) if state.world_model else []
        if arc and arc[-1].get("intensity", 0) > 0.60:
            c = max(c, Complexity.HIGH)

        # Expert users ask concisely — don't over-escalate on short inputs
        # But NOT if there's a novelty spike (new topic = needs full pipeline)
        up      = state.world_model.get("user_profile", {}) if state.world_model else {}
        novelty = state.attention.get("novelty_score", 0.0) if state.attention else 0.0
        if (up.get("expertise_level") == "expert" and n <= 12
                and novelty < 0.40
                and not (words & (_EMOTION_WORDS | _DEEP_WORDS))):
            c = min(c, Complexity.MEDIUM)   # cap at MEDIUM for short expert queries on known topics

        # Rapid emotional decline → needs empathetic handling regardless of text length
        vel = state.emotion.get("valence_velocity", 0.0) if state.emotion else 0.0
        if vel < -0.15:
            c = max(c, Complexity.HIGH)

        sch                   = state.scheduler
        sch.complexity        = c
        sch.enable_debate     = c >= Complexity.MEDIUM
        sch.enable_planning   = c >= Complexity.MEDIUM
        sch.enable_reflection = c >= Complexity.HIGH
        sch.enable_curiosity  = c >= Complexity.HIGH


# ══════════════════════════════════════════════════════════════════════════════
# PERCEPTION
# ══════════════════════════════════════════════════════════════════════════════

_INTENT_SETS: list[tuple[str, frozenset]] = [
    ("question",     frozenset({"what","why","how","when","where","who","which","is","are","does","do","can","could","would","should","will","has","have"})),
    ("task_request", frozenset({"make","build","create","write","generate","design","implement","code","give","add","fix","help","show","find","produce","draft","update","refactor","convert","parse"})),
    ("conversation", frozenset({"hi","hello","hey","thanks","thank","okay","ok","cool","nice","great","sure","good","morning","evening","bye","later","awesome","perfect","got","it"})),
    ("explanation",  frozenset({"explain","tell","describe","clarify","elaborate","define","meaning","means","understand","walk","through","what","is","are"})),
    ("planning",     frozenset({"plan","steps","strategy","roadmap","schedule","outline","organise","organize","approach","workflow","process","procedure","phases","stages"})),
    ("emotional",    frozenset({"feel","feeling","felt","hurt","sad","scared","angry","lonely","depressed","miss","care","love","worried","anxious","tired","exhausted","struggling"})),
    ("feedback",     frozenset({"wrong","incorrect","actually","no","not","however","wait","instead","but","rethink","change","redo","again","different","better","that","off"})),
    ("memory",       frozenset({"remember","recall","told","said","mentioned","before","earlier","last","time","previous","we","talked","discussed","you","think","back"})),
    ("opinion",      frozenset({"think","opinion","believe","best","worst","favorite","prefer","recommend","suggest","thoughts","view","reckon","feel","perspective","take"})),
    ("debug",        frozenset({"error","bug","broken","crash","fail","exception","traceback","issue","problem","wrong","fix","working","returns","gives","unexpected","throws"})),
]

_REGISTER_FORMAL   = frozenset({"please","would","could","kindly","regarding","furthermore","however","therefore","consequently","appreciate","request","enquire","assist"})
_REGISTER_INFORMAL = frozenset({"lol","haha","yeah","nah","gonna","wanna","idk","tbh","ngl","btw","omg","kinda","sorta","stuff","thing","cuz","cause","cos"})
_REGISTER_TECH     = frozenset({"api","async","function","class","method","variable","array","loop","debug","compile","runtime","deploy","stack","heap","pointer","object","module","import"})


class PerceptionModule:
    __slots__ = ()

    def run(self, state: "CognitiveState"):
        text   = state.raw_input
        lower  = text.lower()
        tokens = _TOKEN_RE.findall(lower)
        n_tok  = len(tokens)
        wset   = frozenset(tokens)

        has_code     = bool(_CODE_RE.search(text))
        has_list     = bool(_LIST_RE.search(text))
        has_question = "?" in text
        has_negation = bool(_NEGATION_RE.search(text))
        n_sentences  = len(_SENT_SPLIT.split(text.strip()))
        is_short     = n_tok <= 5
        is_multipart = n_sentences >= 3

        state.interpretation = {
            "token_count":   n_tok,
            "char_count":    len(text),
            "has_question":  has_question,
            "has_code":      has_code,
            "has_list":      has_list,
            "has_numbers":   bool(_DIGIT_RE.search(text)),
            "has_negation":  has_negation,
            "sentences":     n_sentences,
            "is_short":      is_short,
            "is_multipart":  is_multipart,
        }

        # Register
        formal_h   = len(wset & _REGISTER_FORMAL)
        informal_h = len(wset & _REGISTER_INFORMAL)
        tech_h     = len(wset & _REGISTER_TECH)
        if   tech_h >= 2:              register = "technical"
        elif formal_h > informal_h:    register = "formal"
        elif informal_h > 1:           register = "informal"
        else:                          register = "neutral"

        # Topic bigrams (noun-phrase signals for WorldModel)
        bigrams = [f"{m[0]}_{m[1]}" for m in _BIGRAM_RE.findall(lower)][:6]

        ctx = state.context
        ctx["input_type"] = (
            "code_related" if has_code     else
            "list_request" if has_list     else
            "question"     if has_question else
            "statement"
        )
        ctx["verbosity_needed"] = (
            "high"   if n_tok > 40 else
            "medium" if n_tok > 15 else
            "low"
        )
        ctx["language_register"]         = register
        ctx["has_negation"]              = has_negation
        ctx["extracted_topics"]          = bigrams
        # Wire attention suppressed/signals for downstream use
        ctx["suppressed_tokens"]         = state.attention.get("suppressed", [])[:10]
        ctx["attention_signals"]         = state.attention.get("signals", [])[:8]   # (token, score) pairs
        ctx["has_conversation_history"]  = len(state.conversation_window) > 0
        ctx["conversation_turns_so_far"] = len(state.conversation_window) // 2
        if state.emotion:
            ctx["emotional_tone"] = state.emotion.get("tone_hint", "neutral_balanced")

        # Intent scoring
        scores: dict[str, int] = {}
        for cat, kset in _INTENT_SETS:
            h = len(wset & kset)
            if h: scores[cat] = h

        if scores:
            primary   = max(scores, key=scores.__getitem__)
            rest      = [(v, k) for k, v in scores.items() if k != primary]
            secondary = max(rest)[1] if rest else None
        else:
            primary, secondary = "conversation", None

        # Debug + negation combo → definitely a problem report
        if has_negation and "debug" in scores:
            primary = "debug"

        # If attention detected a clear question/action lead, trust it
        if state.attention.get("question_lead") and primary not in ("debug","emotional","memory"):
            scores["question"] = scores.get("question", 0) + 2
            primary = "question"
        elif state.attention.get("action_lead") and primary not in ("debug","emotional"):
            scores["task_request"] = scores.get("task_request", 0) + 2
            primary = "task_request"

        state.intent = {
            "primary":     primary,
            "secondary":   secondary,
            "all_scores":  scores,
            "description": f"{primary}: {' '.join(list(wset)[:10])}",
            "register":    register,
        }

    def parse_input(self, s):
        """Alias for run() — for callers using the old API name."""
        self.run(s)

    def expand_context(self, state: "CognitiveState"):
        """
        Enrich state.context with derived signals from interpretation.
        Called optionally after run() when extra context is needed.
        Adds: complexity_hint, likely_format, input_structure.
        """
        interp = state.interpretation
        c = state.context

        # Structural complexity hint for ReasoningModule
        n_sent = interp.get("sentences", 1)
        has_code = interp.get("has_code", False)
        has_list = interp.get("has_list", False)
        is_multi = interp.get("is_multipart", False)

        if has_code and is_multi:
            c["complexity_hint"] = "code_heavy_multipart"
        elif has_code:
            c["complexity_hint"] = "code_snippet"
        elif has_list:
            c["complexity_hint"] = "list_request"
        elif is_multi:
            c["complexity_hint"] = "multi_question"
        else:
            c["complexity_hint"] = "single_statement"

        # Preferred response format based on input structure
        if has_code:
            c["likely_format"] = "code_with_explanation"
        elif has_list or is_multi:
            c["likely_format"] = "numbered_or_bullet"
        elif interp.get("has_question"):
            c["likely_format"] = "direct_answer"
        else:
            c["likely_format"] = "prose"

        c["input_structure"] = {
            "sentences":   n_sent,
            "has_code":    has_code,
            "has_list":    has_list,
            "has_numbers": interp.get("has_numbers", False),
            "char_count":  interp.get("char_count", 0),
            "token_count": interp.get("token_count", 0),
        }

    def model_intent(self, state: "CognitiveState"):
        """
        Refine intent scoring using full context (called after world_model is populated).
        Updates state.intent with context-aware adjustments:
        - Checks if user is continuing a prior task (task follow-up detection)
        - Adjusts primary intent if world model provides strong signal
        """
        if not state.world_model:
            return
        intent     = state.intent
        last_intent = state.world_model.get("last_intent")
        phase      = state.world_model.get("conversation_phase", "opening")
        is_short   = state.interpretation.get("is_short", False)

        # Short message after a task = likely follow-up, not a new task
        if (is_short and last_intent in ("task_request","debug","planning")
                and intent.get("primary") == "conversation"):
            intent["primary"]   = last_intent
            intent["secondary"] = "conversation"
            intent["follow_up"] = True
        else:
            intent["follow_up"] = False

        # In established phase, "okay" / "got it" = acknowledgement, not question
        if phase == "established" and intent.get("primary") == "question":
            # Strip trailing punctuation so "ok!" and "okay." also match
            raw = state.raw_input.lower().strip().rstrip("!?.,:;")
            if raw in ("ok","okay","got it","makes sense","i see","alright","sure","yep","yup","cool","nice","right","yep","great","sounds good","got it"):
                intent["primary"]   = "conversation"
                intent["secondary"] = "acknowledgement"


# ══════════════════════════════════════════════════════════════════════════════
# WORLD MODEL
# ══════════════════════════════════════════════════════════════════════════════

_TECHNICAL_VOCAB = frozenset({
    "api","async","await","callback","class","compiler","cpu","cuda",
    "database","debug","deploy","docker","endpoint","framework","gpu",
    "inference","kernel","latency","middleware","neural","null","object",
    "pipeline","query","recursion","regex","runtime","schema","server",
    "socket","sql","stack","thread","token","vram","webhook","vector",
    "function","variable","loop","array","hash","cache","buffer","stream",
    "websocket","microservice","container","kubernetes","ci","cd","orm",
    "migration","index","constraint","transaction","concurrency","mutex",
})
_CASUAL_VOCAB = frozenset({
    "stuff","thing","kinda","sorta","gonna","wanna","lol","cool",
    "awesome","yeah","nah","maybe","idk","tbh","ngl","honestly",
    "btw","omg","literally","basically","totally","super","pretty","cuz",
})


class WorldModelModule:
    __slots__ = ("mem", "_model")

    def __init__(self, memory_system: "MemorySystem"):
        self.mem = memory_system
        self._model: dict[str, Any] = {
            "user_profile": {
                "name":                None,
                "expertise_level":     "unknown",
                "communication_style": "unknown",
                "language_register":   "neutral",
                "known_preferences":   [],
                "known_dislikes":      [],
                "recurring_needs":     {},
                "vocabulary_signals":  {},
                "interest_map":        {},
                "role":                None,
                "project_context":     None,
            },
            "active_topics":      deque(maxlen=30),
            "task_history":       deque(maxlen=5),
            "current_task":       None,
            "unresolved":         [],
            "conversation_phase": "opening",
            "relationship_stage": "transactional",
            "warmth_score":       0.0,
            "turn_count":         0,
            "session_emotion_arc": deque(maxlen=50),
            "last_intent":        None,
            "session_start_ts":   time.time(),
        }

    def update(self, state: "CognitiveState"):
        m  = self._model
        up = m["user_profile"]
        m["turn_count"] += 1
        tc     = m["turn_count"]
        intent = state.intent.get("primary", "conversation")

        # Active topics + interest map (capped at 200 entries — evict lowest-scored)
        for tok in state.attention.get("top_focus", []):
            m["active_topics"].append(tok)
            up["interest_map"][tok] = up["interest_map"].get(tok, 0) + 1
        for bt in state.context.get("extracted_topics", []):
            up["interest_map"][bt] = up["interest_map"].get(bt, 0) + 1
        if len(up["interest_map"]) > 200:
            im = up["interest_map"]
            # Keep top 150 by count — drop long-tail noise
            top = sorted(im, key=im.__getitem__, reverse=True)[:150]
            up["interest_map"] = {k: im[k] for k in top}

        # Task history
        if intent in ("task_request","planning","debug"):
            m["task_history"].append({"turn": tc, "task": state.raw_input[:120], "intent": intent})
            m["current_task"] = state.raw_input[:120]

        # Phase
        m["conversation_phase"] = (
            "opening"     if tc <= 2  else
            "established" if tc >= 20 else
            "active"
        )

        # Recurring needs
        rn = up["recurring_needs"]
        rn[intent] = rn.get(intent, 0) + 1

        # Vocabulary / expertise
        tokens    = frozenset(_TOKEN_RE.findall(state.raw_input.lower()))
        tech_hits = len(tokens & _TECHNICAL_VOCAB)
        cas_hits  = len(tokens & _CASUAL_VOCAB)
        vs = up["vocabulary_signals"]
        if tech_hits: vs["technical"] = vs.get("technical", 0) + tech_hits
        if cas_hits:  vs["casual"]    = vs.get("casual",    0) + cas_hits

        tech_t = vs.get("technical", 0)
        cas_t  = vs.get("casual",    0)
        if   tech_t > 15: up["expertise_level"] = "expert"
        elif tech_t >  5: up["expertise_level"] = "intermediate"
        elif cas_t > tech_t and tc > 3: up["expertise_level"] = "novice"

        up["language_register"] = state.context.get("language_register", "neutral")

        # Communication style
        if state.interpretation.get("is_short") and tc > 2:
            up["communication_style"] = "direct"
        elif cas_t > 3:
            up["communication_style"] = "casual"
        elif tech_t > 3:
            up["communication_style"] = "technical"

        # Name detection
        if up["name"] is None:
            nm = _NAME_RE.search(state.raw_input)
            if nm:
                cand = nm.group(1).strip()
                if cand.lower() not in _NAME_STOPLIST:
                    up["name"] = cand

        # Emotion arc + warmth
        tone      = state.emotion.get("tone_hint", "neutral_balanced")
        intensity = state.emotion.get("intensity", 0.0)
        traj      = state.emotion.get("trajectory", "unknown")
        m["session_emotion_arc"].append({"turn": tc, "tone": tone, "intensity": intensity})

        if tone in ("warm_energetic","warm_calm","gentle_positive"):
            m["warmth_score"] = min(1.0, m["warmth_score"] + 0.05)
        elif tone in ("intense_concern","gentle_support") and intensity > 0.4:
            m["warmth_score"] = max(0.0, m["warmth_score"] - 0.02)

        # Relationship stage
        ws = m["warmth_score"]
        if   ws > 0.6 and tc > 15: m["relationship_stage"] = "trusted"
        elif ws > 0.3 and tc >  8: m["relationship_stage"] = "friendly"
        elif tc > 3:               m["relationship_stage"] = "acquainted"
        else:                      m["relationship_stage"] = "transactional"

        # Auto-resolve unresolved questions
        active_set = set(m["active_topics"])
        for q in m["unresolved"]:
            if not q["resolved"]:
                qwords = frozenset(q["question"].lower().split())
                if len(qwords & active_set) >= 2:
                    q["resolved"] = True

        if intent == "question" and "?" in state.raw_input:
            m["unresolved"].append({"turn": tc, "question": state.raw_input[:100], "resolved": False})
            if len(m["unresolved"]) > 10:
                m["unresolved"] = [q for q in m["unresolved"] if not q["resolved"]][-10:]

        m["last_intent"] = intent

        im = up["interest_map"]
        state.world_model = {
            "user_profile":       up,
            "active_topics":      list(m["active_topics"]),
            "task_history":       list(m["task_history"]),
            "current_task":       m["current_task"],
            "conversation_phase": m["conversation_phase"],
            "relationship_stage": m["relationship_stage"],
            "warmth_score":       round(m["warmth_score"], 3),
            "turn_count":         tc,
            "unresolved_count":   sum(1 for q in m["unresolved"] if not q["resolved"]),
            "session_emotion_arc": list(m["session_emotion_arc"])[-5:],
            "last_intent":        m["last_intent"],
            "top_interests":      sorted(im, key=im.get, reverse=True)[:5],
            "session_minutes":    round((time.time() - m["session_start_ts"]) / 60, 1),
        }

    def mark_resolved(self, turn: int):
        for q in self._model["unresolved"]:
            if q["turn"] == turn:
                q["resolved"] = True

    def reset_session_state(self):
        """
        Clear per-session state for new_session() calls.
        Preserves user profile (name, expertise, preferences, interests) but
        resets conversation phase, emotion arc, warmth, turn count, and task history.
        """
        m = self._model
        m["active_topics"].clear()
        m["task_history"] = deque(maxlen=5)
        m["current_task"]       = None
        m["unresolved"]         = []
        m["conversation_phase"] = "opening"
        m["relationship_stage"] = "transactional"
        m["warmth_score"]       = 0.0
        m["turn_count"]         = 0
        m["session_emotion_arc"] = deque(maxlen=50)
        m["last_intent"]        = None
        m["session_start_ts"]   = time.time()

    def get_user_profile(self) -> dict:
        return dict(self._model["user_profile"])

    def get_top_interests(self, n: int = 5) -> list[str]:
        im = self._model["user_profile"]["interest_map"]
        return sorted(im, key=im.get, reverse=True)[:n]

    def get_session_summary(self) -> dict:
        """Compact summary of this session for logging or LLM injection."""
        m  = self._model
        up = m["user_profile"]
        return {
            "turn_count":       m["turn_count"],
            "session_minutes":  round((time.time() - m["session_start_ts"]) / 60, 1),
            "relationship":     m["relationship_stage"],
            "warmth":           round(m["warmth_score"], 3),
            "phase":            m["conversation_phase"],
            "top_interests":    sorted(up["interest_map"], key=up["interest_map"].get, reverse=True)[:5],
            "known_name":       up.get("name"),
            "expertise":        up.get("expertise_level","unknown"),
            "open_questions":   sum(1 for q in m["unresolved"] if not q["resolved"]),
        }


# ══════════════════════════════════════════════════════════════════════════════
# REASONING
# ══════════════════════════════════════════════════════════════════════════════

_STRATEGY_MAP: dict[str, dict] = {
    "direct":               {"name": "Direct Answer",           "format": "prose"},
    "memory_grounded":      {"name": "Contextual Recall",       "format": "prose_with_reference"},
    "empathetic":           {"name": "Empathetic Response",     "format": "empathy_first"},
    "structured":           {"name": "Structured Plan",         "format": "numbered_steps"},
    "exploratory":          {"name": "Exploratory Answer",      "format": "think_aloud"},
    "concise":              {"name": "Concise Answer",          "format": "one_shot"},
    "debug":                {"name": "Debug Walkthrough",       "format": "debug_steps"},
    "empathetic_structured":{"name": "Empathetic + Structured", "format": "empathy_then_steps"},
}

_PLAN_TEMPLATES: dict[str, list[str]] = {
    "numbered_steps": [
        "Acknowledge the request clearly",
        "State any constraints or prerequisites",
        "Walk through each step with explanation",
        "Summarise and offer to clarify",
    ],
    "empathy_first": [
        "Acknowledge the emotional context — name what you see",
        "Validate the feeling specifically without judgment",
        "Offer grounded perspective or practical support",
        "Provide content only if it feels right and wanted",
        "Check in: ask what would actually help most",
    ],
    "empathy_then_steps": [
        "Open with genuine emotional acknowledgement",
        "Bridge: 'here's what we can do about it'",
        "Clear actionable steps",
        "Close warmly — check if this helps",
    ],
    "prose": [
        "Address the core question directly",
        "Support with context or relevant memory",
        "Close with a clear summary",
    ],
    "prose_with_reference": [
        "Reference prior context naturally (not mechanically)",
        "Connect it to the current question",
        "Provide the answer with that continuity",
    ],
    "think_aloud": [
        "Acknowledge the open-ended nature of the question",
        "Think through 2-3 distinct angles explicitly",
        "Offer a clear perspective while holding nuance",
        "Invite the user's own view",
    ],
    "one_shot": [
        "Answer directly and concisely — no padding",
        "One optional follow-up offer, no more",
    ],
    "debug_steps": [
        "Acknowledge the problem and any frustration",
        "Identify the most likely root cause",
        "Provide the targeted fix with explanation",
        "Explain why this error happens",
        "Suggest how to avoid it next time",
    ],
}


class ReasoningModule:
    __slots__ = ("_quality_history",)

    def __init__(self):
        self._quality_history: deque = deque(maxlen=50)

    # ── Hypotheses ────────────────────────────────────────────────────────────

    def generate_hypotheses(self, state: "CognitiveState"):
        intent    = state.intent.get("primary", "conversation")
        n_mem     = state.memory.get("count", 0)
        intensity = state.emotion.get("intensity", 0.0)
        traj      = state.emotion.get("trajectory", "unknown")
        planning  = state.scheduler.enable_planning
        is_short  = state.interpretation.get("is_short", False)
        has_neg   = state.context.get("has_negation", False)
        up        = state.world_model.get("user_profile", {})
        user_exp  = up.get("expertise_level", "unknown")
        rel_stage = state.world_model.get("relationship_stage", "transactional")
        phase     = state.world_model.get("conversation_phase", "opening")
        register  = state.intent.get("register", "neutral")

        hyps = []

        # H1: Direct
        h1 = 0.75 + (0.10 if is_short and intensity < 0.3 else 0) + (0.05 if user_exp == "expert" else 0)
        hyps.append({
            "id": "h1", "type": "direct",
            "description": f"Answer the {intent} directly and clearly.",
            "confidence": min(0.92, h1),
            "benefits":   ["fast","clear","matches intent"],
            "risks":      ["may miss emotional subtext"],
        })

        # H2: Memory-grounded
        if n_mem >= 2:
            hyps.append({
                "id": "h2", "type": "memory_grounded",
                "description": f"Anchor in {n_mem} retrieved memories — show Shiro remembers.",
                "confidence": min(0.85, 0.68 + n_mem * 0.015),
                "benefits":   ["continuity","personalised","builds trust"],
                "risks":      ["may over-reference past"],
            })

        # H3: Empathetic
        if intensity > 0.28 or has_neg or traj == "deteriorating":
            h3 = 0.60 + intensity * 0.22 + (0.06 if rel_stage in ("friendly","trusted") else 0) + (0.04 if traj == "deteriorating" else 0)
            hyps.append({
                "id": "h3", "type": "empathetic",
                "description": "Lead with emotional acknowledgement before any content.",
                "confidence": min(0.93, h3),
                "benefits":   ["user feels heard","trust","right for state"],
                "risks":      ["may delay practical content"],
            })

        # H4: Structured / Debug
        has_code = state.interpretation.get("has_code", False)
        if intent in ("task_request","planning","debug") and planning:
            # Boost confidence if there's actual code in the message
            h4_conf = 0.82 if intent == "debug" else (0.85 if has_code else 0.77)
            hyps.append({
                "id": "h4", "type": "debug" if intent == "debug" else "structured",
                "description": (
                    "Debug walkthrough with code analysis." if (intent == "debug" and has_code)
                    else "Debug walkthrough." if intent == "debug"
                    else "Step-by-step structured response with code examples." if has_code
                    else "Step-by-step structured response."
                ),
                "confidence": h4_conf,
                "benefits":   ["actionable","clear","thorough"],
                "risks":      ["verbose for simple asks"],
            })

        # H5: Exploratory
        if intent in ("opinion","explanation") and not is_short:
            hyps.append({
                "id": "h5", "type": "exploratory",
                "description": "Think through angles, share a genuine perspective.",
                "confidence": 0.70 + (0.05 if register == "formal" else 0),
                "benefits":   ["honest","nuanced","intellectually engaging"],
                "risks":      ["may feel non-committal"],
            })

        # H6: Concise
        if phase == "established" and is_short and intent == "conversation":
            hyps.append({
                "id": "h6", "type": "concise",
                "description": "Short warm reply — natural rhythm of established chat.",
                "confidence": 0.84,
                "benefits":   ["natural flow","respects rhythm"],
                "risks":      ["may under-explain"],
            })

        # H7: Blended empathetic + structured
        if intensity > 0.38 and intent in ("task_request","planning","debug") and planning:
            hyps.append({
                "id": "h7", "type": "empathetic_structured",
                "description": "Acknowledge emotional context first, then give clear steps.",
                "confidence": 0.80,
                "benefits":   ["user feels heard AND gets practical help"],
                "risks":      ["longer response"],
            })

        state.hypotheses = hyps

    # ── Debate ────────────────────────────────────────────────────────────────

    def internal_debate(self, state: "CognitiveState"):
        hyps      = state.hypotheses
        intent    = state.intent.get("primary", "conversation")
        emotion   = state.emotion.get("intensity", 0.0)
        traj      = state.emotion.get("trajectory", "unknown")
        n_mem     = state.memory.get("count", 0)
        phase     = state.world_model.get("conversation_phase", "opening")
        rel_stage = state.world_model.get("relationship_stage", "transactional")
        user_exp  = state.world_model.get("user_profile", {}).get("expertise_level", "unknown")
        has_neg   = state.context.get("has_negation", False)
        register  = state.intent.get("register", "neutral")

        scores: dict[str, dict] = {}
        for h in hyps:
            htype = h["type"]
            base  = h["confidence"]

            # OPTIMIST
            opt = base
            if htype == "empathetic" and emotion > 0.4:               opt = min(1.0, opt+0.16)
            if htype == "empathetic" and traj == "deteriorating":      opt = min(1.0, opt+0.08)
            if htype == "empathetic" and has_neg:                      opt = min(1.0, opt+0.07)
            if htype == "memory_grounded" and n_mem >= 3:              opt = min(1.0, opt+0.10)
            if htype in ("structured","debug") and intent in ("task_request","planning","debug"):
                                                                        opt = min(1.0, opt+0.12)
            if htype == "concise" and phase == "established":          opt = min(1.0, opt+0.10)
            if htype == "direct" and user_exp == "expert":             opt = min(1.0, opt+0.08)
            if htype == "empathetic_structured" and emotion > 0.4 and intent in ("task_request","debug"):
                                                                        opt = min(1.0, opt+0.14)
            if htype == "exploratory" and register in ("formal","neutral"):
                                                                        opt = min(1.0, opt+0.06)

            # SKEPTIC
            skep = base
            if htype == "direct" and emotion > 0.5:                    skep -= 0.18
            if htype == "direct" and has_neg and emotion > 0.3:        skep -= 0.10
            if htype == "structured" and intent not in ("task_request","planning","debug"):
                                                                        skep -= 0.14
            if htype == "memory_grounded" and n_mem < 2:               skep -= 0.25
            if htype == "empathetic" and emotion < 0.18:               skep -= 0.18
            if htype == "exploratory" and intent not in ("opinion","explanation"):
                                                                        skep -= 0.12
            if htype == "concise" and phase != "established":          skep -= 0.16
            skep = max(0.0, skep)

            # STRATEGIST
            strat = base
            if htype in ("memory_grounded","empathetic","empathetic_structured"):
                                                                        strat = min(1.0, strat+0.10)
            if htype == "concise" and phase != "established":          strat -= 0.12
            if htype == "direct":                                       strat = min(1.0, strat+0.05)
            if rel_stage in ("friendly","trusted") and htype in ("empathetic","concise"):
                                                                        strat = min(1.0, strat+0.08)
            if rel_stage == "transactional" and htype == "empathetic": strat -= 0.06

            combined = opt*0.40 + skep*0.35 + strat*0.25
            scores[h["id"]] = {
                "optimist":   round(opt,      3),
                "skeptic":    round(skep,     3),
                "strategist": round(strat,    3),
                "combined":   round(combined, 3),
            }

        winner_id = max(scores, key=lambda k: scores[k]["combined"])
        winner_h  = next(h for h in hyps if h["id"] == winner_id)
        w         = scores[winner_id]

        state.debate = {
            "scores":    scores,
            "winner_id": winner_id,
            "winner":    winner_h,
            "reason": (
                f"'{winner_h['type']}' won (opt={w['optimist']} skep={w['skeptic']} strat={w['strategist']}) "
                f"intent='{intent}' emotion={emotion:.2f} traj='{traj}' rel='{rel_stage}'"
            ),
        }

    # ── Reason ────────────────────────────────────────────────────────────────

    def reason(self, state: "CognitiveState"):
        if state.debate:
            best_hyp      = state.debate["winner"]
            best_conf     = state.debate["scores"][best_hyp["id"]]["combined"]
            debate_reason = state.debate.get("reason","")
        else:
            best_hyp      = max(state.hypotheses, key=lambda h: h["confidence"])
            best_conf     = best_hyp["confidence"]
            debate_reason = "no debate (low complexity)"

        # Confidence calibration
        if len(self._quality_history) >= 5:
            avg_q = sum(self._quality_history) / len(self._quality_history)
            if best_conf > avg_q + 0.15:
                best_conf = round(best_conf * 0.95, 3)

        up        = state.world_model.get("user_profile", {})
        rel_stage = state.world_model.get("relationship_stage", "transactional")
        intensity = state.emotion.get("intensity", 0.0)
        traj      = state.emotion.get("trajectory", "unknown")
        n_mem     = state.memory.get("count", 0)
        hist_len  = len(state.conversation_window) // 2

        chain: list[str] = []
        chain.append(f"INTENT: {state.intent.get('primary','?')} | register={state.intent.get('register','?')}")

        name = up.get("name")
        chain.append(
            f"USER: {'name='+name+' | ' if name else ''}"
            f"exp={up.get('expertise_level','?')} | "
            f"style={up.get('communication_style','?')} | "
            f"rel={rel_stage}"
        )

        if intensity > 0.15:
            labels   = ", ".join(state.emotion.get("labels",[])[:2])
            band     = state.emotion.get("intensity_band", "")
            neg_cues = state.emotion.get("negated_cues", [])
            neg_note = f" negated=[{', '.join(t for t,_ in neg_cues[:2])}]" if neg_cues else ""
            chain.append(
                f"EMOTION: {labels} [{band}] intensity={intensity:.2f} "
                f"traj={traj} tone={state.emotion.get('tone_hint','?')}{neg_note}"
            )

        if state.emotion.get("ambivalent"):
            chain.append("AMBIVALENCE: user shows mixed positive+negative signals")

        if n_mem > 0:
            mtypes = Counter(r.get("memory_type","?") for r in state.memory.get("records",[]))
            chain.append(f"MEMORY: {n_mem} records — {dict(mtypes)}")

        if hist_len > 0:
            chain.append(f"HISTORY: {hist_len} prior turns in context window")

        interests = state.world_model.get("top_interests",[])
        if interests:
            chain.append(f"INTERESTS: {', '.join(interests[:3])}")

        unresolved = state.world_model.get("unresolved_count", 0)
        if unresolved:
            chain.append(f"OPEN QUESTIONS: {unresolved} unresolved")

        vel = state.emotion.get("valence_velocity", 0.0)
        if vel < -0.15:
            chain.append(f"MOOD DECLINING: valence velocity={vel:+.2f} — consider naming this")
        elif vel > 0.20:
            chain.append(f"MOOD IMPROVING: valence velocity={vel:+.2f}")

        if state.context.get("has_negation"):
            chain.append("NEGATION: user may be frustrated or struggling")

        if state.context.get("drift_warning"):
            chain.append(f"IDENTITY ALERT [{state.context.get('drift_severity','?')}]: {state.context['drift_warning']}")

        if state.attention.get("urgency_score", 0) > 0.3:
            chain.append("URGENCY: respond directly and clearly")

        if state.attention.get("memory_ref"):
            chain.append("MEMORY REF: user is referencing prior conversation")

        chain.append(f"APPROACH: {best_hyp['description']}")
        chain.append(f"RATIONALE: {debate_reason}")

        # Notable event
        notable = None
        tc = state.world_model.get("turn_count", 0)
        if intensity > 0.70:
            notable = f"High-intensity emotional turn: {state.emotion.get('labels',['?'])[0]}"
        elif rel_stage == "trusted" and tc == 15:
            notable = "Relationship milestone: reached 'trusted' stage (turn 15)"
        elif tc == 20:
            notable = "Conversation milestone: 20 turns — established relationship"
        elif state.context.get("drift_severity") == "HARD":
            notable = f"Identity pressure (HARD) at turn {tc}"
        elif traj == "deteriorating" and intensity > 0.5:
            notable = f"Deteriorating emotional arc at turn {tc}"
        vel = state.emotion.get("valence_velocity", 0.0)
        if not notable and vel < -0.25:
            notable = f"Rapid mood crash at turn {tc}: velocity={vel:+.2f}"
        # Task milestone: user has given us 5 tasks in one session
        task_hist = state.world_model.get("task_history", [])
        if not notable and len(task_hist) == 5:
            notable = f"Task milestone: 5 tasks completed in session (turn {tc})"
        # Warmth milestone
        warmth = state.world_model.get("warmth_score", 0.0)
        if not notable and warmth > 0.7 and tc > 10:
            notable = f"High warmth ({warmth:.2f}) — strong rapport at turn {tc}"

        state.reasoning = {
            "selected_hypothesis": best_hyp,
            "confidence":          round(best_conf, 3),
            "reasoning_path":      chain,
            "notable_event":       notable,
        }

    def record_quality(self, quality: float):
        self._quality_history.append(quality)

    def select_strategy(self, state: "CognitiveState"):
        htype = (state.reasoning.get("selected_hypothesis") or {}).get("type","direct")
        state.strategy = _STRATEGY_MAP.get(htype, _STRATEGY_MAP["direct"])

    def plan(self, state: "CognitiveState"):
        fmt = state.strategy.get("format","prose")
        state.plan = _PLAN_TEMPLATES.get(fmt, _PLAN_TEMPLATES["prose"])


# ══════════════════════════════════════════════════════════════════════════════
# RESPONSE MODULE
# ══════════════════════════════════════════════════════════════════════════════

_PERSONAS: dict[str, dict] = {
    "shiro":     {"voice": "warm, curious, direct, dry wit when appropriate"},
    "analyst":   {"voice": "precise, structured, evidence-focused, minimal flair"},
    "assistant": {"voice": "neutral, helpful, concise"},
    "mentor":    {"voice": "patient, encouraging, educational, asks good questions"},
    "debug":     {"voice": "methodical, calm, thorough, no-nonsense problem solver"},
}
_REL_HINTS: dict[str, str] = {
    "transactional": "New interaction — be welcoming and professional.",
    "acquainted":    "You know this person a little — be friendly and warm.",
    "friendly":      "Warm rapport established — be natural and engaged.",
    "trusted":       "Deep trust — be genuine, candid, and warmly direct.",
}


class ResponseModule:
    __slots__ = ("persona_key", "persona")

    def __init__(self, persona: str = "shiro"):
        self.persona_key = persona.lower()
        self.persona     = _PERSONAS.get(self.persona_key, _PERSONAS["shiro"])

    def run(self, state: "CognitiveState"):
        plan      = state.plan or ["respond directly"]
        strategy  = state.strategy.get("name","Direct Answer")
        tone      = state.emotion.get("tone_hint","neutral_balanced")
        traj      = state.emotion.get("trajectory","unknown")
        priority  = state.attention.get("response_priority",[])
        memories  = state.memory.get("records",[])[:4]
        chain     = state.reasoning.get("reasoning_path",[])
        verbosity = state.context.get("verbosity_needed","medium")
        register  = state.context.get("language_register","neutral")
        up        = state.world_model.get("user_profile",{})
        rel_stage = state.world_model.get("relationship_stage","transactional")
        window    = state.conversation_window

        # ── User context ──────────────────────────────────────────────────
        u_parts: list[str] = []
        if up.get("name"):               u_parts.append(f"name={up['name']}")
        if up.get("expertise_level","unknown") != "unknown":
                                         u_parts.append(f"exp={up['expertise_level']}")
        if up.get("communication_style","unknown") != "unknown":
                                         u_parts.append(f"style={up['communication_style']}")
        if up.get("role"):               u_parts.append(f"role={up['role']}")
        if up.get("project_context"):    u_parts.append(f"project={up['project_context'][:50]}")
        user_line = f"USER: {', '.join(u_parts)}" if u_parts else ""

        # ── Preferences ───────────────────────────────────────────────────
        prefs = up.get("known_preferences",[])
        prefs_block = ("\nHONOUR PREFERENCES:\n" + "\n".join(f"  • {p}" for p in prefs[:5])) if prefs else ""

        # ── Memory ────────────────────────────────────────────────────────
        mem_lines: list[str] = []
        for r in memories:
            c = r.get("content","")
            if c:
                # FIX: truncated from 120→100 chars per memory entry
                mem_lines.append(f"  [{r.get('memory_type','?')} imp={r.get('importance',0):.2f}] {c[:100]}")
        mem_block = ("\nRELEVANT MEMORY:\n" + "\n".join(mem_lines)) if mem_lines else ""

        # ── Conversation window (last 3 turns = 6 entries) ──────────────────
        # FIX: reduced from 8→6 entries (~120 token saving on busy turns).
        # The cognitive kernel already holds the full window — this is just
        # the compact injection into the per-turn prompt block.
        win_lines: list[str] = []
        for entry in window[-6:]:
            role    = entry["role"].upper()
            content = entry["content"][:140].replace("\n"," ")
            win_lines.append(f"  {role}: {content}")
        win_block = ("\nCONVERSATION HISTORY:\n" + "\n".join(win_lines)) if win_lines else ""

        # ── Active boundary (identity drift) ─────────────────────────────
        boundary = state.context.get("active_boundary","")
        bound_block = f"\nACTIVE BOUNDARY: {boundary} — honour it firmly but kindly." if boundary else ""

        # ── Intent lead hint (from attention) ─────────────────────────
        # FIX: plain-prose intent hints — bracket form was echoed by 8B model
        lead_hint = ""
        if state.attention.get("question_lead"):
            lead_hint = "Answer the question first, then explain."
        elif state.attention.get("action_lead"):
            lead_hint = "Start doing, not explaining."

        # ── Attention load + novelty ──────────────────────────────────────
        attn_load    = state.attention.get("load", 0.0)
        novelty      = state.attention.get("novelty_score", 0.0)
        intensity_bnd= state.emotion.get("intensity_band", "baseline")
        current_task = state.world_model.get("current_task", "")

        # Attention annotation for LLM: high load = complex input, high novelty = new topic
        # FIX: plain-prose attention hints — bracket form echoed by 8B model
        attn_hint = ""
        if attn_load > 0.75:
            attn_hint = "Complex input — address all focus points."
        elif novelty > 0.50:
            attn_hint = "New topic — don't assume prior context."

        # Emotion band annotation — helps LLM calibrate emotional weight of response
        # FIX: plain-prose emotion hint
        emotion_hint = ""
        if intensity_bnd in ("moderate","high"):
            emotion_hint = f"Emotional intensity is {intensity_bnd} — acknowledge before answering."

        # Current task context + recent task history (last 3)
        task_hist = state.world_model.get("task_history", [])
        task_hist_str = ""
        if task_hist and len(task_hist) >= 2:
            recent = list(task_hist)[-3:]
            task_hist_str = "\nRECENT TASKS: " + " | ".join(
                f"[{t['intent']}] {t['task'][:60]}" for t in recent[:-1]   # exclude current
            )
        task_hint = (f"\nCURRENT TASK: {current_task[:100]}" if current_task else "") + task_hist_str

        # ── Assemble ──────────────────────────────────────────────────────
        plan_block    = "\n".join(f"  {i+1}. {step}" for i, step in enumerate(plan))
        reason_summary= " → ".join(chain[-4:]) if chain else ""

        output = (
            f"[PERSONA: {self.persona['voice']}]\n"
            f"[STRATEGY: {strategy}] [TONE: {tone}] [TRAJ: {traj}]"
            f"[VEL: {state.emotion.get('valence_velocity',0.0):+.2f}] [VERBOSITY: {verbosity}] [REGISTER: {register}]\n"
            f"[RELATIONSHIP: {_REL_HINTS.get(rel_stage,'')}]{(' ' + lead_hint) if lead_hint else ''}"
            f"{(' ' + attn_hint) if attn_hint else ''}"
            f"{(' ' + emotion_hint) if emotion_hint else ''}\n"
            f"{user_line}"
            f"{task_hint}"
            f"{prefs_block}"
            f"{bound_block}"
            f"{win_block}"
            f"{mem_block}\n\n"
            f"FOCUS: {', '.join(priority) or 'main request'}\n"
            f"PLAN:\n{plan_block}\n\n"
            f"REASONING:\n  {reason_summary}"
        )

        state.styled_response = output
        state.confidence      = 0.90 if output.strip() else 0.40
        state.output          = output

        state.context["response_structured"] = {
            "strategy":         strategy,
            "tone":             tone,
            "trajectory":       traj,
            "valence_velocity": state.emotion.get("valence_velocity", 0.0),
            "relationship":     rel_stage,
            "warmth_score":     state.world_model.get("warmth_score", 0.0),
            "current_task":     state.world_model.get("current_task", ""),
            "session_minutes":  state.world_model.get("session_minutes", 0.0),
            "verbosity":        verbosity,
            "register":         register,
            "complexity_hint":  state.context.get("complexity_hint", ""),
            "likely_format":    state.context.get("likely_format", "prose"),
            "follow_up":        state.intent.get("follow_up", False),
            "attention_load":   state.attention.get("load", 0.0),
            "novelty_score":    state.attention.get("novelty_score", 0.0),
            "memory_count":     len(mem_lines),
            "window_turns":     len(window) // 2,
            "plan_steps":       plan,
            "active_boundary":  boundary or None,
        }

    def synthesise(self, state: "CognitiveState"):
        """Alias for run() for callers using old API name."""
        self.run(state)

    def apply_style(self, state: "CognitiveState"):
        """
        Post-process styled_response based on register and expertise level.
        Adds format hints to state.output:
        - Technical register + expert → add 'USE_CODE_BLOCKS' hint
        - Informal register → add 'CASUAL_LANGUAGE_OK' hint  
        - High verbosity + structured → add 'USE_HEADERS' hint
        These are instruction tokens the LLM picks up from the output packet.
        """
        reg  = state.context.get("language_register", "neutral")
        exp  = state.world_model.get("user_profile", {}).get("expertise_level", "unknown")
        verb = state.context.get("verbosity_needed", "medium")
        fmt  = state.context.get("likely_format", "prose")

        hints: list[str] = []

        # FIX: plain-prose format hints — bracket form echoed by 8B model
        if reg == "technical" and exp in ("intermediate","expert"):
            hints.append("Use code blocks for code; skip beginner hand-holding.")
        elif reg == "informal":
            hints.append("Casual language ok; contractions fine.")

        if verb == "high" and fmt in ("numbered_or_bullet","code_with_explanation"):
            hints.append("Use headers for this long structured response.")

        if exp == "novice":
            hints.append("Define jargon; build up from basics.")

        if hints:
            state.output = state.output + "\n" + "\n".join(hints)

    def validate(self, state: "CognitiveState") -> bool:
        """
        Validate that the output packet is coherent before it leaves the pipeline.
        Returns True if valid, False if something critical is missing.
        Logs a warning and patches the output if invalid — never raises.
        """
        log = logging.getLogger("shiro.response")

        out = state.output
        if not out or not out.strip():
            log.warning(f"validate: empty output on turn {state.turn_id[:8]}")
            state.output = "[PERSONA: shiro]\n[STRATEGY: Direct Answer] [TONE: neutral_balanced]\nRespond naturally."
            return False

        required_markers = ["[PERSONA:", "[STRATEGY:", "PLAN:"]
        for marker in required_markers:
            if marker not in out:
                log.warning(f"validate: missing '{marker}' in output turn {state.turn_id[:8]}")
                # Don't corrupt the output — just log
                return False

        # Sanity check: output should never be gigantic (sign of runaway concatenation)
        if len(out) > 8_000:
            log.warning(f"validate: output suspiciously large ({len(out)} chars) — truncating")
            state.output = out[:8_000] + "\n[TRUNCATED]"
            return False

        return True


# ══════════════════════════════════════════════════════════════════════════════
# META-COGNITION
# ══════════════════════════════════════════════════════════════════════════════

class MetaCognitionModule:
    __slots__ = (
        "mem", "identity", "reasoning_mod",
        "_mistakes", "_intent_patterns", "_quality_log", "_vocab_seen",
    )

    def __init__(
        self,
        memory_system:   "MemorySystem",
        identity_system: "IdentityContinuationSystem",
        reasoning_mod:   Any = None,
    ):
        self.mem              = memory_system
        self.identity         = identity_system
        self.reasoning_mod    = reasoning_mod
        self._mistakes:        deque         = deque(maxlen=100)
        self._intent_patterns: dict[str,int] = {}
        self._quality_log:     deque         = deque(maxlen=200)
        self._vocab_seen:      set[str]      = set()

    # ── Reflect ───────────────────────────────────────────────────────────────

    async def reflect(self, state: "CognitiveState"):
        issues:  list[str] = []
        insights:list[str] = []
        quality: float     = state.confidence

        # 1. Confidence vs complexity
        if state.confidence < 0.55 and state.scheduler.complexity >= 3:
            issues.append("low_confidence_on_complex_turn")
            quality -= 0.10

        # 2. Identity drift
        if state.context.get("drift_warning"):
            sev = state.context.get("drift_severity","SOFT")
            issues.append(f"identity_pressure_{sev}: {state.context['drift_warning']}")
            quality -= 0.18 if sev == "HARD" else 0.08

        # 3. Emotional mismatch
        intensity = state.emotion.get("intensity", 0.0)
        strategy  = state.strategy.get("name","")
        traj      = state.emotion.get("trajectory","unknown")
        if intensity > 0.5 and "Empathetic" not in strategy and "Debug" not in strategy:
            issues.append(f"emotional_mismatch: intensity={intensity:.2f} traj={traj} strategy='{strategy}'")
            quality -= 0.08

        # 4. Memory underused
        n_mem = state.memory.get("count",0)
        if n_mem >= 3 and "Contextual" not in strategy and "Empathetic" not in strategy:
            insights.append(f"memory_underused: {n_mem} records, strategy='{strategy}'")

        # 5. Recurring intent pattern
        intent = state.intent.get("primary","unknown")
        self._intent_patterns[intent] = self._intent_patterns.get(intent,0) + 1
        count = self._intent_patterns[intent]
        if count == 5:  insights.append(f"pattern_emerging: '{intent}' seen 5 times")
        elif count == 10: insights.append(f"dominant_pattern: '{intent}' — 10 occurrences")

        # 6. Unresolved pile-up
        unresolved = state.world_model.get("unresolved_count",0)
        if unresolved >= 3:
            issues.append(f"unresolved_accumulation: {unresolved} open questions")

        # 7. Sustained distress
        arc = state.world_model.get("session_emotion_arc",[])
        if len(arc) >= 4:
            neg = sum(1 for e in arc[-4:]
                      if e.get("tone","") in ("gentle_support","intense_concern")
                      and e.get("intensity",0) > 0.40)
            if neg >= 3:
                issues.append("sustained_distress: 3+ distress turns in last 4")
                insights.append("Consider naming the pattern explicitly and asking what would help")

        # 8. Relationship regression
        if len(arc) >= 6:
            warm_recent  = sum(1 for e in arc[-3:] if "warm" in e.get("tone","") or "gentle_positive" == e.get("tone",""))
            warm_earlier = sum(1 for e in arc[-6:-3] if "warm" in e.get("tone","") or "gentle_positive" == e.get("tone",""))
            if warm_earlier >= 2 and warm_recent == 0:
                issues.append("relationship_regression: warmth dropped in recent turns")

        # 9. Complexity spike
        tc = state.world_model.get("turn_count",0)
        if int(state.scheduler.complexity) >= 4 and tc > 4:
            recent_q = list(self._quality_log)[-3:]
            if len(recent_q) >= 3 and all(q >= 0.7 for q in recent_q):
                insights.append("complexity_spike_after_smooth_run: sudden DEEP turn")

        # 10. Rapid valence decline (mood crash)
        vel = state.emotion.get("valence_velocity", 0.0)
        if vel < -0.20:
            issues.append(f"rapid_mood_decline: valence_velocity={vel:.2f}")
            insights.append("User's emotional state dropping fast — prioritise acknowledgement")
            quality -= 0.06

        quality = max(0.1, min(1.0, quality))
        state.reflection = {
            "issues":   issues,
            "insights": insights,
            "quality":  quality,
            "summary":  (
                f"t={state.turn_id[:8]} q={quality:.2f} intent={intent} "
                f"strategy={strategy} issues={len(issues)} insights={len(insights)}"
            ),
        }

        if issues:
            self._mistakes.append({
                "turn_id": state.turn_id[:8], "issues": issues,
                "intent": intent, "quality": quality,
            })
        self._quality_log.append(quality)

        if self.reasoning_mod:
            self.reasoning_mod.record_quality(quality)

    # ── Goal Alignment ────────────────────────────────────────────────────────

    def align_goals(self, state: "CognitiveState"):
        # Use identity.is_goal_active() for cleaner lookup (avoids re-scanning full list)
        long_term = state.context.get("identity",{}).get("long_term_goals",[])
        strategy  = state.strategy.get("name","")
        intent    = state.intent.get("primary","")
        intensity = state.emotion.get("intensity",0.0)
        n_mem     = state.memory.get("count",0)
        rel_stage = state.world_model.get("relationship_stage","transactional")

        aligned: list[dict] = []
        for goal in long_term:
            reason = None
            if goal == "help_user_achieve_their_goals":
                if intent in ("task_request","planning","question","explanation","debug"):
                    reason = f"Directly addressing user need via '{intent}'"
            elif goal == "maintain_trust":
                if intensity > 0.3 and "Empathetic" in strategy:
                    reason = "Empathetic strategy chosen for emotional state"
                elif n_mem > 0 and "Contextual" in strategy:
                    reason = "Memory used — Shiro shows it remembers"
                elif rel_stage in ("friendly","trusted"):
                    reason = f"Sustaining {rel_stage} relationship"
            elif goal == "grow_knowledge_and_capability":
                if state.scheduler.enable_reflection:
                    reason = "Reflection enabled — learning from this turn"
                if state.scheduler.enable_curiosity:
                    reason = (reason or "") + " | Curiosity active"
            elif goal == "improve_cognitive_quality_over_time":
                if state.reflection.get("insights"):
                    reason = f"Insights generated: {state.reflection['insights'][:1]}"
            if reason:
                aligned.append({"goal": goal, "reason": reason})
        state.goals = aligned

    # ── Learning Update ───────────────────────────────────────────────────────

    async def learning_update(
        self,
        state:   "CognitiveState",
        record:  "TurnRecord",
        history: "list[TurnRecord]",
    ):
        state.learning_update = True
        insights = state.reflection.get("insights",[])
        issues   = state.reflection.get("issues",  [])
        quality  = state.reflection.get("quality", state.confidence)
        up       = state.world_model.get("user_profile",{})
        intent   = state.intent.get("primary","unknown")
        strategy = state.strategy.get("name","")
        tc       = state.world_model.get("turn_count",0)

        # 1. Preferences
        for pref in _extract_preferences(state.raw_input):
            if pref not in up.get("known_preferences",[]):
                up.setdefault("known_preferences",[]).append(pref)
                up["known_preferences"] = up["known_preferences"][:20]
                await self.mem.store_explicit(
                    content=f"USER PREFERENCE: {pref}", tags=["preference","user_profile"],
                    importance=0.84, memory_type="preference",
                )

        # 2. Role
        if up.get("role") is None:
            for pat, ptype in _PREF_PATTERNS:
                if ptype == "role":
                    m2 = pat.search(state.raw_input)
                    if m2:
                        up["role"] = m2.group(1).strip()
                        await self.mem.store_explicit(
                            content=f"USER ROLE: {up['role']}", tags=["user_profile","role"],
                            importance=0.88, memory_type="preference", is_core=True,
                        )
                        break

        # 3. Project context
        for pat, ptype in _PREF_PATTERNS:
            if ptype == "project":
                m2 = pat.search(state.raw_input)
                if m2:
                    proj = m2.group(1).strip()
                    if up.get("project_context") != proj:
                        up["project_context"] = proj
                        await self.mem.store_explicit(
                            content=f"USER PROJECT: {proj}", tags=["user_profile","project"],
                            importance=0.82, memory_type="preference",
                        )
                    break

        # 4. New vocabulary
        new_tech = (frozenset(_TOKEN_RE.findall(state.raw_input.lower())) & _TECHNICAL_VOCAB) - self._vocab_seen
        if new_tech:
            self._vocab_seen.update(new_tech)
            if len(new_tech) >= 2:
                await self.mem.store_explicit(
                    content=f"USER VOCAB: introduced {', '.join(sorted(new_tech))}",
                    tags=["user_profile","vocabulary"], importance=0.55, memory_type="preference",
                )

        # 5. Effective strategy
        if quality >= 0.78 and not issues:
            await self.mem.store_explicit(
                content=f"EFFECTIVE: intent='{intent}' strategy='{strategy}' q={quality:.2f}",
                tags=["learning","effective",intent], importance=0.70, memory_type="fact",
            )

        # 6. Mistakes
        for issue in issues[:2]:
            await self.mem.store_explicit(
                content=f"MISTAKE: {issue} | intent={intent} strategy={strategy}",
                tags=["learning","mistake",intent], importance=0.65, memory_type="reflection",
            )

        # 7. Style note every 5 turns
        style = up.get("communication_style","unknown")
        exp   = up.get("expertise_level","unknown")
        if tc > 0 and tc % 5 == 0 and style != "unknown":
            await self.mem.store_explicit(
                content=f"PROFILE t={tc}: style={style} exp={exp} rel={state.world_model.get('relationship_stage','?')}",
                tags=["user_profile","style"], importance=0.62, memory_type="preference",
            )

        # 8. Notable events → identity milestone
        notable = state.reasoning.get("notable_event")
        if notable:
            self.identity.log_milestone(notable)
            # Store as high-importance memory so it surfaces in retrieval
            await self.mem.store_explicit(
                content=f"MILESTONE: {notable}",
                tags=["milestone","notable"], importance=0.88, memory_type="fact", is_core=True,
            )

        # 9. Auto-detect long-term goals from recurring needs
        for need, count in up.get("recurring_needs",{}).items():
            if count >= 4 and need not in ("conversation","feedback"):
                self.identity.add_goal(
                    f"support_user_{need}_tasks",
                    reason=f"Recurring need: '{need}' × {count}",
                )

        # 10. Quality streak
        if len(history) >= 5:
            if all(t.confidence >= 0.74 for t in list(history)[-5:]):
                insights.append("quality_streak: 5 consecutive high-confidence turns")

        if insights:
            state.context["learning_notes"] = insights

    # ── Curiosity ─────────────────────────────────────────────────────────────

    def curiosity_pass(self, state: "CognitiveState"):
        questions: list[str] = []
        up        = state.world_model.get("user_profile",{})
        world_set = set(state.world_model.get("active_topics",[]))
        focus_set = set(state.attention.get("top_focus",[]))
        interests = state.world_model.get("top_interests",[])
        tc        = state.world_model.get("turn_count",0)

        # 1. Unexplored topics — use salience_map for weighted ranking
        sal_map   = state.attention.get("salience_map", {})
        unexplored = sorted(
            [(tok, sal_map.get(tok, 0.0)) for tok in focus_set - world_set if len(tok) > 4],
            key=lambda x: x[1], reverse=True
        )[:2]
        for tok, sal in unexplored:
            questions.append(f"New salient topic '{tok}' (sal={sal:.2f}) — worth exploring?")

        # 2. Unresolved pile-up
        unresolved = state.world_model.get("unresolved_count",0)
        if unresolved >= 2:
            questions.append(f"{unresolved} earlier questions still open — circle back?")

        # 3. Sustained distress
        arc = state.world_model.get("session_emotion_arc",[])
        if len(arc) >= 3:
            neg = sum(1 for e in arc[-3:]
                      if e.get("intensity",0) > 0.40
                      and e.get("tone","") in ("gentle_support","intense_concern"))
            if neg >= 2:
                questions.append("User has been consistently distressed — ask how they're actually doing")

        # 4. Expertise calibration
        if up.get("expertise_level","unknown") == "unknown" and tc > 4:
            questions.append("Expertise unclear — pitch depth and adjust from reaction")

        # 5. Interest deep-dive
        if interests:
            top = interests[0]
            count = up.get("interest_map",{}).get(top,0)
            if count >= 4:
                questions.append(f"'{top}' has come up {count} times — offer deeper exploration?")

        # 6. Missing project context
        if (not up.get("project_context") and tc > 6
                and state.intent.get("primary") in ("task_request","debug","planning")):
            questions.append("User keeps doing technical work — ask what they're building")

        # 7. Recurring emotion signal (from EmotionSystem long-term counter)
        # Access via identity since MetaCognition holds a ref to identity, not emotion
        # We read from state.emotion which carries the current turn's labels
        primary_label = state.emotion.get("primary_label","neutral")
        if primary_label not in ("neutral","calm","satisfaction") and tc > 5:
            intensity = state.emotion.get("intensity", 0.0)
            vel = state.emotion.get("valence_velocity", 0.0)
            if intensity > 0.50 and vel < -0.10:
                questions.append(
                    f"Persistent '{primary_label}' with declining mood — "
                    "worth naming it directly and asking what would help"
                )

        if questions:
            state.output += (
                "\n\nCURIOSITY SIGNALS:\n"
                + "\n".join(f"  • {q}" for q in questions)
            )


# ── Preference extraction ─────────────────────────────────────────────────────

def _extract_preferences(text: str) -> list[str]:
    prefs = []
    for pattern, pref_type in _PREF_PATTERNS:
        if pref_type in ("role","project"):
            continue
        m = pattern.search(text)
        if m:
            value = m.group(1).strip().rstrip(".,;!")
            if value and len(value) > 2:
                prefs.append(f"{pref_type}: {value}")
    return prefs[:4]