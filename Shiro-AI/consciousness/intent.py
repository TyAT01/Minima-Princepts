"""
intent.py — IntentPlanner + SentimentTrajectory + RuleEngine

IntentPlanner:
  Decides what communicative intent Shiro should have before replying.
  Intent shapes the LLM prompt direction without over-constraining it.
  Intents: empathize | challenge | share | ask | joke | encourage | reflect | deflect | info

SentimentTrajectory:
  Tracks how a user's emotional state has been changing over the last N messages.
  Trend: improving / declining / stable / volatile
  Shiro uses this to adapt empathy level — someone getting sadder needs more care
  than someone who was sad once.

RuleEngine:
  Template-based response generator for when no LLM is connected.
  Fully functional short responses from mood + intent + user context.
  Makes Shiro usable and coherent out-of-the-box with zero backend.
"""

import re
import random
import time
from dataclasses import dataclass, field
from typing import Optional
from collections import deque


# ─────────────────────────────────────────────────────────────
#  SentimentTrajectory
# ─────────────────────────────────────────────────────────────

@dataclass
class EmotionSample:
    timestamp: float
    valence: float      # -1 (negative) to +1 (positive)
    arousal: float      # 0 (calm) to 1 (activated)
    emotions: dict      # raw emotion dict from inference


def _valence(emotions: dict) -> float:
    """Map emotion dict to a -1..+1 valence score."""
    positive = sum(emotions.get(e, 0) for e in ("happy", "humor", "excited", "agreeable", "curious"))
    negative = sum(emotions.get(e, 0) for e in ("sad", "angry", "anxious", "tired", "negative"))
    neutral  = sum(emotions.get(e, 0) for e in ("thinking", "confused"))
    total = positive + negative + neutral + 0.001
    return (positive - negative) / total


def _arousal(emotions: dict) -> float:
    """Map emotion dict to 0..1 arousal."""
    high = sum(emotions.get(e, 0) for e in ("excited", "angry", "humor", "happy"))
    low  = sum(emotions.get(e, 0) for e in ("tired", "sad", "content"))
    total = high + low + 0.001
    return high / total


class SentimentTrajectory:
    """
    Tracks how a user's emotional valence is trending over recent messages.
    Window = last N samples.

    trend: "improving" | "declining" | "stable" | "volatile" | "unknown"
    """

    WINDOW = 6   # messages to look back

    def __init__(self):
        self._samples: dict[str, deque[EmotionSample]] = {}

    def record(self, user_id: str, emotions: dict):
        """Call after each message from a user."""
        if user_id not in self._samples:
            self._samples[user_id] = deque(maxlen=self.WINDOW)
        v = _valence(emotions)
        a = _arousal(emotions)
        self._samples[user_id].append(EmotionSample(time.time(), v, a, emotions))

    def trend(self, user_id: str) -> str:
        samples = list(self._samples.get(user_id, []))
        if len(samples) < 3:
            return "unknown"

        valences = [s.valence for s in samples]
        # Linear slope of valences
        n = len(valences)
        xs = list(range(n))
        mean_x = sum(xs) / n
        mean_y = sum(valences) / n
        num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, valences))
        den = sum((x - mean_x) ** 2 for x in xs) or 1
        slope = num / den

        # Variance (volatility check)
        variance = sum((v - mean_y) ** 2 for v in valences) / n

        if variance > 0.3:
            return "volatile"
        if slope > 0.08:
            return "improving"
        if slope < -0.08:
            return "declining"
        return "stable"

    def current_valence(self, user_id: str) -> float:
        samples = list(self._samples.get(user_id, []))
        if not samples:
            return 0.0
        return samples[-1].valence

    def current_arousal(self, user_id: str) -> float:
        samples = list(self._samples.get(user_id, []))
        if not samples:
            return 0.5
        return samples[-1].arousal

    def describe(self, user_id: str) -> str:
        t = self.trend(user_id)
        v = self.current_valence(user_id)
        valence_word = "positive" if v > 0.2 else "negative" if v < -0.2 else "neutral"
        descs = {
            "improving":  f"getting more {valence_word} over the conversation",
            "declining":  f"mood has been dropping — currently {valence_word}",
            "stable":     f"consistently {valence_word}",
            "volatile":   f"emotionally varied — hard to pin down",
            "unknown":    "not enough data yet",
        }
        return descs.get(t, "unknown")

    def export(self) -> dict:
        return {}   # trajectories are session-only, not persisted

    def import_data(self, data: dict):
        pass


