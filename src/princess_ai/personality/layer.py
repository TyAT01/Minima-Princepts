"""Persona policy constraints."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class PersonaPolicy:
    humor: float = 0.6
    formality: float = 0.4
    boundaries: str = "Keep the tone friendly and appropriate for public streams."


class PersonalityLayer:
    def __init__(self, policy: PersonaPolicy | None = None) -> None:
        self._policy = policy or PersonaPolicy()

    def apply(self, text: str) -> str:
        prefix = "Princess: "
        return f"{prefix}{text.strip()}"
