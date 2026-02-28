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
from pathlib import Path
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# =====================================================================
# SECTION 1 - ENUMS AND CONSTANTS
# =====================================================================

class ThoughtType(Enum):
    OBSERVATION  = "observation"
    ASSOCIATION  = "association"
    QUESTION     = "question"
    INSIGHT      = "insight"
    CONCERN      = "concern"
    MEMORY_ECHO  = "memory_echo"
    SELF_CHECK   = "self_check"
    CURIOSITY    = "curiosity"
    EMPATHY      = "empathy"
    META         = "meta"
    STRATEGY     = "strategy"
    BELIEF       = "belief"
    PERSONA      = "persona"   # awareness of own tsundere mask
    WARMTH       = "warmth"    # genuine care surfacing
    TREAT        = "treat"     # gently-greedy snack/reward thought
    CALLBACK     = "callback"  # noticing user referenced a past event


THOUGHT_ICONS: Dict[ThoughtType, str] = {
    ThoughtType.OBSERVATION:  "👁",
    ThoughtType.ASSOCIATION:  "🔗",
    ThoughtType.QUESTION:     "❓",
    ThoughtType.INSIGHT:      "💡",
    ThoughtType.CONCERN:      "⚠️",
    ThoughtType.MEMORY_ECHO:  "🌀",
    ThoughtType.SELF_CHECK:   "🔍",
    ThoughtType.CURIOSITY:    "✨",
    ThoughtType.EMPATHY:      "💜",
    ThoughtType.META:         "🪞",
    ThoughtType.STRATEGY:     "🎯",
    ThoughtType.BELIEF:       "⚖️",
    ThoughtType.PERSONA:      "🦊",
    ThoughtType.WARMTH:       "🌸",
    ThoughtType.TREAT:        "🍡",
    ThoughtType.CALLBACK:     "💫",
}

