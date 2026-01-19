"""Presence tracking for chat and voice channels."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List


@dataclass(slots=True)
class PresenceSnapshot:
    channel: str
    participants: List[str]
    last_updated: datetime


class PresenceTracker:
    def __init__(self) -> None:
        self._participants: Dict[str, set[str]] = {"text": set(), "voice": set()}
        self._last_updated: Dict[str, datetime] = {
            "text": datetime.utcnow(),
            "voice": datetime.utcnow(),
        }
        self._lock = threading.Lock()

    def join(self, channel: str, user: str) -> PresenceSnapshot:
        with self._lock:
            self._participants.setdefault(channel, set()).add(user)
            self._last_updated[channel] = datetime.utcnow()
            return self._snapshot(channel)

    def leave(self, channel: str, user: str) -> PresenceSnapshot:
        with self._lock:
            self._participants.setdefault(channel, set()).discard(user)
            self._last_updated[channel] = datetime.utcnow()
            return self._snapshot(channel)

    def snapshot(self) -> List[PresenceSnapshot]:
        with self._lock:
            return [self._snapshot(channel) for channel in sorted(self._participants.keys())]

    def _snapshot(self, channel: str) -> PresenceSnapshot:
        participants = sorted(self._participants.get(channel, set()))
        last_updated = self._last_updated.get(channel, datetime.utcnow())
        return PresenceSnapshot(
            channel=channel, participants=participants, last_updated=last_updated
        )
