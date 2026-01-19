"""Unified event bus with backpressure handling."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Iterable, List

from princess_ai.schemas.events import Event


@dataclass(slots=True)
class EventBusMetrics:
    max_size: int
    current_size: int
    dropped_events: int


class EventBus:
    def __init__(self, max_size: int = 500) -> None:
        self._queue: Deque[Event] = deque()
        self._max_size = max_size
        self._dropped = 0

    def publish(self, events: Iterable[Event]) -> None:
        for event in events:
            if len(self._queue) >= self._max_size:
                self._queue.popleft()
                self._dropped += 1
            self._queue.append(event)

    def drain(self, limit: int | None = None) -> List[Event]:
        items: List[Event] = []
        while self._queue and (limit is None or len(items) < limit):
            items.append(self._queue.popleft())
        return items

    def metrics(self) -> EventBusMetrics:
        return EventBusMetrics(
            max_size=self._max_size,
            current_size=len(self._queue),
            dropped_events=self._dropped,
        )
