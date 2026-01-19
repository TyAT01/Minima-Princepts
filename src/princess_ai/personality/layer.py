"""Persona policy constraints."""

from __future__ import annotations

import logging
from dataclasses import dataclass


@dataclass(slots=True)
class PersonaPolicy:
    humor: float = 0.6
    formality: float = 0.4
    boundaries: str = "Keep the tone friendly and appropriate for public streams."


class PersonalityLayer:
    def __init__(self, policy: PersonaPolicy | None = None, persona_name: str = "Aurelia Vale") -> None:
        self._policy = policy or PersonaPolicy()
        self._persona_name = persona_name
        self._logger = logging.getLogger(__name__)

    def apply(self, text: str) -> str:
        try:
            prefix = f"{self._persona_name}: "
            return f"{prefix}{text.strip()}"
        except Exception as exc:  # noqa: BLE001 - keep personality layer resilient
            self._logger.exception("Failed to apply personality layer: %s", exc)
            return text
