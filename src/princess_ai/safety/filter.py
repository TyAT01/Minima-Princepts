"""Safety filters for input and output."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from princess_ai.schemas.events import OutputMessage


@dataclass(slots=True)
class SafetyPolicy:
    blocked_terms: tuple[str, ...] = (
        "slur1",
        "slur2",
        "explicit_term",
    )
    replacement: str = "[filtered]"


class SafetyFilter:
    def __init__(self, policy: SafetyPolicy | None = None) -> None:
        self._policy = policy or SafetyPolicy()
        self._logger = logging.getLogger(__name__)

    def filter_input(self, text: str) -> str:
        return self._rewrite(text)

    def filter_output(self, message: OutputMessage) -> OutputMessage:
        return OutputMessage(
            text=self._rewrite(message.text),
            intent=message.intent,
            metadata=message.metadata,
        )

    def _rewrite(self, text: str) -> str:
        try:
            for term in self._policy.blocked_terms:
                if term.lower() in text.lower():
                    text = text.replace(term, self._policy.replacement)
            return text
        except Exception as exc:  # noqa: BLE001 - keep safety filter resilient
            self._logger.exception("Failed to rewrite safety text: %s", exc)
            return text
