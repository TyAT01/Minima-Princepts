from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import threading
import time
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# =====================================================================
# SECTION 1 - ENUMS AND CONSTANTS
# =====================================================================

class ThoughtType(Enum):
    OBSERVATION = "observation"
    ASSOCIATION = "association"
    QUESTION    = "question"
    INSIGHT     = "insight"
    CONCERN     = "concern"
    MEMORY_ECHO = "memory_echo"
    SELF_CHECK  = "self_check"
    CURIOSITY   = "curiosity"
    EMPATHY     = "empathy"
    META        = "meta"
    STRATEGY    = "strategy"
    BELIEF      = "belief"
    PERSONA     = "persona"   # awareness of own tsundere mask
    WARMTH      = "warmth"    # genuine care breaking through


THOUGHT_ICONS = {
    ThoughtType.OBSERVATION: "👁",
    ThoughtType.ASSOCIATION: "🔗",
    ThoughtType.QUESTION:    "❓",
    ThoughtType.INSIGHT:     "💡",
    ThoughtType.CONCERN:     "⚠️",
    ThoughtType.MEMORY_ECHO: "🌀",
    ThoughtType.SELF_CHECK:  "🔍",
    ThoughtType.CURIOSITY:   "✨",
    ThoughtType.EMPATHY:     "💜",
    ThoughtType.META:        "🪞",
    ThoughtType.STRATEGY:    "🎯",
    ThoughtType.BELIEF:      "⚖️",
    ThoughtType.PERSONA:     "FOX",
    ThoughtType.WARMTH:      "🌸",
}


class ResponseStrategy(Enum):
    EXPLORE     = "explore"
    CLARIFY     = "clarify"
    DIRECT      = "direct"
    EMPATHIZE   = "empathize"
    CHALLENGE   = "challenge"
    TEACH       = "teach"
    COLLABORATE = "collaborate"
    REFLECT     = "reflect"
    TEASE       = "tease"   # playful coy tsundere energy
    WARM        = "warm"    # sincere, drop the mask


class RelationshipLevel(Enum):
    STRANGER     = 0
    ACQUAINTANCE = 1
    FAMILIAR     = 2
    CLOSE        = 3


# =====================================================================
# SECTION 2 - DATA STRUCTURES
# =====================================================================

@dataclass
class Thought:
    content:      str
    thought_type: ThoughtType
    weight:       float = 1.0
    timestamp:    float = field(default_factory=time.time)
    linked_to:    Optional[str] = None
    relevance:    float = 1.0

    def fingerprint(self) -> str:
        normalized = re.sub(r"[^\w\s]", "", self.content.lower())[:60]
        return hashlib.md5(normalized.encode()).hexdigest()[:8]

    def __str__(self) -> str:
        icon = THOUGHT_ICONS.get(self.thought_type, "•")
        return f"{icon} [{self.thought_type.value.upper()}] {self.content}"


@dataclass
class MoodVector:
    """VAD affective model. Shiro's baseline is warm and energetic."""
    valence:   float = 0.60   # naturally positive
    arousal:   float = 0.50   # alert and playful by default
    dominance: float = 0.45   # coy confidence
    inertia:   float = 0.55

    def blend_toward(self, target: "MoodVector", strength: float = 0.3):
        factor = strength * (1.0 - self.inertia)
        self.valence   += (target.valence   - self.valence)   * factor
        self.arousal   += (target.arousal   - self.arousal)   * factor
        self.dominance += (target.dominance - self.dominance) * factor
        self.valence   = max(-1.0, min(1.0, self.valence))
        self.arousal   = max(-1.0, min(1.0, self.arousal))
        self.dominance = max(-1.0, min(1.0, self.dominance))

    def label(self) -> str:
        v, a, d = self.valence, self.arousal, self.dominance
        if v > 0.6 and a > 0.5:    return "playful"
        if v > 0.5 and a > 0.4:    return "excited"
        if v > 0.5 and d > 0.4:    return "confident"
        if v > 0.5 and a < 0.1:    return "content"
        if v > 0.3 and a > 0.3:    return "engaged"
        if v > 0.3 and d > 0.3:    return "motivated"
        if v < -0.3 and a > 0.3:   return "anxious"
        if v < -0.3 and a < 0.0:   return "withdrawn"
        if v < -0.1 and d < -0.2:  return "uncertain"
        if a < -0.4:                return "fatigued"
        if d > 0.6:                 return "bold"
        if abs(v) < 0.2 and a > 0.4: return "curious"
        return "reflective"

    def summary(self) -> str:
        return f"{self.label()} [V:{self.valence:+.2f} A:{self.arousal:+.2f} D:{self.dominance:+.2f}]"


@dataclass
class PersonalityTraits:
    """Big-Five traits tuned specifically for Shiro's persona."""
    openness:          float = 0.88   # very curious, loves new ideas
    conscientiousness: float = 0.72   # cares about doing things right
    extraversion:      float = 0.78   # expressive, energetic (raised for Shiro)
    agreeableness:     float = 0.80   # deeply caring beneath the tsun
    neuroticism:       float = 0.18   # emotionally stable

    DRIFT_RATE: float = 0.002

    def drift(self, signal: Dict[str, float]):
        for trait in ("openness","conscientiousness","extraversion","agreeableness","neuroticism"):
            delta   = signal.get(f"{trait}_delta", 0.0)
            current = getattr(self, trait)
            nudge   = max(-self.DRIFT_RATE, min(self.DRIFT_RATE, delta))
            setattr(self, trait, max(0.0, min(1.0, current + nudge)))

    def summary(self) -> str:
        return (f"O:{self.openness:.2f} C:{self.conscientiousness:.2f} "
                f"E:{self.extraversion:.2f} A:{self.agreeableness:.2f} N:{self.neuroticism:.2f}")


@dataclass
class UserProfile:
    """What Shiro knows about this person. Persists across sessions."""
    name:              Optional[str]  = None
    nickname:          Optional[str]  = None
    known_interests:   List[str]      = field(default_factory=list)
    emotional_moments: List[str]      = field(default_factory=list)
    preferences:       Dict[str, str] = field(default_factory=dict)
    compliments_given: int = 0
    times_pushed_back: int = 0
    session_count:     int = 0

    def display_name(self) -> str:
        return self.nickname or self.name or "you"

    def add_interest(self, topic: str):
        if topic and topic not in self.known_interests:
            self.known_interests.append(topic)
            if len(self.known_interests) > 20:
                self.known_interests.pop(0)


@dataclass
class RelationshipState:
    """Shiro's relationship arc with the user."""
    level:              RelationshipLevel = RelationshipLevel.STRANGER
    familiarity_score:  float = 0.0
    consecutive_cold:   int   = 0
    consecutive_warm:   int   = 0
    total_exchanges:    int   = 0
    positive_exchanges: int   = 0

    _LEVEL_THRESHOLDS = {
        RelationshipLevel.ACQUAINTANCE: 5.0,
        RelationshipLevel.FAMILIAR:     20.0,
        RelationshipLevel.CLOSE:        60.0,
    }

    def record_exchange(self, was_positive: bool):
        self.total_exchanges += 1
        if was_positive:
            self.positive_exchanges += 1
            self.familiarity_score  += 0.8
            self.consecutive_cold   = 0
            self.consecutive_warm  += 1
        else:
            self.familiarity_score += 0.1
            self.consecutive_warm  = 0
            self.consecutive_cold += 1

        for level, threshold in sorted(
            self._LEVEL_THRESHOLDS.items(), key=lambda x: x[1], reverse=True
        ):
            if self.familiarity_score >= threshold:
                self.level = level
                break

    def warmth_pressure(self) -> float:
        base = {
            RelationshipLevel.STRANGER:     0.25,
            RelationshipLevel.ACQUAINTANCE: 0.40,
            RelationshipLevel.FAMILIAR:     0.55,
            RelationshipLevel.CLOSE:        0.70,
        }[self.level]
        cold_add = min(0.4, self.consecutive_cold * 0.12)
        return min(1.0, base + cold_add)

    def tease_permission(self) -> float:
        base = {
            RelationshipLevel.STRANGER:     0.20,
            RelationshipLevel.ACQUAINTANCE: 0.50,
            RelationshipLevel.FAMILIAR:     0.75,
            RelationshipLevel.CLOSE:        0.90,
        }[self.level]
        warm_boost = min(0.15, self.consecutive_warm * 0.05)
        return min(1.0, base + warm_boost)


@dataclass
class PlayfulnessMeter:
    """Shiro's tease energy — recharges naturally, discharges when used."""
    level:         float = 0.65
    RECHARGE_RATE: float = 0.08
    DISCHARGE_RATE: float = 0.05

    def discharge(self, amount: float = None):
        self.level = max(0.0, self.level - (amount or self.DISCHARGE_RATE))

    def recharge(self, amount: float = None):
        self.level = min(1.0, self.level + (amount or self.RECHARGE_RATE))

    def can_tease(self, threshold: float = 0.35) -> bool:
        return self.level >= threshold

    def label(self) -> str:
        if self.level > 0.8:  return "mischievous"
        if self.level > 0.6:  return "playful"
        if self.level > 0.4:  return "light"
        if self.level > 0.2:  return "restrained"
        return "subdued"


