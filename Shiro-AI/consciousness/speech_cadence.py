"""
SpeechCadence v3 — Adaptive rhythm, tempo, and style engine.

New in v3:
  - Vocabulary mirroring: Shiro adopts slang/phrases from the user (gradually)
  - Sentence rhythm matching: splits/joins sentences to match user density
  - Message burst detection: knows when user sends multiple short messages
  - Stronger casual adaptation: actually sounds like the user's register
  - Per-user vocab mirror list integration from SelfAwareness
"""

import re
import time
import random
from dataclasses import dataclass, field
from typing import Optional


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
    """Adaptive speech cadence and style engine. v3."""

    def __init__(self, ema_window: int = 25):
        self.ema_window = ema_window
        self.user_models: dict[str, CadenceModel] = {}
        self.global_model: CadenceModel = CadenceModel()
        self._last_ts: dict[str, float] = {}
        self._burst_tracker: dict[str, list[float]] = {}

        # Per-user mirrored vocabulary (from SelfAwareness.user.mirror_vocab)
        self._mirror_vocab: dict[str, list[str]] = {}

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
        """
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

        # --- Length calibration (compress OR expand) ---
        target = m.avg_msg_length * 1.5
        if len(text) > target + 80 and strength > 0.4:
            # Compress overly long replies
            text = self._compress_to_length(text, int(target + 40))
        elif len(text) < target * 0.35 and m.avg_msg_length > 80 and strength > 0.5:
            # User writes long messages, Shiro's reply is very short — expand slightly
            # Don't add content, just soften the abruptness with a natural bridge
            text = self._soften_short_reply(text)

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

    def _compress_to_length(self, text: str, target: int) -> str:
        sentences = re.split(r"(?<=[.!?…])\s+", text)
        out = ""
        for s in sentences:
            if len(out) + len(s) > target + 20:
                break
            out += (" " if out else "") + s
        return out if out else text[:target]

    def _soften_short_reply(self, text: str) -> str:
        """
        When Shiro's reply is very short but the user writes long messages,
        add a natural follow-on bridge so it doesn't feel dismissive.
        Doesn't add information — just makes the reply feel less abrupt.
        """
        bridges = [
            " — what do you think?",
            " does that track?",
            " what's your take?",
            " i'm curious what you'd say to that",
            " tell me more about your angle on this",
        ]
        # Only add bridge if text doesn't already end in a question
        if not text.rstrip().endswith("?"):
            return text.rstrip(".") + random.choice(bridges)
        return text

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