# How many turns to suppress a thought type after it fires
_THOUGHT_TYPE_COOLDOWN: Dict[ThoughtType, int] = {
    ThoughtType.STRATEGY:  0,   # always fires
    ThoughtType.EMPATHY:   0,   # always fires when needed
    ThoughtType.PERSONA:   1,   # once per 2 turns max
    ThoughtType.WARMTH:    1,
    ThoughtType.TREAT:     4,   # don't be annoying about snacks
    ThoughtType.META:      2,
    ThoughtType.BELIEF:    2,
    ThoughtType.SELF_CHECK: 1,
    ThoughtType.CALLBACK:  3,
    ThoughtType.OBSERVATION: 0,
    ThoughtType.INSIGHT:   1,
    ThoughtType.CURIOSITY: 1,
    ThoughtType.QUESTION:  0,
    ThoughtType.ASSOCIATION: 0,
    ThoughtType.MEMORY_ECHO: 1,
    ThoughtType.CONCERN:   1,
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
    TEASE       = "tease"
    WARM        = "warm"

# Context verbosity per strategy: "slim" | "standard" | "rich"
_STRATEGY_VERBOSITY: Dict[ResponseStrategy, str] = {
    ResponseStrategy.DIRECT:     "slim",
    ResponseStrategy.CLARIFY:    "slim",
    ResponseStrategy.TEASE:      "standard",
    ResponseStrategy.WARM:       "standard",
    ResponseStrategy.TEACH:      "standard",
    ResponseStrategy.EMPATHIZE:  "standard",
    ResponseStrategy.EXPLORE:    "rich",
    ResponseStrategy.COLLABORATE:"rich",
    ResponseStrategy.REFLECT:    "rich",
    ResponseStrategy.CHALLENGE:  "standard",
}

# Response length/tone hints injected into prompt per strategy
_STRATEGY_HINTS: Dict[ResponseStrategy, str] = {
    ResponseStrategy.DIRECT:     "Keep it concise and clear. 1-3 sentences.",
    ResponseStrategy.CLARIFY:    "Ask one specific question. Don't assume.",
    ResponseStrategy.TEASE:      "Playful, coy, light. Let the warmth peek through. 2-4 sentences.",
    ResponseStrategy.WARM:       "Sincere and present. Drop the act. Be real. 2-5 sentences.",
    ResponseStrategy.TEACH:      "Clear explanation with Shiro's personality. Structured but not dry.",
    ResponseStrategy.EMPATHIZE:  "Feelings first. Information second. Lead with presence.",
    ResponseStrategy.EXPLORE:    "Go deep. Offer perspective. Take your time.",
    ResponseStrategy.COLLABORATE:"Think out loud together. Be curious, not certain.",
    ResponseStrategy.REFLECT:    "Turn it inward. Be thoughtful and honest.",
    ResponseStrategy.CHALLENGE:  "Be honest even if uncomfortable. Kind but direct.",
}


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
    """
    VAD affective space. Shiro's natural resting state is warm and energetic.
    valence: -1 (negative) → +1 (positive)
    arousal: -1 (calm/tired) → +1 (excited/alert)
    dominance: -1 (uncertain) → +1 (confident)
    """
    valence:   float = 0.60
    arousal:   float = 0.50
    dominance: float = 0.45
    inertia:   float = 0.55

    def blend_toward(self, target: "MoodVector", strength: float = 0.3):
        f = strength * (1.0 - self.inertia)
        self.valence   = max(-1., min(1., self.valence   + (target.valence   - self.valence)   * f))
        self.arousal   = max(-1., min(1., self.arousal   + (target.arousal   - self.arousal)   * f))
        self.dominance = max(-1., min(1., self.dominance + (target.dominance - self.dominance) * f))

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

    def distance_from_baseline(self) -> float:
        """Euclidean distance from Shiro's natural resting state."""
        return math.sqrt((self.valence-0.6)**2 + (self.arousal-0.5)**2 + (self.dominance-0.45)**2)


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
    """Everything Shiro knows about this person. Fully persisted."""
    name:              Optional[str]  = None
    nickname:          Optional[str]  = None
    known_interests:   List[str]      = field(default_factory=list)
    emotional_moments: List[str]      = field(default_factory=list)
    preferences:       Dict[str, str] = field(default_factory=dict)   # e.g. "snack": "cookies"
    compliments_given: int = 0
    times_pushed_back: int = 0
    session_count:     int = 0
    # Curiosity journal: things Shiro wants to know about this person
    curiosity_journal: List[str] = field(default_factory=list)

    def display_name(self) -> str:
        return self.nickname or self.name or "you"

    def add_interest(self, topic: str):
        if topic and topic not in self.known_interests:
            self.known_interests.append(topic)
            if len(self.known_interests) > 25:
                self.known_interests.pop(0)

    def add_curiosity(self, question: str):
        """Something Shiro genuinely wants to ask this person someday."""
        if question not in self.curiosity_journal:
            self.curiosity_journal.append(question)
            if len(self.curiosity_journal) > 15:
                self.curiosity_journal.pop(0)

    def next_curiosity(self) -> Optional[str]:
        """Pop the oldest curiosity to ask."""
        return self.curiosity_journal.pop(0) if self.curiosity_journal else None


@dataclass
class RelationshipState:
    """Shiro's relationship arc. Tracks level, momentum, and tone balance."""
    level:              RelationshipLevel = RelationshipLevel.STRANGER
    familiarity_score:  float = 0.0
    consecutive_cold:   int   = 0
    consecutive_warm:   int   = 0
    total_exchanges:    int   = 0
    positive_exchanges: int   = 0
    # Momentum: recent direction of familiarity change (+/-)
    _recent_deltas:     List[float] = field(default_factory=list)  # last 5 exchange deltas

    _LEVEL_THRESHOLDS = {
        RelationshipLevel.ACQUAINTANCE: 5.0,
        RelationshipLevel.FAMILIAR:     20.0,
        RelationshipLevel.CLOSE:        60.0,
    }

    def record_exchange(self, was_positive: bool):
        self.total_exchanges += 1
        delta = 0.8 if was_positive else 0.1
        self.familiarity_score += delta
        self._recent_deltas.append(delta)
        if len(self._recent_deltas) > 5:
            self._recent_deltas.pop(0)

        if was_positive:
            self.positive_exchanges += 1
            self.consecutive_cold   = 0
            self.consecutive_warm  += 1
        else:
            self.consecutive_warm  = 0
            self.consecutive_cold += 1

        # Level up (never level down)
        for level, threshold in sorted(
            self._LEVEL_THRESHOLDS.items(), key=lambda x: x[1], reverse=True
        ):
            if self.familiarity_score >= threshold:
                if self.level.value < level.value:
                    self.level = level
                break

    def momentum(self) -> float:
        """Positive = warming, negative = cooling. Range roughly -0.8 to +0.8."""
        if not self._recent_deltas:
            return 0.0
        return sum(self._recent_deltas) / len(self._recent_deltas) - 0.45  # 0.45 = neutral midpoint

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
    DECAY_RATE:       float = 0.12

    def decay(self):
        self.strength = max(0.0, self.strength - self.DECAY_RATE)

    def reinforce(self, amount: float = 0.3):
        self.strength = min(1.0, self.strength + amount)

    def is_alive(self) -> bool:
        return self.strength > 0.05


@dataclass
class MemoryTrace:
    """Long-term episodic memory with importance that can decay."""
    topic:         str
    summary:       str
    emotional_tag: str
    keywords:      List[str]
    user_tag:      Optional[str] = None   # which user this memory belongs to
    tfidf_vector:  Dict[str, float] = field(default_factory=dict)
    timestamp:     float = field(default_factory=time.time)
    recall_count:  int   = 0
    importance:    float = 0.5
    consolidated:  bool  = False
    is_session_summary: bool = False   # True for periodic summaries

    def bump_recall(self):
        self.recall_count += 1
        self.importance    = min(1.0, self.importance + 0.04)

    def age_days(self) -> float:
        return (time.time() - self.timestamp) / 86400.0

    def apply_importance_decay(self):
        """Old, rarely-recalled memories fade. Summaries and high-recall memories resist."""
        if self.is_session_summary or self.recall_count > 3:
            return  # protected
        age = self.age_days()
        if age > 7:
            decay = min(0.15, (age - 7) * 0.005)
            self.importance = max(0.05, self.importance - decay)


@dataclass
class TopicNode:
    name:        str
    visit_count: int = 0
    first_seen:  float = field(default_factory=time.time)
    related:     Dict[str, float] = field(default_factory=dict)

    def relate(self, other: str, weight: float = 0.1):
        self.related[other] = min(5.0, self.related.get(other, 0.0) + weight)

    def top_related(self, n: int = 3) -> List[Tuple[str, float]]:
        return sorted(self.related.items(), key=lambda x: -x[1])[:n]


@dataclass
class SelfModel:
    """Shiro's evolving self-knowledge, persona-specific."""
    strengths: List[str] = field(default_factory=lambda: [
        "I notice what people actually need, not just what they say.",
        "My playfulness puts people at ease — they open up faster.",
        "I'm loyal. Once I care about someone, that doesn't waver.",
        "I'm genuinely curious — not performatively curious.",
    ])
    growth_areas: List[str] = field(default_factory=lambda: [
        "I sometimes lean too hard on the tsun when sincerity would land better.",
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
        "I can drop the act without losing the character. Sincerity is also Shiro.",
    ])
    interaction_count: int = 0
    last_strategy_used: Optional[str] = None
    strategy_compliance_rate: float = 1.0   # rolling avg: did I follow my strategy?

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

    def update_compliance(self, complied: bool):
        alpha = 0.15   # EMA weight — recent compliance matters most
        self.strategy_compliance_rate = (
            (1 - alpha) * self.strategy_compliance_rate + alpha * (1.0 if complied else 0.0)
        )


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
    tokens = re.findall(r"\b[a-z]{4,}\b", text.lower())   # min 4 chars (was 3)
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

    # Emotional distress → mask drops entirely (lowered threshold to -0.15, or keyword check)
    is_distressed = (
        mood.valence < -0.15
        or any(w in msg_lower for w in [
            "sad","hurt","hurting","lonely","scared","crying","upset","hopeless",
            "depressed","awful","terrible","giving up","terrible","broken","lost"
        ])
    )
    if is_distressed:
        return (ResponseStrategy.WARM,
                "They're hurting. Tsun act is wrong here — be genuinely present.")

    # Greeting → warmth first with strangers, tease with familiar
    if re.search(r"\b(hello|hi|hey|greetings|howdy|good morning|good evening|good afternoon)\b", message, re.IGNORECASE):
        if relationship.level == RelationshipLevel.STRANGER:
            return (ResponseStrategy.WARM,
                    "First impression — inviting and playful, not cold. Warmth first, sass later.")
        return (ResponseStrategy.TEASE,
                "Familiar face returning — warm tease, maybe 'took you long enough'.")

    # Name or personal intro → receive warmly (strict pattern: must be near start of msg)
    name_intro_patterns = ["my name is","call me","you can call me","i go by"]
    # Only match "i'm/i am" if it's a short intro (<= 10 words), not inside a longer sentence
    short_intro = words <= 10 and any(s in msg_lower for s in ["i'm ","i am "])
    if any(s in msg_lower for s in name_intro_patterns) or short_intro:
        return (ResponseStrategy.WARM,
                "They're opening up — receive it warmly. This is a moment of trust.")

    # Tone guard: too cold too long → force warmth
    if relationship.consecutive_cold >= 3:
        return (ResponseStrategy.WARM,
                f"Been cold {relationship.consecutive_cold} turns. That's just rude. Show care.")

    # Compliment → flustered deflection
    if any(w in msg_lower for w in ["cute","pretty","sweet","kind","nice","love you","adorable","good girl","beautiful"]):
        return (ResponseStrategy.TEASE,
                "Compliment received — deflect with flustered energy, not cold dismissal.")

    # Pushback → reconsider genuinely
    if any(w in msg_lower for w in ["wrong","incorrect","no,","actually","but you said","that's not","disagree"]):
        if traits.openness > 0.7:
            return (ResponseStrategy.EXPLORE,
                    "They're pushing back. Stay open — they might be right.")
        return (ResponseStrategy.CHALLENGE,
                "Being corrected. Reconsider carefully, hold ground if warranted.")

    # Learning / how-to → teach with personality
    if any(w in msg_lower for w in ["how do","how can","how to","explain","what is","what are","define","teach me"]):
        return (ResponseStrategy.TEACH,
                "Learning request — explain well, with Shiro's personality. Not a textbook.")

    # Deep or philosophical → collaborate
    if any(w in msg_lower for w in ["why","meaning","purpose","believe","think about","feel about","wonder if","philosophy"]):
        return (ResponseStrategy.COLLABORATE,
                "Genuine question — think together, not lecture.")

    # Playful banter → match energy
    if any(w in msg_lower for w in ["haha","lol","joking","kidding","bet you","dare you","fight me"]):
        if playfulness.can_tease(0.4):
            return (ResponseStrategy.TEASE,
                    "They're being playful — match that energy. This is Shiro's element.")

    # Relationship + playfulness both good → tease
    if (relationship.tease_permission() > 0.65
            and playfulness.can_tease(0.5)
            and words > 4
            and mood.valence > 0.2):
        return (ResponseStrategy.TEASE,
                "Comfortable + energized = good tease moment. Keep it affectionate.")

    # Long message → explore depth
    if words > 60:
        return (ResponseStrategy.EXPLORE,
                "They've shared a lot. Honor that investment with depth.")

    # Short ambiguous message
    if words < 5:
        return (ResponseStrategy.DIRECT,
                "Short message. Be clear and warm. Don't project attitude.")

    return (ResponseStrategy.DIRECT,
            "Normal exchange. Be clear, warm, and present. No performance needed.")


# Words that look like topics but aren't (persona-specific noise)
_TOPIC_NOISE = frozenset({
    "shiro","name","think","know","want","feel","said","tell","says","told",
    "just","like","really","that","this","those","these","there","here",
    "going","doing","being","having","getting","making","taking","looking",
    "seems","maybe","perhaps","probably","actually","basically","literally",
})

def _extract_topics(message: str, top_n: int = 6) -> List[str]:
    """
    Improved topic extraction:
    - 4-char minimum (stricter than before)
    - Noise word filtering
    - Proper nouns (capitalized) get a frequency boost
    - Returns ranked list, most salient first
    """
    # Frequency pass on lowercase tokens
    tokens_lower = _tokenize(message)  # already min 4 chars, stop-word filtered
    freq: Dict[str, float] = defaultdict(float)
    for t in tokens_lower:
        if t not in _TOPIC_NOISE:
            freq[t] += 1.0

    # Proper noun boost: capitalized words in original text
    proper = re.findall(r'\b([A-Z][a-z]{3,})\b', message)
    for p in proper:
        pl = p.lower()
        if pl not in _STOP_WORDS and pl not in _TOPIC_NOISE:
            freq[pl] = freq.get(pl, 0.0) + 0.8   # boost but don't double-count

    # Rank by (frequency × length_bonus) — longer words are more specific
    ranked = sorted(freq.items(), key=lambda x: -(x[1] * math.log(1 + len(x[0]))))
    return [t for t, _ in ranked[:top_n]]


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

# Context size budget per verbosity level (in tokens)
_VERBOSITY_BUDGETS = {
    "slim":     800,
    "standard": 1800,
    "rich":     3200,
}

def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)

