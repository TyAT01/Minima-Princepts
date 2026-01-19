"""Discord voice adapter using transcript log files."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable

from princess_ai.input_adapters.base import InputAdapter
from princess_ai.schemas.events import Event


class DiscordVoiceAdapter(InputAdapter):
    """Discord voice adapter that tails a local transcript log file."""

    def __init__(self, transcript_path: Path | None = None, username_fallback: str = "discord_user") -> None:
        self._logger = logging.getLogger(__name__)
        self._transcript_path = transcript_path or self._resolve_log_path()
        self._username_fallback = username_fallback
        self._offset = 0
        if not self._transcript_path:
            self._logger.warning(
                "DiscordVoiceAdapter disabled; set PRINCESS_DISCORD_TRANSCRIPT_LOG to enable file input."
            )
        else:
            self._logger.info("DiscordVoiceAdapter watching %s", self._transcript_path)

    def poll(self) -> Iterable[Event]:
        if not self._transcript_path:
            return []
        try:
            if not self._transcript_path.exists():
                return []
            with self._transcript_path.open("r", encoding="utf-8") as handle:
                handle.seek(self._offset)
                lines = handle.readlines()
                self._offset = handle.tell()
        except (OSError, ValueError) as exc:
            self._logger.warning("Failed to read Discord transcript log: %s", exc)
            return []

        events = []
        for line in lines:
            parsed = self._parse_line(line)
            if parsed:
                events.append(parsed)
        return events

    def _parse_line(self, line: str) -> Event | None:
        cleaned = line.strip()
        if not cleaned:
            return None
        username, _, message = cleaned.partition(":")
        username = username.strip() or self._username_fallback
        text = message.strip() if message else cleaned
        if not text:
            return None
        return Event(
            source="discord",
            user_id=username,
            username=username,
            text=text,
            metadata={"mode": "voice_transcript"},
        )

    @staticmethod
    def _resolve_log_path() -> Path | None:
        value = os.getenv("PRINCESS_DISCORD_TRANSCRIPT_LOG", "").strip()
        return Path(value) if value else None
