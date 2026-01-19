"""Safety filters for input and output."""

from __future__ import annotations

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

    def filter_input(self, text: str) -> str:
        return self._rewrite(text)

    def filter_output(self, message: OutputMessage) -> OutputMessage:
        return OutputMessage(
            text=self._rewrite(message.text),
            intent=message.intent,
            metadata=message.metadata,
        )

    def _rewrite(self, text: str) -> str:
        for term in self._policy.blocked_terms:
            if term.lower() in text.lower():
                text = text.replace(term, self._policy.replacement)
        return text