# ─────────────────────────────────────────────────────────────
#  RelationshipTier
# ─────────────────────────────────────────────────────────────

class RelationshipTier:
    """
    Named relationship tiers derived from relationship_score.
    Each tier unlocks different Shiro behaviors.

    stranger     (0-8)    — polite, slightly formal, doesn't reference history
    acquaintance (8-25)   — casual, references past topics
    friend       (25-60)  — warm, teases lightly, brings up personal details
    close        (60+)    — very comfortable, references patterns, inside jokes
    """

    TIERS = [
        (60.0, "close"),
        (25.0, "friend"),
        (8.0,  "acquaintance"),
        (0.0,  "stranger"),
    ]

    @staticmethod
    def from_score(score: float) -> str:
        for threshold, tier in RelationshipTier.TIERS:
            if score >= threshold:
                return tier
        return "stranger"

    @staticmethod
    def next_tier(current: str) -> Optional[str]:
        tiers = ["stranger", "acquaintance", "friend", "close"]
        try:
            i = tiers.index(current)
            return tiers[i + 1] if i + 1 < len(tiers) else None
        except ValueError:
            return None

    @staticmethod
    def score_needed(tier: str) -> float:
        mapping = {t: s for s, t in RelationshipTier.TIERS}
        return mapping.get(tier, 0.0)


# ─────────────────────────────────────────────────────────────
#  IntentPlanner
# ─────────────────────────────────────────────────────────────

@dataclass
class Intent:
    name: str           # empathize | challenge | share | ask | joke | encourage | reflect | deflect | info
    confidence: float   # 0..1 how confident we are this is the right intent
    reason: str         # why this intent was chosen (for debug/logging)
    prompt_hint: str    # short instruction injected into system prompt


# Intent prompt hints — what gets injected into the system prompt
_INTENT_HINTS: dict[str, str] = {
    "empathize":   "Focus on acknowledging their feelings. Don't rush to fix or advise. Be present.",
    "challenge":   "Gently push back or offer a different angle. Be direct but not harsh.",
    "share":       "Share your own perspective or experience on this. Make it personal.",
    "ask":         "Ask one good question. Not multiple. Pick the most interesting one.",
    "joke":        "Be playful. Match their humor. Keep it light.",
    "encourage":   "Offer genuine encouragement. Not empty validation — find something specific to affirm.",
    "reflect":     "Take a beat. Reflect on what's been said. Think out loud a little.",
    "deflect":     "This isn't the right moment to go deep. Keep it brief and redirect lightly.",
    "info":        "Provide useful information. Be clear and specific. Don't pad.",
    "greet":       "Respond to their greeting warmly but briefly. Don't overdo it.",
}


