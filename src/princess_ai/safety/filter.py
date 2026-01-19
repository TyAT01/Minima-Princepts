"""Safety filters for input and output."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Iterable

from princess_ai.schemas.events import OutputMessage


@dataclass(slots=True)
class SafetyPolicy:
    blocked_terms: tuple[str, ...] = ("slur1", "slur2", "explicit_term")
    replacement: str = "[filtered]"
    category_terms: Dict[str, tuple[str, ...]] = field(
        default_factory=lambda: {
            "hate": ("slur1", "slur2"),
            "sexual": ("explicit_term",),
            "harassment": ("idiot", "stupid"),
            "self_harm": ("kill myself", "self harm"),
        }
    )
    safe_rephrase: str = "Let's keep things respectful and safe."
    stream_safe_mode: bool = True


class SafetyFilter:
    def __init__(self, policy: SafetyPolicy | None = None) -> None:
        self._policy = policy or SafetyPolicy()
        self._logger = logging.getLogger(__name__)

    def filter_input(self, text: str) -> str:
        return self._rewrite(text)

    def filter_output(self, message: OutputMessage) -> OutputMessage:
        filtered = self._rewrite(message.text)
        categories = self._detect_categories(filtered)
        if categories and self._policy.stream_safe_mode:
            filtered = self._policy.safe_rephrase
        metadata = dict(message.metadata)
        if categories:
            metadata["safety_categories"] = categories
        return OutputMessage(text=filtered, intent=message.intent, metadata=metadata)

    def _rewrite(self, text: str) -> str:
        try:
            for term in self._policy.blocked_terms:
                if term.lower() in text.lower():
                    text = text.replace(term, self._policy.replacement)
            return text
        except Exception as exc:  # noqa: BLE001 - keep safety filter resilient
            self._logger.exception("Failed to rewrite safety text: %s", exc)
            return text

    def _detect_categories(self, text: str) -> list[str]:
        try:
            lowered = text.lower()
            categories: list[str] = []
            for category, terms in self._policy.category_terms.items():
                if any(term in lowered for term in terms):
                    categories.append(category)
            return categories
        except Exception as exc:  # noqa: BLE001 - keep safety filter resilient
            self._logger.exception("Failed to detect safety categories: %s", exc)
            return []
