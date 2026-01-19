"""Learning and adaptation controller."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(slots=True)
class EngagementStats:
    user_affinity: Dict[str, float] = field(default_factory=dict)
    style_adjustments: Dict[str, float] = field(default_factory=dict)
    sentiment_scores: Dict[str, float] = field(default_factory=dict)
    feedback_flags: List[str] = field(default_factory=list)


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

    def record_signal(self, user_id: str, sentiment: float, feedback: str | None = None) -> None:
        try:
            self._stats.sentiment_scores[user_id] = sentiment
            if feedback:
                self._stats.feedback_flags.append(feedback)
        except Exception as exc:  # noqa: BLE001 - keep learning resilient
            self._logger.exception("Failed to record signal: %s", exc)
