"""Twitch chat adapters."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Iterable

from princess_ai.input_adapters.base import InputAdapter
from princess_ai.schemas.events import Event


class TwitchChatAdapter(InputAdapter):
    """Native Twitch IRC chat adapter with reconnect/backoff."""

    def __init__(
        self,
        username: str | None = None,
        token: str | None = None,
        channel: str | None = None,
        username_fallback: str = "twitch_user",
    ) -> None:
        self._logger = logging.getLogger(__name__)
        self._username = username or os.getenv("PRINCESS_TWITCH_USERNAME", "").strip()
        self._token = token or os.getenv("PRINCESS_TWITCH_TOKEN", "").strip()
        self._channel = (channel or os.getenv("PRINCESS_TWITCH_CHANNEL", "").strip()).lstrip("#")
        self._username_fallback = username_fallback
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._loop = asyncio.get_event_loop()
        self._connected = False
        if self._username and self._token and self._channel:
            if self._loop.is_running():
                self._loop.create_task(self._run())
            else:
                self._logger.warning("Event loop not running; Twitch adapter idle.")
        else:
            self._logger.warning("Twitch adapter missing credentials or channel.")

    def poll(self) -> Iterable[Event]:
        events: list[Event] = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return events

    async def _run(self) -> None:
        backoff = 1
        while True:
            try:
                await self._connect()
                backoff = 1
            except Exception as exc:  # noqa: BLE001 - keep adapter resilient
                self._logger.exception("Twitch adapter error: %s", exc)
                await asyncio.sleep(min(backoff, 30))
                backoff *= 2

    async def _connect(self) -> None:
        self._logger.info("Connecting to Twitch IRC channel #%s", self._channel)
        reader, writer = await asyncio.open_connection("irc.chat.twitch.tv", 6667)
        writer.write(f"PASS {self._token}\r\n".encode())
        writer.write(f"NICK {self._username}\r\n".encode())
        writer.write(f"JOIN #{self._channel}\r\n".encode())
        await writer.drain()
        self._connected = True
        while True:
            line = await reader.readline()
            if not line:
                break
            decoded = line.decode(errors="ignore").strip()
            if decoded.startswith("PING"):
                writer.write("PONG :tmi.twitch.tv\r\n".encode())
                await writer.drain()
                continue
            event = self._parse_irc_message(decoded)
            if event:
                await self._queue.put(event)
        self._connected = False

    def _parse_irc_message(self, line: str) -> Event | None:
        try:
            if "PRIVMSG" not in line:
                return None
            prefix, _, content = line.partition(" PRIVMSG ")
            username = prefix.split("!", maxsplit=1)[0].lstrip(":") if prefix else self._username_fallback
            _, _, message = content.partition(" :")
            if not message.strip():
                return None
            return Event(
                source="twitch",
                user_id=username,
                username=username,
                text=message.strip(),
                metadata={"channel": self._channel},
            )
        except Exception as exc:  # noqa: BLE001 - keep parsing resilient
            self._logger.exception("Failed to parse Twitch message: %s", exc)
            return None


class TwitchLogAdapter(InputAdapter):
    """Fallback adapter that tails a local log file."""

    def __init__(self, log_path: Path | None = None, username_fallback: str = "twitch_user") -> None:
        self._logger = logging.getLogger(__name__)
        self._log_path = log_path or self._resolve_log_path()
        self._username_fallback = username_fallback
        self._offset = 0
        if not self._log_path:
            self._logger.warning(
                "TwitchLogAdapter disabled; set PRINCESS_TWITCH_CHAT_LOG to enable file input."
            )
        else:
            self._logger.info("TwitchLogAdapter watching %s", self._log_path)

    def poll(self) -> Iterable[Event]:
        if not self._log_path:
            return []
        try:
            if not self._log_path.exists():
                return []
            with self._log_path.open("r", encoding="utf-8") as handle:
                handle.seek(self._offset)
                lines = handle.readlines()
                self._offset = handle.tell()
        except (OSError, ValueError) as exc:
            self._logger.warning("Failed to read Twitch chat log: %s", exc)
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
            source="twitch",
            user_id=username,
            username=username,
            text=text,
        )

    @staticmethod
    def _resolve_log_path() -> Path | None:
        value = os.getenv("PRINCESS_TWITCH_CHAT_LOG", "").strip()
        return Path(value) if value else None