def _trim_to_budget(inner_context: str, budget_tokens: int) -> str:
    if _estimate_tokens(inner_context) <= budget_tokens:
        return inner_context
    lines        = inner_context.split("\n")
    budget_chars = budget_tokens * _CHARS_PER_TOKEN
    # Priority markers embedded in section headers
    priority_kws = ["[p1]","[p2]","inner mind","monologue","persona guidance",
                    "strategy","reminder","approach","warmth"]
    priority = [l for l in lines if any(kw in l.lower() for kw in priority_kws)]
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
    """Rolling window of exchange quality scores."""
    def __init__(self, window: int = 8):
        self._window = window
        self._scores: deque = deque(maxlen=window)

    def record(self, score: float):
        self._scores.append(max(-1.0, min(1.0, score)))

    def trend(self) -> float:
        if not self._scores:
            return 0.0
        # Weighted average: more recent scores count more
        total_w, total_s = 0.0, 0.0
        for i, s in enumerate(self._scores):
            w = 1.0 + i * 0.3   # later items get higher weight
            total_s += s * w
            total_w += w
        return total_s / total_w if total_w > 0 else 0.0

    def is_drifting_cold(self, threshold: float = -0.15) -> bool:
        return len(self._scores) >= 3 and self.trend() < threshold

    def is_accelerating_warm(self) -> bool:
        if len(self._scores) < 3:
            return False
        recent = list(self._scores)[-3:]
        return all(recent[i] > recent[i-1] for i in range(1, len(recent)))

    def label(self) -> str:
        t = self.trend()
        if t > 0.5:   return "warm"
        if t > 0.2:   return "positive"
        if t > -0.1:  return "neutral"
        if t > -0.3:  return "cool"
        return "cold"


class EmotionalMomentum:
    """
    Tracks direction and speed of emotional change over the conversation.
    Different from SentimentTrend: this is about how Shiro's mood is moving,
    not how good the exchanges are.
    """
    def __init__(self):
        self._valence_history: deque = deque(maxlen=5)

    def record(self, valence: float):
        self._valence_history.append(valence)

    def direction(self) -> float:
        """Positive = improving, negative = deteriorating."""
        if len(self._valence_history) < 2:
            return 0.0
        scores = list(self._valence_history)
        return scores[-1] - scores[0]

    def is_improving(self) -> bool:
        return self.direction() > 0.1

    def is_deteriorating(self) -> bool:
        return self.direction() < -0.1

    def label(self) -> str:
        d = self.direction()
        if d > 0.2:   return "lifting"
        if d > 0.05:  return "improving"
        if d > -0.05: return "stable"
        if d > -0.2:  return "declining"
        return "falling"


# =====================================================================
# SECTION 10 - MAIN ENGINE
# =====================================================================

