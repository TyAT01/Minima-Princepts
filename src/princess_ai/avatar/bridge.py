"""Avatar animation bridge."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class AvatarSignal:
    emotion: str
    viseme: str


class AvatarBridge:
    def send(self, signal: AvatarSignal) -> None:
        return None