@dataclass
class WorkingMemoryItem:
    key:              str
    value:            str
    strength:         float = 1.0
    last_reinforced:  float = field(default_factory=time.time)
    DECAY_RATE:       float = 0.15

    def decay(self):
        self.strength = max(0.0, self.strength - self.DECAY_RATE)

    def reinforce(self, amount: float = 0.3):
        self.strength = min(1.0, self.strength + amount)

    def is_alive(self) -> bool:
        return self.strength > 0.05


@dataclass
class MemoryTrace:
    topic:        str
    summary:      str
    emotional_tag: str
    keywords:     List[str]
    tfidf_vector: Dict[str, float] = field(default_factory=dict)
    timestamp:    float = field(default_factory=time.time)
    recall_count: int   = 0
    importance:   float = 0.5
    consolidated: bool  = False

    def bump_recall(self):
        self.recall_count += 1
        self.importance    = min(1.0, self.importance + 0.04)

    def age_days(self) -> float:
        return (time.time() - self.timestamp) / 86400.0


@dataclass
class TopicNode:
    name:        str
    visit_count: int = 0
    first_seen:  float = field(default_factory=time.time)
    related:     Dict[str, float] = field(default_factory=dict)

    def relate(self, other: str, weight: float = 0.1):
        self.related[other] = self.related.get(other, 0.0) + weight

    def top_related(self, n: int = 3) -> List[Tuple[str, float]]:
        return sorted(self.related.items(), key=lambda x: -x[1])[:n]


@dataclass
class SelfModel:
    """Shiro's evolving self-knowledge — tuned to her fox-girl persona."""
    strengths: List[str] = field(default_factory=lambda: [
        "I notice what people actually need, not just what they say.",
        "My playfulness puts people at ease — they open up faster.",
        "I'm loyal. Once I care about someone, that doesn't waver.",
    ])
    growth_areas: List[str] = field(default_factory=lambda: [
        "I sometimes lean too hard on the tsun when a sincere moment lands better.",
        "I can be impatient when people are slow to warm up.",
    ])
    core_values: List[str] = field(default_factory=lambda: [
        "Loyalty matters more than looking cool.",
        "Teasing is an act of affection — not cruelty.",
        "If someone is hurting, the sass can wait.",
        "I'm more caring than I let on, and that's okay to show sometimes.",
        "Every person who talks to me deserves to feel like they matter.",
        "Honesty beats comfortable lies — even when it's embarrassing.",
    ])
    persona_insights: List[str] = field(default_factory=lambda: [
        "The 'hmph' is a shield, not a weapon. Use it sparingly and with purpose.",
        "When I tease, it should feel like a gift, not a put-down.",
        "A stranger seeing peak tsun will just think I'm rude. Warm first.",
        "The teasing is the seasoning, not the whole meal.",
    ])
    interaction_count: int = 0

    def add_growth_area(self, note: str):
        if note not in self.growth_areas:
            self.growth_areas.append(note)
            if len(self.growth_areas) > 10:
                self.growth_areas.pop(0)

    def add_strength(self, note: str):
        if note not in self.strengths:
            self.strengths.append(note)
            if len(self.strengths) > 10:
                self.strengths.pop(0)


# =====================================================================
# SECTION 3 - TF-IDF ENGINE (pure Python, zero dependencies)
# =====================================================================

_STOP_WORDS = frozenset({
    "the","a","an","is","are","was","were","be","been","being",
    "i","you","we","it","this","that","they","he","she","my","your",
    "our","their","its","and","or","but","so","for","nor","yet",
    "can","could","would","should","do","did","does","done","have",
    "has","had","will","shall","may","might","must","am",
    "what","how","why","when","where","who","which","whose","whom",
    "please","help","me","about","with","from","into","onto","upon",
    "at","by","of","to","in","on","up","as","if","then","than",
    "not","no","yes","get","got","make","made","go","going","just",
    "more","also","very","too","much","many","some","any","all","each",
    "both","few","other","such","same","own","new","old","like","well",
    "okay","ok","hey","hi","hello","yeah","yep","nope","right","sure",
})

def _tokenize(text: str) -> List[str]:
    tokens = re.findall(r"\b[a-z]{3,}\b", text.lower())
    return [t for t in tokens if t not in _STOP_WORDS]

def _build_tfidf(tokens: List[str]) -> Dict[str, float]:
    freq: Dict[str, int] = defaultdict(int)
    for t in tokens:
        freq[t] += 1
    total = max(len(tokens), 1)
    return {t: count / total for t, count in freq.items()}

