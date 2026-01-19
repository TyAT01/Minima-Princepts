"""Learning and adaptation controller."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass(slots=True)
class EngagementStats:
    user_affinity: Dict[str, float] = field(default_factory=dict)
    style_adjustments: Dict[str, float] = field(default_factory=dict)


class LearningController:
    def __init__(self) -> None:
        self._stats = EngagementStats()

    def update_affinity(self, user_id: str, delta: float) -> None:
        self._stats.user_affinity[user_id] = self._stats.user_affinity.get(user_id, 0.0) + delta

    def adjust_style(self, key: str, value: float) -> None:
        self._stats.style_adjustments[key] = value

    def snapshot(self) -> EngagementStats:
        return self._stats
