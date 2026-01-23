from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EmotionState:
    tone: str = "neutral"
    intensity: float = 0.3


class EmotionEngine:
    """Simple emotional shading engine."""

    def detect(self, text: str) -> EmotionState:
        lowered = text.lower()
        if any(word in lowered for word in ["happy", "excited", "great"]):
            return EmotionState(tone="upbeat", intensity=0.7)
        if any(word in lowered for word in ["sad", "down", "tired"]):
            return EmotionState(tone="gentle", intensity=0.5)
        return EmotionState()
