"""Event normalization and priority routing for multi-channel inputs."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Iterable, List

from princess_ai.runtime.rate_limit import RateLimiter, RateLimitConfig
from princess_ai.schemas.events import Event


@dataclass(slots=True)
class PriorityPolicy:
    source_priority: Dict[str, int] = field(
        default_factory=lambda: {"discord": 3, "twitch": 2, "youtube": 1, "text": 4}
    )
    mode_bias: Dict[str, int] = field(default_factory=lambda: {"stream": -1, "private": 1})


class EventRouter:
    def __init__(
        self,
        policy: PriorityPolicy | None = None,
        rate_limit: RateLimitConfig | None = None,
    ) -> None:
        self._policy = policy or PriorityPolicy()
        self._rate_limiter = RateLimiter(rate_limit)
        self._logger = logging.getLogger(__name__)

    def select(self, events: Iterable[Event], mode: str = "default") -> List[Event]:
        try:
            prioritized = sorted(events, key=lambda evt: self._score(evt, mode), reverse=True)
            selected: List[Event] = []
            for event in prioritized:
                channel_key = f"{event.source}:{event.metadata.get('channel', event.user_id)}"
                if not self._rate_limiter.allow(channel_key):
                    continue
                selected.append(event)
            return selected
        except Exception as exc:  # noqa: BLE001 - keep routing resilient
            self._logger.exception("Failed to route events: %s", exc)
            return list(events)

    def _score(self, event: Event, mode: str) -> int:
        base = self._policy.source_priority.get(event.source, 0)
        bias = self._policy.mode_bias.get(mode, 0)
        return base + bias + (1 if event.metadata.get("priority") == "high" else 0)
