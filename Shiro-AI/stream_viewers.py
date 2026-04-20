"""
stream_viewers.py — Shiro Stream Viewer Identity + Chat Pace System v1.0
=========================================================================
Tracks who's in chat and how fast it's moving.
Used by StreamHostBrain and StreamLearning.

Classes
-------
  ChatPaceMeter   — rolling window chat velocity, classifies dead/slow/medium/active/fast
  ViewerProfile   — per-viewer identity: messages, topics, engagement, Shiro interactions
  ViewerRoster    — manages all ViewerProfiles this stream session

Shiro-specific additions vs Fenilux original
--------------------------------------------
  - ViewerProfile.platform: tracks "twitch" | "youtube" | "discord" | "local"
  - ViewerRoster.get_by_platform() for multi-platform streams
  - ViewerProfile stores last 15 messages (was 10)
  - ChatPaceMeter emits a combined_rate = max(1min_rate, 0.5 * 15s_burst)
    with configurable thresholds
  - All classes are thread-safe
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timedelta
from typing import Optional


# ──────────────────────────────────────────────────────────────────────────────
# ChatPaceMeter
# ──────────────────────────────────────────────────────────────────────────────

class ChatPaceMeter:
    """
    Measures real-time chat velocity and classifies current pace mode.

    Modes
    -----
    dead    0 msg/min       — silence; Shiro should carry the room
    slow    < 3 msg/min     — small/growing chat; warm and engage
    medium  3–10 msg/min    — healthy pace; balanced hosting
    active  10–25 msg/min   — lively; be selective with replies
    fast    25+ msg/min     — flooding; batch replies, step back from proactive
    """

    WINDOW_SECONDS = 60
    BURST_WINDOW   = 15

    def __init__(self):
        self._timestamps: deque[datetime] = deque()
        self._lock = threading.Lock()

    def record_message(self):
        with self._lock:
            now = datetime.now()
            self._timestamps.append(now)
            cutoff = now - timedelta(seconds=self.WINDOW_SECONDS)
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()

    def rate_per_minute(self) -> float:
        with self._lock:
            now    = datetime.now()
            cutoff = now - timedelta(seconds=self.WINDOW_SECONDS)
            recent = sum(1 for t in self._timestamps if t >= cutoff)
            return recent * (60 / self.WINDOW_SECONDS)

    def burst_rate(self) -> float:
        """Messages per minute over the last 15 seconds."""
        with self._lock:
            now    = datetime.now()
            cutoff = now - timedelta(seconds=self.BURST_WINDOW)
            recent = sum(1 for t in self._timestamps if t >= cutoff)
            return recent * (60 / self.BURST_WINDOW)

    def combined_rate(self) -> float:
        return max(self.rate_per_minute(), self.burst_rate() * 0.5)

    def mode(self) -> str:
        rate = self.combined_rate()
        if rate == 0:     return "dead"
        if rate < 3:      return "slow"
        if rate < 10:     return "medium"
        if rate < 25:     return "active"
        return "fast"

    def is_flooding(self) -> bool:
        return self.burst_rate() > 20

    def is_slow_or_dead(self) -> bool:
        return self.mode() in ("dead", "slow")


# ──────────────────────────────────────────────────────────────────────────────
# ViewerProfile
# ──────────────────────────────────────────────────────────────────────────────

class ViewerProfile:
    """Everything Shiro knows about one viewer this stream session."""

    def __init__(self, username: str, platform: str = "unknown"):
        self.username         = username
        self.platform         = platform            # twitch | youtube | discord | local
        self.first_seen       = datetime.now()
        self.message_count    = 0
        self.messages: deque  = deque(maxlen=15)    # last 15 messages
        self.topics_mentioned: list[str] = []
        self.emotional_tone   = "neutral"           # positive | neutral | negative
        self.engagement_score = 0.0
        self.is_regular       = False               # seen in prior sessions (set externally)
        self.shiro_interactions = 0                 # times Shiro directly addressed them
        self.last_addressed: Optional[datetime] = None
        self.is_creator       = False

    def record_message(self, text: str):
        self.message_count    += 1
        self.engagement_score  = min(10.0, self.engagement_score + 0.3)
        self.messages.append({"text": text, "time": datetime.now()})

    def get_recent_messages(self, n: int = 3) -> list[str]:
        return [m["text"] for m in list(self.messages)[-n:]]

    def minutes_since_addressed(self) -> float:
        if self.last_addressed is None:
            return 9999.0
        return (datetime.now() - self.last_addressed).total_seconds() / 60

    def mark_addressed(self):
        self.shiro_interactions += 1
        self.last_addressed = datetime.now()

    def summary(self) -> str:
        parts = [f"{self.username}({self.platform}, {self.message_count} msgs"]
        if self.topics_mentioned:
            parts.append(f"topics: {', '.join(self.topics_mentioned[-3:])}")
        if self.shiro_interactions:
            parts.append(f"addressed {self.shiro_interactions}x")
        return ", ".join(parts) + ")"


# ──────────────────────────────────────────────────────────────────────────────
# ViewerRoster
# ──────────────────────────────────────────────────────────────────────────────

class ViewerRoster:
    """Manages all viewers seen this stream session."""

    def __init__(self):
        self._viewers: dict[str, ViewerProfile] = {}
        self._lock = threading.Lock()

    def get_or_create(self, username: str, platform: str = "unknown") -> ViewerProfile:
        key = f"{platform}:{username}"
        with self._lock:
            if key not in self._viewers:
                self._viewers[key] = ViewerProfile(username, platform=platform)
            return self._viewers[key]

    def record_message(self, username: str, text: str,
                       platform: str = "unknown") -> ViewerProfile:
        profile = self.get_or_create(username, platform)
        profile.record_message(text)
        return profile

    def get_active(self, last_n_minutes: float = 5.0) -> list[ViewerProfile]:
        cutoff = datetime.now() - timedelta(minutes=last_n_minutes)
        with self._lock:
            return [
                v for v in self._viewers.values()
                if v.messages and list(v.messages)[-1]["time"] > cutoff
            ]

    def get_neglected(self, min_msgs: int = 2,
                      min_silence_min: float = 5.0) -> list[ViewerProfile]:
        return [
            v for v in self.get_active(8.0)
            if (v.message_count >= min_msgs
                and v.minutes_since_addressed() > min_silence_min
                and not v.is_creator)
        ]

    def get_newcomer(self) -> Optional[ViewerProfile]:
        """First-timer within last 2 min who hasn't been welcomed yet."""
        cutoff = datetime.now() - timedelta(minutes=2)
        candidates = [
            v for v in self._viewers.values()
            if (v.first_seen > cutoff
                and v.shiro_interactions == 0
                and v.message_count >= 1
                and not v.is_creator)
        ]
        return candidates[0] if candidates else None

    def get_by_platform(self, platform: str) -> list[ViewerProfile]:
        with self._lock:
            return [v for v in self._viewers.values() if v.platform == platform]

    def viewer_context_block(self, max_viewers: int = 4) -> str:
        # FIX: compact single-line format saves ~40 tokens vs multi-line bullet list.
        # max_viewers 6→4 for 8B model context budget.
        active = self.get_active(8.0)
        if not active:
            return ""
        active.sort(key=lambda v: v.message_count, reverse=True)
        summaries = [v.summary() for v in active[:max_viewers]]
        return "Viewers: " + " | ".join(summaries)

    def total_unique(self) -> int:
        return len(self._viewers)

    def active_count(self, minutes: float = 5.0) -> int:
        return len(self.get_active(minutes))

    def mark_creator(self, username: str, platform: str = "unknown"):
        p = self.get_or_create(username, platform)
        p.is_creator = True