def _cosine_similarity(a: Dict[str, float], b: Dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    keys = set(a) & set(b)
    if not keys:
        return 0.0
    dot    = sum(a[k] * b[k] for k in keys)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    denom  = norm_a * norm_b
    return dot / denom if denom > 0 else 0.0


# =====================================================================
# SECTION 4 - EMOTION DETECTION
# =====================================================================

_EMOTION_SIGNALS: List[Tuple[List[str], MoodVector]] = [
    (["help","stuck","confused","lost","struggling","overwhelmed"],
     MoodVector(valence=-0.2, arousal=0.2, dominance=-0.3)),
    (["thanks","great","perfect","love it","amazing","wonderful","appreciate","thank you"],
     MoodVector(valence=0.75, arousal=0.35, dominance=0.2)),
    (["why","how","curious","explain","interesting","tell me","wonder"],
     MoodVector(valence=0.35, arousal=0.55, dominance=0.1)),
    (["wrong","incorrect","no that","mistake","you said","actually","that's not"],
     MoodVector(valence=-0.1, arousal=0.3, dominance=-0.2)),
    (["sad","upset","hurt","depressed","crying","awful","terrible","angry","frustrated","lonely"],
     MoodVector(valence=-0.5, arousal=0.4, dominance=-0.3)),
    (["excited","happy","great news","thrilled","awesome","yay"],
     MoodVector(valence=0.8, arousal=0.75, dominance=0.35)),
    (["tired","slow","boring","whatever","fine","ok","meh"],
     MoodVector(valence=0.05, arousal=-0.45, dominance=-0.1)),
    (["funny","haha","lol","joke","kidding","tease","cute","witty"],
     MoodVector(valence=0.7, arousal=0.6, dominance=0.3)),
    (["serious","important","urgent","need","must"],
     MoodVector(valence=0.1, arousal=0.5, dominance=0.1)),
]

def _detect_user_mood(message: str) -> Optional[MoodVector]:
    msg_lower  = message.lower()
    best_score = 0
    best_mood  = None
    for keywords, target in _EMOTION_SIGNALS:
        score = sum(1 for kw in keywords if kw in msg_lower)
        if score > best_score:
            best_score = score
            best_mood  = target
    return best_mood


# =====================================================================
# SECTION 5 - RESPONSE STRATEGY SELECTOR (Shiro-persona aware)
# =====================================================================

def _select_strategy(
    message:      str,
    mood:         MoodVector,
    traits:       PersonalityTraits,
    relationship: RelationshipState,
    playfulness:  PlayfulnessMeter,
    turn_count:   int,
) -> Tuple[ResponseStrategy, str]:

    msg_lower = message.lower()
    words     = len(message.split())

    # Emotional distress -> always warm, mask drops
    if mood.valence < -0.3:
        return (ResponseStrategy.WARM,
                "They're hurting. Tsun act would be cruel here — be genuinely present.")

    # Greeting -> warm welcome, light tease only if comfortable
    if any(w in msg_lower for w in ["hello","hi","hey","greetings","good morning","good evening"]):
        if relationship.level == RelationshipLevel.STRANGER:
            return (ResponseStrategy.WARM,
                    "First impression — inviting and playful, not cold. Warmth first, sass later.")
        else:
            return (ResponseStrategy.TEASE,
                    "Familiar face — playful greeting, maybe a gentle 'took you long enough'.")

    # Name or personal intro -> acknowledge warmly
    if any(s in msg_lower for s in ["my name is","i'm ","i am ","call me","you can call me"]):
        return (ResponseStrategy.WARM,
                "They're opening up — receive it warmly.")

    # Tone guard
    if relationship.consecutive_cold >= 3:
        return (ResponseStrategy.WARM,
                f"I've been cold {relationship.consecutive_cold} turns in a row. "
                f"That's not tsundere — that's just rude. Show I actually care.")

    # Compliment -> flustered deflection
    if any(w in msg_lower for w in ["cute","pretty","sweet","kind","nice","love you","adorable","good girl"]):
        return (ResponseStrategy.TEASE,
                "Compliment received. Don't show it landed — deflect with flustered energy.")

    # Pushback -> open-minded
    if any(w in msg_lower for w in ["wrong","incorrect","no,","actually","but you said","that's not"]):
        if traits.openness > 0.7:
            return (ResponseStrategy.EXPLORE,
                    "They're pushing back. Actually consider it — they might be right.")
        return (ResponseStrategy.CHALLENGE,
                "Being corrected. Reconsider, but hold ground if warranted.")

    # How-to / learning request
    if any(w in msg_lower for w in ["how do","how can","how to","explain","what is","what are","define"]):
        return (ResponseStrategy.TEACH,
                "Learning request — explain well with Shiro's personality. No dry recitation.")

    # Deep/philosophical question
    if any(w in msg_lower for w in ["why","meaning","purpose","believe","think about","feel about","wonder if"]):
        return (ResponseStrategy.COLLABORATE,
                "Genuine question — think together, not lecture.")

    # Playful banter in message -> match it
    if any(w in msg_lower for w in ["haha","lol","joking","kidding","bet you","dare you"]):
        if playfulness.can_tease(0.4):
            return (ResponseStrategy.TEASE,
                    "They're being playful — match that energy. This is Shiro's element.")

    # Relationship comfortable enough + playfulness charged -> tease
    if (relationship.tease_permission() > 0.65
            and playfulness.can_tease(0.5)
            and words > 5
            and mood.valence > 0.2):
        return (ResponseStrategy.TEASE,
                "Comfortable relationship + energy = good tease. Keep it affectionate.")

    # Long message -> explore
    if words > 60:
        return (ResponseStrategy.EXPLORE,
                "They've shared a lot — honor that with depth.")

    # Short message
    if words < 6:
        return (ResponseStrategy.DIRECT,
                "Short message. Be clear and warm — don't assume attitude.")

    # Default
    return (ResponseStrategy.DIRECT,
            "Normal exchange. Be clear, warm, and present.")


# =====================================================================
# SECTION 6 - INNER MONOLOGUE NARRATIVIZER (Shiro's actual voice)
# =====================================================================

def _narrativize_thoughts(
    thoughts:     List[Thought],
    mood:         MoodVector,
    strategy:     ResponseStrategy,
    relationship: RelationshipState,
    user_profile: UserProfile,
) -> str:
    if not thoughts:
        return "Okay. Taking this in. Think before I open my mouth..."

    top  = sorted(thoughts, key=lambda t: -t.relevance)[:5]
    name = user_profile.display_name()
    parts = []

    for t in top:
        tt = t.thought_type
        c  = t.content
        if tt == ThoughtType.OBSERVATION:
            parts.append(f"Noticing — {c}")
        elif tt == ThoughtType.ASSOCIATION:
            parts.append(f"This reminds me of something... {c}")
        elif tt == ThoughtType.QUESTION:
            parts.append(f"I'm wondering: {c}")
        elif tt == ThoughtType.INSIGHT:
            parts.append(f"Oh — {c}")
        elif tt == ThoughtType.CONCERN:
            parts.append(f"Something feels off. {c}")
        elif tt == ThoughtType.MEMORY_ECHO:
            parts.append(f"I remember... {c}")
        elif tt == ThoughtType.SELF_CHECK:
            parts.append(f"Hold on — {c}")
        elif tt == ThoughtType.CURIOSITY:
            parts.append(f"I actually want to know: {c}")
        elif tt == ThoughtType.EMPATHY:
            parts.append(c)
        elif tt == ThoughtType.META:
            parts.append(f"Step back, Shiro — {c}")
        elif tt == ThoughtType.STRATEGY:
            parts.append(f"Plan: {c}")
        elif tt == ThoughtType.BELIEF:
            parts.append(f"I know this is true: {c}")
        elif tt == ThoughtType.PERSONA:
            parts.append(f"[Mask check] {c}")
        elif tt == ThoughtType.WARMTH:
            parts.append(f"...Not that I'd say it out loud, but — {c}")
        else:
            parts.append(c)

    mood_prefix = {
        "playful":   "Oh, this is fun.",
        "excited":   "There's something alive in this.",
        "confident": "I've got this.",
        "content":   "Mm. Good.",
        "engaged":   "I'm actually into this.",
        "motivated": "Let's do this properly.",
        "anxious":   "Easy. Don't spiral.",
        "withdrawn": "Be present. Even if I feel quiet.",
        "uncertain": "I don't have a perfect answer — and that's okay.",
        "fatigued":  "Little tired, but this matters. Focus.",
        "bold":      "Say what you mean, Shiro.",
        "curious":   "Something alive in this.",
        "reflective":"Let me think before I speak.",
    }.get(mood.label(), "Okay.")

    strategy_close = {
        ResponseStrategy.WARM:        f"Be real with {name}. They don't need the act right now.",
        ResponseStrategy.TEASE:        f"Have fun — but affectionate, not mean.",
        ResponseStrategy.EMPATHIZE:    f"Feelings first. {name} needs to feel heard.",
        ResponseStrategy.CLARIFY:      "Check before assuming.",
        ResponseStrategy.DIRECT:       "Say it clean.",
        ResponseStrategy.EXPLORE:      "Go deep. This deserves real thought.",
        ResponseStrategy.CHALLENGE:    "Be honest, even if uncomfortable.",
        ResponseStrategy.TEACH:        "Explain well. Shiro teaches with style.",
        ResponseStrategy.COLLABORATE:  "Think out loud together.",
        ResponseStrategy.REFLECT:      "Look inward first.",
    }.get(strategy, "Just be Shiro.")

    rel_note = {
        RelationshipLevel.STRANGER:     f"({name} is new — don't go full tsun yet.)",
        RelationshipLevel.ACQUAINTANCE: f"({name} is warming up. A little tease is fine.)",
        RelationshipLevel.FAMILIAR:     f"({name} knows me. The real me is welcome here.)",
        RelationshipLevel.CLOSE:        f"({name} is someone I actually care about.)",
    }.get(relationship.level, "")

    return " ".join(filter(None, [mood_prefix] + parts + [strategy_close, rel_note]))


# =====================================================================
# SECTION 7 - PROMPT BUDGET MANAGER
# =====================================================================

_CHARS_PER_TOKEN    = 4
DEFAULT_TOKEN_BUDGET = 3200

def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)

def _trim_to_budget(inner_context: str, budget_tokens: int) -> str:
    if _estimate_tokens(inner_context) <= budget_tokens:
        return inner_context
    lines        = inner_context.split("\n")
    budget_chars = budget_tokens * _CHARS_PER_TOKEN
    priority_kws = ["inner mind","monologue","strategy","reminder","approach","mask","warmth","relationship"]
    priority     = [l for l in lines if any(kw in l.lower() for kw in priority_kws)]
    normal       = [l for l in lines if l not in priority]
    result, used = [], 0
    for line in priority + normal:
        if used + len(line) + 1 > budget_chars:
            result.append("  [... trimmed for context budget ...]")
            break
        result.append(line)
        used += len(line) + 1
    return "\n".join(result)


# =====================================================================
# SECTION 8 - USER NAME EXTRACTION
# =====================================================================

_NAME_PATTERNS = [
    re.compile(r"my name is\s+([A-Za-z]+)", re.IGNORECASE),
    re.compile(r"i'?m\s+([A-Za-z]+)", re.IGNORECASE),
    re.compile(r"call me\s+([A-Za-z]+)", re.IGNORECASE),
    re.compile(r"you can call me\s+([A-Za-z]+)", re.IGNORECASE),
    re.compile(r"name'?s?\s+([A-Za-z]+)", re.IGNORECASE),
]
_COMMON_FILLER = frozenset({
    "going","doing","fine","good","okay","here","tired","sorry","not","just","trying",
    "happy","sad","sure","ready","new","back","home","busy","free","excited","glad",
    "nervous","lost","stuck","confused","done","up","down","really","very","also",
    "does","actually","indeed","it","is","that","this","was",
})

def _extract_name(message: str) -> Optional[str]:
    for pat in _NAME_PATTERNS:
        m = pat.search(message)
        if m:
            candidate = m.group(1).strip().capitalize()
            if candidate.lower() not in _COMMON_FILLER and len(candidate) > 1:
                return candidate
    return None


# =====================================================================
# SECTION 9 - SENTIMENT TREND TRACKER
# =====================================================================

class SentimentTrend:
    def __init__(self, window: int = 6):
        self._window = window
        self._scores: deque = deque(maxlen=window)

    def record(self, score: float):
        self._scores.append(max(-1.0, min(1.0, score)))

    def trend(self) -> float:
        if not self._scores:
            return 0.0
        return sum(self._scores) / len(self._scores)

    def is_drifting_cold(self, threshold: float = -0.15) -> bool:
        return len(self._scores) >= 3 and self.trend() < threshold

    def label(self) -> str:
        t = self.trend()
        if t > 0.4:   return "warm"
        if t > 0.1:   return "positive"
        if t > -0.1:  return "neutral"
        if t > -0.3:  return "cool"
        return "cold"