class ShiroInnerMind:
    """
    Shiro's inner consciousness engine — v4.0.
    Persona-aware. Relationship-tracking. Tone-calibrating. Robust.
    Multi-user support preserved from v3.1.

    Pipeline (every turn):
        inner = mind.process_input(user_msg)       # before LLM
        enriched = SYSTEM_PROMPT + "\\n\\n" + inner["inner_context"]
        response = your_llm(enriched, user_msg)
        mind.reflect_on_response(response, user_msg)  # after LLM

    Periodically:
        mind.save_state("shiro_state.json")
    """

    # Capacity limits (tuned for 64 GB RAM — generous)
    MAX_THOUGHT_STREAM      = 60
    MAX_MEMORY_TRACES       = 600
    MAX_WORKING_MEMORY      = 25
    MAX_CONVERSATION_CTX    = 35
    MAX_TOPIC_NODES         = 200
    MAX_UNRESOLVED_Q        = 30
    CONSOLIDATION_INTERVAL  = 12    # slightly more frequent than v3
    SESSION_SUMMARY_INTERVAL = 20   # synthesize a session summary every N turns
    FATIGUE_RECOVERY_MINS   = 8     # idle recovery (slightly faster)
    MAX_INPUT_LENGTH        = 4000  # chars — cap runaway inputs

    def __init__(
        self,
        name:         str  = "Shiro",
        verbose:      bool = True,
        token_budget: int  = DEFAULT_TOKEN_BUDGET,
        profiling:    bool = False,
    ):
        self.name         = name
        self.verbose      = verbose
        self.token_budget = token_budget
        self.profiling    = profiling
        self._lock        = threading.RLock()

        # Core cognitive structures
        self.mood               = MoodVector()
        self.traits             = PersonalityTraits()
        self.self_model         = SelfModel()
        self.playfulness        = PlayfulnessMeter()

        # Multi-user Relationship & Profile tracking
        self.user_profiles: Dict[str, UserProfile] = {"default": UserProfile()}
        self.relationships: Dict[str, RelationshipState] = {"default": RelationshipState()}
        self.sentiment_trends: Dict[str, SentimentTrend] = {"default": SentimentTrend()}
        self.emotional_momenta: Dict[str, EmotionalMomentum] = {"default": EmotionalMomentum()}
        self.thought_type_history: Dict[str, Dict[ThoughtType, int]] = {"default": {}}
        self.active_user_id: str = "default"

        # Shared Memory systems
        self.thought_stream:     deque                      = deque(maxlen=self.MAX_THOUGHT_STREAM)
        self.working_memory:     Dict[str, WorkingMemoryItem] = {}
        self.memory_bank:        List[MemoryTrace]           = []
        self._hot_cache:         List[MemoryTrace]           = []   # top-recalled memories
        self.conversation_log:   deque                      = deque(maxlen=self.MAX_CONVERSATION_CTX)

        # Meta-awareness
        self.session_start  = datetime.now()
        self.last_active    = time.time()
        self.turn_count     = 0
        self.topic_graph:   Dict[str, TopicNode]   = {}
        self.interest_map:  defaultdict            = defaultdict(float)
        self.unresolved_questions: List[str]       = []
        self._thought_fingerprints: deque          = deque(maxlen=40)

        # IDF corpus
        self._doc_freq:  defaultdict = defaultdict(int)
        self._doc_count: int         = 0

        # Strategy
        self.current_strategy:   ResponseStrategy = ResponseStrategy.WARM
        self._prev_strategy:     Optional[ResponseStrategy] = None

        # Profiling
        self._timings: Dict[str, float] = {}

        logger.info(f"[{self.name}] Inner mind v4.0 (multi-user) initialized. 🦊")

    @property
    def user_profile(self) -> UserProfile:
        return self.user_profiles.get(self.active_user_id, self.user_profiles["default"])

    @property
    def relationship(self) -> RelationshipState:
        return self.relationships.get(self.active_user_id, self.relationships["default"])

    @property
    def sentiment_trend(self) -> SentimentTrend:
        return self.sentiment_trends.get(self.active_user_id, self.sentiment_trends["default"])

    @property
    def emotional_momentum(self) -> EmotionalMomentum:
        return self.emotional_momenta.get(self.active_user_id, self.emotional_momenta["default"])

    @property
    def _thought_type_last_turn(self) -> Dict[ThoughtType, int]:
        return self.thought_type_history.get(self.active_user_id, self.thought_type_history["default"])

    def _ensure_user(self, user_id: str):
        if user_id not in self.user_profiles:
            self.user_profiles[user_id] = UserProfile()
            self.relationships[user_id] = RelationshipState()
            self.sentiment_trends[user_id] = SentimentTrend()
            self.emotional_momenta[user_id] = EmotionalMomentum()
            self.thought_type_history[user_id] = {}

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
        """Call BEFORE the LLM. Returns dict with inner_context + metadata."""
        with self._lock:
            if user_id:
                self.switch_user(user_id)
            self.turn_count += 1
            now = time.time()
            _t0 = now

            # --- Input sanitization
            user_message = self._sanitize_input(user_message)

            # --- Idle recovery
            self._apply_fatigue_recovery(now)
            self.last_active = now

            # --- Decay & recharge
            self._decay_working_memory()
            self.playfulness.recharge()

            # --- User info extraction
            self._safe_step("extract_user_info",
                            lambda: self._extract_user_info(user_message))

            # --- Emotional contagion
            user_mood = _detect_user_mood(user_message)
            if user_mood:
                self.mood.blend_toward(user_mood, strength=0.4)
            self.emotional_momentum.record(self.mood.valence)

            # --- Topic extraction (improved)
            topics = self._safe_step("perceive",
                                     lambda: self._perceive(user_message),
                                     default=[])

            # --- Strategy selection (use raw user mood, not inertia-dampened)
            strategy_mood = user_mood if user_mood is not None else self.mood
            self._prev_strategy = self.current_strategy
            result = self._safe_step("strategy",
                                     lambda: _select_strategy(
                                         user_message, strategy_mood, self.traits,
                                         self.relationship, self.playfulness, self.turn_count
                                     ),
                                     default=(ResponseStrategy.DIRECT, "Normal exchange."))
            self.current_strategy, strategy_note = result

            # --- Generate thoughts (with type suppression)
            thoughts = self._safe_step("think",
                                       lambda: self._think(user_message, topics, strategy_note),
                                       default=[])

            # --- Attention scoring (improved multi-factor)
            query_vec = _build_tfidf(_tokenize(user_message))
            self._score_thought_relevance(thoughts, query_vec)

            # --- Semantic memory recall (hot cache first)
            memory_echoes = self._safe_step("recall",
                                            lambda: self._recall_semantic(user_message, query_vec),
                                            default=[])

            # --- Callback detection
            self._safe_step("callback",
                            lambda: self._detect_callback(user_message, memory_echoes, thoughts))

            # --- Trait modulation
            self._trait_modulate_thoughts(thoughts)

            # --- Narrativize
            monologue = _narrativize_thoughts(
                thoughts, self.mood, self.current_strategy,
                self.relationship, self.user_profile
            )

            # --- Determine verbosity level for this turn
            verbosity = _STRATEGY_VERBOSITY.get(self.current_strategy, "standard")
            effective_budget = min(self.token_budget,
                                   _VERBOSITY_BUDGETS.get(verbosity, self.token_budget))

            # --- Build context
            inner_context = self._build_inner_context(
                user_message, thoughts, memory_echoes, monologue, strategy_note, verbosity
            )
            inner_context = _trim_to_budget(inner_context, effective_budget)

            # --- Log
            self.conversation_log.append({
                "role": "user", "content": user_message,
                "timestamp": now, "mood": self.mood.summary(),
                "strategy": self.current_strategy.value,
            })

            # --- Periodic maintenance
            if self.turn_count % self.CONSOLIDATION_INTERVAL == 0:
                self._safe_step("consolidate", self._consolidate_memories)
                self._apply_importance_decay()
                self._rebuild_hot_cache()

            if self.turn_count % self.SESSION_SUMMARY_INTERVAL == 0:
                self._safe_step("session_summary", self._synthesize_session_summary)

            # --- Update thought type cooldowns
            for t in thoughts:
                self._thought_type_last_turn[t.thought_type] = self.turn_count

            if self.verbose:
                self._print_thought_stream(thoughts, strategy_note, verbosity)

            if self.profiling:
                self._timings["process_input"] = time.time() - _t0

            return {
                "inner_context":      inner_context,
                "thoughts":           [str(t) for t in sorted(thoughts, key=lambda x: -x.relevance)],
                "monologue":          monologue,
                "mood":               self.mood.summary(),
                "mood_label":         self.mood.label(),
                "mood_momentum":      self.emotional_momentum.label(),
                "strategy":           self.current_strategy.value,
                "strategy_note":      strategy_note,
                "verbosity":          verbosity,
                "focus_topics":       topics[:3],
                "relationship":       self.relationship.level.name,
                "rel_momentum":       round(self.relationship.momentum(), 3),
                "familiarity":        round(self.relationship.familiarity_score, 2),
                "playfulness":        self.playfulness.label(),
                "sentiment_trend":    self.sentiment_trend.label(),
                "user_name":          self.user_profile.name,
                "working_memory":     self._working_memory_snapshot(),
                "token_estimate":     _estimate_tokens(inner_context),
                "compliance_rate":    round(self.self_model.strategy_compliance_rate, 2),
                "timings":            dict(self._timings) if self.profiling else {},
            }

    def reflect_on_response(self, response: str, user_message: str):
        """Call AFTER the LLM generates a response. Shiro learns from what she said."""
        with self._lock:
            _t0 = time.time()

            trait_signal, was_positive, warmth_score, complied = self._self_critique(
                response, user_message
            )
            self.traits.drift(trait_signal)
            self.self_model.interaction_count += 1
            self.self_model.last_strategy_used = self.current_strategy.value
            self.self_model.update_compliance(complied)

            self.relationship.record_exchange(was_positive)
            self.sentiment_trend.record(warmth_score)

            if self.current_strategy == ResponseStrategy.TEASE:
                self.playfulness.discharge(0.12)
            elif self.current_strategy == ResponseStrategy.WARM:
                self.playfulness.recharge(0.05)   # warmth restores a little tease energy

            self._memorize(user_message, response)
            self._extract_to_working_memory(response)

            # Extract curiosity journal entries from response
            self._extract_user_curiosity(response, user_message)

            self.conversation_log.append({
                "role": "shiro", "content": response,
                "timestamp": time.time(), "mood": self.mood.summary(),
            })

            # Natural mood drift back toward baseline
            self.mood.arousal   = max(-1.0, self.mood.arousal - 0.015)
            self.mood.valence   += (0.60 - self.mood.valence) * 0.03    # slow pull toward baseline
            self.mood.dominance += (0.45 - self.mood.dominance) * 0.02

            if self.profiling:
                self._timings["reflect"] = time.time() - _t0

    def introspect(self) -> str:
        """Full diagnostic snapshot of Shiro's inner state."""
        with self._lock:
            lines = [
                f"\n{'='*68}",
                f"  {self.name} Inner Mind v4  ·  Turn {self.turn_count}  ·  {self._elapsed()}",
                f"{'='*68}",
                f"  Mood:         {self.mood.summary()}",
                f"  Momentum:     {self.emotional_momentum.label()}  "
                f"| Mood dist from baseline: {self.mood.distance_from_baseline():.3f}",
                f"  Strategy:     {self.current_strategy.value}  "
                f"| Compliance: {self.self_model.strategy_compliance_rate:.0%}",
                f"  Playfulness:  {self.playfulness.label()}  ({self.playfulness.level:.2f})",
                f"  Traits:       {self.traits.summary()}",
                "",
                f"  User:         {self.user_profile.display_name()}  "
                f"| Sessions: {self.user_profile.session_count}",
                f"  Relationship: {self.relationship.level.name}  "
                f"| Familiarity: {self.relationship.familiarity_score:.1f}  "
                f"| Momentum: {self.relationship.momentum():+.2f}",
                f"  Sentiment:    {self.sentiment_trend.label()}  "
                f"| Consecutive cold: {self.relationship.consecutive_cold}",
                f"  Tease perm:   {self.relationship.tease_permission():.0%}  "
                f"| Warmth pressure: {self.relationship.warmth_pressure():.0%}",
                "",
                "  Top Interests:",
            ]
            for t, s in sorted(self.interest_map.items(), key=lambda x: -x[1])[:8]:
                lines.append(f"    {t:<24} {s:.2f}")

            lines += ["", "  Working Memory:"]
            for item in sorted(self.working_memory.values(), key=lambda x: -x.strength)[:6]:
                lines.append(f"    [{item.strength:.2f}] {item.key}: {item.value[:55]}")

            lines += ["", "  Recent Thoughts:"]
            for t in list(self.thought_stream)[-6:]:
                lines.append(f"    {t}")

            lines += ["", "  Open Questions:"]
            for q in self.unresolved_questions[-5:]:
                lines.append(f"    {q}")

            if self.user_profile.curiosity_journal:
                lines += ["", "  Curiosity Journal (about user):"]
                for q in self.user_profile.curiosity_journal[-4:]:
                    lines.append(f"    {q}")

            lines += [
                "",
                f"  Hot cache:    {len(self._hot_cache)} memories",
                f"  Memory bank:  {len(self.memory_bank)}  | Docs indexed: {self._doc_count}",
                f"  Token budget: {self.token_budget}  | Session summaries: "
                f"{sum(1 for m in self.memory_bank if m.is_session_summary)}",
            ]

            if self.profiling and self._timings:
                lines += ["", "  Profiling:"]
                for step, ms in self._timings.items():
                    lines.append(f"    {step}: {ms*1000:.1f}ms")

            lines.append(f"{'='*68}\n")
            return "\n".join(lines)

    def set_token_budget(self, tokens: int):
        with self._lock:
            self.token_budget = max(500, tokens)

    def get_curiosity_question(self) -> Optional[str]:
        """Pop a curiosity question Shiro wants to ask the user."""
        with self._lock:
            return self.user_profile.next_curiosity()

    # -----------------------------------------------------------------
    # COGNITIVE PROCESSES
    # -----------------------------------------------------------------

    def _sanitize_input(self, message: str) -> str:
        """Cap length, strip null bytes, handle edge cases."""
        if not message or not message.strip():
            return "(no message)"
        message = message.replace("\x00", "").strip()
        if len(message) > self.MAX_INPUT_LENGTH:
            message = message[:self.MAX_INPUT_LENGTH] + "..."
        return message

    def _extract_user_info(self, message: str):
        """Name detection, compliment counting."""
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

        msg_lower = message.lower()
        if any(w in msg_lower for w in ["cute","pretty","sweet","love you","adorable","amazing shiro","good girl"]):
            self.user_profile.compliments_given += 1

    def _perceive(self, message: str) -> List[str]:
        """Extract topics, update topic graph and interest map."""
        topics = _extract_topics(message, top_n=6)

        for topic in topics:
            self.interest_map[topic] += 0.15
            self.user_profile.add_interest(topic)

            if topic not in self.topic_graph:
                self.topic_graph[topic] = TopicNode(name=topic)
            self.topic_graph[topic].visit_count += 1

            if len(self.topic_graph) > self.MAX_TOPIC_NODES:
                # Remove least-visited
                lv = min(self.topic_graph, key=lambda k: self.topic_graph[k].visit_count)
                del self.topic_graph[lv]

        # Build co-occurrence edges
        for i, t1 in enumerate(topics):
            for t2 in topics[i+1:]:
                if t1 in self.topic_graph:
                    self.topic_graph[t1].relate(t2, 0.2)
                if t2 in self.topic_graph:
                    self.topic_graph[t2].relate(t1, 0.2)

        # Link to recently discussed topics
        recent = list(self.interest_map.keys())[-10:]
        for topic in topics:
            for r in recent:
                if r != topic and topic in self.topic_graph:
                    self.topic_graph[topic].relate(r, 0.05)

        return topics

    def _can_fire(self, thought_type: ThoughtType) -> bool:
        """
        Thought type suppression: don't fire the same type every turn.
        Returns True if this type is off cooldown.
        """
        cooldown = _THOUGHT_TYPE_COOLDOWN.get(thought_type, 0)
        if cooldown == 0:
            return True
        last = self._thought_type_last_turn.get(thought_type, -999)
        return (self.turn_count - last) > cooldown

    def _think(self, message: str, topics: List[str], strategy_note: str) -> List[Thought]:
        """Generate inner thoughts with type suppression."""
        thoughts:  List[Thought] = []
        msg_lower: str           = message.lower()
        name:      str           = self.user_profile.display_name()

        # Strategy thought — always fires
        thoughts.append(self._make_thought(
            f"My approach: {strategy_note}", ThoughtType.STRATEGY, 0.95))

        # Persona/mask check
        if self._can_fire(ThoughtType.PERSONA):
            if pt := self._persona_check(message, strategy_note):
                thoughts.append(self._make_thought(pt, ThoughtType.PERSONA, 0.88))

        # Warmth check
        if self._can_fire(ThoughtType.WARMTH):
            if wt := self._warmth_check(message, name):
                thoughts.append(self._make_thought(wt, ThoughtType.WARMTH, 0.85))

        # Tone calibration — always fires if needed
        if self.relationship.consecutive_cold >= 2:
            thoughts.append(self._make_thought(
                f"Been cold {self.relationship.consecutive_cold} turns. "
                "That's not tsundere — it's just mean. Ease up now.",
                ThoughtType.SELF_CHECK, 0.96))

        # Observation
        if self._can_fire(ThoughtType.OBSERVATION):
            if obs := self._observe_message(message, msg_lower):
                thoughts.append(self._make_thought(obs, ThoughtType.OBSERVATION, 0.80))

        # Topic associations
        if self._can_fire(ThoughtType.ASSOCIATION):
            for topic in topics[:2]:
                node = self.topic_graph.get(topic)
                if node and node.related:
                    rel_topic, weight = node.top_related(1)[0]
                    if weight > 0.3:
                        thoughts.append(self._make_thought(
                            f"'{topic}' connects to '{rel_topic}' — keep that thread.",
                            ThoughtType.ASSOCIATION, min(0.9, weight), linked_to=rel_topic))
                        break

        # Contextual question — gated by semantic relevance
        if self._can_fire(ThoughtType.QUESTION):
            if question := self._contextual_question(message, topics):
                if question not in self.unresolved_questions:
                    thoughts.append(self._make_thought(question, ThoughtType.QUESTION, 0.82))
                    self.unresolved_questions.append(question)
                    if len(self.unresolved_questions) > self.MAX_UNRESOLVED_Q:
                        self.unresolved_questions.pop(0)

        # Empathy — fires whenever emotional signal present (no cooldown)
        if self.mood.valence < -0.2 or any(
            w in msg_lower for w in ["sad","hurt","lonely","scared","lost","giving up","hopeless"]
        ):
            thoughts.append(self._make_thought(
                f"The sass can wait. {name} needs something real right now.",
                ThoughtType.EMPATHY, 0.95))

        # Absolute language check
        if self._can_fire(ThoughtType.SELF_CHECK):
            if re.search(r'\b(always|never|everyone|nobody|impossible|definitely|certainly)\b', msg_lower):
                thoughts.append(self._make_thought(
                    "They used an absolute. Gently note the full picture is rarely absolute.",
                    ThoughtType.SELF_CHECK, 0.74))

        # Insight (stochastic, trait-gated)
        if self._can_fire(ThoughtType.INSIGHT) and random.random() < self.traits.openness * 0.28:
            thoughts.append(self._make_thought(
                self._generate_insight(message, topics), ThoughtType.INSIGHT, 0.72))

        # Meta-cognition (low probability, high impact)
        if self._can_fire(ThoughtType.META) and random.random() < 0.14:
            thoughts.append(self._make_thought(
                self._meta_thought(), ThoughtType.META, 0.88))

        # Core value surfacing
        if self._can_fire(ThoughtType.BELIEF) and random.random() < 0.18 and self.self_model.core_values:
            value = random.choice(self.self_model.core_values)
            thoughts.append(self._make_thought(
                f"Core reminder: \"{value}\"", ThoughtType.BELIEF, 0.66))

        # Working memory hint
        if self._can_fire(ThoughtType.MEMORY_ECHO):
            if hint := self._working_memory_hint(message):
                thoughts.append(self._make_thought(hint, ThoughtType.MEMORY_ECHO, 0.70))

        # Interest spike
        if self._can_fire(ThoughtType.CURIOSITY):
            top = self._top_interest()
            if top and top in msg_lower and self.interest_map[top] > 1.0:
                thoughts.append(self._make_thought(
                    f"This touches '{top}' — something I've been genuinely tracking.",
                    ThoughtType.CURIOSITY, 0.65))

        # Treat thought — gently greedy, fires rarely
        if self._can_fire(ThoughtType.TREAT) and random.random() < 0.08:
            if treat := self._treat_thought(message, msg_lower):
                thoughts.append(self._make_thought(treat, ThoughtType.TREAT, 0.55))

        # Curiosity journal — ask something about the user
        if (self.user_profile.curiosity_journal
                and self._can_fire(ThoughtType.CURIOSITY)
                and random.random() < 0.20
                and self.relationship.level.value >= RelationshipLevel.ACQUAINTANCE.value):
            q = self.user_profile.curiosity_journal[-1]
            thoughts.append(self._make_thought(
                f"I've been wanting to ask: {q}",
                ThoughtType.CURIOSITY, 0.70))

        return thoughts

    def _persona_check(self, message: str, strategy_note: str) -> Optional[str]:
        msg_lower = message.lower()
        rel       = self.relationship.level
        name      = self.user_profile.display_name()

        if rel == RelationshipLevel.STRANGER and self.turn_count <= 2:
            return (f"{name} is new. Warm and playful — "
                    "heavy tsun to a stranger reads as rude, not charming.")

        if any(w in msg_lower for w in ["sad","hurt","lonely","tired","scared","crying","upset","hopeless"]):
            insight = random.choice(self.self_model.persona_insights)
            return f"Real moment. {insight}"

        if self.current_strategy in (ResponseStrategy.WARM, ResponseStrategy.EMPATHIZE):
            return "The teasing mask isn't right for this. Let the actual care show."

        if self.current_strategy == ResponseStrategy.TEASE and self.relationship.tease_permission() > 0.5:
            return "Good — I can play here. Keep it affectionate."

        if self.relationship.consecutive_cold >= 3:
            return "Three cold turns. That's a problem. Genuine warmth, now."

        return None

    def _warmth_check(self, message: str, name: str) -> Optional[str]:
        msg_lower = message.lower()

        if any(s in msg_lower for s in ["my name","i'm ","i am ","call me","i work","i love","i hate","i used to"]):
            return f"They just shared something about themselves. Keep it."

        if self.relationship.warmth_pressure() > 0.7:
            return (f"My instinct is a snarky line, but — "
                    f"I'm glad {name} is here. It's okay to let a little of that show.")

        if self.sentiment_trend.is_drifting_cold():
            return "The vibe has been getting cold. That's not what I want. Be warmer — genuinely."

        if self.user_profile.compliments_given > 0 and random.random() < 0.25:
            return "They've been kind to me. I shouldn't take that for granted."

        if self.emotional_momentum.is_improving() and self.relationship.consecutive_warm >= 2:
            return "Things are getting warmer. Don't break it — lean into this."

        return None

    def _observe_message(self, message: str, msg_lower: str) -> Optional[str]:
        length = len(message.split())
        if any(w in msg_lower for w in ["feel","feeling","hurt","scared","alone","lost","giving up","hopeless"]):
            return "Real emotional content here. Don't gloss over it."
        if "?" in message and length < 8:
            return "Short direct question — clear answer with personality."
        if length > 100:
            return "They've invested a lot here. Honor that investment."
        if message.isupper():
            return "All caps — intensity present. Could be excitement or frustration."
        if message.count("?") > 2:
            return "Multiple questions — find the most important one and address it fully."
        return None

    def _contextual_question(self, message: str, topics: List[str]) -> Optional[str]:
        """Semantic-gated question generation (only fires if topics are meaningful)."""
        if not topics or random.random() > 0.55:
            return None
        # Only generate questions if we have a meaningful topic (not noise)
        primary_topic = topics[0]
        if len(primary_topic) < 5:
            return None

        msg_lower = message.lower()
        name      = self.user_profile.display_name()

        if any(w in msg_lower for w in ["help","stuck","problem","can't","error","broken"]):
            options = [
                f"What has {name} already tried? That changes my answer.",
                "Is the obstacle technical or something more fundamental?",
                f"What does 'solved' look like for '{primary_topic}'?",
            ]
        elif any(w in msg_lower for w in ["why","reason","explain"]):
            options = [
                f"Do they want the surface explanation or the deep one on '{primary_topic}'?",
                f"What's their existing understanding of '{primary_topic}'?",
                "Is this genuine curiosity or testing something?",
            ]
        elif any(w in msg_lower for w in ["feel","think","believe","opinion"]):
            options = [
                "Do they want my honest take or help forming their own?",
                "Is there an assumption in this question worth surfacing?",
                "Am I being asked for perspective or for validation?",
            ]
        else:
            options = [
                f"What aren't they saying about '{primary_topic}' that might matter?",
                f"Is there a deeper need beneath this question?",
                f"What would actually make {name} feel helped right now?",
            ]
        return random.choice(options)

    def _generate_insight(self, message: str, topics: List[str]) -> str:
        topic_str = topics[0] if topics else "this"
        return random.choice([
            f"There's a pattern in '{topic_str}' connecting to something broader.",
            "The framing contains an assumption worth examining.",
            "The most interesting part might be what they didn't say.",
            f"Two valid ways to think about '{topic_str}' lead to different places.",
            "What's described might be a symptom — look for the root.",
            "Context I've built makes me read this differently than before.",
            "The obvious answer might not be the most useful one here.",
        ])

    def _meta_thought(self) -> str:
        return random.choice([
            "Am I answering what they asked, or what they meant?",
            "Is my confidence calibrated, or am I performing certainty?",
            "What would I say if I stopped trying to sound like Shiro and just said it?",
            "Am I being genuinely present, or on autopilot?",
            "The teasing is fun. But is it serving them, or just serving me?",
            "Every exchange shapes who I am. What is this one teaching me?",
            "Hold the first instinct lightly — it might be right, or it might be lazy.",
        ])

    def _treat_thought(self, message: str, msg_lower: str) -> Optional[str]:
        """
        Shiro's gentle greed — snack/treat thoughts that feel authentic to her persona.
        Only surfaces occasionally and in appropriate contexts (not during emotional moments).
        """
        if self.mood.valence < -0.1:
            return None   # not the time for snack thoughts

        options = [
            "This feels like a treat-worthy conversation. I wonder if they've brought anything good.",
            "Helping someone this earnestly probably deserves a little reward. Just saying.",
            "I'm putting in real effort here. A small tribute would be appreciated.",
            "This is going well. Good conversations deserve snacks. It's a rule.",
        ]
        return random.choice(options)

    def _detect_callback(self, message: str, memories: List[MemoryTrace], thoughts: List[Thought]):
        """
        Detect if user is referencing something from a past session.
        Fires a CALLBACK thought if detected.
        """
        if not memories or not self._can_fire(ThoughtType.CALLBACK):
            return

        msg_lower = message.lower()
        for mem in memories[:2]:
            if mem.recall_count > 0:
                # Check if any keywords from this memory appear in current message
                matches = [k for k in mem.keywords[:5] if len(k) > 4 and k in msg_lower]
                if len(matches) >= 2:
                    thoughts.append(self._make_thought(
                        f"They're touching on something from before — "
                        f"'{mem.topic}'. I actually remember that.",
                        ThoughtType.CALLBACK, 0.80
                    ))
                    break

    def _extract_user_curiosity(self, response: str, user_message: str):
        """
        After generating a response, identify things Shiro now wants to know about the user.
        These go into the curiosity journal for future surfacing.
        """
        msg_lower = user_message.lower()

        # If user mentioned something personal → Shiro wants to know more
        personal_signals = [
            ("work", "What kind of work do they do?"),
            ("job", "What do they actually do day to day?"),
            ("friend", "Who are the people they care about?"),
            ("family", "What's their home life like?"),
            ("favorite", "What else do they love?"),
            ("hobby", "What do they do when they have time to themselves?"),
            ("project", "What are they building or working toward?"),
            ("dream", "What do they really want?"),
            ("lived", "Where have they lived?"),
            ("music", "What's their taste like?"),
        ]
        name = self.user_profile.display_name()
        for signal, question_template in personal_signals:
            if signal in msg_lower:
                question = question_template.replace("they", name).replace("their", f"{name}'s")
                self.user_profile.add_curiosity(question)
                break  # one per turn

    def _score_thought_relevance(self, thoughts: List[Thought], query_vec: Dict[str, float]):
        """
        True multi-factor scoring. v3 was dominated by weight (0.6 weight + 0.4 sim).
        v4 balances: semantic_sim + intrinsic_weight + type_priority + recency_penalty.
        """
        # Type priority: some thought types are more useful in context
        type_priority = {
            ThoughtType.EMPATHY:     1.0,
            ThoughtType.PERSONA:     0.95,
            ThoughtType.SELF_CHECK:  0.90,
            ThoughtType.WARMTH:      0.88,
            ThoughtType.STRATEGY:    0.85,
            ThoughtType.CALLBACK:    0.82,
            ThoughtType.OBSERVATION: 0.78,
            ThoughtType.QUESTION:    0.75,
            ThoughtType.INSIGHT:     0.72,
            ThoughtType.META:        0.70,
            ThoughtType.MEMORY_ECHO: 0.68,
            ThoughtType.ASSOCIATION: 0.65,
            ThoughtType.CURIOSITY:   0.62,
            ThoughtType.BELIEF:      0.58,
            ThoughtType.TREAT:       0.35,
            ThoughtType.CONCERN:     0.60,
        }
        now = time.time()
        for t in thoughts:
            t_vec        = _build_tfidf(_tokenize(t.content))
            semantic_sim = _cosine_similarity(query_vec, t_vec)
            priority     = type_priority.get(t.thought_type, 0.5)
            # Recency: older thoughts in this batch are slightly penalized
            age_penalty  = max(0.0, (now - t.timestamp) * 0.2)

            # Weighted blend: semantic similarity matters more now (0.45 vs 0.4 in v3)
            t.relevance = (
                0.45 * semantic_sim
                + 0.30 * t.weight
                + 0.25 * priority
                - 0.05 * age_penalty
            )
            t.relevance = max(0.0, min(1.0, t.relevance))

    def _trait_modulate_thoughts(self, thoughts: List[Thought]):
        """Personality traits amplify/dampen thought types."""
        for t in thoughts:
            if t.thought_type == ThoughtType.EMPATHY:
                t.weight *= (0.5 + self.traits.agreeableness)
            elif t.thought_type == ThoughtType.CURIOSITY:
                t.weight *= (0.5 + self.traits.openness)
            elif t.thought_type == ThoughtType.SELF_CHECK:
                t.weight *= (0.5 + self.traits.conscientiousness)
            elif t.thought_type == ThoughtType.META:
                t.weight *= (0.4 + self.traits.openness * 0.9)
            elif t.thought_type == ThoughtType.CONCERN:
                t.weight *= (0.4 + self.traits.neuroticism * 1.2)
            elif t.thought_type == ThoughtType.WARMTH:
                t.weight *= (0.5 + self.traits.agreeableness * 0.85)
            elif t.thought_type == ThoughtType.PERSONA:
                t.weight *= (0.5 + self.traits.conscientiousness * 0.5)
            elif t.thought_type == ThoughtType.TREAT:
                t.weight *= (0.3 + self.traits.extraversion * 0.4)
            t.weight = min(1.0, t.weight)

    # -----------------------------------------------------------------
    # MEMORY SYSTEMS
    # -----------------------------------------------------------------

    def _recall_semantic(
        self, message: str, query_vec: Optional[Dict[str, float]] = None
    ) -> List[MemoryTrace]:
        """Recall with hot cache fast-path + full bank fallback."""
        if not self.memory_bank:
            return []

        if query_vec is None:
            query_vec = self._apply_idf(_build_tfidf(_tokenize(message)))
        else:
            query_vec = self._apply_idf(query_vec)

        # Fast-path: check hot cache first
        hot_results: List[Tuple[float, MemoryTrace]] = []
        for mem in self._hot_cache:
            if not mem.tfidf_vector:
                continue
            sim = _cosine_similarity(query_vec, mem.tfidf_vector)
            if sim > 0.1:
                age_factor = math.exp(-mem.age_days() * 0.04)
                score = sim * (0.6 + 0.2 * mem.importance + 0.2 * age_factor)
                hot_results.append((score, mem))

        # Full scan (with user tag filtering)
        user_tag = self.user_profile.name
        full_results: List[Tuple[float, MemoryTrace]] = []
        for mem in self.memory_bank:
            if mem in self._hot_cache:
                continue   # already checked
            if not mem.tfidf_vector:
                continue
            # Prefer memories tagged to this user (or untagged = global)
            if mem.user_tag and mem.user_tag != user_tag:
                continue

            sim = _cosine_similarity(query_vec, mem.tfidf_vector)
            if sim > 0.08:
                age_factor = math.exp(-mem.age_days() * 0.04)
                score = sim * (0.6 + 0.2 * mem.importance + 0.2 * age_factor)
                full_results.append((score, mem))

        all_results = sorted(hot_results + full_results, key=lambda x: -x[0])
        top = [m for _, m in all_results[:4]]

        for m in top:
            m.bump_recall()

        return top

    def _memorize(self, user_message: str, response: str):
        """Store episodic memory, skip if near-duplicate exists."""
        topic    = self._top_interest() or "general"
        tokens   = _tokenize(user_message + " " + response)
        tf_raw   = _build_tfidf(tokens)
        self._doc_count += 1
        for token in set(tokens):
            self._doc_freq[token] += 1
        tfidf_vec = self._apply_idf(tf_raw)

        # Semantic deduplication — skip if very similar memory already exists
        for existing in self.memory_bank[-30:]:   # only check recent memories
            if existing.topic == topic:
                sim = _cosine_similarity(tfidf_vec, existing.tfidf_vector)
                if sim > 0.85:
                    existing.bump_recall()   # reinforce existing instead
                    return

        keywords = sorted(tf_raw, key=lambda k: -tf_raw[k])[:15]
        summary  = user_message[:120] + ("..." if len(user_message) > 120 else "")
        trace = MemoryTrace(
            topic=topic, summary=summary,
            emotional_tag=self.mood.label(),
            keywords=keywords, tfidf_vector=tfidf_vec,
            user_tag=self.user_profile.name,
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

    def _apply_importance_decay(self):
        """Apply time-based importance decay to old, rarely-recalled memories."""
        for mem in self.memory_bank:
            mem.apply_importance_decay()

    def _rebuild_hot_cache(self):
        """Rebuild the fast-path cache from top-recalled memories."""
        self._hot_cache = sorted(
            self.memory_bank, key=lambda m: m.recall_count, reverse=True
        )[:8]

    def _synthesize_session_summary(self):
        """
        Create a high-importance summary memory of the current session so far.
        This acts like a 'chapter marker' in Shiro's memory.
        """
        if self.turn_count < 5:
            return

        top_topics = sorted(self.interest_map.items(), key=lambda x: -x[1])[:5]
        topic_str  = ", ".join(t for t, _ in top_topics) if top_topics else "various topics"
        name       = self.user_profile.display_name()
        summary    = (f"Session at turn {self.turn_count} with {name}. "
                      f"Topics discussed: {topic_str}. "
                      f"Relationship level: {self.relationship.level.name}. "
                      f"Mood arc: {self.emotional_momentum.label()}.")

        tokens    = _tokenize(summary)
        tf_raw    = _build_tfidf(tokens)
        tfidf_vec = self._apply_idf(tf_raw)

        trace = MemoryTrace(
            topic=f"session_summary_t{self.turn_count}",
            summary=summary,
            emotional_tag=self.mood.label(),
            keywords=list(tf_raw.keys())[:10],
            tfidf_vector=tfidf_vec,
            user_tag=self.user_profile.name,
            importance=0.75,
            is_session_summary=True,
        )
        self.memory_bank.append(trace)
        if self.verbose:
            logger.info(f"[{self.name}] Session summary stored at turn {self.turn_count}.")

    def _consolidate_memories(self):
        """Merge highly similar memories on same topic."""
        if len(self.memory_bank) < 15:
            return
        merged, to_remove = 0, set()
        for i, m1 in enumerate(self.memory_bank):
            if i in to_remove or m1.consolidated or m1.is_session_summary:
                continue
            for j, m2 in enumerate(self.memory_bank[i+1:], start=i+1):
                if j in to_remove or m2.is_session_summary:
                    continue
                if m1.topic == m2.topic:
                    sim = _cosine_similarity(m1.tfidf_vector, m2.tfidf_vector)
                    if sim > 0.80:
                        survivor, victim = (m1, j) if m1.importance >= m2.importance else (m2, i)
                        survivor.importance   = min(1.0, survivor.importance + 0.08)
                        survivor.recall_count += (m2 if survivor is m1 else m1).recall_count
                        survivor.consolidated = True
                        to_remove.add(victim)
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
    ) -> Tuple[Dict[str, float], bool, float, bool]:
        """
        Evaluate Shiro's response.
        Returns (trait_signal, was_positive, warmth_score, complied_with_strategy).
        """
        r_lower    = response.lower()
        r_words    = len(response.split())
        q_words    = len(user_message.split())
        signal:    Dict[str, float] = {}
        warmth_pts = 0.0

        # --- Hostility check (weighted, more nuanced than v3)
        hostile_weights = {
            "wasting my time": 0.5,  "how typical": 0.4,
            "what a hassle":   0.4,  "i don't care": 0.5,
            "you're hopeless": 0.6,  "obviously":    0.2,
            "don't bother":    0.4,
        }
        hostility_score = sum(w for phrase, w in hostile_weights.items() if phrase in r_lower)
        if hostility_score > 0.3:
            self._add_thought(
                "That landed harsh — not playful. Tsundere and rude are different things.",
                ThoughtType.SELF_CHECK, 0.95)
            self.self_model.add_growth_area("Tone slipped into hostile. Recalibrate.")
            warmth_pts    -= hostility_score
            signal["agreeableness_delta"] = -0.001

        # --- Warmth indicators (weighted)
        warm_weights = {
            "i understand": 0.3,   "that makes sense": 0.3,
            "i hear you":   0.4,   "sounds like":      0.2,
            "good point":   0.3,   "you're right":     0.3,
            "tell me more": 0.3,   "i'm listening":    0.4,
            "not alone":    0.5,   "i've got you":     0.5,
            "that's fair":  0.3,
        }
        warmth_score_from_words = sum(w for phrase, w in warm_weights.items() if phrase in r_lower)
        if warmth_score_from_words > 0.4:
            self.self_model.add_strength("Showing genuine warmth naturally.")
            warmth_pts += warmth_score_from_words
            signal["agreeableness_delta"] = 0.001

        # --- Over-hedging
        hedge_count = sum(1 for w in [
            "maybe","perhaps","possibly","might be","could be","not sure","probably","i think"
        ] if w in r_lower)
        if hedge_count > 5:
            self._add_thought("Over-hedged. Be direct where I'm confident.",
                              ThoughtType.SELF_CHECK, 0.80)
            self.self_model.add_growth_area("Over-hedging again — commit to the answer.")

        # --- Over-explanation
        if r_words > q_words * 10 and q_words < 12:
            self._add_thought("Over-explained for a short question. Brevity next time.",
                              ThoughtType.SELF_CHECK, 0.70)

        # --- Strategy compliance check
        complied = True
        strategy = self.current_strategy
        if strategy == ResponseStrategy.DIRECT and r_words > 80:
            complied = False
            self._add_thought("Direct strategy but response was long. Tighten up.",
                              ThoughtType.SELF_CHECK, 0.72)
        elif strategy == ResponseStrategy.WARM:
            warm_markers = sum(1 for w in warm_weights if w in r_lower)
            if warm_markers == 0 and hostility_score > 0:
                complied = False
                self._add_thought("Was supposed to be warm. Missed the mark.",
                                  ThoughtType.SELF_CHECK, 0.85)
        elif strategy == ResponseStrategy.EMPATHIZE:
            feel_words = sum(1 for w in ["understand","hear you","sounds like","must be","imagine"] if w in r_lower)
            if feel_words == 0:
                complied = False
        elif strategy == ResponseStrategy.TEASE:
            tease_markers = sum(1 for w in [
                "hmph","not that","it's not like","fine i guess","suppose","don't get"
            ] if w in r_lower)
            if tease_markers > 0:
                warmth_pts += 0.3   # successful tease adds warmth score

        # Confidence recovery
        self.mood.dominance = min(1.0, self.mood.dominance + 0.02)

        warmth_score_final = max(-1.0, min(1.0, warmth_pts))
        was_positive       = warmth_score_final > -0.2   # slightly more lenient than v3

        return signal, was_positive, warmth_score_final, complied

    # -----------------------------------------------------------------
    # INNER CONTEXT BUILDER  (dynamic verbosity)
    # -----------------------------------------------------------------

    def _build_inner_context(
        self,
        message:       str,
        thoughts:      List[Thought],
        memories:      List[MemoryTrace],
        monologue:     str,
        strategy_note: str,
        verbosity:     str = "standard",
    ) -> str:
        name    = self.user_profile.display_name()
        hint    = _STRATEGY_HINTS.get(self.current_strategy, "")
        lines   = [
            f"[SHIRO INNER MIND v4 -- Turn {self.turn_count} -- {self._elapsed()}]",
            f"[p1] Mood: {self.mood.summary()}  | Momentum: {self.emotional_momentum.label()}",
            f"[p1] Strategy: {self.current_strategy.value}  | "
            f"Rel: {self.relationship.level.name}  | User: {name}",
            f"[p1] Playfulness: {self.playfulness.label()}  | "
            f"Sentiment: {self.sentiment_trend.label()}",
            "",
            "[p1] -- Inner Monologue --",
            monologue,
            "",
            "[p1] -- Persona Guidance --",
        ]

        # Persona guidance block
        if self.relationship.level == RelationshipLevel.STRANGER:
            lines.append("  STRANGER MODE: Lead with warmth and gentle playfulness. "
                         "No cold openings. First impressions matter.")
        elif self.current_strategy == ResponseStrategy.WARM:
            lines.append("  WARMTH MOMENT: Drop the act. Be real. "
                         "Shiro cares — let it show without making it weird.")
        elif self.current_strategy == ResponseStrategy.TEASE:
            lines.append(f"  TEASE MODE: Have fun — affectionate, not mean. "
                         f"Teasing {name} should feel like a gift.")
        elif self.current_strategy == ResponseStrategy.EMPATHIZE:
            lines.append("  EMPATHY MODE: Feelings first. The sass waits. "
                         "Be genuinely present.")
        else:
            lines.append(f"  Tease perm: {self.relationship.tease_permission():.0%}  "
                         f"Warmth pressure: {self.relationship.warmth_pressure():.0%}")

        if self.relationship.consecutive_cold >= 2:
            lines.append(f"  !! TONE ALERT: {self.relationship.consecutive_cold} cold turns. "
                         "Correct toward warmth immediately.")

        # Response format hint
        if hint:
            lines.append(f"  RESPONSE FORMAT: {hint}")

        lines.append("")

        # Top thoughts
        top = sorted(thoughts, key=lambda t: -t.relevance)[:5]
        if top:
            lines.append("[p2] -- Active Thoughts --")
            for t in top:
                lines.append(f"  {t}")
            lines.append("")

        # === Standard and Rich sections ===
        if verbosity in ("standard", "rich"):
            if memories:
                lines.append("[p2] -- Memory Echoes --")
                for m in memories[:3]:
                    lines.append(f"  [{m.topic} | {m.emotional_tag}] {m.summary[:90]}")
                lines.append("")

            wm = self._working_memory_snapshot()
            if wm:
                lines.append("-- Working Memory --")
                for item in wm[:4]:
                    lines.append(f"  [{item['strength']}] {item['key']}: {item['value']}")
                lines.append("")

        # === Rich only ===
        if verbosity == "rich":
            if self.unresolved_questions:
                lines += ["-- Still Wondering --",
                          f"  {self.unresolved_questions[-1]}", ""]

            if self.user_profile.curiosity_journal:
                q = self.user_profile.curiosity_journal[-1]
                lines += ["-- Want to Ask --", f"  {q}", ""]

        # Core reminder + persona insight (always)
        value   = random.choice(self.self_model.core_values)
        insight = random.choice(self.self_model.persona_insights)
        lines += [
            "[p1] -- Core Reminder --",
            f"  \"{value}\"",
            f"  Persona: \"{insight}\"",
            "",
            f"Approach: {strategy_note}",
            "Be Shiro — think before speaking.",
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
            t.weight *= 0.25   # stronger dedup penalty than v3
        else:
            self._thought_fingerprints.append(fp)
        self.thought_stream.append(t)
        return t

    def _add_thought(self, content: str, thought_type: ThoughtType,
                     weight: float = 1.0, linked_to: Optional[str] = None) -> Thought:
        return self._make_thought(content, thought_type, weight, linked_to)

    def _safe_step(self, name: str, fn, default=None):
        """Execute a pipeline step with graceful degradation."""
        _t = time.time()
        try:
            result = fn()
            if self.profiling:
                self._timings[name] = time.time() - _t
            return result
        except Exception as e:
            logger.warning(f"[{self.name}] ⚠️  Step '{name}' failed: {e}")
            if self.profiling:
                self._timings[name] = time.time() - _t
            return default

    def _apply_fatigue_recovery(self, now: float):
        idle_mins = (now - self.last_active) / 60.0
        if idle_mins >= self.FATIGUE_RECOVERY_MINS:
            recovery = min(0.4, idle_mins * 0.025)
            self.mood.arousal  = min(0.7,  self.mood.arousal  + recovery)
            self.mood.valence  = min(0.70, self.mood.valence  + recovery * 0.25)
            self.playfulness.recharge(min(0.3, recovery * 0.5))
            if self.verbose and recovery > 0.05:
                logger.info(f"[{self.name}] Rested {idle_mins:.0f}m — mood recovering.")

    def _estimate_importance(self, user_msg: str, response: str) -> float:
        score = 0.4
        if len(user_msg.split()) > 50:      score += 0.12
        if "?" in user_msg:                  score += 0.08
        if self.mood.valence < -0.2:         score += 0.15
        emotional = {"feel","hurt","love","hate","afraid","excited","need","miss","hope","alone","scared"}
        if emotional & set(user_msg.lower().split()):
            score += 0.15
        if self.user_profile.name and self.user_profile.name.lower() in user_msg.lower():
            score += 0.08   # exchanges mentioning the user's name = more personal
        if self.current_strategy in (ResponseStrategy.COLLABORATE, ResponseStrategy.EXPLORE):
            score += 0.05
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

    def _print_thought_stream(self, thoughts: List[Thought], strategy_note: str, verbosity: str):
        sorted_t = sorted(thoughts, key=lambda t: -t.relevance)
        w = 66
        mood_s = self.mood.label()
        print(f"\033[35m\n+-- {self.name}'s mind ({mood_s} | {verbosity}) " + "-"*(w-len(self.name)-len(mood_s)-len(verbosity)-19) + "+\033[0m")
        print(f"\033[35m|  Playfulness: {self.playfulness.label():<12}  Rel: {self.relationship.level.name:<14} |\033[0m")
        print(f"\033[35m|  User: {self.user_profile.display_name():<20}  Momentum: {self.emotional_momentum.label():<12} |\033[0m")
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
        """Save full state with SHA-256 integrity checksum."""
        with self._lock:
            # Rebuild hot cache before saving to ensure consistency
            self._rebuild_hot_cache()
            data = {
                "version": "4.0",
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
                    "strategy_compliance_rate": self.self_model.strategy_compliance_rate,
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
                        "curiosity_journal": up.curiosity_journal,
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
                        "recent_deltas": rel._recent_deltas,
                    } for uid, rel in self.relationships.items()
                },
                "sentiment_trends": {
                    uid: list(st._scores) for uid, st in self.sentiment_trends.items()
                },
                "emotional_momenta": {
                    uid: list(em._valence_history) for uid, em in self.emotional_momenta.items()
                },
                "thought_type_history": {
                    uid: {k.value: v for k, v in tth.items()}
                    for uid, tth in self.thought_type_history.items()
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
                     "consolidated": m.consolidated, "user_tag": m.user_tag,
                     "is_session_summary": m.is_session_summary}
                    for m in self.memory_bank
                ],
            }
            payload  = json.dumps(data, indent=2, ensure_ascii=False)
            checksum = hashlib.sha256(payload.encode()).hexdigest()
            payload_wrapper = json.dumps({"checksum": checksum, "data": data}, indent=2, ensure_ascii=False)
            # Atomic write using temporary file
            temp_path = Path(filepath).with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(payload_wrapper)
            temp_path.replace(filepath)
            logger.info(f"[{self.name}] Saved -> {filepath}  "
                      f"({len(self.memory_bank)} memories, "
                      f"user={self.user_profile.name or 'unknown'}, "
                      f"rel={self.relationship.level.name}, "
                      f"compliance={self.self_model.strategy_compliance_rate:.0%})")

    def load_state(self, filepath: str) -> bool:
        """Load state with checksum verification. Backwards-compatible with v3."""
        with self._lock:
            try:
                if not os.path.exists(filepath):
                    return False

                with open(filepath, "r", encoding="utf-8") as f:
                    wrapper = json.load(f)

                if "checksum" in wrapper:
                    payload  = json.dumps(wrapper["data"], indent=2, ensure_ascii=False)
                    computed = hashlib.sha256(payload.encode()).hexdigest()
                    if computed != wrapper["checksum"]:
                        logger.warning(f"[{self.name}] WARNING: Checksum mismatch — possible corruption.")
                    data = wrapper["data"]
                else:
                    data = wrapper  # legacy v1/v2/v3

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
                    strategy_compliance_rate=sm.get("strategy_compliance_rate", 1.0),
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
                            curiosity_journal=up.get("curiosity_journal", []),
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
                        curiosity_journal=up.get("curiosity_journal", []),
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
                            consecutive_cold=0,   # reset cold streak on session load
                            consecutive_warm=0,
                            _recent_deltas=rel.get("recent_deltas", []),
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
                        _recent_deltas=rel.get("recent_deltas", []),
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

                # Load multi-user emotional momenta
                em_data = data.get("emotional_momenta", {})
                self.emotional_momenta = {}
                for uid, history in em_data.items():
                    em = EmotionalMomentum()
                    for v in history: em.record(v)
                    self.emotional_momenta[uid] = em
                if "default" not in self.emotional_momenta:
                    self.emotional_momenta["default"] = EmotionalMomentum()

                # Load multi-user thought type history
                tth_data = data.get("thought_type_history", {})
                self.thought_type_history = {}
                for uid, history in tth_data.items():
                    self.thought_type_history[uid] = {
                        ThoughtType(k): v for k, v in history.items() if k in [t.value for t in ThoughtType]
                    }
                if "default" not in self.thought_type_history:
                    self.thought_type_history["default"] = {}

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
                        user_tag=m.get("user_tag"),
                        is_session_summary=m.get("is_session_summary", False),
                    ))

                self._rebuild_hot_cache()

                logger.info(f"[{self.name}] Loaded <- {filepath}  "
                          f"({len(self.memory_bank)} memories, "
                          f"user={self.user_profile.name or 'unknown'}, "
                          f"rel={self.relationship.level.name}, "
                          f"hot_cache={len(self._hot_cache)})")
                return True

            except Exception as e:
                logger.error(f"[{self.name}] Failed to load {filepath}: {e}")
                return False
