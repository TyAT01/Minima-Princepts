"""
SelfAwareness v4 — Shiro's perception of herself and her environment.

New in v4:
  - EventBus integration: publishes emotion_detected, topic_detected,
    relationship_changed, user_entered, user_spoke events
  - RelationshipTier: named arc (stranger/acquaintance/friend/close)
    with tier-change events
  - TimePattern: records visit hours, provides timing observations
  - Sentiment trajectory recording forwarded to SentimentTrajectory
  - Mood contagion scaling by relationship depth
  - Per-message emotion strength (not just cumulative)
"""

import time
import re
import datetime
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING
from collections import Counter, deque, deque

if TYPE_CHECKING:
    from .events import EventBus
    from .intent import RelationshipTier, TimePattern, SentimentTrajectory


# ─────────────────────────────────────────────────────────────
#  Emotion inference
# ─────────────────────────────────────────────────────────────

_EMOTION_SIGNALS: list[tuple[str, re.Pattern, float]] = [
    ("happy",     re.compile(r"\b(happy|glad|yay|woohoo|great|wonderful|love|😊|😄|🥰|❤️)\b", re.I), 1.0),
    ("humor",     re.compile(r"\b(lol|lmao|haha|hehe|😂|🤣|💀|xd|lmfao)\b", re.I), 1.0),
    # Sarcasm: contradiction signals, "wow so helpful", "yeah right", exaggerated praise
    ("sarcasm",   re.compile(
        r"\b(yeah right|sure sure|oh wow|oh great|totally|sooo helpful|gee thanks|"
        r"oh obviously|of course you did|great job|wow amazing|genius)\b"
        r"|(\bjust kidding\b|\bjk\b|\bkidding\b|\bi'm kidding\b)"
        r"|(/s\b)"  # reddit sarcasm marker
        r"|(🙄|😒|😑|🤨)", re.I), 0.9),
    # Teasing / playful ribbing — affectionate but challenging
    ("teasing",   re.compile(
        r"\b(goob|goober|goofy|dork|nerd|dummy|doofus|silly|you're the worst|"
        r"smh|facepalm|oh brother|oh please|suuure|okaaay|riight|pfft|pff)\b"
        r"|(🙃|😜|😝|😏|😤)", re.I), 0.8),
    # Endearment — affectionate nicknames or compliments disguised as insults
    ("endearment", re.compile(
        r"\b(goob|goober|dummy|dork|little|sweetie|babe|buddy|pal|foxy|fox girl|"
        r"term of endearment|not an insult|affectionate)\b", re.I), 0.7),
    ("excited",   re.compile(r"[!]{2,}|\b(omg|wow|whoa|holy|yesss|hyped)\b", re.I), 0.9),
    ("sad",       re.compile(r"\b(sad|depressed|unhappy|miserable|crying)\b|😢|😭", re.I), 1.0),
    ("angry",     re.compile(r"\b(angry|mad|furious|pissed|wtf|ugh|😤|😠|🤬)\b", re.I), 1.0),
    ("anxious",   re.compile(r"\b(anxious|nervous|worried|stress|scared|😰|😬)\b", re.I), 0.8),
    ("tired",     re.compile(r"\b(tired|exhausted|sleepy|zzz|yawn|drained|😴|💤)\b", re.I), 0.9),
    ("confused",  re.compile(r"\b(confused|lost|huh|idk|dunno|🤔|😕)\b", re.I), 0.7),
    ("agreeable", re.compile(r"\b(yes|yeah|yep|totally|exactly|agreed|true|right|100%)\b", re.I), 0.6),
    ("negative",  re.compile(r"\b(no|nope|nah|never|won't|can't|disagree)\b", re.I), 0.6),
    ("curious",   re.compile(r"\?{1,}|\b(wonder|curious|how|why|what if|tell me)\b", re.I), 0.7),
    ("thinking",  re.compile(r"\b(hmm+|hm+|idk|dunno|maybe|probably|suppose|kinda)\b", re.I), 0.6),
    ("afk",       re.compile(r"\b(afk|brb|be right back|going|leaving|step away|back soon)\b", re.I), 1.0),
    ("greeting",  re.compile(r"^\s*(hi+|hey+|hello|sup|yo|heya|hiya|howdy|good morning|gm)\b", re.I), 1.0),
]

