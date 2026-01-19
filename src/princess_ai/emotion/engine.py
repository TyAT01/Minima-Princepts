"""Emotion state and expression mapping."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class EmotionState:
    mood: str = "warm"
    valence: float = 0.7
    arousal: float = 0.5


class EmotionEngine:
    def __init__(self) -> None:
        self._state = EmotionState()

    def update(self, sentiment_delta: float) -> None:
        self._state.valence = max(0.0, min(1.0, self._state.valence + sentiment_delta))

    def express(self, text: str) -> str:
        if self._state.valence > 0.75:
            return f"{text} ✨"
        if self._state.valence < 0.3:
            return f"{text}..."
        return text

    @property
    def state(self) -> EmotionState:
        return self._state
