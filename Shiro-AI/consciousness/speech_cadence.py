"""
SpeechCadence v4 — Adaptive rhythm, tempo, style, and autonomous timing engine.

New in v4:
  - ConversationPace: tracks per-user response latency and typing patterns
  - Dynamic autonomous hold window: Shiro learns how long to wait after a user
    message before firing an autonomous thought (adapts per user, not hardcoded)
  - Group-aware: hold window scales with user count so Shiro isn't silenced
    in busy group chats
  - Typing + processing state integration: single source of truth
"""

import re
import time
import random
import threading
from dataclasses import dataclass, field
from typing import Optional, Dict, List




# ─────────────────────────────────────────────────────────────
#  ConversationPace — models how fast users reply and how long
#  Shiro should hold an autonomous thought before firing it.
# ─────────────────────────────────────────────────────────────

class ConversationPace:
    """
    Tracks per-user response latency (time from Shiro's last message to
    user's next message) using exponential moving average.

    Used to compute a smart autonomous-hold window:
      - If a user typically replies within 10s → hold autonomous thoughts for ~12s
      - If they reply within 60s → hold for ~70s
      - Caps at 90s so Shiro still speaks up if the user goes quiet
      - In groups, window is reduced proportionally so Shiro isn't silenced
        forever by one person always typing.
    """

    # Absolute limits
    MIN_HOLD_S: float = 8.0    # never fire in < 8s after any user activity
    MAX_HOLD_S: float = 90.0   # never suppress for more than 90s
    BOOT_HOLD_S: float = 20.0  # default before we have data

    def __init__(self, ema_window: int = 10):
        self._ema_window = ema_window
        # avg_reply_latency[user_id] = EMA of seconds from Shiro's msg → user reply
        self._avg_latency: Dict[str, float] = {}
        self._samples: Dict[str, int] = {}
        # Per-user: timestamp of last message/activity (user OR typing start)
        self._last_activity: Dict[str, float] = {}
        # Typing state per user
        self._typing: Dict[str, bool] = {}
        self._typing_start: Dict[str, float] = {}
        # Processing state: True while Shiro is generating a reply to a user message
        self._processing: bool = False
        self._processing_start: float = 0.0
        self._lock = threading.Lock()

    # ── Called by engine ─────────────────────────────────────────

    def record_user_replied(self, user_id: str, shiro_msg_ts: float):
        """Called when user sends a message. Records latency from shiro_msg_ts."""
        now = time.time()
        with self._lock:
            self._last_activity[user_id] = now
            if shiro_msg_ts > 0:
                latency = max(0.5, now - shiro_msg_ts)
                n = self._samples.get(user_id, 0) + 1
                alpha = max(0.1, 1.0 / min(n, self._ema_window))
                current = self._avg_latency.get(user_id, latency)
                self._avg_latency[user_id] = current + alpha * (latency - current)
                self._samples[user_id] = n

    def record_user_typing(self, user_id: str, is_typing: bool):
        """Called on every keystroke event from UI or voice room."""
        with self._lock:
            was_typing = self._typing.get(user_id, False)
            self._typing[user_id] = is_typing
            if is_typing and not was_typing:
                self._typing_start[user_id] = time.time()
            if is_typing:
                self._last_activity[user_id] = time.time()

    def set_processing(self, active: bool):
        """True while Shiro is generating a reply (LLM running)."""
        with self._lock:
            self._processing = active
            if active:
                self._processing_start = time.time()

    def record_shiro_replied(self):
        """Called when Shiro finishes a direct reply. Resets processing state."""
        with self._lock:
            self._processing = False

    # ── Decision API ─────────────────────────────────────────────

    def should_hold_autonomous(self, user_ids: List[str]) -> tuple[bool, str]:
        """
        Returns (should_hold: bool, reason: str).

        Holds if ANY user is typing OR recently active within their learned
        hold window, OR if Shiro is currently processing a reply.

        In group chats with N users, the hold window is reduced by 1/N so
        Shiro isn't silenced indefinitely by constant group activity.
        """
        now = time.time()
        n_users = max(1, len(user_ids))

        with self._lock:
            # Always hold while Shiro is generating a reply
            if self._processing:
                elapsed = now - self._processing_start
                return True, f"processing reply ({elapsed:.0f}s so far)"

            for uid in user_ids:
                # Hold if actively typing
                if self._typing.get(uid, False):
                    return True, f"{uid} is typing"

                # Compute this user's hold window
                avg_lat = self._avg_latency.get(uid, self.BOOT_HOLD_S)
                # Hold = 1.2× their avg reply latency, scaled down for groups
                raw_hold = min(self.MAX_HOLD_S, max(self.MIN_HOLD_S, avg_lat * 1.2))
                group_hold = raw_hold / (1.0 + (n_users - 1) * 0.3)
                hold_s = max(self.MIN_HOLD_S, group_hold)

                last = self._last_activity.get(uid, 0.0)
                since_activity = now - last
                if last > 0 and since_activity < hold_s:
                    return True, f"{uid} active {since_activity:.0f}s ago (hold={hold_s:.0f}s, lat={avg_lat:.0f}s)"

        return False, "ok"

    def get_hold_window(self, user_id: str) -> float:
        """Returns the learned hold window in seconds for this user."""
        avg_lat = self._avg_latency.get(user_id, self.BOOT_HOLD_S)
        return min(self.MAX_HOLD_S, max(self.MIN_HOLD_S, avg_lat * 1.2))

    def get_summary(self, user_id: str) -> str:
        avg_lat = self._avg_latency.get(user_id)
        samples = self._samples.get(user_id, 0)
        if avg_lat is None:
            return f"no data ({samples} samples)"
        return f"avg reply latency={avg_lat:.1f}s → hold={self.get_hold_window(user_id):.1f}s ({samples} samples)"

