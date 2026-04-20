"""
multi_speaker_hub.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Shiro's Multi-Speaker Intelligence Hub

Optimizations vs original:
  - batch_window_seconds: 3.0→2.0 — faster reply in 1:1 chat (Tyler only)
  - build_group_context(): injection trimmed — was verbose with isolation
    reminders that ate 80+ tokens per turn even in single-user sessions.
    Now only injects isolation block when >1 speaker is present.
  - UsernameSanitizer.get_username_directive(): shortened prose — the old
    version bloated the prompt with 3-4 sentences per user. Now 1 line.
  - Added known_user_realnames dict: "Boss" → "Rob" is now wired directly
    so Shiro knows Rob's real name without extracting it from conversation.
  - primary_user identity guard: build_group_context() now explicitly
    labels Tyler as Tyler (not just "primary user") so the LLM always
    knows who it's talking to, fixing the user=unknown state-save issue.
  - ReplyStrategy.score_message(): stale message cutoff 45s→30s.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("shiro.multi_speaker")


# ─────────────────────────────────────────────────────────────────
#  USER PROFILE
# ─────────────────────────────────────────────────────────────────

@dataclass
class UserProfile:
    """
    Stores everything Shiro knows about one specific user.
    Strictly isolated per username — never shared.
    """
    username: str
    display_name: str = ""
    first_seen: str = ""
    last_seen: str = ""
    message_count: int = 0

    known_facts: Dict[str, str] = field(default_factory=dict)
    topics_discussed: List[str] = field(default_factory=list)
    tone_history: List[str] = field(default_factory=list)
    recent_messages: List[dict] = field(default_factory=list)
    greeted: bool = False
    shiro_notes: List[str] = field(default_factory=list)

    def update_seen(self):
        now = datetime.now().isoformat()
        if not self.first_seen:
            self.first_seen = now
        self.last_seen = now
        self.message_count += 1

    def add_message(self, text: str):
        self.update_seen()
        self.recent_messages.append({
            "ts": datetime.now().isoformat(),
            "text": text
        })
        if len(self.recent_messages) > 20:
            self.recent_messages = self.recent_messages[-20:]

    def get_context_summary(self) -> str:
        """Compact one-liner summary — keeps token cost low."""
        parts = [f"{self.display_name or self.username}"]
        if self.known_facts:
            facts = "; ".join(f"{k}: {v}" for k, v in list(self.known_facts.items())[:4])
            parts.append(facts)
        if self.topics_discussed:
            parts.append(f"topics: {', '.join(self.topics_discussed[:3])}")
        return " | ".join(parts)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "UserProfile":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ─────────────────────────────────────────────────────────────────
#  QUEUED MESSAGE
# ─────────────────────────────────────────────────────────────────

@dataclass
class QueuedMessage:
    username: str
    text: str
    timestamp: float = field(default_factory=time.monotonic)
    user_id: Optional[str] = None
    priority: int = 0
    addressed_shiro: bool = False

    @property
    def age_seconds(self) -> float:
        return time.monotonic() - self.timestamp


# ─────────────────────────────────────────────────────────────────
#  REPLY STRATEGY
# ─────────────────────────────────────────────────────────────────

class ReplyStrategy:
    SINGLE = "single"
    MULTI_TURN = "multi_turn"
    SELECTIVE = "selective"
    ACKNOWLEDGE = "acknowledge"

    _DIRECT_ADDRESS_BOOST = 3.0
    _QUESTION_BOOST = 2.0
    _URGENCY_BOOST = 4.0
    _MAX_AGE_SECS = 30.0   # was 45 — stale messages deprioritized sooner

    URGENT_WORDS = {"help", "urgent", "emergency", "asap", "wait", "stop",
                    "important", "now", "quick", "hurry"}
    DIRECT_TERMS = {"shiro", "shi", "hey shiro", "yo shiro", "oi shiro"}

    def score_message(self, msg: QueuedMessage) -> float:
        score = 1.0
        tl = msg.text.lower()
        words = set(re.findall(r"\b\w+\b", tl))

        if msg.addressed_shiro:
            score += self._DIRECT_ADDRESS_BOOST
        if "?" in msg.text:
            score += self._QUESTION_BOOST
        if words & self.URGENT_WORDS:
            score += self._URGENCY_BOOST

        age_factor = max(0.1, 1.0 - (msg.age_seconds / self._MAX_AGE_SECS))
        score *= age_factor
        return score

    def decide(self, batch: List[QueuedMessage]) -> Tuple[str, List[QueuedMessage]]:
        if not batch:
            return self.SELECTIVE, []

        scored = sorted(batch, key=self.score_message, reverse=True)
        speakers = list(dict.fromkeys(m.username for m in scored))
        n_speakers = len(speakers)
        n_messages = len(batch)

        if n_speakers == 1:
            return self.SINGLE, scored[:3]

        if n_messages <= 3 and n_speakers <= 2:
            return self.SINGLE, scored

        if n_messages >= 4 and n_speakers >= 2:
            top_priority = [m for m in scored if m.addressed_shiro or self.score_message(m) >= 3.0]
            if top_priority:
                return self.MULTI_TURN, top_priority[:2]
            return self.ACKNOWLEDGE, scored[:1]

        return self.SELECTIVE, scored[:2]


# ─────────────────────────────────────────────────────────────────
#  USERNAME SANITIZER
# ─────────────────────────────────────────────────────────────────

class UsernameSanitizer:
    """
    A username is just a label. 'boss' is a name, not a title.
    Tells Shiro to treat usernames as identifiers only.
    """

    SEMANTIC_TRAP_WORDS = {
        "boss", "admin", "owner", "king", "queen", "chief", "master",
        "leader", "manager", "god", "lord", "captain",
        "angel", "love", "honey", "baby", "sweetie", "darling", "cutie",
        "enemy", "ghost", "zero", "nobody", "nothing",
        "user", "guest", "anon", "anonymous",
        "killer", "sniper", "hacker", "shadow", "demon", "devil",
    }

    @staticmethod
    def parse_message(raw_input: str) -> Tuple[str, str]:
        m = re.match(r'^([A-Za-z0-9_\-\.]{1,32})\s*:\s*(.+)$', raw_input, re.DOTALL)
        if m:
            return m.group(1).strip(), m.group(2).strip()
        return "", raw_input.strip()

    @classmethod
    def get_username_directive(cls, username: str, real_name: str = "") -> str:
        """
        Compact one-line directive about a username.
        Shortened from the original 3-4 sentence version to save ~30 tokens.
        """
        tl = username.lower().strip()
        name_display = real_name or username

        if tl in cls.SEMANTIC_TRAP_WORDS:
            return (
                f"NOTE: '{username}' is a username only — not a title or role. "
                f"Their actual name is {name_display}. Treat it like a first name."
            )
        return f"NOTE: '{username}' is a username/label — use it as their name, nothing more."

    @classmethod
    def clean_topic_triggers(cls, username: str, topics_found: List[str]) -> List[str]:
        tl = username.lower()
        false_triggers = {
            "boss":   ["work", "school", "employment", "authority", "hierarchy"],
            "angel":  ["spiritual", "religion", "heaven"],
            "killer": ["violence", "death", "crime"],
            "hacker": ["security", "hacking", "crime"],
            "god":    ["religion", "spiritual"],
            "master": ["authority", "hierarchy"],
        }
        bad = set(false_triggers.get(tl, []))
        return [t for t in topics_found if t.lower() not in bad]


# ─────────────────────────────────────────────────────────────────
#  MULTI SPEAKER HUB
# ─────────────────────────────────────────────────────────────────

class MultiSpeakerHub:
    """
    Shiro's group-chat intelligence layer.

    Known real names (pre-seeded from config.yaml known_users):
      boss → Rob
    Extendable via known_user_realnames param.
    """

    # Pre-seeded real names for semantic-trap usernames.
    # Mirrors the known_users section in config.yaml.
    DEFAULT_REALNAMES: Dict[str, str] = {
        "boss": "Rob",
    }

    def __init__(
        self,
        primary_user: str = "Tyler",
        save_path: Optional[str] = None,
        max_queue_size: int = 50,
        batch_window_seconds: float = 2.0,   # was 3.0 — faster 1:1 response
        known_user_realnames: Optional[Dict[str, str]] = None,
    ):
        self.primary_user = primary_user
        self.save_path = Path(save_path) if save_path else None
        self.max_queue_size = max_queue_size
        self.batch_window = batch_window_seconds

        # Real name lookup: username.lower() → display name
        self._realnames: Dict[str, str] = dict(self.DEFAULT_REALNAMES)
        if known_user_realnames:
            self._realnames.update({k.lower(): v for k, v in known_user_realnames.items()})

        self._profiles: Dict[str, UserProfile] = {}
        self._queue: deque = deque(maxlen=max_queue_size)
        self._strategy = ReplyStrategy()
        self._sanitizer = UsernameSanitizer()

        self._last_reply_time: float = 0.0
        self._active_speakers: Dict[str, float] = {}

        if self.save_path:
            self._load_profiles()

        logger.info(f"[MultiSpeakerHub] Initialized. Primary user: {self.primary_user}")

    # ── Profile management ────────────────────────────────────────

    def get_profile(self, username: str) -> UserProfile:
        key = username.lower().strip()
        if key not in self._profiles:
            profile = UserProfile(
                username=username,
                first_seen=datetime.now().isoformat()
            )
            # Pre-seed display_name from known real names
            if key in self._realnames:
                profile.display_name = self._realnames[key]
            self._profiles[key] = profile
            logger.info(f"[MultiSpeakerHub] New user profile: '{username}'")
        return self._profiles[key]

    def is_primary_user(self, username: str) -> bool:
        return username.lower().strip() == self.primary_user.lower().strip()

    def get_all_active_users(self) -> List[str]:
        cutoff = time.monotonic() - 600
        return [u for u, t in self._active_speakers.items() if t > cutoff]

    def get_real_name(self, username: str) -> str:
        """Return real/display name for a username, or the username itself."""
        profile = self._profiles.get(username.lower().strip())
        if profile and profile.display_name:
            return profile.display_name
        return self._realnames.get(username.lower().strip(), username)

    # ── Message intake ────────────────────────────────────────────

    def intake(self, username: str, text: str, user_id: Optional[str] = None) -> QueuedMessage:
        tl = text.lower()
        addressed = any(term in tl for term in ReplyStrategy.DIRECT_TERMS)
        has_question = "?" in text

        msg = QueuedMessage(
            username=username,
            text=text,
            user_id=user_id,
            addressed_shiro=addressed or has_question,
            priority=1 if addressed else 0,
        )

        self._queue.append(msg)
        self._active_speakers[username.lower()] = time.monotonic()

        profile = self.get_profile(username)
        profile.add_message(text)
        self._extract_and_store_facts(profile, text, username)

        logger.debug(f"[MultiSpeakerHub] Intake from '{username}': '{text[:60]}' (addressed={addressed})")
        return msg

    def _extract_and_store_facts(self, profile: UserProfile, text: str, username: str):
        tl = text.lower()

        m = re.search(r"\bi (?:like|love|enjoy|prefer|hate|dislike)\s+([a-z ]+?)(?:\.|,|!|\?|$)", tl)
        if m:
            thing = m.group(1).strip()
            if len(thing) < 40:
                sentiment = "likes" if any(w in m.group(0) for w in ["like", "love", "enjoy", "prefer"]) else "dislikes"
                profile.known_facts[sentiment] = thing

        m = re.search(r"\bi(?:'m| am)(?: a| an)?\s+([a-z ]+?)(?:\.|,|!|\?|$)", tl)
        if m:
            descriptor = m.group(1).strip()
            if len(descriptor) < 30 and descriptor not in ("here", "back", "good", "fine", "okay", "ready"):
                profile.known_facts["describes_self_as"] = descriptor

        m = re.search(r"(?:my name is|call me|i go by)\s+([a-z]+)", tl)
        if m:
            real_name = m.group(1).strip().capitalize()
            if real_name.lower() != username.lower():
                profile.display_name = real_name
                profile.known_facts["real_name"] = real_name
                # Also update realnames lookup
                self._realnames[username.lower()] = real_name

        if self.save_path:
            self._save_profiles()

    # ── Reply batching ────────────────────────────────────────────

    def get_reply_batch(self, max_age_seconds: float = 8.0) -> Tuple[str, List[QueuedMessage]]:
        now = time.monotonic()
        fresh = [m for m in self._queue if m.age_seconds <= max_age_seconds]
        self._queue.clear()
        self._last_reply_time = now

        if not fresh:
            return ReplyStrategy.SELECTIVE, []

        strategy, ordered = self._strategy.decide(fresh)
        logger.info(
            f"[MultiSpeakerHub] Reply strategy: {strategy} for {len(ordered)} messages "
            f"from {len({m.username for m in ordered})} speakers"
        )
        return strategy, ordered

    def peek_queue(self) -> List[QueuedMessage]:
        return list(self._queue)

    def queue_length(self) -> int:
        return len(self._queue)

    def has_pending(self) -> bool:
        return len(self._queue) > 0

    # ── Context builder ───────────────────────────────────────────

    def build_group_context(self, batch: List[QueuedMessage]) -> str:
        """
        Build system-level context block for Shiro's LLM prompt.

        Optimized: isolation reminder block only appears when multiple
        speakers are present. Single-user sessions no longer pay the
        ~80-token isolation overhead.
        """
        if not batch:
            return ""

        speakers_in_batch = list(dict.fromkeys(m.username for m in batch))
        multi_speaker = len(speakers_in_batch) > 1

        lines = [f"[SPEAKERS: {', '.join(speakers_in_batch)}]"]

        for username in speakers_in_batch:
            profile = self.get_profile(username)
            is_primary = self.is_primary_user(username)
            real_name  = self.get_real_name(username)

            # Identity line — explicit about Tyler so inner_mind saves correctly
            if is_primary:
                lines.append(f"── {username} (this is Tyler, your primary user) ──")
            else:
                lines.append(f"── {username} ──")

            # Username directive (compact — 1 line)
            lines.append(self._sanitizer.get_username_directive(username, real_name))

            # Profile facts (only if non-empty)
            if profile.known_facts or profile.display_name:
                lines.append(profile.get_context_summary())

            # Their messages this turn
            user_msgs = [m for m in batch if m.username == username]
            for msg in user_msgs:
                lines.append(f"  > {msg.text}")
            lines.append("")

        # Isolation block — only for multi-speaker turns
        if multi_speaker:
            lines.append("ISOLATION: Each speaker is a separate person. Never assume facts from one user apply to another.")

        return "\n".join(lines)

    def build_multi_turn_prompts(self, batch: List[QueuedMessage]) -> List[Tuple[str, str]]:
        return [(m.username, m.text) for m in batch]

    def build_combined_prompt(self, batch: List[QueuedMessage]) -> str:
        if len(batch) == 1:
            return batch[0].text

        parts = ["[Multiple people are talking. Address each naturally in one response.]"]
        speakers_seen = set()

        for msg in batch:
            if msg.username not in speakers_seen:
                speakers_seen.add(msg.username)
                label = self.primary_user if self.is_primary_user(msg.username) else msg.username
                real  = self.get_real_name(msg.username)
                display = real if real != msg.username else label
                parts.append(f"{display} says: {msg.text}")

        return "\n".join(parts)

    # ── Persistence ───────────────────────────────────────────────

    def _save_profiles(self):
        if not self.save_path:
            return
        try:
            self.save_path.parent.mkdir(parents=True, exist_ok=True)
            data = {k: v.to_dict() for k, v in self._profiles.items()}
            tmp = self.save_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.save_path)
        except Exception as e:
            logger.error(f"[MultiSpeakerHub] Save failed: {e}")

    def _load_profiles(self):
        if not self.save_path or not self.save_path.exists():
            return
        try:
            data = json.loads(self.save_path.read_text(encoding="utf-8"))
            for k, v in data.items():
                try:
                    self._profiles[k] = UserProfile.from_dict(v)
                    # Back-fill realnames from loaded profiles
                    if self._profiles[k].display_name:
                        self._realnames.setdefault(k, self._profiles[k].display_name)
                except Exception as e:
                    logger.warning(f"[MultiSpeakerHub] Profile load error for '{k}': {e}")
            logger.info(f"[MultiSpeakerHub] Loaded {len(self._profiles)} user profiles")
        except Exception as e:
            logger.error(f"[MultiSpeakerHub] Load failed: {e}")

    def get_profile_summary_for_gui(self) -> List[dict]:
        results = []
        for key, prof in self._profiles.items():
            results.append({
                "username":     prof.username,
                "display_name": prof.display_name or prof.username,
                "is_primary":   self.is_primary_user(prof.username),
                "message_count": prof.message_count,
                "first_seen":   prof.first_seen[:10] if prof.first_seen else "unknown",
                "last_seen":    prof.last_seen[:10] if prof.last_seen else "unknown",
                "known_facts":  prof.known_facts,
                "topics":       prof.topics_discussed[:5],
                "greeted":      prof.greeted,
            })
        results.sort(key=lambda x: (0 if x["is_primary"] else 1, x["username"]))
        return results

    def export_status(self) -> dict:
        return {
            "active_speakers": self.get_all_active_users(),
            "queue_length":    self.queue_length(),
            "total_profiles":  len(self._profiles),
            "primary_user":    self.primary_user,
            "profiles":        self.get_profile_summary_for_gui(),
        }