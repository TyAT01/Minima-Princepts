"""Short-term conversation state."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Iterable

from princess_ai.schemas.events import Event


@dataclass(slots=True)
class ConversationState:
    max_turns: int = 20
    events: Deque[Event] = field(default_factory=deque)

    def add_events(self, new_events: Iterable[Event]) -> None:
        for event in new_events:
            self.events.append(event)
        while len(self.events) > self.max_turns:
            self.events.popleft()

    def recent(self) -> list[Event]:
        return list(self.events)