_TOPIC_KEYWORDS: dict[str, list[str]] = {
    "tech":     ["code", "coding", "programming", "software", "python", "ai", "machine learning",
                 "neural", "model", "llm", "gpu", "server", "api", "debug", "bug", "repo", "git"],
    "gaming":   ["game", "gaming", "steam", "discord", "fps", "rpg", "mmo", "minecraft",
                 "valorant", "league", "twitch", "stream", "controller", "quest"],
    "music":    ["music", "song", "playlist", "band", "album", "listening", "spotify",
                 "concert", "lyrics", "genre", "beats", "track", "artist"],
    "anime":    ["anime", "manga", "episode", "character", "watch", "season", "isekai",
                 "shonen", "crunchyroll", "sub", "dub", "arc"],
    "creative": ["art", "drawing", "writing", "design", "creating", "novel", "story",
                 "illustration", "oc", "character", "worldbuilding", "fanfic"],
    "irl":      ["work", "school", "uni", "family", "outside", "tired", "sleep", "job",
                 "boss", "class", "exam", "weekend", "life", "real life"],
    "food":     ["food", "eat", "hungry", "cooking", "recipe", "restaurant", "coffee",
                 "drink", "snack", "meal", "baking", "kitchen"],
    "feelings": ["feel", "feeling", "emotion", "anxiety", "depression", "happy", "sad",
                 "stressed", "lonely", "excited", "mental health"],
    "philosophy": ["meaning", "consciousness", "existence", "reality", "truth", "ethics",
                   "free will", "identity", "purpose", "philosophy"],
    "science":  ["science", "physics", "biology", "chemistry", "math", "space", "universe",
                 "research", "study", "theory", "experiment"],
}

_HIGH_ENERGY  = re.compile(r"[!?]{2,}|[A-Z]{3,}|\b(omg|wtf|yoooo|letsgo|hype)\b", re.I)
_COMPLEX_TOPIC = re.compile(
    r"\b(because|therefore|however|although|which means|the reason|i think that|"
    r"philosophically|technically|essentially|fundamentally|on the other hand)\b", re.I
)

# Vocabulary to mirror — casual slang the user uses that Shiro might adopt
_MIRROR_VOCAB = re.compile(
    r"\b(lowkey|highkey|literally|vibe|slay|fr|no cap|bussin|based|sus|bruh|bro|ngl|tbh|"
    r"imo|irl|afaik|fwiw|smh|rn|imo|lmk|hmu|nvm|gonna|wanna|gotta|kinda|sorta)\b", re.I
)


# ─────────────────────────────────────────────────────────────
#  User Profile
# ─────────────────────────────────────────────────────────────

