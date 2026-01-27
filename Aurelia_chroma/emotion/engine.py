"""Emotion state and expression mapping."""

from __future__ import annotations

import logging
from dataclasses import dataclass


@dataclass(slots=True)
class EmotionState:
    mood: str = "warm"
    valence: float = 0.7
    arousal: float = 0.5


class EmotionEngine:
    def __init__(self) -> None:
        self._state = EmotionState()
        self._logger = logging.getLogger(__name__)

    def update(self, sentiment_delta: float) -> None:
        try:
            self._state.valence = max(
                0.0, min(1.0, self._state.valence + sentiment_delta)
            )
        except Exception as exc:
            self._logger.exception("Failed to update emotion state: %s", exc)

    def express(self, text: str) -> str:
        try:
            if self._state.valence > 0.75:
                return f"{text} ✨"
            if self._state.valence < 0.3:
                return f"{text}..."
            return text
        except Exception as exc:
            self._logger.exception("Failed to express emotion: %s", exc)
            return text

    @property
    def state(self) -> EmotionState:
        return self._state