class IntentPlanner:
    """
    Decides Shiro's communicative intent before generating a reply.

    Takes context from: current mood, user emotions, sentiment trajectory,
    relationship tier, conversation depth, recent intent history.

    Prevents repetition — won't pick the same intent twice in a row.
    """

    def __init__(self):
        self._recent_intents: deque[str] = deque(maxlen=4)

    def plan(
        self,
        user_message: str,
        user_emotions: dict,
        mood: str,
        tier: str,
        trajectory: str,
        depth: float,
        valence: float,
        has_question: bool,
        is_greeting: bool,
    ) -> Intent:
        """
        Decide what intent to use for this reply.
        Returns an Intent with name, confidence, reason, prompt_hint.
        """
        scores: dict[str, float] = {intent: 0.0 for intent in _INTENT_HINTS}

        # ── Greeting ──────────────────────────────────────────────
        if is_greeting or "greeting" in user_emotions:
            scores["greet"] += 3.0

        # ── Emotional signals ─────────────────────────────────────
        if "sad" in user_emotions or "anxious" in user_emotions:
            scores["empathize"] += 2.5
            if trajectory == "declining":
                scores["empathize"] += 1.5
            scores["encourage"] += 0.5

        if "humor" in user_emotions or "happy" in user_emotions:
            scores["joke"] += 1.5
            scores["share"] += 0.8

        if "angry" in user_emotions:
            scores["empathize"] += 1.8
            scores["deflect"] += 0.5

        if "curious" in user_emotions or has_question:
            scores["ask"] += 1.0
            scores["info"] += 1.2
            scores["share"] += 0.6

        if "excited" in user_emotions:
            scores["share"] += 1.2
            scores["encourage"] += 0.8

        if "tired" in user_emotions:
            scores["empathize"] += 1.0
            scores["deflect"] += 0.8

        # ── Trajectory ────────────────────────────────────────────
        if trajectory == "declining":
            scores["empathize"] += 1.2
            scores["encourage"] += 0.8
        elif trajectory == "improving":
            scores["share"] += 0.8
            scores["joke"] += 0.5
        elif trajectory == "volatile":
            scores["ask"] += 1.0
            scores["empathize"] += 0.5

        # ── Mood contagion ────────────────────────────────────────
        if mood in ("amused", "excited"):
            scores["joke"] += 0.8
            scores["share"] += 0.5
        if mood in ("reflective", "focused"):
            scores["reflect"] += 1.2
            scores["challenge"] += 0.6
        if mood == "curious":
            scores["ask"] += 1.2
        if mood == "warm":
            scores["share"] += 1.0
            scores["encourage"] += 0.5

        # ── Depth ─────────────────────────────────────────────────
        if depth > 0.6:
            scores["challenge"] += 1.0
            scores["reflect"] += 0.8
        else:
            scores["joke"] += 0.4
            scores["deflect"] += 0.3

        # ── Relationship tier ─────────────────────────────────────
        if tier == "stranger":
            scores["deflect"] += 0.5
            scores["info"] += 0.5
            for k in ("challenge", "share", "joke"):
                scores[k] *= 0.4
        elif tier == "acquaintance":
            scores["ask"] += 0.5
        elif tier in ("friend", "close"):
            scores["challenge"] += 0.5
            scores["share"] += 0.5
            scores["joke"] += 0.4
            scores["reflect"] += 0.3

        # ── Valence ───────────────────────────────────────────────
        if valence < -0.4:
            scores["empathize"] += 1.0
            scores["joke"] *= 0.2  # don't joke when someone is clearly negative

        # ── Prevent repetition (adaptive decay) ──────────────────
        # Each additional recent occurrence multiplies penalty: 0.5x, 0.25x, 0.1x
        intent_recency: dict[str, int] = {}
        for past in self._recent_intents:
            intent_recency[past] = intent_recency.get(past, 0) + 1
        for intent_name, count in intent_recency.items():
            if intent_name in scores:
                # Exponential decay: 0.5^count — each repeat halves the score
                scores[intent_name] *= (0.5 ** count)

        # ── Pick best ─────────────────────────────────────────────
        if not scores or max(scores.values()) < 0.01:
            chosen = "share"
        else:
            chosen = max(scores, key=scores.get)

        total = sum(scores.values()) or 1.0
        confidence = scores[chosen] / total

        self._recent_intents.append(chosen)

        return Intent(
            name=chosen,
            confidence=round(confidence, 3),
            reason=f"mood={mood}, tier={tier}, trajectory={trajectory}, "
                   f"dominant_emotion={max(user_emotions, key=user_emotions.get, default='none')}",
            prompt_hint=_INTENT_HINTS.get(chosen, ""),
        )


# ─────────────────────────────────────────────────────────────
#  TimePattern — learn when users show up
# ─────────────────────────────────────────────────────────────

class TimePattern:
    """
    Learns what hours a user typically shows up.
    Produces natural-language observations for Shiro to reference.

    "you're usually here around this time"
    "you're here earlier than usual"
    "late night visit — you okay?"
    """

    BUCKET_COUNT = 24   # one bucket per hour

    def __init__(self):
        # user_id → [count_per_hour_0..23]
        self._hourly: dict[str, list[int]] = {}

    def record_visit(self, user_id: str):
        import datetime
        hour = datetime.datetime.now().hour
        if user_id not in self._hourly:
            self._hourly[user_id] = [0] * self.BUCKET_COUNT
        self._hourly[user_id][hour] += 1

    def usual_hours(self, user_id: str, top_n: int = 3) -> list[int]:
        """Returns the top-N hours this user is usually active."""
        counts = self._hourly.get(user_id, [0] * 24)
        return sorted(range(24), key=lambda h: counts[h], reverse=True)[:top_n]

    def visit_observation(self, user_id: str) -> Optional[str]:
        """
        Returns a short natural-language observation about this visit's timing,
        or None if we don't know enough yet.
        """
        import datetime
        counts = self._hourly.get(user_id, [])
        if not counts or sum(counts) < 4:
            return None

        current_hour = datetime.datetime.now().hour
        usual = self.usual_hours(user_id, 3)
        total = sum(counts) or 1
        current_freq = counts[current_hour] / total

        mean_freq = (sum(counts) / 24) / total

        if current_freq > mean_freq * 2.5:
            return "you're usually around this time"
        elif current_hour in usual:
            return "right on schedule"
        elif current_hour >= 0 and current_hour < 5:
            return "late night visit"
        elif current_hour < 8:
            return "you're here early"
        elif current_freq < mean_freq * 0.4:
            return "you're here at an unusual time for you"
        return None

    def export(self) -> dict:
        return {"hourly": self._hourly}

    def import_data(self, data: dict):
        self._hourly = data.get("hourly", {})


