from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FilterResult:
    cleaned_text: str
    flagged: bool = False
    reason: str = ""


class ContentFilter:
    """Lightweight content filter for routing and logging."""

    def __init__(self, banned_phrases: list[str] | None = None) -> None:
        self._banned_phrases = banned_phrases or []

    def apply(self, text: str) -> FilterResult:
        lowered = text.lower()
        for phrase in self._banned_phrases:
            if phrase.lower() in lowered:
                return FilterResult(cleaned_text=text, flagged=True, reason=f"Matched phrase: {phrase}")
        return FilterResult(cleaned_text=text)
