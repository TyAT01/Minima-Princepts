"""Learning and adaptation controller."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict


@dataclass(slots=True)
class EngagementStats:
    user_affinity: Dict[str, float] = field(default_factory=dict)
    style_adjustments: Dict[str, float] = field(default_factory=dict)


class LearningController:
    def __init__(self) -> None:
        self._stats = EngagementStats()
        self._logger = logging.getLogger(__name__)

    def update_affinity(self, user_id: str, delta: float) -> None:
        try:
            self._stats.user_affinity[user_id] = (
                self._stats.user_affinity.get(user_id, 0.0) + delta
            )
        except Exception as exc:  # noqa: BLE001 - keep learning resilient
            self._logger.exception("Failed to update affinity: %s", exc)

    def adjust_style(self, key: str, value: float) -> None:
        try:
            self._stats.style_adjustments[key] = value
        except Exception as exc:  # noqa: BLE001 - keep learning resilient
            self._logger.exception("Failed to adjust style: %s", exc)

    def snapshot(self) -> EngagementStats:
        return self._stats