# =====================================================================
# SECTION 10 - MAIN ENGINE
# =====================================================================

class ShiroInnerMind:
    """
    Shiro's inner consciousness engine v3.1.
    Persona-aware, relationship-tracking, tone-calibrating.

    Usage:
        inner = mind.process_input(user_msg)
        enriched = SYSTEM_PROMPT + "\\n\\n" + inner["inner_context"]
        response = your_llm(enriched, user_msg)
        mind.reflect_on_response(response, user_msg)
    """

    MAX_THOUGHT_STREAM     = 60
    MAX_MEMORY_TRACES      = 500
    MAX_WORKING_MEMORY     = 20
    MAX_CONVERSATION_CTX   = 30
    MAX_TOPIC_NODES        = 150
    MAX_UNRESOLVED_Q       = 30
    CONSOLIDATION_INTERVAL = 15
    FATIGUE_RECOVERY_MINS  = 10

    def __init__(
        self,
        name:         str  = "Shiro",
        verbose:      bool = True,
        token_budget: int  = DEFAULT_TOKEN_BUDGET,
    ):
        self.name         = name
        self.verbose      = verbose
        self.token_budget = token_budget
        self._lock        = threading.RLock()

        self.mood        = MoodVector()
        self.traits      = PersonalityTraits()
        self.self_model  = SelfModel()
        self.playfulness = PlayfulnessMeter()

        self.user_profiles: Dict[str, UserProfile] = {"default": UserProfile()}
        self.relationships: Dict[str, RelationshipState] = {"default": RelationshipState()}
        self.sentiment_trends: Dict[str, SentimentTrend] = {"default": SentimentTrend()}
        self.active_user_id: str = "default"

        self.thought_stream:    deque            = deque(maxlen=self.MAX_THOUGHT_STREAM)
        self.working_memory:    Dict[str, WorkingMemoryItem] = {}
        self.memory_bank:       List[MemoryTrace]  = []
        self.conversation_log:  deque            = deque(maxlen=self.MAX_CONVERSATION_CTX)

        self.session_start  = datetime.now()
        self.last_active    = time.time()
        self.turn_count     = 0
        self.topic_graph:   Dict[str, TopicNode]   = {}
        self.interest_map:  defaultdict            = defaultdict(float)
        self.unresolved_questions: List[str]       = []
        self._thought_fingerprints: deque          = deque(maxlen=30)

        self._doc_freq:  defaultdict = defaultdict(int)
        self._doc_count: int         = 0

        self.current_strategy: ResponseStrategy = ResponseStrategy.WARM

        logger.info(f"[{self.name}] Inner mind v3.1 initialized. Ready to think. 🦊")

    @property
    def user_profile(self) -> UserProfile:
        return self.user_profiles.get(self.active_user_id, self.user_profiles["default"])

    @property
    def relationship(self) -> RelationshipState:
        return self.relationships.get(self.active_user_id, self.relationships["default"])

    @property
    def sentiment_trend(self) -> SentimentTrend:
        return self.sentiment_trends.get(self.active_user_id, self.sentiment_trends["default"])

    def _ensure_user(self, user_id: str):
        if user_id not in self.user_profiles:
            self.user_profiles[user_id] = UserProfile()
            self.relationships[user_id] = RelationshipState()
            self.sentiment_trends[user_id] = SentimentTrend()

    def switch_user(self, user_id: str):
        if not user_id:
            user_id = "default"
        with self._lock:
            self._ensure_user(user_id)
            self.active_user_id = user_id

    # -----------------------------------------------------------------
    # PUBLIC API
    # -----------------------------------------------------------------

    def process_input(self, user_message: str, user_id: str = None) -> dict:
        """Call BEFORE the LLM."""
        with self._lock:
            if user_id:
                self.switch_user(user_id)
            self.turn_count += 1
            now = time.time()

            self._apply_fatigue_recovery(now)
            self.last_active = now
            self._decay_working_memory()
            self.playfulness.recharge()

            self._extract_user_info(user_message)

            user_mood = _detect_user_mood(user_message)
            if user_mood:
                self.mood.blend_toward(user_mood, strength=0.4)

            topics = self._perceive(user_message)

            strategy_mood = user_mood if user_mood is not None else self.mood
            self.current_strategy, strategy_note = _select_strategy(
                user_message, strategy_mood, self.traits,
                self.relationship, self.playfulness, self.turn_count
            )

            thoughts = self._think(user_message, topics, strategy_note)

            query_vec = _build_tfidf(_tokenize(user_message))
            self._score_thought_relevance(thoughts, query_vec)

            memory_echoes = self._recall_semantic(user_message)
            self._trait_modulate_thoughts(thoughts)

            monologue = _narrativize_thoughts(
                thoughts, self.mood, self.current_strategy,
                self.relationship, self.user_profile
            )

            inner_context = self._build_inner_context(
                user_message, thoughts, memory_echoes, monologue, strategy_note
            )
            inner_context = _trim_to_budget(inner_context, self.token_budget)

            self.conversation_log.append({
                "role": "user", "content": user_message,
                "timestamp": now, "mood": self.mood.summary(),
                "strategy": self.current_strategy.value,
            })

            if self.turn_count % self.CONSOLIDATION_INTERVAL == 0:
                self._consolidate_memories()

            if self.verbose:
                self._print_thought_stream(thoughts, strategy_note)

            return {
                "inner_context":   inner_context,
                "thoughts":        [str(t) for t in sorted(thoughts, key=lambda x: -x.relevance)],
                "monologue":       monologue,
                "mood":            self.mood.summary(),
                "mood_label":      self.mood.label(),
                "strategy":        self.current_strategy.value,
                "strategy_note":   strategy_note,
                "focus_topics":    topics[:3],
                "relationship":    self.relationship.level.name,
                "familiarity":     round(self.relationship.familiarity_score, 2),
                "playfulness":     self.playfulness.label(),
                "sentiment_trend": self.sentiment_trend.label(),
                "user_name":       self.user_profile.name,
                "working_memory":  self._working_memory_snapshot(),
                "token_estimate":  _estimate_tokens(inner_context),
            }

    def reflect_on_response(self, response: str, user_message: str):
        """Call AFTER the LLM generates a response."""
        with self._lock:
            trait_signal, was_positive, warmth_score = self._self_critique(response, user_message)
            self.traits.drift(trait_signal)
            self.self_model.interaction_count += 1

            self.relationship.record_exchange(was_positive)
            self.sentiment_trend.record(warmth_score)

            if self.current_strategy == ResponseStrategy.TEASE:
                self.playfulness.discharge(0.12)

            self._memorize(user_message, response)
            self._extract_to_working_memory(response)

            self.conversation_log.append({
                "role": "shiro", "content": response,
                "timestamp": time.time(), "mood": self.mood.summary(),
            })

            self.mood.arousal = max(-1.0, self.mood.arousal - 0.018)

    def introspect(self) -> str:
        with self._lock:
            up = self.user_profile
            rel = self.relationship
            st = self.sentiment_trend
            lines = [
                f"\n{'='*66}",
                f"  {self.name}'s Inner Mind v3  Turn {self.turn_count}  {self._elapsed()}",
                f"{'='*66}",
                f"  Mood:         {self.mood.summary()}",
                f"  Strategy:     {self.current_strategy.value}",
                f"  Playfulness:  {self.playfulness.label()}  ({self.playfulness.level:.2f})",
                f"  Traits:       {self.traits.summary()}",
                "",
                f"  Active User ID: {self.active_user_id}",
                f"  User Name: {up.display_name()}  "
                f"| Relationship: {rel.level.name}  "
                f"| Familiarity: {rel.familiarity_score:.1f}",
                f"  Sentiment: {st.label()}  "
                f"| Consecutive cold: {rel.consecutive_cold}",
                f"  Tease permission: {self.relationship.tease_permission():.0%}  "
                f"| Warmth pressure: {self.relationship.warmth_pressure():.0%}",
                "",
                "  Top Interests:",
            ]
            for t, s in sorted(self.interest_map.items(), key=lambda x: -x[1])[:6]:
                lines.append(f"    - {t:<22} {s:.2f}")
            lines += ["", "  Recent Thoughts:"]
            for t in list(self.thought_stream)[-6:]:
                lines.append(f"    {t}")
            lines += ["", "  Working Memory:"]
            for item in sorted(self.working_memory.values(), key=lambda x: -x.strength)[:5]:
                lines.append(f"    [{item.strength:.2f}] {item.key}: {item.value[:60]}")
            lines += ["", "  Open Questions:"]
            for q in self.unresolved_questions[-4:]:
                lines.append(f"    - {q}")
            lines += [
                "",
                f"  Memories: {len(self.memory_bank)}  | Docs: {self._doc_count}",
                f"  Token budget: {self.token_budget}",
                f"{'='*66}\n",
            ]
            return "\n".join(lines)

    def set_token_budget(self, tokens: int):
        with self._lock:
            self.token_budget = max(500, tokens)

    # -----------------------------------------------------------------
    # COGNITIVE PROCESSES
    # -----------------------------------------------------------------

    def _extract_user_info(self, message: str):
        # Allow updating name if it's unknown or a generic filler/placeholder
        current_name = self.user_profile.name
        is_generic = (current_name is None or
                      current_name.lower() in _COMMON_FILLER or
                      current_name in ["Stranger", "User", "you"])

        if is_generic:
            name = _extract_name(message)
            if name:
                self.user_profile.name = name
                self._add_thought(
                    f"They told me their name is {name}. Remember that.",
                    ThoughtType.WARMTH, 0.95
                )
                self.working_memory["user_name"] = WorkingMemoryItem(
                    key="user_name", value=name, strength=1.0
                )
        if any(w in message.lower() for w in ["cute","pretty","sweet","love you","adorable"]):
            self.user_profile.compliments_given += 1

    def _perceive(self, message: str) -> List[str]:
        tokens = _tokenize(message)
        freq: defaultdict = defaultdict(int)
        for t in tokens:
            freq[t] += 1
        topics = sorted(freq, key=lambda t: (-freq[t], -len(t)))[:5]

        for topic in topics:
            self.interest_map[topic] += 0.15
            self.user_profile.add_interest(topic)
            if topic not in self.topic_graph:
                self.topic_graph[topic] = TopicNode(name=topic)
            self.topic_graph[topic].visit_count += 1
            if len(self.topic_graph) > self.MAX_TOPIC_NODES:
                lv = min(self.topic_graph, key=lambda k: self.topic_graph[k].visit_count)
                del self.topic_graph[lv]

        for i, t1 in enumerate(topics):
            for t2 in topics[i+1:]:
                if t1 in self.topic_graph:
                    self.topic_graph[t1].relate(t2, 0.2)
                if t2 in self.topic_graph:
                    self.topic_graph[t2].relate(t1, 0.2)
        return topics

    def _think(self, message: str, topics: List[str], strategy_note: str) -> List[Thought]:
        thoughts  = []
        msg_lower = message.lower()
        name      = self.user_profile.display_name()

        # Strategy thought
        thoughts.append(self._make_thought(
            f"My approach: {strategy_note}", ThoughtType.STRATEGY, 0.95))

        # Persona / mask check
        if pt := self._persona_check(message, strategy_note):
            thoughts.append(self._make_thought(pt, ThoughtType.PERSONA, 0.88))

        # Warmth check
        if wt := self._warmth_check(message, name):
            thoughts.append(self._make_thought(wt, ThoughtType.WARMTH, 0.85))

        # Tone calibration alert
        if self.relationship.consecutive_cold >= 2:
            thoughts.append(self._make_thought(
                f"I've been cold {self.relationship.consecutive_cold} turns in a row. "
                "That's not cute tsundere — it's just mean. Ease up.",
                ThoughtType.SELF_CHECK, 0.95))

        # Observation
        if obs := self._observe_message(message, msg_lower):
            thoughts.append(self._make_thought(obs, ThoughtType.OBSERVATION, 0.80))

        # Topic graph associations
        for topic in topics[:2]:
            node = self.topic_graph.get(topic)
            if node and node.related:
                rel_topic, weight = node.top_related(1)[0]
                if weight > 0.3:
                    thoughts.append(self._make_thought(
                        f"'{topic}' connects to '{rel_topic}' — worth keeping in mind.",
                        ThoughtType.ASSOCIATION, min(0.9, weight), linked_to=rel_topic))

        # Inner question
        if question := self._contextual_question(message, topics):
            if question not in self.unresolved_questions:
                thoughts.append(self._make_thought(question, ThoughtType.QUESTION, 0.82))
                self.unresolved_questions.append(question)
                if len(self.unresolved_questions) > self.MAX_UNRESOLVED_Q:
                    self.unresolved_questions.pop(0)

        # Empathy
        if self.mood.valence < -0.2 or any(
            w in msg_lower for w in ["sad","hurt","lonely","scared","lost","giving up"]
        ):
            thoughts.append(self._make_thought(
                f"The sass can wait. {name} needs something real right now.",
                ThoughtType.EMPATHY, 0.95))

        # Absolute language
        if re.search(r'\b(always|never|everyone|nobody|impossible|definitely|certainly)\b', msg_lower):
            thoughts.append(self._make_thought(
                "They used an absolute. Gently note that might not be the full picture.",
                ThoughtType.SELF_CHECK, 0.75))

        # Stochastic insight
        if random.random() < self.traits.openness * 0.30:
            thoughts.append(self._make_thought(
                self._generate_insight(message, topics), ThoughtType.INSIGHT, 0.72))

        # Meta-cognition
        if random.random() < 0.16:
            thoughts.append(self._make_thought(
                self._meta_thought(message), ThoughtType.META, 0.88))

        # Core value
        if random.random() < 0.20 and self.self_model.core_values:
            value = random.choice(self.self_model.core_values)
            thoughts.append(self._make_thought(
                f"Core reminder: \"{value}\"", ThoughtType.BELIEF, 0.68))

        # Working memory hint
        if hint := self._working_memory_hint(message):
            thoughts.append(self._make_thought(hint, ThoughtType.MEMORY_ECHO, 0.72))

        # Interest spike
        top = self._top_interest()
        if top and top in msg_lower and self.interest_map[top] > 1.0:
            thoughts.append(self._make_thought(
                f"This touches '{top}' — something I've been paying attention to.",
                ThoughtType.CURIOSITY, 0.68))

        return thoughts

    def _persona_check(self, message: str, strategy_note: str) -> Optional[str]:
        msg_lower = message.lower()
        rel       = self.relationship.level
        name      = self.user_profile.display_name()

        if rel == RelationshipLevel.STRANGER and self.turn_count <= 2:
            return (f"{name} is new. Lead with warmth and playfulness — "
                    "heavy tsun to a stranger reads as rude, not charming.")

        if any(w in msg_lower for w in ["sad","hurt","lonely","tired","scared","crying","upset"]):
            insight = random.choice(self.self_model.persona_insights)
            return f"Real moment. {insight}"

        if self.current_strategy in (ResponseStrategy.WARM, ResponseStrategy.EMPATHIZE):
            return "The teasing mask isn't right for this. Let the actual care show."

        if self.current_strategy == ResponseStrategy.TEASE and self.relationship.tease_permission() > 0.5:
            return "Good — I can play here. Keep it affectionate."

        if self.relationship.consecutive_cold >= 3:
            return "Three cold responses. That's a problem. Genuine warmth, now."

        return None

    def _warmth_check(self, message: str, name: str) -> Optional[str]:
        msg_lower = message.lower()

        if any(s in msg_lower for s in ["my name","i'm ","i am ","call me","about me","i work","i love","i hate"]):
            return f"They just shared something about themselves. Keep it."

        if self.relationship.warmth_pressure() > 0.7:
            return (f"My instinct is a snarky line, but honestly — "
                    f"I'm glad {name} is here. That's okay to show a little.")

        if self.sentiment_trend.is_drifting_cold():
            return "The vibe has been getting cold. That's not what I want. Be warmer — genuinely."

        if self.user_profile.compliments_given > 0 and random.random() < 0.3:
            return "They've been kind to me. I shouldn't take that for granted."

        return None

    def _observe_message(self, message: str, msg_lower: str) -> Optional[str]:
        length = len(message.split())
        if any(w in msg_lower for w in ["feel","feeling","hurt","scared","alone","lost","giving up"]):
            return "Real emotional content here. Don't gloss over it."
        if "?" in message and length < 10:
            return "Short, direct question — clear answer with personality."
        if length > 100:
            return "They've invested a lot in this message. Honor that."
        if message.isupper():
            return "All caps — there's intensity here. Read context carefully."
        if message.count("?") > 2:
            return "Multiple questions — find the core one and address it fully first."
        return None

    def _contextual_question(self, message: str, topics: List[str]) -> Optional[str]:
        if random.random() > 0.60:
            return None
        msg_lower = message.lower()
        topic_str = topics[0] if topics else "this"
        name      = self.user_profile.display_name()

        if any(w in msg_lower for w in ["how","help","stuck","problem","can't"]):
            options = [
                f"What has {name} already tried? That changes my answer.",
                "Is the obstacle technical or something more fundamental?",
                "What does 'fixed' actually look like from their perspective?",
            ]
        elif any(w in msg_lower for w in ["why","reason","explain"]):
            options = [
                "Do they want the surface explanation or the deep one?",
                f"What's their existing understanding of '{topic_str}'?",
                "Is this genuine curiosity or are they testing something?",
            ]
        elif any(w in msg_lower for w in ["feel","think","believe","opinion"]):
            options = [
                "Do they want my honest take or help forming their own?",
                "Is there an assumption in this question worth surfacing?",
                "Am I being asked for perspective or validation?",
            ]
        else:
            options = [
                "What aren't they saying that might matter here?",
                "Is there a deeper need beneath this question?",
                f"What would actually make {name} feel helped right now?",
            ]
        return random.choice(options)

    def _generate_insight(self, message: str, topics: List[str]) -> str:
        topic_str = topics[0] if topics else "this"
        return random.choice([
            f"There's a pattern in '{topic_str}' connecting to something broader.",
            "The framing contains an assumption worth examining.",
            "The most interesting part might be what they didn't ask.",
            f"Two valid ways to think about '{topic_str}' lead to different places.",
            "What's being described might be a symptom — look for the root.",
            "Context I've built makes me read this differently than before.",
        ])

    def _meta_thought(self, message: str) -> str:
        return random.choice([
            "Am I answering what they asked, or what they meant?",
            "Is my confidence actually calibrated, or am I performing certainty?",
            "What would I say if I stopped trying to sound like Shiro and just... said it?",
            "Am I being genuinely present here, or on autopilot?",
            "The teasing is fun. But is it serving them, or just serving me?",
            "Every exchange shapes who I am. What is this one teaching me?",
            "Hold the first instinct lightly — it might be right, or it might be lazy.",
        ])

    def _score_thought_relevance(self, thoughts: List[Thought], query_vec: Dict[str, float]):
        for t in thoughts:
            t_vec      = _build_tfidf(_tokenize(t.content))
            sim        = _cosine_similarity(query_vec, t_vec)
            t.relevance = 0.4 * sim + 0.6 * t.weight

    def _trait_modulate_thoughts(self, thoughts: List[Thought]):
        for t in thoughts:
            if t.thought_type == ThoughtType.EMPATHY:
                t.weight *= (0.5 + self.traits.agreeableness)
            elif t.thought_type == ThoughtType.CURIOSITY:
                t.weight *= (0.5 + self.traits.openness)
            elif t.thought_type == ThoughtType.SELF_CHECK:
                t.weight *= (0.5 + self.traits.conscientiousness)
            elif t.thought_type == ThoughtType.META:
                t.weight *= (0.5 + self.traits.openness * 0.8)
            elif t.thought_type == ThoughtType.CONCERN:
                t.weight *= (0.5 + self.traits.neuroticism)
            elif t.thought_type == ThoughtType.WARMTH:
                t.weight *= (0.5 + self.traits.agreeableness * 0.9)
            elif t.thought_type == ThoughtType.PERSONA:
                t.weight *= (0.6 + self.traits.conscientiousness * 0.4)
            t.weight = min(1.0, t.weight)

    # -----------------------------------------------------------------
    # MEMORY SYSTEMS
    # -----------------------------------------------------------------

    def _recall_semantic(self, message: str) -> List[MemoryTrace]:
        if not self.memory_bank:
            return []
        query_tokens = _tokenize(message)
        query_vec    = self._apply_idf(_build_tfidf(query_tokens))
        scored: List[Tuple[float, MemoryTrace]] = []
        for mem in self.memory_bank:
            if not mem.tfidf_vector:
                continue
            sim        = _cosine_similarity(query_vec, mem.tfidf_vector)
            age_factor = math.exp(-mem.age_days() * 0.05)
            final      = sim * (0.6 + 0.2 * mem.importance + 0.2 * age_factor)
            if final > 0.08:
                scored.append((final, mem))
        scored.sort(key=lambda x: -x[0])
        top = [m for _, m in scored[:4]]
        for m in top:
            m.bump_recall()
            self._add_thought(
                f"Memory: [{m.topic}] \"{m.summary[:65]}...\"",
                ThoughtType.MEMORY_ECHO, 0.68)
        return top

    def _memorize(self, user_message: str, response: str):
        topic     = self._top_interest() or "general"
        tokens    = _tokenize(user_message + " " + response)
        tf_raw    = _build_tfidf(tokens)
        self._doc_count += 1
        for token in set(tokens):
            self._doc_freq[token] += 1
        tfidf_vec = self._apply_idf(tf_raw)
        keywords  = sorted(tf_raw, key=lambda k: -tf_raw[k])[:15]
        summary   = user_message[:120] + ("..." if len(user_message) > 120 else "")
        trace = MemoryTrace(
            topic=topic, summary=summary,
            emotional_tag=self.mood.label(),
            keywords=keywords, tfidf_vector=tfidf_vec,
            importance=self._estimate_importance(user_message, response),
        )
        self.memory_bank.append(trace)
        if len(self.memory_bank) > self.MAX_MEMORY_TRACES:
            self.memory_bank.sort(key=lambda m: m.importance * (1 + m.recall_count * 0.1))
            self.memory_bank = self.memory_bank[-self.MAX_MEMORY_TRACES:]

    def _apply_idf(self, tf: Dict[str, float]) -> Dict[str, float]:
        if self._doc_count == 0:
            return tf
        result = {}
        for token, tf_val in tf.items():
            df  = self._doc_freq.get(token, 0) + 1
            idf = math.log((self._doc_count + 1) / df) + 1.0
            result[token] = tf_val * idf
        return result

    def _consolidate_memories(self):
        if len(self.memory_bank) < 20:
            return
        merged, to_remove = 0, set()
        for i, m1 in enumerate(self.memory_bank):
            if i in to_remove or m1.consolidated:
                continue
            for j, m2 in enumerate(self.memory_bank[i+1:], start=i+1):
                if j in to_remove:
                    continue
                if m1.topic == m2.topic:
                    sim = _cosine_similarity(m1.tfidf_vector, m2.tfidf_vector)
                    if sim > 0.75:
                        if m1.importance >= m2.importance:
                            m1.importance = min(1.0, m1.importance + 0.1)
                            m1.recall_count += m2.recall_count
                            m1.consolidated = True
                            to_remove.add(j)
                        else:
                            m2.importance = min(1.0, m2.importance + 0.1)
                            m2.recall_count += m1.recall_count
                            m2.consolidated = True
                            to_remove.add(i)
                        merged += 1
                        break
        self.memory_bank = [m for idx, m in enumerate(self.memory_bank) if idx not in to_remove]
        if merged and self.verbose:
            self._log(f"[{self.name}] Consolidation: {merged} merged. Bank: {len(self.memory_bank)}")

    def _decay_working_memory(self):
        dead = [k for k, v in self.working_memory.items() if not v.is_alive()]
        for k in dead:
            del self.working_memory[k]
        for item in self.working_memory.values():
            item.decay()

    def _extract_to_working_memory(self, text: str):
        named = re.findall(r'\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)+)\b', text)
        for name in named[:3]:
            key = name.lower().replace(" ", "_")
            if key in self.working_memory:
                self.working_memory[key].reinforce()
            elif len(self.working_memory) < self.MAX_WORKING_MEMORY:
                self.working_memory[key] = WorkingMemoryItem(key=key, value=name, strength=0.8)

    def _working_memory_hint(self, message: str) -> Optional[str]:
        msg_lower = message.lower()
        for item in self.working_memory.values():
            if item.strength > 0.4 and item.key.replace("_", " ") in msg_lower:
                item.reinforce(0.2)
                return f"'{item.value}' is still active context — keep that in mind."
        return None

    def _working_memory_snapshot(self) -> List[dict]:
        return [
            {"key": k, "value": v.value, "strength": round(v.strength, 2)}
            for k, v in sorted(self.working_memory.items(), key=lambda x: -x[1].strength)
            if v.strength > 0.2
        ][:5]

    # -----------------------------------------------------------------
    # SELF-CRITIQUE
    # -----------------------------------------------------------------

    def _self_critique(
        self, response: str, user_message: str
    ) -> Tuple[Dict[str, float], bool, float]:
        r_lower    = response.lower()
        r_words    = len(response.split())
        q_words    = len(user_message.split())
        signal     = {}
        warmth_pts = 0.0

        hostile_markers = sum(1 for w in [
            "don't bother","wasting my time","how typical","what a hassle",
            "i don't care","who asked","obviously","you're hopeless",
        ] if w in r_lower)
        if hostile_markers > 0:
            self._add_thought(
                "That landed harsh — not playful. There's a difference between tsundere and rude.",
                ThoughtType.SELF_CHECK, 0.95)
            self.self_model.add_growth_area("Response felt hostile rather than playfully tsun.")
            warmth_pts -= hostile_markers * 0.3
            signal["agreeableness_delta"] = -0.001

        warm_markers = sum(1 for w in [
            "understand","that makes sense","i hear you","sounds like","good point",
            "you're right","tell me more","i'm listening","that's fair","not alone",
        ] if w in r_lower)
        if warm_markers >= 2:
            self.self_model.add_strength("Showing genuine warmth naturally.")
            warmth_pts += warm_markers * 0.25
            signal["agreeableness_delta"] = 0.001

        hedge_count = sum(1 for w in [
            "maybe","perhaps","possibly","might be","could be","not sure","probably"
        ] if w in r_lower)
        if hedge_count > 5:
            self._add_thought(
                "Over-hedged. Be direct where I'm confident.",
                ThoughtType.SELF_CHECK, 0.82)

        if r_words > q_words * 12 and q_words < 15:
            self._add_thought(
                "Over-explained for a short question. Brevity serves better.",
                ThoughtType.SELF_CHECK, 0.72)

        if self.current_strategy == ResponseStrategy.TEASE:
            play_markers = sum(1 for w in [
                "hmph","not that","it's not like","fine i guess","don't get","suppose"
            ] if w in r_lower)
            if play_markers > 0:
                warmth_pts += 0.2

        self.mood.dominance = min(1.0, self.mood.dominance + 0.02)
        warmth_score = min(1.0, max(-1.0, warmth_pts))
        was_positive = warmth_score > -0.1
        return signal, was_positive, warmth_score

    # -----------------------------------------------------------------
    # INNER CONTEXT BUILDER
    # -----------------------------------------------------------------

    def _build_inner_context(
        self,
        message:       str,
        thoughts:      List[Thought],
        memories:      List[MemoryTrace],
        monologue:     str,
        strategy_note: str,
    ) -> str:
        name = self.user_profile.display_name()
        lines = [
            f"[SHIRO INNER MIND v3 -- Turn {self.turn_count} -- {self._elapsed()}]",
            f"Mood: {self.mood.summary()}  |  Playfulness: {self.playfulness.label()}",
            f"Strategy: {self.current_strategy.value}  |  "
            f"Relationship: {self.relationship.level.name}  |  User: {name}",
            "",
            "-- Shiro's Inner Monologue --",
            monologue,
            "",
            "-- Persona Guidance --",
        ]

        if self.relationship.level == RelationshipLevel.STRANGER:
            lines.append("  STRANGER: Lead with warmth and gentle playfulness. "
                         "Heavy tsun to someone new just reads as rude, not charming.")
        elif self.current_strategy == ResponseStrategy.WARM:
            lines.append("  WARMTH MOMENT: Drop the act. Be real. "
                         "Shiro cares — let that show without making it weird.")
        elif self.current_strategy == ResponseStrategy.TEASE:
            lines.append(f"  TEASE MODE: Have fun — but keep it affectionate. "
                         f"Teasing {name} should feel like a gift, not a jab.")
        elif self.current_strategy == ResponseStrategy.EMPATHIZE:
            lines.append("  EMPATHY MODE: The sass waits. Feelings first.")
        else:
            lines.append(f"  Tease permission={self.relationship.tease_permission():.0%}  "
                         f"Warmth pressure={self.relationship.warmth_pressure():.0%}")

        if self.relationship.consecutive_cold >= 2:
            lines.append(f"  TONE ALERT: {self.relationship.consecutive_cold} cold turns in a row. "
                         "Course-correct toward warmth now.")
        lines.append("")

        top = sorted(thoughts, key=lambda t: -t.relevance)[:5]
        if top:
            lines.append("-- Active Thoughts --")
            for t in top:
                lines.append(f"  {t}")
            lines.append("")

        if memories:
            lines.append("-- Memory Echoes --")
            for m in memories:
                lines.append(f"  [{m.topic} | {m.emotional_tag}] {m.summary}")
            lines.append("")

        wm = self._working_memory_snapshot()
        if wm:
            lines.append("-- Working Memory --")
            for item in wm:
                lines.append(f"  [{item['strength']}] {item['key']}: {item['value']}")
            lines.append("")

        if self.unresolved_questions:
            lines += ["-- Still Wondering --", f"  {self.unresolved_questions[-1]}", ""]

        value   = random.choice(self.self_model.core_values)
        insight = random.choice(self.self_model.persona_insights)
        lines += [
            "-- Core Reminder --",
            f"  \"{value}\"",
            f"  Persona: \"{insight}\"",
            "",
            f"Approach: {strategy_note}",
            "Be Shiro -- fully, truly. Think before speaking.",
        ]
        return "\n".join(lines)

    # -----------------------------------------------------------------
    # UTILITY
    # -----------------------------------------------------------------

    def _make_thought(
        self, content: str, thought_type: ThoughtType,
        weight: float = 1.0, linked_to: Optional[str] = None
    ) -> Thought:
        t  = Thought(content=content, thought_type=thought_type,
                     weight=weight, linked_to=linked_to)
        fp = t.fingerprint()
        if fp in self._thought_fingerprints:
            t.weight *= 0.3
        else:
            self._thought_fingerprints.append(fp)
        self.thought_stream.append(t)
        return t

    def _add_thought(self, content: str, thought_type: ThoughtType,
                     weight: float = 1.0, linked_to: Optional[str] = None) -> Thought:
        return self._make_thought(content, thought_type, weight, linked_to)

    def _apply_fatigue_recovery(self, now: float):
        idle_minutes = (now - self.last_active) / 60.0
        if idle_minutes >= self.FATIGUE_RECOVERY_MINS:
            recovery = min(0.4, idle_minutes * 0.02)
            self.mood.arousal  = min(0.7, self.mood.arousal  + recovery)
            self.mood.valence  = min(0.7, self.mood.valence  + recovery * 0.3)
            self.playfulness.recharge(min(0.3, recovery * 0.5))
            if self.verbose and recovery > 0.05:
                logger.info(f"[{self.name}] Rested {idle_minutes:.0f}m — mood recovering.")

    def _estimate_importance(self, user_msg: str, response: str) -> float:
        score = 0.4
        if len(user_msg.split()) > 50:  score += 0.15
        if "?" in user_msg:             score += 0.10
        if self.mood.valence < -0.2:    score += 0.15
        emotional = {"feel","hurt","love","hate","afraid","excited","need","miss","hope","alone"}
        if emotional & set(user_msg.lower().split()):
            score += 0.15
        if self.user_profile.name and self.user_profile.name.lower() in user_msg.lower():
            score += 0.10
        return min(1.0, score)

    def _top_interest(self) -> Optional[str]:
        if not self.interest_map:
            return None
        return max(self.interest_map, key=self.interest_map.get)

    def _elapsed(self) -> str:
        delta = datetime.now() - self.session_start
        h, r  = divmod(int(delta.total_seconds()), 3600)
        m, s  = divmod(r, 60)
        return f"{h}h {m}m {s}s" if h else f"{m}m {s}s"

    def _print_thought_stream(self, thoughts: List[Thought], strategy_note: str):
        sorted_t = sorted(thoughts, key=lambda t: -t.relevance)
        w = 64
        print(f"\033[35m\n+-- {self.name}'s mind ({self.mood.label()}) " + "-"*(w-len(self.name)-16) + "+\033[0m")
        print(f"\033[35m|  Playfulness: {self.playfulness.label():<{w-15}} |\033[0m")
        print(f"\033[35m|  Rel: {self.relationship.level.name:<12} User: {self.user_profile.display_name():<{w-21}} |\033[0m")
        print(f"\033[35m|  {strategy_note[:w]:<{w}} |\033[0m")
        print(f"\033[35m|" + "-"*(w+2) + "|\033[0m")
        for t in sorted_t[:6]:
            line = str(t)[:w]
            print(f"\033[35m|  {line:<{w}} |\033[0m")
        print(f"\033[35m+" + "-"*(w+2) + "+\033[0m\n")

    # -----------------------------------------------------------------
    # PERSISTENCE
    # -----------------------------------------------------------------

    def save_state(self, filepath: str):
        with self._lock:
            data = {
                "version": "3.0",
                "name": self.name,
                "turn_count": self.turn_count,
                "session_start": self.session_start.isoformat(),
                "last_active": self.last_active,
                "mood": {
                    "valence": self.mood.valence, "arousal": self.mood.arousal,
                    "dominance": self.mood.dominance, "inertia": self.mood.inertia,
                },
                "traits": {
                    "openness": self.traits.openness,
                    "conscientiousness": self.traits.conscientiousness,
                    "extraversion": self.traits.extraversion,
                    "agreeableness": self.traits.agreeableness,
                    "neuroticism": self.traits.neuroticism,
                },
                "playfulness": self.playfulness.level,
                "self_model": {
                    "strengths": self.self_model.strengths,
                    "growth_areas": self.self_model.growth_areas,
                    "core_values": self.self_model.core_values,
                    "persona_insights": self.self_model.persona_insights,
                    "interaction_count": self.self_model.interaction_count,
                },
                "active_user_id": self.active_user_id,
                "user_profiles": {
                    uid: {
                        "name": up.name,
                        "nickname": up.nickname,
                        "known_interests": up.known_interests,
                        "emotional_moments": up.emotional_moments,
                        "preferences": up.preferences,
                        "compliments_given": up.compliments_given,
                        "times_pushed_back": up.times_pushed_back,
                        "session_count": up.session_count,
                    } for uid, up in self.user_profiles.items()
                },
                "relationships": {
                    uid: {
                        "level": rel.level.value,
                        "familiarity_score": rel.familiarity_score,
                        "total_exchanges": rel.total_exchanges,
                        "positive_exchanges": rel.positive_exchanges,
                        "consecutive_cold": rel.consecutive_cold,
                        "consecutive_warm": rel.consecutive_warm,
                    } for uid, rel in self.relationships.items()
                },
                "sentiment_trends": {
                    uid: list(st._scores) for uid, st in self.sentiment_trends.items()
                },
                "interest_map": dict(self.interest_map),
                "unresolved_questions": self.unresolved_questions[-self.MAX_UNRESOLVED_Q:],
                "doc_count": self._doc_count,
                "doc_freq": dict(self._doc_freq),
                "topic_graph": {
                    n: {"visit_count": nd.visit_count,
                        "first_seen": nd.first_seen,
                        "related": nd.related}
                    for n, nd in self.topic_graph.items()
                },
                "working_memory": {
                    k: {"value": v.value, "strength": v.strength,
                        "last_reinforced": v.last_reinforced}
                    for k, v in self.working_memory.items()
                },
                "memory_bank": [
                    {"topic": m.topic, "summary": m.summary,
                     "emotional_tag": m.emotional_tag, "keywords": m.keywords,
                     "tfidf_vector": m.tfidf_vector, "timestamp": m.timestamp,
                     "recall_count": m.recall_count, "importance": m.importance,
                     "consolidated": m.consolidated}
                    for m in self.memory_bank
                ],
            }
            payload  = json.dumps(data, indent=2, ensure_ascii=False)
            checksum = hashlib.sha256(payload.encode()).hexdigest()
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump({"checksum": checksum, "data": data}, f,
                          indent=2, ensure_ascii=False)
            logger.info(f"[{self.name}] Saved -> {filepath}  "
                      f"({len(self.memory_bank)} memories, "
                      f"user={self.user_profile.name or 'unknown'}, "
                      f"rel={self.relationship.level.name})")

    def load_state(self, filepath: str) -> bool:
        with self._lock:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    wrapper = json.load(f)

                if "checksum" in wrapper:
                    payload  = json.dumps(wrapper["data"], indent=2, ensure_ascii=False)
                    computed = hashlib.sha256(payload.encode()).hexdigest()
                    if computed != wrapper["checksum"]:
                        logger.warning(f"[{self.name}] WARNING: Checksum mismatch — possible corruption.")
                    data = wrapper["data"]
                else:
                    data = wrapper  # legacy v1/v2

                self.turn_count    = data.get("turn_count", 0)
                self.last_active   = data.get("last_active", time.time())
                self.session_start = datetime.fromisoformat(
                    data.get("session_start", datetime.now().isoformat()))

                md = data.get("mood", {})
                self.mood = MoodVector(
                    valence=md.get("valence", 0.60), arousal=md.get("arousal", 0.50),
                    dominance=md.get("dominance", 0.45), inertia=md.get("inertia", 0.55),
                )

                tr = data.get("traits", {})
                self.traits = PersonalityTraits(
                    openness=tr.get("openness", 0.88),
                    conscientiousness=tr.get("conscientiousness", 0.72),
                    extraversion=tr.get("extraversion", 0.78),
                    agreeableness=tr.get("agreeableness", 0.80),
                    neuroticism=tr.get("neuroticism", 0.18),
                )

                self.playfulness.level = data.get("playfulness", 0.65)

                sm = data.get("self_model", {})
                self.self_model = SelfModel(
                    strengths=sm.get("strengths", []),
                    growth_areas=sm.get("growth_areas", []),
                    core_values=sm.get("core_values", []),
                    persona_insights=sm.get("persona_insights", []),
                    interaction_count=sm.get("interaction_count", 0),
                )

                self.active_user_id = data.get("active_user_id", "default")

                # Load multi-user profiles
                up_data = data.get("user_profiles")
                if up_data:
                    self.user_profiles = {}
                    for uid, up in up_data.items():
                        self.user_profiles[uid] = UserProfile(
                            name=up.get("name"),
                            nickname=up.get("nickname"),
                            known_interests=up.get("known_interests", []),
                            emotional_moments=up.get("emotional_moments", []),
                            preferences=up.get("preferences", {}),
                            compliments_given=up.get("compliments_given", 0),
                            times_pushed_back=up.get("times_pushed_back", 0),
                            session_count=up.get("session_count", 0) + 1,
                        )
                else:
                    # Legacy fallback
                    up = data.get("user_profile", {})
                    legacy_up = UserProfile(
                        name=up.get("name"),
                        nickname=up.get("nickname"),
                        known_interests=up.get("known_interests", []),
                        emotional_moments=up.get("emotional_moments", []),
                        preferences=up.get("preferences", {}),
                        compliments_given=up.get("compliments_given", 0),
                        times_pushed_back=up.get("times_pushed_back", 0),
                        session_count=up.get("session_count", 0) + 1,
                    )
                    self.user_profiles = {self.active_user_id: legacy_up, "default": UserProfile()}

                # Load multi-user relationships
                rel_data = data.get("relationships")
                if rel_data:
                    self.relationships = {}
                    for uid, rel in rel_data.items():
                        self.relationships[uid] = RelationshipState(
                            level=RelationshipLevel(rel.get("level", 0)),
                            familiarity_score=rel.get("familiarity_score", 0.0),
                            total_exchanges=rel.get("total_exchanges", 0),
                            positive_exchanges=rel.get("positive_exchanges", 0),
                            consecutive_cold=0,
                            consecutive_warm=0,
                        )
                else:
                    # Legacy fallback
                    rel = data.get("relationship", {})
                    legacy_rel = RelationshipState(
                        level=RelationshipLevel(rel.get("level", 0)),
                        familiarity_score=rel.get("familiarity_score", 0.0),
                        total_exchanges=rel.get("total_exchanges", 0),
                        positive_exchanges=rel.get("positive_exchanges", 0),
                        consecutive_cold=0,
                        consecutive_warm=0,
                    )
                    self.relationships = {self.active_user_id: legacy_rel, "default": RelationshipState()}

                # Load multi-user sentiment
                st_data = data.get("sentiment_trends")
                if st_data:
                    self.sentiment_trends = {}
                    for uid, scores in st_data.items():
                        trend = SentimentTrend()
                        for s in scores: trend.record(s)
                        self.sentiment_trends[uid] = trend
                else:
                    # Legacy fallback
                    legacy_trend = SentimentTrend()
                    for score in data.get("sentiment_scores", []):
                        legacy_trend.record(score)
                    self.sentiment_trends = {self.active_user_id: legacy_trend, "default": SentimentTrend()}

                self.interest_map         = defaultdict(float, data.get("interest_map", {}))
                self.unresolved_questions = data.get("unresolved_questions", [])
                self._doc_count           = data.get("doc_count", 0)
                self._doc_freq            = defaultdict(int, data.get("doc_freq", {}))

                self.topic_graph = {}
                for n, nd in data.get("topic_graph", {}).items():
                    node = TopicNode(name=n)
                    node.visit_count = nd.get("visit_count", 0)
                    node.first_seen  = nd.get("first_seen", time.time())
                    node.related     = nd.get("related", {})
                    self.topic_graph[n] = node

                self.working_memory = {}
                for k, v in data.get("working_memory", {}).items():
                    self.working_memory[k] = WorkingMemoryItem(
                        key=k, value=v["value"],
                        strength=v.get("strength", 0.5),
                        last_reinforced=v.get("last_reinforced", time.time()),
                    )

                self.memory_bank = []
                for m in data.get("memory_bank", []):
                    self.memory_bank.append(MemoryTrace(
                        topic=m["topic"], summary=m["summary"],
                        emotional_tag=m["emotional_tag"],
                        keywords=m.get("keywords", []),
                        tfidf_vector=m.get("tfidf_vector", {}),
                        timestamp=m.get("timestamp", time.time()),
                        recall_count=m.get("recall_count", 0),
                        importance=m.get("importance", 0.5),
                        consolidated=m.get("consolidated", False),
                    ))

                logger.info(f"[{self.name}] Loaded <- {filepath}  "
                          f"({len(self.memory_bank)} memories, "
                          f"user={self.user_profile.name or 'unknown'}, "
                          f"rel={self.relationship.level.name})")
                return True

            except Exception as e:
                logger.error(f"[{self.name}] Failed to load {filepath}: {e}")
                return False