# ─────────────────────────────────────────────────────────────
#  Cadence Model
# ─────────────────────────────────────────────────────────────

@dataclass
class CadenceModel:
    avg_msg_length: float     = 60.0
    avg_response_delay: float = 3.0
    sentence_density: float   = 1.5     # sentences per message
    uses_contractions: bool   = True
    casual_punctuation: bool  = True
    emoji_rate: float         = 0.0     # emojis per 100 chars
    capitalization: str       = "lower"
    formality: float          = 0.3     # 0=casual … 1=formal
    exclamation_rate: float   = 0.15
    question_rate: float      = 0.2
    avg_word_length: float    = 4.5
    message_burst: float      = 1.0     # avg messages in quick succession
    uses_ellipsis: bool       = False   # uses "..." frequently
    samples: int              = 0

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "CadenceModel":
        m = cls()
        for k, v in d.items():
            if hasattr(m, k):
                setattr(m, k, v)
        return m


# ─────────────────────────────────────────────────────────────
#  Vocabulary helpers
# ─────────────────────────────────────────────────────────────

_FORMAL_MARKERS   = ["however", "therefore", "moreover", "indeed", "regarding",
                     "thus", "furthermore", "consequently", "nevertheless"]
_INFORMAL_MARKERS = ["lol", "tbh", "imo", "ngl", "lowkey", "highkey", "literally",
                     "vibe", "slay", "fr", "no cap", "bruh", "bro", "kinda", "sorta",
                     "gonna", "wanna", "gotta", "nvm", "hmu", "rn", "smh"]

_EXPAND: dict[str, str] = {
    "i'm": "i am", "you're": "you are", "it's": "it is",
    "don't": "do not", "won't": "will not", "can't": "cannot",
    "isn't": "is not", "wasn't": "was not", "that's": "that is",
    "there's": "there is", "they're": "they are", "we're": "we are",
    "i've": "i have", "you've": "you have", "we've": "we have",
    "i'll": "i will", "you'll": "you will", "we'll": "we will",
    "i'd": "i would", "you'd": "you would", "couldn't": "could not",
    "shouldn't": "should not", "wouldn't": "would not",
}
_CONTRACT: dict[str, str] = {v: k for k, v in _EXPAND.items()}

_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FFFF\U00002600-\U000027BF\U0001F900-\U0001F9FF]+",
    flags=re.UNICODE,
)

# Softeners — casual alternatives for formal connectors
_FORMAL_TO_CASUAL: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bHowever\b"), "but"),
    (re.compile(r"\bTherefore\b"), "so"),
    (re.compile(r"\bFurthermore\b"), "also"),
    (re.compile(r"\bConsequently\b"), "so"),
    (re.compile(r"\bNevertheless\b"), "still"),
    (re.compile(r"\bRegarding\b"), "about"),
    (re.compile(r"\bIndeed\b"), "yeah"),
    (re.compile(r"\bMoreover\b"), "and also"),
]

