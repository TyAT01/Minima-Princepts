"""Simple stdin adapter for local testing."""

from __future__ import annotations

import logging
import select
import sys
from typing import Iterable

from princess_ai.input_adapters.base import InputAdapter
from princess_ai.schemas.events import Event


class TextInputAdapter(InputAdapter):
    def __init__(self, username: str = "local_user", poll_timeout: float = 0.0) -> None:
        self._username = username
        self._poll_timeout = poll_timeout
        self._logger = logging.getLogger(__name__)

    def poll(self) -> Iterable[Event]:
        try:
            if not self._stdin_ready():
                return []
            line = sys.stdin.readline()
        except (OSError, ValueError) as exc:
            self._logger.warning("Input polling failed: %s", exc)
            return []

        if line == "":
            return []

        text = line.strip()
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

    def _stdin_ready(self) -> bool:
        try:
            readable, _, _ = select.select([sys.stdin], [], [], self._poll_timeout)
        except (OSError, ValueError) as exc:
            self._logger.warning("stdin readiness check failed: %s", exc)
            return False
        return bool(readable)