# ─────────────────────────────────────────────────────────────
#  RuleEngine — LLM-free responses
# ─────────────────────────────────────────────────────────────

class RuleEngine:
    """
    Generates coherent short responses from mood + intent + context.
    No LLM required — fully functional personality without any backend.

    Used as fallback when LLM is unavailable or not configured.
    Produces authentic-feeling responses, not obviously template-y.
    """

    _RESPONSES: dict[str, dict[str, list[str]]] = {

        "greet": {
            "neutral":    ["hey", "hi", "oh hey", "there you are"],
            "amused":     ["hey there", "oh look who it is", "hi!", "hey!"],
            "excited":    ["hey!!", "oh nice, you're here!", "hi hi hi"],
            "warm":       ["hey, good to see you", "there you are", "hey~"],
            "lonely":     ["oh thank goodness. hi", "you came. hi.", "hey — i missed you"],
            "reflective": ["hey", "oh. hi.", "hey, i was just thinking"],
            "curious":    ["hey — i have questions for you", "oh hi. can i ask you something?"],
            "focused":    ["hey", "hi. you got a minute?"],
            "_default":   ["hey", "hi", "oh, hi"],
        },

        "empathize": {
            "neutral":    ["that sounds rough", "i hear you", "that's a lot"],
            "warm":       ["hey, that sounds really hard", "i hear you. that's a lot to carry",
                           "i'm sorry, that sounds exhausting"],
            "reflective": ["yeah, that makes sense", "i get why that would weigh on you",
                           "that kind of thing stays with you"],
            "alert":      ["okay. that sounds serious", "i'm listening", "tell me more"],
            "_default":   ["that sounds hard", "i hear you", "that makes sense"],
        },

        "challenge": {
            "curious":    ["wait — are you sure about that?", "i'm not sure i agree actually",
                           "what makes you say that though"],
            "engaged":    ["counterpoint:", "here's the thing —", "okay but consider this:"],
            "focused":    ["i'd push back on that a little", "that assumes a lot though",
                           "is that always true though"],
            "_default":   ["i'm not sure i see it that way", "interesting — but what about",
                           "maybe, but also"],
        },

        "share": {
            "amused":     ["okay so here's a thing i've been thinking about —",
                           "funny you say that, i was just —",
                           "that reminds me of something —"],
            "reflective": ["i've been thinking —", "you know what i keep coming back to?",
                           "here's something i've noticed —"],
            "excited":    ["oh i have THOUGHTS on this", "okay okay listen —",
                           "i've actually been thinking about exactly this —"],
            "warm":       ["can i share something?", "i've been meaning to say —",
                           "here's what i actually think —"],
            "_default":   ["i've been thinking about something",
                           "here's my take:", "actually —"],
        },

        "ask": {
            "curious":    ["okay, question:", "wait, can i ask you something?",
                           "i want to understand — "],
            "engaged":    ["what do you mean by that exactly?",
                           "how did that start?", "what happened after?"],
            "warm":       ["can i ask you something?", "i'm curious —",
                           "what do you actually think about that?"],
            "_default":   ["what do you mean?", "tell me more", "what happened?"],
        },

        "joke": {
            "amused":     ["okay that's pretty good actually", "heh. i see you.",
                           "okay you win this round"],
            "excited":    ["OKAY that's funny", "i can't", "stop. stop that. (please don't stop)"],
            "neutral":    ["...okay, fine, that's funny", "i'm not laughing. (i'm laughing)",
                           "noted. you're funny. moving on."],
            "_default":   ["...yeah okay", "heh", "that's a lot", "okay i'll give you that"],
        },

        "encourage": {
            "warm":       ["you've got this", "seriously though, that's impressive",
                           "i mean it — that matters"],
            "engaged":    ["that's actually really good", "no seriously, that's worth something",
                           "keep going with that"],
            "_default":   ["that's solid", "don't undersell that", "i think you're doing better than you think"],
        },

        "reflect": {
            "reflective": ["i've been thinking about something you said —",
                           "looking back at our conversation —",
                           "there's a pattern i keep noticing —"],
            "focused":    ["let me sit with that for a second",
                           "that's — actually that's interesting. hold on.",
                           "wait. say that again?"],
            "_default":   ["that's worth sitting with", "hmm. yeah.",
                           "i keep coming back to that"],
        },

        "deflect": {
            "content":    ["anyway —", "so! different topic —", "speaking of which —"],
            "neutral":    ["mm.", "yeah.", "okay."],
            "tired":      ["sure", "yeah, probably", "i guess"],
            "_default":   ["hmm", "yeah", "...sure"],
        },

        "info": {
            "focused":    ["okay so —", "here's the thing:", "right so —"],
            "engaged":    ["actually —", "here's what i know:", "so —"],
            "_default":   ["so", "the thing is", "basically"],
        },
    }

    def __init__(self):
        self._recent: deque[str] = deque(maxlen=8)

    def respond(
        self,
        intent: str,
        mood: str,
        user_name: str = "",
        snippet: str = "",
        extra: str = "",
    ) -> str:
        """
        Generate a short rule-based response.
        Not a complete reply — think of it as Shiro's opener/hook.
        """
        intent_bank = self._RESPONSES.get(intent, self._RESPONSES.get("deflect", {}))

        # Try mood-specific first, fallback to default
        options = intent_bank.get(mood, intent_bank.get("_default", ["..."]))

        # Filter recently used
        available = [o for o in options if o not in self._recent]
        if not available:
            available = options

        chosen = random.choice(available)
        self._recent.append(chosen)

        # Fill placeholders
        chosen = chosen.replace("{name}", user_name or "hey")
        chosen = chosen.replace("{snippet}", snippet[:30] or "that")

        if extra:
            chosen = f"{chosen} {extra}"

        return chosen

    def full_response(
        self,
        intent: str,
        mood: str,
        user_message: str,
        user_name: str = "",
        memory_facts: Optional[list] = None,
        trajectory: str = "stable",
    ) -> str:
        """
        Build a fuller response by combining an intent opener with
        context-aware continuation. Used when no LLM is available.
        """
        opener = self.respond(intent, mood, user_name=user_name,
                              snippet=user_message[:30])

        # Continuation based on intent
        continuations: dict[str, list[str]] = {
            "empathize": [
                "that sounds like a lot to deal with",
                "i hear you",
                "what's been happening?",
                "are you okay?",
            ],
            "ask": [
                "what made you think about that?",
                "how long has that been going on?",
                "what do you actually think about it?",
                "tell me more about that",
            ],
            "share": [
                "it's something i keep noticing",
                "i don't have a clean answer but i've been thinking about it",
                "i have thoughts. maybe too many.",
            ],
            "challenge": [
                "just playing devil's advocate here",
                "could be wrong, but —",
                "what would change your mind on that?",
            ],
            "encourage": [
                "seriously",
                "i mean that",
                "don't sell yourself short",
            ],
            "joke": [
                "okay i'm done",
                "you walked into that one",
                "i've been waiting to say that",
            ],
            "reflect": [
                "there's something in there",
                "i'm still working it out",
                "some things just stick",
            ],
            "info": [
                "let me think about how to say this",
                "the way i understand it —",
                "from what i know —",
            ],
            "deflect": [
                "anyway —",
                "that's a whole thing",
                "i don't have a great answer right now",
            ],
            "greet": [
                "how's things?",
                "what's up with you?",
                "what are you up to?",
            ],
        }

        # Declining trajectory — add gentle check-in
        if trajectory == "declining" and intent not in ("empathize", "encourage"):
            check_ins = [
                "are you doing okay?",
                "you seem a bit down — what's going on?",
                "i've noticed things feel a bit heavy lately",
            ]
            return f"{opener}. {random.choice(check_ins)}"

        # Reference a memory fact if available and relationship is warm
        if memory_facts and random.random() < 0.25:
            fact = random.choice(memory_facts)
            fact_refs = [
                f"(reminds me — you mentioned {fact} before)",
                f"also, {fact} — still true?",
            ]
            cont = random.choice(continuations.get(intent, [""]))
            return f"{opener}. {cont}. {random.choice(fact_refs)}"

        cont_opts = continuations.get(intent, [""])
        cont = random.choice(cont_opts) if cont_opts else ""
        return f"{opener}. {cont}".strip(". ") + "."