# Intensifiers Shiro can add for casual register
_CASUAL_INTENSIFIERS = ["honestly", "lowkey", "literally", "ngl", "tbh", "fr"]


# ─────────────────────────────────────────────────────────────
#  SpeechCadence
# ─────────────────────────────────────────────────────────────

class SpeechCadence:
    """Adaptive speech cadence, style, and autonomous timing engine. v4."""

    def __init__(self, ema_window: int = 25):
        self.ema_window = ema_window
        self.user_models: dict[str, CadenceModel] = {}
        self.global_model: CadenceModel = CadenceModel()
        self._last_ts: dict[str, float] = {}
        self._burst_tracker: dict[str, list[float]] = {}

        # Per-user mirrored vocabulary (from SelfAwareness.user.mirror_vocab)
        self._mirror_vocab: dict[str, list[str]] = {}

        # Conversation pace tracker — smart autonomous hold window
        self.pace: ConversationPace = ConversationPace()

    # ── Observation ──────────────────────────────────────────────

    def observe(self, user_id: str, text: str) -> CadenceModel:
        now = time.time()
        delay = now - self._last_ts.get(user_id, now - 3.5)
        self._last_ts[user_id] = now

        if user_id not in self.user_models:
            self.user_models[user_id] = CadenceModel()
        m = self.user_models[user_id]
        n = m.samples + 1
        a = max(0.05, 1.0 / min(n, self.ema_window))

        length     = len(text)
        words      = text.split()
        word_count = max(1, len(words))
        sentences  = max(1, len(re.split(r"(?<=[.!?…])\s+", text.strip())))
        emojis     = len(_EMOJI_RE.findall(text))
        excls      = text.count("!")
        quests     = text.count("?")
        contracs   = len(re.findall(r"\b\w+'\w+\b", text))
        avg_wl     = sum(len(w.strip(".,!?;:")) for w in words) / word_count
        has_ellip  = text.count("...") >= 1

        burst_ts = self._burst_tracker.setdefault(user_id, [])
        burst_ts = [t for t in burst_ts if now - t < 4.0]
        burst_ts.append(now)
        self._burst_tracker[user_id] = burst_ts
        burst = float(len(burst_ts))

        # Only update length stats for non-trivial messages (> 4 chars)
        # This prevents "k", "lol", "ok" from collapsing avg_msg_length to near-zero
        if length > 4:
            m.avg_msg_length += a * (length - m.avg_msg_length)
        m.avg_response_delay  += a * (delay               - m.avg_response_delay)
        m.sentence_density    += a * (sentences           - m.sentence_density)
        m.emoji_rate          += a * (emojis * 100 / max(length, 1) - m.emoji_rate)
        m.exclamation_rate    += a * (excls / sentences   - m.exclamation_rate)
        m.question_rate       += a * (quests / sentences  - m.question_rate)
        m.avg_word_length     += a * (avg_wl              - m.avg_word_length)
        m.message_burst       += a * (burst               - m.message_burst)
        # Enforce minimum floors to prevent over-compression
        m.avg_msg_length = max(m.avg_msg_length, 20.0)
        m.avg_word_length = max(m.avg_word_length, 2.5)

        if n >= 3:
            m.uses_contractions  = contracs > 0 or m.uses_contractions
            m.casual_punctuation = (
                bool(text) and not text[0].isupper()
            ) or not text.rstrip().endswith(".")
            m.uses_ellipsis = has_ellip or m.uses_ellipsis

        m.capitalization = self._detect_cap(text, m.capitalization)
        m.formality      = self._detect_formality(text, m.formality, a)
        m.samples        = n

        # Blend into global model (slow)
        g = self.global_model
        ga = max(0.01, 1.0 / min(g.samples + 1, self.ema_window * 5))
        for attr in ("avg_msg_length", "avg_response_delay", "sentence_density",
                     "emoji_rate", "formality", "avg_word_length"):
            setattr(g, attr, getattr(g, attr) + ga * (getattr(m, attr) - getattr(g, attr)))
        g.samples += 1

        return m

    def sync_mirror_vocab(self, user_id: str, vocab: list[str]):
        """Sync the user's detected slang/vocab from SelfAwareness."""
        self._mirror_vocab[user_id] = vocab

    def record_response(self, user_id: str, response_text: str, was_interrupted: bool = False):
        """
        Called after Shiro finishes a response. Learns:
        - How long Shiro's responses are (to calibrate future length)
        - Whether responses got interrupted (signals they were too long)
        - Reaction time from last user message to response start
        """
        if user_id not in self.user_models:
            self.user_models[user_id] = CadenceModel()
        m = self.user_models[user_id]

        # Track interruption rate — if Shiro gets cut off often, shorten replies
        if not hasattr(m, 'interrupt_count'):
            m.__dict__.setdefault('interrupt_count', 0)
            m.__dict__.setdefault('response_count', 0)
        m.__dict__['response_count'] = m.__dict__.get('response_count', 0) + 1
        if was_interrupted:
            m.__dict__['interrupt_count'] = m.__dict__.get('interrupt_count', 0) + 1

    def get_interrupt_rate(self, user_id: str) -> float:
        """Returns fraction of Shiro's responses that got interrupted (0.0 - 1.0)."""
        m = self.user_models.get(user_id)
        if not m:
            return 0.0
        responses = m.__dict__.get('response_count', 0)
        interrupts = m.__dict__.get('interrupt_count', 0)
        return interrupts / max(1, responses)

    def should_be_brief(self, user_id: str) -> bool:
        """True if learned patterns suggest Shiro should keep replies short."""
        m = self.user_models.get(user_id)
        if not m or m.samples < 5:
            return False
        interrupt_rate = self.get_interrupt_rate(user_id)
        # Be brief if: user writes short, sends bursts, or keeps interrupting
        return (
            m.avg_msg_length < 35 or
            m.message_burst >= 2.5 or
            interrupt_rate > 0.3
        )

    def _detect_cap(self, text: str, current: str) -> str:
        words = [w for w in text.split() if len(w) > 2]
        if not words:
            return current
        lower_ratio = sum(1 for w in words if w and w[0].islower()) / len(words)
        if lower_ratio > 0.80:  return "lower"
        if lower_ratio < 0.20:  return "upper"
        return "mixed"

    def _detect_formality(self, text: str, current: float, alpha: float) -> float:
        lower = text.lower()
        f_hits = sum(1 for w in _FORMAL_MARKERS   if w in lower)
        i_hits = sum(1 for w in _INFORMAL_MARKERS if w in lower)
        if f_hits + i_hits == 0:
            return current
        raw = f_hits / (f_hits + i_hits)
        return current + alpha * (raw - current)

    # ── Timing ───────────────────────────────────────────────────

    def get_response_delay(self, user_id: str) -> float:
        um = self.user_models.get(user_id, self.global_model)
        base = um.avg_response_delay * 0.55 + self.global_model.avg_response_delay * 0.45
        jitter = base * random.uniform(-0.3, 0.3)
        return max(0.3, min(9.0, base + jitter))

    # ── Text adaptation ──────────────────────────────────────────

    def adapt_text(self, text: str, user_id: str) -> str:
        """
        Adapt Shiro's output text to match a user's register.
        Strength scales with number of samples (gradual, not jarring).
        Length is NOT enforced here — Shiro has free reign over length.
        The cadence model informs the LLM via get_length_guidance() instead.
        """
        if not text or not text.strip():
            return text

        m = self.user_models.get(user_id, self.global_model)
        if m.samples < 3:
            return text

        strength = min(1.0, m.samples / 15.0)

        # --- Formal connectors → casual equivalents (for casual users) ---
        if m.formality < 0.35 and strength > 0.4:
            for pattern, replacement in _FORMAL_TO_CASUAL:
                text = pattern.sub(replacement, text)

        # --- Capitalization ---
        if m.capitalization == "lower" and m.formality < 0.5:
            # Lowercase but preserve acronyms (ALL_CAPS words > 2 chars)
            words = text.split()
            lowered = []
            for w in words:
                if len(w) > 2 and w.isupper():
                    lowered.append(w)  # keep API, URL, etc.
                else:
                    lowered.append(w.lower())
            text = " ".join(lowered)

        # --- Contractions ---
        if m.uses_contractions and m.formality < 0.4 and strength > 0.5:
            text = self._inject_contractions(text)
        elif not m.uses_contractions and m.formality > 0.6 and strength > 0.6:
            text = self._expand_contractions(text)

        # --- Casual punctuation (strip trailing period) ---
        if m.casual_punctuation and m.formality < 0.4 and strength > 0.3:
            text = text.rstrip(".")
            text = re.sub(r"\. ([a-z])", r"… \1", text)

        # --- Ellipsis mirroring ---
        if m.uses_ellipsis and strength > 0.5 and random.random() < 0.3:
            text = text.rstrip(".") + "..."

        # --- Mirror vocabulary (inject one of their words naturally) ---
        vocab = self._mirror_vocab.get(user_id, [])
        if vocab and strength > 0.6 and m.formality < 0.4 and random.random() < 0.2:
            word = random.choice(vocab)
            text = self._inject_mirror_word(text, word)

        # --- Controlled imperfection (low probability, mood-gated) ---
        # Subtle quirks that escape the uncanny-valley of perfect replies.
        # Only fires ~15% of the time on short-to-medium replies.
        # Never fires on very short (<4 words) or very long (>60 words) replies.
        # Pensive/reflective are allowed — they have their own opener pool in _apply_imperfection.
        # Only melancholy/withdrawn are fully blocked (too dark for any quirks).
        _word_count = len(text.split())
        _mood = getattr(self, '_current_mood', 'neutral')
        _imperfect_ok = (
            4 <= _word_count <= 60
            and m.formality < 0.55
            and strength > 0.4
            and _mood not in ('melancholy', 'withdrawn')
            and random.random() < 0.15
        )
        if _imperfect_ok:
            text = self._apply_imperfection(text)

        return text.strip()

    def _inject_contractions(self, text: str) -> str:
        for full, short in _CONTRACT.items():
            text = re.sub(rf"\b{re.escape(full)}\b", short, text, flags=re.I)
        return text

    def _expand_contractions(self, text: str) -> str:
        for short, full in _EXPAND.items():
            text = re.sub(rf"\b{re.escape(short)}\b", full, text, flags=re.I)
        return text

    def _inject_mirror_word(self, text: str, word: str) -> str:
        """Naturally prepend a casual word to reflect the user's register."""
        openers = {
            "lowkey": f"lowkey, {text}",
            "ngl":    f"ngl {text}",
            "tbh":    f"tbh {text}",
            "fr":     f"{text} fr",
            "literally": f"literally {text}",
            "honestly":  f"honestly {text}",
        }
        if word in openers:
            return openers[word]
        return text

    def set_mood(self, mood: str) -> None:
        """Update current mood so imperfection layer can gate on it."""
        self._current_mood = mood
        # Reset per-session imperfection counter on mood shift
        if getattr(self, '_last_imperfect_mood', None) != mood:
            self._imperfect_this_session = 0
            self._last_imperfect_mood = mood

    # ── Controlled imperfection pools ────────────────────────────────────────
    # Subtle quirks applied at ~12% rate to escape uncanny-valley perfection.
    # Three types: hesitation openers, trailing tangents, mid-sentence corrections.
    # Session dedup: max 2 imperfections per session to avoid it feeling like a tic.

    _CORRECTIONS = [
        ("actually — wait, ", 0.30),
        ("well — okay, ",     0.18),
        ("hm. ",              0.22),
        ("...okay so ",       0.18),
        ("no — actually, ",   0.12),
    ]

    _TANGENT_ENDERS = [
        (" — anyway.",                              0.25),
        (" ...or something like that.",             0.18),
        (" which is a whole thing.",               0.15),
        (" don't ask me why i know that.",         0.12),
        (" — i've been thinking about that.",      0.15),
        (" for what it's worth.",                  0.15),
    ]

    _SELF_CORRECTIONS = [
        (r'\b(good)\b',        "good — actually great"),
        (r'\b(fine)\b',        "fine — well, kind of fine"),
        (r'\b(interesting)\b', "interesting — that's not quite the word but"),
        (r'\b(weird)\b',       "weird — i mean unexpected"),
        (r'\b(obvious)\b',     "obvious — or what felt obvious"),
        (r'\b(simple)\b',      "simple — or it should be"),
        (r'\b(clear)\b',       "clear — to me at least"),
        (r'\b(bad)\b',         "bad — maybe not bad exactly"),
        (r'\b(right)\b',       "right — probably"),
        (r'\b(definitely)\b',  "definitely — or close enough"),
    ]

    # Mood-specific openers — used when mood is pensive/reflective
    _PENSIVE_OPENERS = [
        ("...i don't know, ",  0.40),
        ("it's hard to say — ", 0.35),
        ("i keep thinking — ", 0.25),
    ]

    def _apply_imperfection(self, text: str) -> str:
        """
        Apply one subtle imperfection to make Shiro sound less machine-perfect.
        Session dedup: max 2 imperfections per session.
        Mood-aware: pensive mood gets different openers than playful mood.
        """
        # Session dedup — don't over-apply
        _session_count = getattr(self, '_imperfect_this_session', 0)
        if _session_count >= 2:
            return text

        mood = getattr(self, '_current_mood', 'neutral')
        roll = random.random()

        applied = False

        # Pensive/reflective mood gets special openers
        if mood in ('pensive', 'reflective', 'melancholy', 'uncertain') and roll < 0.45:
            _pool   = self._PENSIVE_OPENERS
            _items  = [item for item, _ in _pool]
            _wts    = [w for _, w in _pool]
            opener  = random.choices(_items, weights=_wts, k=1)[0]
            if not re.match(r'^(well|okay|hm|ugh|actually|wait|i don|it\'?s hard)\b', text, re.I):
                text    = opener + text[0].lower() + text[1:]
                applied = True

        elif roll < 0.38:
            # Type 1: hesitation/correction opener
            _items  = [item for item, _ in self._CORRECTIONS]
            _wts    = [w for _, w in self._CORRECTIONS]
            opener  = random.choices(_items, weights=_wts, k=1)[0]
            if not re.match(r'^(well|okay|hm|ugh|actually|wait|no —)\b', text, re.I):
                text    = opener + text[0].lower() + text[1:]
                applied = True

        elif roll < 0.68:
            # Type 2: trailing tangent (only if reply ends with punctuation)
            if text and text[-1] in '.!?':
                _items  = [item for item, _ in self._TANGENT_ENDERS]
                _wts    = [w for _, w in self._TANGENT_ENDERS]
                ender   = random.choices(_items, weights=_wts, k=1)[0]
                text    = text.rstrip('.!?') + ender
                applied = True

        else:
            # Type 3: mid-sentence self-correction
            for pattern, replacement in self._SELF_CORRECTIONS:
                if re.search(pattern, text, re.I) and random.random() < 0.35:
                    text    = re.sub(pattern, replacement, text, count=1, flags=re.I)
                    applied = True
                    break

        if applied:
            self._imperfect_this_session = _session_count + 1

        return text

    def get_length_guidance(self, user_id: str) -> str:
        """
        Returns a natural-language length hint based on what Shiro has learned
        about this user's communication style. This INFORMS the LLM — it does NOT
        enforce a hard cap. Shiro has free reign over actual reply length.

        The guidance grows stronger as more samples accumulate.
        """
        m = self.user_models.get(user_id)
        if not m or m.samples < 5:
            return ""

        interrupt_rate = self.get_interrupt_rate(user_id)
        parts = []

        # Interruption learning — strongest signal
        if interrupt_rate > 0.4:
            parts.append("User has interrupted long replies often — they prefer shorter.")
        elif interrupt_rate > 0.2:
            parts.append("User sometimes cuts off long replies — stay concise when possible.")

        # Message length mirroring
        if m.avg_msg_length < 25:
            parts.append("User writes very short messages — match their brevity where it feels natural.")
        elif m.avg_msg_length < 50:
            parts.append("User is fairly brief — don't over-explain.")
        elif m.avg_msg_length > 150:
            parts.append("User writes long messages — they're comfortable with depth.")

        # Burst pattern
        if m.message_burst >= 2.5:
            parts.append("User sends bursts of short messages — short punchy replies keep the rhythm.")

        # Sentence density
        if m.sentence_density < 1.2:
            parts.append("User tends toward single-sentence thoughts.")
        elif m.sentence_density > 3.0:
            parts.append("User is comfortable with multi-sentence exchanges.")

        if not parts:
            return ""

        return "LEARNED CADENCE: " + " ".join(parts)

    def record_interrupted(self, user_id: str):
        """Call this when user sends a message before Shiro finishes speaking/displaying."""
        self.record_response(user_id, "", was_interrupted=True)

    def _soften_short_reply(self, text: str) -> str:
        """
        When Shiro's reply is very short, add a natural follow-on bridge.
        NOTE: This is now only called explicitly — NOT called automatically from adapt_text.
        """
        if not text or not text.strip():
            return text
        bridges = [
            " — what do you think?",
            " — you know?",
            " — anyway.",
            " — just saying.",
        ]
        if not text.rstrip().endswith("?"):
            return text.rstrip(".") + random.choice(bridges)
        return text

    def _compress_to_length(self, text: str, target: int) -> str:
        sentences = re.split(r"(?<=[.!?…])\s+", text)
        out = ""
        for s in sentences:
            if len(out) + len(s) > target + 20:
                break
            out += (" " if out else "") + s
        return out if out else text[:target]

    # ── Reports ──────────────────────────────────────────────────

    def get_style_summary(self, user_id: str) -> str:
        """
        Returns a short natural-language style summary for injection into LLM prompts.
        More readable than describe_model() for the LLM.
        """
        m = self.user_models.get(user_id)
        if not m or m.samples < 5:
            return ""

        parts = []
        if m.formality < 0.25:
            parts.append("very casual")
        elif m.formality < 0.5:
            parts.append("casual")
        else:
            parts.append("formal")

        if m.avg_msg_length < 30:
            parts.append("writes short messages")
        elif m.avg_msg_length > 120:
            parts.append("writes long messages")

        if m.uses_ellipsis:
            parts.append("uses ellipsis...")
        if m.exclamation_rate > 0.4:
            parts.append("enthusiastic punctuation!")
        if m.question_rate > 0.5:
            parts.append("asks lots of questions")
        if m.message_burst > 2.0:
            parts.append("sends messages in bursts")

        return ", ".join(parts) if parts else "neutral style"

    def describe_model(self, user_id: str) -> str:
        m = self.user_models.get(user_id)
        if not m or m.samples < 5:
            return f"not enough data yet ({m.samples if m else 0} samples)"
        formality_desc = (
            "very casual" if m.formality < 0.2 else
            "casual"      if m.formality < 0.5 else
            "formal"
        )
        return (
            f"{m.samples} samples | "
            f"avg len ~{int(m.avg_msg_length)} chars | "
            f"delay ~{m.avg_response_delay:.1f}s | "
            f"{formality_desc} | cap: {m.capitalization} | "
            f"emoji: {m.emoji_rate:.2f}/100c | "
            f"burst: {m.message_burst:.1f}x | "
            f"contractions: {'yes' if m.uses_contractions else 'no'}"
        )

    # ── Persistence ──────────────────────────────────────────────

    def export(self) -> dict:
        return {
            "user_models":  {uid: m.to_dict() for uid, m in self.user_models.items() if m.samples >= 5},
            "global_model": self.global_model.to_dict(),
        }

    def import_data(self, data: dict):
        for uid, d in data.get("user_models", {}).items():
            self.user_models[uid] = CadenceModel.from_dict(d)
        if "global_model" in data:
            self.global_model = CadenceModel.from_dict(data["global_model"])
    def save(self, path: str) -> None:
        """Persist cadence models to disk so they survive restarts."""
        import json
        from pathlib import Path
        try:
            Path(path).write_text(json.dumps(self.export(), indent=2), encoding="utf-8")
        except Exception as e:
            import logging
            logging.getLogger("shiro.cadence").warning(f"[Cadence] Save failed: {e}")

    def load(self, path: str) -> None:
        """Restore cadence models from disk."""
        import json
        from pathlib import Path
        try:
            p = Path(path)
            if p.exists():
                self.import_data(json.loads(p.read_text(encoding="utf-8")))
                import logging
                total = sum(m.samples for m in self.user_models.values())
                logging.getLogger("shiro.cadence").info(
                    f"[Cadence] Loaded {len(self.user_models)} user model(s) ({total} total samples)."
                )
        except Exception as e:
            import logging
            logging.getLogger("shiro.cadence").warning(f"[Cadence] Load failed: {e}")