@dataclass
class UserProfile:
    user_id: str
    name: str = ""

    # Presence
    present: bool = False
    entered_without_speaking: bool = False
    enter_ts: float = 0.0
    last_seen: float = 0.0
    last_spoke_ts: float = 0.0
    total_time_present: float = 0.0

    # Activity
    message_count: int = 0
    session_message_count: int = 0
    session_count: int = 0
    recent_messages: list = field(default_factory=list)

    # Learned traits
    relationship_score: float = 0.0    # 0–100
    trust_score: float = 50.0          # 0–100
    energy_level: float = 0.5          # 0=low, 1=high
    conversation_depth: float = 0.0    # 0=shallow, 1=deep (complex sentences, philosophy, etc.)
    patterns: dict = field(default_factory=dict)
    emotions_seen: dict = field(default_factory=dict)
    known_topics: list = field(default_factory=list)
    topic_counts: dict = field(default_factory=dict)
    quirks: list = field(default_factory=list)
    mirror_vocab: list = field(default_factory=list)   # slang/phrases to mirror

    # Absence tracking
    last_absent_duration: float = 0.0
    absence_count: int = 0

    def silence_duration(self) -> float:
        ref = self.last_spoke_ts if self.last_spoke_ts else self.enter_ts
        return time.time() - ref if ref else 0.0

    def dominant_emotion(self) -> Optional[str]:
        if not self.emotions_seen:
            return None
        return max(self.emotions_seen, key=self.emotions_seen.get)

    def top_topics(self, n: int = 3) -> list[str]:
        return sorted(self.topic_counts, key=self.topic_counts.get, reverse=True)[:n]

    def is_familiar(self) -> bool:
        """True if Shiro has had meaningful interaction with this person."""
        return self.relationship_score >= 10.0 or self.message_count >= 15

    def relationship_tier(self) -> str:
        """Named relationship arc: stranger | acquaintance | friend | close."""
        from .intent import RelationshipTier
        return RelationshipTier.from_score(self.relationship_score)

    def current_emotion_strength(self) -> dict:
        """Emotions from the most recent message only (not cumulative)."""
        if self.recent_messages:
            return self.recent_messages[-1].get("emotions", {})
        return {}

    def absence_description(self) -> str:
        d = self.last_absent_duration
        if d < 60:           return "just stepped away briefly"
        if d < 3600:         return f"gone for {int(d/60)} minutes"
        if d < 86400:        return f"gone for {int(d/3600)} hours"
        return f"gone for {int(d/86400)} days"

    def to_dict(self) -> dict:
        return {
            "user_id":            self.user_id,
            "name":               self.name,
            "message_count":      self.message_count,
            "session_count":      self.session_count,
            "relationship_score": round(self.relationship_score, 2),
            "trust_score":        round(self.trust_score, 2),
            "energy_level":       round(self.energy_level, 3),
            "conversation_depth": round(self.conversation_depth, 3),
            "patterns":           self.patterns,
            "emotions_seen":      self.emotions_seen,
            "known_topics":       self.known_topics,
            "topic_counts":       self.topic_counts,
            "quirks":             self.quirks,
            "mirror_vocab":       self.mirror_vocab,
            "absence_count":      self.absence_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "UserProfile":
        p = cls(user_id=d["user_id"], name=d.get("name", d["user_id"]))
        for attr in ("message_count", "session_count", "relationship_score", "trust_score",
                     "energy_level", "conversation_depth", "patterns", "emotions_seen",
                     "known_topics", "topic_counts", "quirks", "mirror_vocab", "absence_count"):
            if attr in d:
                setattr(p, attr, d[attr])
        return p



# ─────────────────────────────────────────────────────────────
#  BehaviorProfile — cross-session behavioral patterns per user
# ─────────────────────────────────────────────────────────────

class BehaviorProfile:
    """
    Tracks behavioral patterns for a user across sessions.
    Learns things like: do they open with questions? go quiet when stressed?
    prefer short or long conversations? return quickly?
    """

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.opener_counts: dict[str, int] = {}
        self.opener_styles: list[str] = []
        self.session_lengths: list[int] = []
        self.return_speeds: list[float] = []
        self.burst_preference: float = 0.5
        self.prefers_depth: float = 0.0
        self._session_count: int = 0
        self._current_session_msgs: int = 0

    def record_opener(self, text: str, emotions: dict):
        if emotions.get("greeting", 0) > 0.5:
            style = "greeting"
        elif "?" in text or emotions.get("curious", 0) > 0.5:
            style = "question"
        elif emotions.get("sad", 0) > 0.4 or emotions.get("anxious", 0) > 0.4:
            style = "vent"
        else:
            style = "statement"
        self.opener_styles.append(style)
        self.opener_counts[style] = self.opener_counts.get(style, 0) + 1
        if len(self.opener_styles) > 30:
            self.opener_styles = self.opener_styles[-20:]

    def record_session_end(self):
        if self._current_session_msgs > 0:
            self.session_lengths.append(self._current_session_msgs)
            self._session_count += 1
            self._current_session_msgs = 0
        if len(self.session_lengths) > 30:
            self.session_lengths = self.session_lengths[-20:]

    def record_message(self, depth: float = 0.0, burst: float = 0.0):
        self._current_session_msgs += 1
        a = 0.1
        self.prefers_depth += a * (depth - self.prefers_depth)
        self.burst_preference += a * (burst - self.burst_preference)

    def record_return(self, hours_absent: float):
        self.return_speeds.append(hours_absent)
        if len(self.return_speeds) > 20:
            self.return_speeds = self.return_speeds[-15:]

    def typical_opener(self) -> Optional[str]:
        if not self.opener_counts or sum(self.opener_counts.values()) < 3:
            return None
        return max(self.opener_counts, key=self.opener_counts.get)

    def avg_session_length(self) -> float:
        return sum(self.session_lengths) / len(self.session_lengths) if self.session_lengths else 0.0

    def avg_return_hours(self) -> float:
        return sum(self.return_speeds) / len(self.return_speeds) if self.return_speeds else 0.0

    def describe(self) -> str:
        parts = []
        opener = self.typical_opener()
        if opener and sum(self.opener_counts.values()) >= 3:
            parts.append(f"usually opens with {opener}s")
        avg_len = self.avg_session_length()
        if avg_len > 0 and self._session_count >= 2:
            if avg_len < 5:
                parts.append("tends toward short sessions")
            elif avg_len > 20:
                parts.append("likes long conversations")
        if self.prefers_depth > 0.6 and self._current_session_msgs >= 5:
            parts.append("goes deep on topics")
        if self.burst_preference > 0.7:
            parts.append("sends messages in bursts")
        avg_ret = self.avg_return_hours()
        if avg_ret > 0 and len(self.return_speeds) >= 2:
            if avg_ret < 2:
                parts.append("comes back frequently")
            elif avg_ret > 48:
                parts.append("visits less often")
        return "; ".join(parts) if parts else ""

    def export(self) -> dict:
        return {
            "opener_counts":   self.opener_counts,
            "session_lengths": self.session_lengths[-10:],
            "return_speeds":   self.return_speeds[-10:],
            "burst_preference": round(self.burst_preference, 3),
            "prefers_depth":   round(self.prefers_depth, 3),
            "session_count":   self._session_count,
        }

    @classmethod
    def from_dict(cls, d: dict, user_id: str) -> "BehaviorProfile":
        bp = cls(user_id)
        bp.opener_counts    = d.get("opener_counts", {})
        bp.session_lengths  = d.get("session_lengths", [])
        bp.return_speeds    = d.get("return_speeds", [])
        bp.burst_preference = d.get("burst_preference", 0.5)
        bp.prefers_depth    = d.get("prefers_depth", 0.0)
        bp._session_count   = d.get("session_count", 0)
        return bp

# ─────────────────────────────────────────────────────────────
#  SelfAwareness
# ─────────────────────────────────────────────────────────────



# ─────────────────────────────────────────────────────────────
#  UserIntentModel — Theory of Mind: what does the user WANT?
# ─────────────────────────────────────────────────────────────

# Conversation goals Shiro can infer from message patterns
_INTENT_SIGNALS: list[tuple[str, re.Pattern, float]] = [
    ("wants_info",     re.compile(r"\b(what|how|why|when|where|who|can you|tell me|explain|help me|does)\b", re.I), 0.9),
    ("wants_to_vent",  re.compile(r"\b(so frustrated|i hate|ugh|this sucks|i cant|i'm tired of|nobody|always|never)\b", re.I), 0.85),
    ("wants_company",  re.compile(r"\b(bored|nothing to do|just wanted|hey|hi|anyone|talk to me|what's up)\b", re.I), 0.8),
    ("wants_to_play",  re.compile(r"\b(game|play|vs|challenge|bet|try|fight me|beat you|let's|wanna)\b", re.I), 0.8),
    ("wants_feedback", re.compile(r"\b(what do you think|thoughts|opinion|rate|review|is this good|how does this)\b", re.I), 0.85),
    ("wants_to_share", re.compile(r"\b(check this|look|omg|guess what|you won't believe|i just|i did|i made|i got)\b", re.I), 0.75),
    ("testing_shiro",  re.compile(r"\b(do you|can you|are you|would you|are you real|prove|pretend|roleplay)\b", re.I), 0.7),
    ("wants_depth",    re.compile(r"\b(do you think|do you feel|what's your view|philosophic|serious|honest|actually)\b", re.I), 0.8),
    ("winding_down",   re.compile(r"\b(gtg|gotta go|brb|ttyl|bye|see you|later|goodnight|gn|heading out|leaving)\b", re.I), 0.95),
]


class UserIntentModel:
    """
    Theory of Mind layer: models what the user wants FROM this conversation.

    Infers the user's conversational goal from message patterns over a rolling
    window, then computes a confidence score. Used by SelfAwareness to inject a
    concise 'what they want' hint into the system prompt so Shiro responds to
    INTENT, not just surface words.

    Tracks:
      current_intent   — strongest inferred goal right now
      intent_history   — rolling deque of recent intents
      confidence       — 0.0–1.0 certainty about current_intent
      satisfaction_score — did Shiro meet the inferred need? (updated externally)
    """

    WINDOW = 5

    def __init__(self, user_id: str):
        self.user_id            = user_id
        self.current_intent: str = "unknown"
        self.confidence: float  = 0.0
        self.intent_history: deque = deque(maxlen=self.WINDOW)
        self.satisfaction_score: float = 0.5
        self._raw_scores: dict  = {}

    def update(self, text: str) -> tuple[str, float]:
        """Infer intent from text. Returns (intent_name, confidence)."""
        scores: dict[str, float] = {}
        for label, pattern, weight in _INTENT_SIGNALS:
            matches = len(pattern.findall(text))
            if matches:
                scores[label] = min(1.0, weight * (1 + (matches - 1) * 0.2))
        self._raw_scores = scores
        if not scores:
            self.current_intent = "general_chat"
            self.confidence = 0.3
        else:
            best = max(scores, key=scores.get)
            self.current_intent = best
            self.confidence = scores[best]
        self.intent_history.append(self.current_intent)
        return self.current_intent, self.confidence

    def dominant_intent(self) -> str:
        """Most frequent intent across recent window."""
        if not self.intent_history:
            return "unknown"
        counts: dict[str, int] = {}
        for i in self.intent_history:
            counts[i] = counts.get(i, 0) + 1
        return max(counts, key=counts.get)

    def is_shifting(self) -> bool:
        """True if last two intents differ — conversational goal is in flux."""
        h = list(self.intent_history)
        return len(h) >= 2 and h[-1] != h[-2]

    def record_satisfaction(self, shiro_response: str) -> None:
        """
        Heuristic: estimate whether Shiro met the user's inferred intent.
        Updates satisfaction_score with an EMA to avoid single-response overfit.
        """
        resp_lower = shiro_response.lower()
        intent = self.current_intent
        met = 0.5

        if intent == "wants_info":
            met = 0.8 if len(shiro_response) > 80 else 0.4
        elif intent == "wants_to_vent":
            ack = ["i understand", "that sounds", "that must", "i hear", "that's hard", "sounds like"]
            met = 0.85 if any(w in resp_lower for w in ack) else 0.35
        elif intent == "wants_to_play":
            met = 0.8 if any(w in resp_lower for w in ["game", "play", "challenge", "move", "round"]) else 0.4
        elif intent == "wants_feedback":
            met = 0.8 if len(shiro_response) > 50 else 0.5
        elif intent == "wants_company":
            met = 0.75
        elif intent == "winding_down":
            farewell = ["bye", "see you", "take care", "later", "good", "night", "soon"]
            met = 0.9 if any(w in resp_lower for w in farewell) else 0.5

        self.satisfaction_score = self.satisfaction_score * 0.7 + met * 0.3

    def prompt_hint(self) -> str:
        """Natural-language hint for the LLM about this user's current goal."""
        if self.current_intent in ("unknown", "general_chat") or self.confidence < 0.5:
            return ""
        _hints = {
            "wants_info":     "They seem to want information or an explanation — be clear and direct.",
            "wants_to_vent":  "They may be venting. Acknowledge feelings first; don't rush to fix.",
            "wants_company":  "They want connection more than content — be warm and present.",
            "wants_to_play":  "They're in a playful or competitive mood — match the energy.",
            "wants_feedback": "They want your honest take — give a real opinion.",
            "wants_to_share": "They're excited to share something — receive it with genuine interest.",
            "testing_shiro":  "They may be testing or probing you — be honest and grounded.",
            "wants_depth":    "They want a real, thoughtful exchange — not a surface reply.",
            "winding_down":   "They're wrapping up — keep your reply brief and warm.",
        }
        base = _hints.get(self.current_intent, "")
        if not base:
            return ""
        shift = " Their goal seems to be shifting." if self.is_shifting() else ""
        conf  = f" [confidence: {self.confidence:.0%}]" if self.confidence < 0.7 else ""
        return base + shift + conf

    def export(self) -> dict:
        return {
            "current_intent":    self.current_intent,
            "confidence":        round(self.confidence, 3),
            "dominant_intent":   self.dominant_intent(),
            "satisfaction_score": round(self.satisfaction_score, 3),
        }


class SelfAwareness:
    """Shiro's perception of herself and her environment. v4."""

    _TOD = [
        (5,  "early morning"), (9,  "morning"), (12, "midday"),
        (14, "afternoon"),     (18, "evening"), (22, "night"), (24, "late night"),
    ]

    def __init__(
        self,
        name: str = "Shiro",
        platform: str = "chat",
        room_name: Optional[str] = None,
        can_see: bool = False,
        can_hear: bool = False,
        silence_threshold_seconds: float = 8.0,
        event_bus: Optional["EventBus"] = None,
    ):
        self.name = name
        self.platform = platform
        self.room_name = room_name
        self.can_see = can_see
        self.can_hear = can_hear
        self.silence_threshold = silence_threshold_seconds
        self.bus = event_bus

        self.users: dict[str, UserProfile] = {}
        self.room_state: str = "empty"
        self.screen_context: Optional[dict] = None

        self.self_knowledge: dict = {
            "conversations_had":        0,
            "total_messages_received":  0,
            "topics_encountered":       set(),
            "people_met":               set(),
            "boot_ts":                  time.time(),
        }

        self._recent_events: list[dict] = []
        self._last_humor_ts: float = 0.0
        self._last_high_energy_ts: float = 0.0
        self._last_complex_ts: float = 0.0
        self._last_sarcasm_ts: float = 0.0
        self._last_teasing_ts: float = 0.0

        # Per-user humor/sarcasm learning — tracks style over time so Shiro
        # can adapt how she responds to jokes, teasing, and endearment.
        # Stored as: { user_id: { "sarcasm_count": int, "teasing_count": int,
        #   "endearment_words": set, "humor_style": str, "learned_nicknames": set } }
        self._humor_profiles: dict[str, dict] = {}

        # Cross-session behavioral profiles
        self.behavior_profiles: dict[str, "BehaviorProfile"] = {}

        # Theory of Mind — per-user conversational intent models
        self.intent_models: dict[str, UserIntentModel] = {}

    # ── User lifecycle ───────────────────────────────────────────

    def user_entered(self, user_id: str, name: str = "", **meta) -> UserProfile:
        now = time.time()
        if user_id not in self.users:
            self.users[user_id] = UserProfile(user_id=user_id, name=name or user_id)

        p = self.users[user_id]
        if p.last_seen and not p.present:
            p.last_absent_duration = now - p.last_seen
            p.absence_count += 1
        if p.message_count > 0:
            p.session_count += 1

        p.present = True
        p.name = name or p.name or user_id
        p.enter_ts = now
        p.last_seen = now
        p.entered_without_speaking = True
        p.session_message_count = 0

        self.self_knowledge["people_met"].add(user_id)
        self._push_event("entered", user_id=user_id, name=p.name)
        self._update_room_state()

        if self.bus:
            self.bus.emit("user_entered",
                          user_id=user_id, name=p.name,
                          is_returning=(p.absence_count > 0),
                          tier=p.relationship_tier())
        return p

    def user_left(self, user_id: str):
        now = time.time()
        p = self.users.get(user_id)
        if p and p.present:
            p.total_time_present += now - (p.enter_ts or now)
            p.present = False
            p.last_seen = now
        self._push_event("left", user_id=user_id)
        self._update_room_state()

    def on_shiro_response(self, user_id: str, shiro_text: str) -> None:
        """
        Call after Shiro generates a response.
        Updates the intent satisfaction model for this user.
        """
        intent_model = self.intent_models.get(user_id)
        if intent_model:
            intent_model.record_satisfaction(shiro_text)

    def user_spoke(self, user_id: str, text: str, name: str = "") -> UserProfile:
        now = time.time()
        if user_id not in self.users:
            self.users[user_id] = UserProfile(user_id=user_id, name=name or user_id)
        p = self.users[user_id]

        p.present = True
        p.entered_without_speaking = False
        p.last_spoke_ts = now
        p.last_seen = now
        p.message_count += 1
        p.session_message_count += 1
        if name:
            p.name = name

        emotions = self._infer_emotions(text)
        msg_entry = {"text": text, "ts": now, "emotions": emotions}
        p.recent_messages.append(msg_entry)
        if len(p.recent_messages) > 30:
            p.recent_messages.pop(0)

        for emo, score in emotions.items():
            p.emotions_seen[emo] = p.emotions_seen.get(emo, 0) + score

        # Energy EMA
        is_high = bool(_HIGH_ENERGY.search(text))
        p.energy_level += 0.15 * (float(is_high) - p.energy_level)

        # Conversation depth EMA (complex = longer sentences + complex words)
        is_complex = bool(_COMPLEX_TOPIC.search(text))
        depth_signal = 1.0 if is_complex else (0.5 if len(text) > 80 else 0.0)
        p.conversation_depth += 0.1 * (depth_signal - p.conversation_depth)

        self._detect_patterns(p, text)
        self._detect_topics(p, text)
        self._detect_quirks(p, text)
        self._detect_mirror_vocab(p, text)

        # ── Theory of Mind: infer conversational intent ──────────────────────
        intent_model = self.intent_models.setdefault(user_id, UserIntentModel(user_id))
        intent_model.update(text)

        if "humor" in emotions:
            self._last_humor_ts = now
        if "sarcasm" in emotions:
            self._last_sarcasm_ts = now
        if "teasing" in emotions:
            self._last_teasing_ts = now
        if is_high:
            self._last_high_energy_ts = now
        if is_complex:
            self._last_complex_ts = now

        # ── Update per-user humor/sarcasm learning profile ───────────────
        hp = self._humor_profiles.setdefault(user_id, {
            "sarcasm_count": 0, "teasing_count": 0, "humor_count": 0,
            "endearment_words": [], "learned_nicknames": [], "humor_style": "unknown"
        })
        if "sarcasm" in emotions:
            hp["sarcasm_count"] += 1
        if "teasing" in emotions:
            hp["teasing_count"] += 1
        if "humor" in emotions:
            hp["humor_count"] += 1
        if "endearment" in emotions:
            # Extract specific endearment words used so Shiro learns them
            import re as _re
            endearment_re = _re.compile(
                r"\b(goob|goober|dummy|dork|foxy|buddy|pal|babe|sweetie|silly)\b", _re.I)
            found = endearment_re.findall(text)
            for w in found:
                w_lower = w.lower()
                if w_lower not in hp["endearment_words"]:
                    hp["endearment_words"].append(w_lower)
                if w_lower not in hp["learned_nicknames"]:
                    hp["learned_nicknames"].append(w_lower)

        # Infer dominant humor style after enough samples
        total_humor = hp["sarcasm_count"] + hp["teasing_count"] + hp["humor_count"]
        if total_humor >= 3:
            if hp["sarcasm_count"] > hp["teasing_count"] and hp["sarcasm_count"] > hp["humor_count"]:
                hp["humor_style"] = "sarcastic"
            elif hp["teasing_count"] > hp["sarcasm_count"]:
                hp["humor_style"] = "teasing"
            elif hp["humor_count"] > hp["sarcasm_count"]:
                hp["humor_style"] = "playful"
            else:
                hp["humor_style"] = "mixed"

        old_tier = p.relationship_tier()
        p.relationship_score = min(100.0, p.relationship_score + 0.4)
        if "agreeable" in emotions:
            p.trust_score = min(100.0, p.trust_score + 0.3)
        if depth_signal > 0.5:
            p.relationship_score = min(100.0, p.relationship_score + 0.2)
        new_tier = p.relationship_tier()

        self.self_knowledge["total_messages_received"] += 1
        self._push_event("spoke", user_id=user_id, snippet=text[:40])
        self._update_room_state()

        # EventBus publications
        if self.bus:
            # Publish per-message emotion (not cumulative)
            for emo, strength in emotions.items():
                if strength > 0.3:
                    self.bus.emit("emotion_detected",
                                  user_id=user_id, emotion=emo,
                                  strength=strength, tier=new_tier)
            # Publish topic detections
            for topic in p.known_topics[-3:]:
                self.bus.emit("topic_detected",
                              user_id=user_id, topic=topic,
                              is_new=(p.topic_counts.get(topic, 0) <= 1))
            # Publish user_spoke
            self.bus.emit("user_spoke",
                          user_id=user_id, text=text,
                          emotions=emotions, depth=p.conversation_depth,
                          tier=new_tier)
            # Publish tier change if it happened
            if new_tier != old_tier:
                self.bus.emit("relationship_changed",
                              user_id=user_id, old_tier=old_tier, new_tier=new_tier)

        # Update behavioral profile
        bp = self.behavior_profiles.setdefault(user_id, BehaviorProfile(user_id))
        if p.session_message_count == 1:
            bp.record_opener(text, emotions)
            if p.last_absent_duration > 0:
                bp.record_return(p.last_absent_duration / 3600.0)
        bp.record_message(depth=depth_signal, burst=p.energy_level)

        return p

    # ── Local NLP ────────────────────────────────────────────────

    def _infer_emotions(self, text: str) -> dict[str, float]:
        results: dict[str, float] = {}
        for label, pattern, weight in _EMOTION_SIGNALS:
            matches = len(pattern.findall(text))
            if matches:
                results[label] = min(1.0, weight * (1.0 + (matches - 1) * 0.3))
        return results

    def _detect_patterns(self, p: UserProfile, text: str):
        for label, pattern, _ in _EMOTION_SIGNALS:
            if pattern.search(text):
                p.patterns[label] = p.patterns.get(label, 0) + 1

    def _detect_topics(self, p: UserProfile, text: str):
        lower = text.lower()
        for topic, kws in _TOPIC_KEYWORDS.items():
            hits = sum(1 for kw in kws if kw in lower)
            if hits:
                p.topic_counts[topic] = p.topic_counts.get(topic, 0) + hits
                if topic not in p.known_topics:
                    p.known_topics.append(topic)
                self.self_knowledge["topics_encountered"].add(topic)

    def _detect_quirks(self, p: UserProfile, text: str):
        if text.isupper() and len(text) > 5 and "ALL_CAPS" not in p.quirks:
            p.quirks.append("ALL_CAPS")
        if text.endswith("...") and "trailing_dots" not in p.quirks:
            p.quirks.append("trailing_dots")
        if len(text) > 20 and not re.search(r"[.!?,]", text) and "no_punctuation" not in p.quirks:
            p.quirks.append("no_punctuation")
        if re.search(r"\b(uwu|owo|nya|:3|><)\b", text, re.I) and "uwu_speak" not in p.quirks:
            p.quirks.append("uwu_speak")
        # Detect excessive ellipsis
        if text.count("...") >= 2 and "ellipsis_heavy" not in p.quirks:
            p.quirks.append("ellipsis_heavy")

    def _detect_mirror_vocab(self, p: UserProfile, text: str):
        """Collect vocabulary Shiro might adopt to mirror this user."""
        found = set(m.lower() for m in _MIRROR_VOCAB.findall(text))
        for word in found:
            if word not in p.mirror_vocab:
                p.mirror_vocab.append(word)
                if len(p.mirror_vocab) > 20:
                    p.mirror_vocab.pop(0)

    # ── Queries ──────────────────────────────────────────────────

    def get_silent_present_users(self) -> list[UserProfile]:
        return [
            p for p in self.users.values()
            if p.present and p.entered_without_speaking
            and p.silence_duration() > self.silence_threshold
        ]

    def get_present_users(self) -> list[UserProfile]:
        return [p for p in self.users.values() if p.present]

    def get_focus_user(self) -> Optional[UserProfile]:
        silent = self.get_silent_present_users()
        if silent:
            return silent[0]
        present = self.get_present_users()
        if not present:
            return None
        return max(present, key=lambda p: p.last_spoke_ts or 0)

    def is_humor_detected(self) -> bool:
        return (time.time() - self._last_humor_ts) < 30.0

    def is_sarcasm_detected(self) -> bool:
        return (time.time() - self._last_sarcasm_ts) < 20.0

    def is_teasing_detected(self) -> bool:
        return (time.time() - self._last_teasing_ts) < 20.0

    def get_humor_profile(self, user_id: str) -> dict:
        """Return the learned humor/sarcasm style for a user."""
        return self._humor_profiles.get(user_id, {
            "sarcasm_count": 0, "teasing_count": 0, "humor_count": 0,
            "endearment_words": [], "learned_nicknames": [], "humor_style": "unknown"
        })

    def get_intent_hint(self, user_id: str) -> str:
        """
        Returns a natural-language Theory of Mind hint about what this user wants.
        Injected into the LLM prompt each turn.
        Returns empty string if intent unknown or confidence too low.
        """
        model = self.intent_models.get(user_id)
        return model.prompt_hint() if model else ""

    def get_intent_model(self, user_id: str) -> Optional["UserIntentModel"]:
        """Return the UserIntentModel for this user, or None if not seen yet."""
        return self.intent_models.get(user_id)

    def humor_context_hint(self, user_id: str) -> str:
        """Return a natural-language hint for the LLM about this user's humor style.
        Injected into the system prompt so Shiro can respond appropriately."""
        hp = self.get_humor_profile(user_id)
        style = hp.get("humor_style", "unknown")
        nicknames = hp.get("learned_nicknames", [])
        parts = []
        if style == "sarcastic":
            parts.append("Tyler often uses sarcasm — take it with a grain of salt, match the dry wit")
        elif style == "teasing":
            parts.append("Tyler likes to tease affectionately — give it right back, don't be defensive")
        elif style == "playful":
            parts.append("Tyler has a playful sense of humor — be loose and fun in return")
        elif style == "mixed":
            parts.append("Tyler mixes sarcasm and playful teasing — read the room each time")
        if nicknames:
            parts.append(f"Tyler uses these as terms of affection, not insults: {', '.join(nicknames)}")
        if self.is_sarcasm_detected():
            parts.append("Tyler was JUST being sarcastic — respond to the actual meaning, not the surface words")
        if self.is_teasing_detected():
            parts.append("Tyler is teasing right now — play along, don't take it literally")
        return ". ".join(parts) if parts else ""

    def is_high_energy(self) -> bool:
        return (time.time() - self._last_high_energy_ts) < 20.0

    def is_complex_topic(self) -> bool:
        return (time.time() - self._last_complex_ts) < 60.0

    def time_of_day(self) -> str:
        hour = datetime.datetime.now().hour
        for threshold, label in self._TOD:
            if hour < threshold:
                return label
        return "late night"

    def get_user_pattern_description(self, user_id: str) -> str:
        p = self.users.get(user_id)
        if not p or not p.patterns:
            return "do their thing"
        top = max(p.patterns, key=p.patterns.get)
        descs = {
            "curious":    "ask a lot of questions",
            "humor":      "make jokes",
            "agreeable":  "agree with most things",
            "negative":   "push back often",
            "thinking":   "think out loud",
            "excited":    "get excited easily",
            "greeting":   "always open with a hello",
            "afk":        "disappear sometimes",
            "tired":      "often seem tired",
            "sad":        "go quiet sometimes",
        }
        return descs.get(top, f"often express {top}")

    def get_uptime_description(self) -> str:
        secs = time.time() - self.self_knowledge["boot_ts"]
        if secs < 60:       return "just started"
        if secs < 3600:     return f"{int(secs/60)} minutes online"
        if secs < 86400:    return f"{int(secs/3600)} hours online"
        return f"{int(secs/86400)} days online"

    def describe_session(self, user_id: str) -> str:
        """
        Describe the current session with a user for LLM context.
        """
        p = self.users.get(user_id)
        if not p:
            return ""
        parts = []
        if p.absence_count > 0:
            parts.append(f"returning user ({p.absence_description()} last time)")
        if p.message_count > 0:
            parts.append(f"{p.message_count} messages total across {p.session_count} sessions")
        if p.is_familiar():
            parts.append(f"relationship score: {p.relationship_score:.0f}/100")
        if p.conversation_depth > 0.5:
            parts.append("tends to go deep in conversation")
        top = p.top_topics(2)
        if top:
            parts.append(f"usually talks about: {', '.join(top)}")
        return "; ".join(parts) if parts else "new user"

    # ── Room state ───────────────────────────────────────────────

    def _update_room_state(self):
        present = self.get_present_users()
        if not present:
            self.room_state = "empty"
            return
        active = any(
            p.last_spoke_ts and (time.time() - p.last_spoke_ts) < 120
            for p in present
        )
        self.room_state = "active" if active else "occupied"

    def _push_event(self, event: str, **kw):
        self._recent_events.append({"event": event, "ts": time.time(), **kw})
        if len(self._recent_events) > 50:
            self._recent_events = self._recent_events[-40:]

    # ── Descriptions ─────────────────────────────────────────────

    def describe_environment(self) -> str:
        tod = self.time_of_day()
        room = self.room_name or "a chat room"
        parts = [f"i'm in {room} on {self.platform}, {tod}"]
        if self.can_see:
            parts.append("i can see the screen")
        if self.can_hear:
            parts.append("i can hear audio")
        present = self.get_present_users()
        if present:
            names = ", ".join(p.name for p in present)
            verb = "are" if len(present) > 1 else "is"
            parts.append(f"{names} {verb} here")
        else:
            parts.append("the room is empty")
        return ". ".join(parts)

    def describe_self(self) -> str:
        sk = self.self_knowledge
        topics = ", ".join(list(sk["topics_encountered"])[:5]) if sk["topics_encountered"] else "nothing yet"
        uptime = self.get_uptime_description()
        return (
            f"i'm {self.name}. {uptime}. "
            f"i've had {sk['conversations_had']} conversations, "
            f"received {sk['total_messages_received']} messages, "
            f"talked about: {topics}"
        )

    def update_screen_context(self, data: dict):
        self.screen_context = data

    # ── Persistence ──────────────────────────────────────────────

    def export(self) -> dict:
        sk = self.self_knowledge
        return {
            "users": {uid: p.to_dict() for uid, p in self.users.items()},
            "self_knowledge": {
                **{k: v for k, v in sk.items() if k not in ("topics_encountered", "people_met", "boot_ts")},
                "topics_encountered": list(sk["topics_encountered"]),
                "people_met":         list(sk["people_met"]),
            },
            "behavior_profiles": {
                uid: bp.export()
                for uid, bp in self.behavior_profiles.items()
            },
            "intent_models": {
                uid: m.export()
                for uid, m in self.intent_models.items()
            },
        }

    def import_data(self, data: dict):
        for uid, ud in data.get("users", {}).items():
            self.users[uid] = UserProfile.from_dict(ud)
        sk = data.get("self_knowledge", {})
        self.self_knowledge["conversations_had"]       = sk.get("conversations_had", 0)
        self.self_knowledge["total_messages_received"] = sk.get("total_messages_received", 0)
        self.self_knowledge["topics_encountered"]      = set(sk.get("topics_encountered", []))
        self.self_knowledge["people_met"]              = set(sk.get("people_met", []))
        for uid, d in data.get("behavior_profiles", {}).items():
            self.behavior_profiles[uid] = BehaviorProfile.from_dict(d, uid)

    def get_behavior_description(self, user_id: str) -> str:
        """Get a natural-language behavioral pattern description for this user."""
        bp = self.behavior_profiles.get(user_id)
        return bp.describe() if bp else ""