"""Simple stdin adapter for local testing."""

from __future__ import annotations

from typing import Iterable

from princess_ai.input_adapters.base import InputAdapter
from princess_ai.schemas.events import Event


class TextInputAdapter(InputAdapter):
    def __init__(self, username: str = "local_user") -> None:
        self._username = username

    def poll(self) -> Iterable[Event]:
        text = input("You: ").strip()
        if not text:
            return []
        return [
            Event(
                source="text",
                user_id=self._username,
                username=self._username,
                text=text,
            )
        ]
