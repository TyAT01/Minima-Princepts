"""YouTube live chat adapters."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode
from urllib.request import urlopen, Request

from adapters.base import InputAdapter
from adapters.schemas import Event


class YouTubeChatAdapter(InputAdapter):
    """Native YouTube live chat adapter using polling."""

    def __init__(
        self,
        api_key: str | None = None,
        token: str | None = None,
        live_chat_id: str | None = None,
        username_fallback: str = "youtube_user",
    ) -> None:
        self._logger = logging.getLogger(__name__)
        self._api_key = api_key or os.getenv("AURELIA_VALE_YOUTUBE_API_KEY", "").strip()
        self._token = token or os.getenv("AURELIA_VALE_YOUTUBE_TOKEN", "").strip()
        self._live_chat_id = live_chat_id or os.getenv("AURELIA_VALE_YOUTUBE_LIVE_CHAT_ID", "").strip()
        self._username_fallback = username_fallback
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._loop = asyncio.get_event_loop()
        self._next_page_token: str | None = None
        self._recent_ids = deque(maxlen=300)
        self._reconnects = 0
        if self._api_key and self._live_chat_id:
            if self._loop.is_running():
                self._loop.create_task(self._run())
            else:
                self._logger.warning("Event loop not running; YouTube adapter idle.")
        else:
            self._logger.warning("YouTube adapter missing API key or liveChatId.")

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
                await self._poll()
                backoff = 1
            except Exception as exc:
                self._logger.exception("YouTube adapter error: %s", exc)
                self._reconnects += 1
                await asyncio.sleep(min(backoff, 30))
                backoff *= 2

    async def _poll(self) -> None:
        query = {
            "liveChatId": self._live_chat_id,
            "part": "snippet,authorDetails",
            "maxResults": 200,
            "key": self._api_key,
        }
        if self._next_page_token:
            query["pageToken"] = self._next_page_token
        url = f"https://www.googleapis.com/youtube/v3/liveChat/messages?{urlencode(query)}"

        def fetch_messages():
            with urlopen(url, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))

        loop = asyncio.get_event_loop()
        payload = await loop.run_in_executor(None, fetch_messages)
        self._next_page_token = payload.get("nextPageToken")
        polling_ms = payload.get("pollingIntervalMillis", 2000)
        for item in payload.get("items", []):
            snippet = item.get("snippet", {})
            author = item.get("authorDetails", {})
            text = snippet.get("displayMessage", "")
            if not text:
                continue
            message_id = item.get("id")
            if message_id:
                if message_id in self._recent_ids:
                    continue
                self._recent_ids.append(message_id)
            event = Event(
                source="youtube",
                user_id=author.get("channelId", self._username_fallback),
                username=author.get("displayName", self._username_fallback),
                text=text,
                metadata={
                    "message_type": snippet.get("type"),
                    "channel": self._live_chat_id,
                    "message_id": message_id,
                },
            )
            await self._queue.put(event)
        await asyncio.sleep(polling_ms / 1000)

    async def send_message(self, text: str) -> None:
        """Sends a message to the YouTube live chat."""
        if not self._token:
            self._logger.warning("YouTube adapter missing OAuth token; cannot send message.")
            return

        self._logger.info(f"Sending message to YouTube: {text}")
        url = f"https://www.googleapis.com/youtube/v3/liveChat/messages?part=snippet&key={self._api_key}"
        data = {
            "snippet": {
                "liveChatId": self._live_chat_id,
                "type": "textMessageEvent",
                "textMessageDetails": {
                    "messageText": text
                }
            }
        }
        try:
            req = Request(url, data=json.dumps(data).encode("utf-8"), method="POST")
            req.add_header("Authorization", f"Bearer {self._token}")
            req.add_header("Content-Type", "application/json")

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, urlopen, req)
        except Exception as e:
            self._logger.error(f"Failed to send YouTube message: {e}")


class YouTubeLogAdapter(InputAdapter):
    """Fallback adapter that tails a local log file."""

    def __init__(self, log_path: Path | None = None, username_fallback: str = "youtube_user") -> None:
        self._logger = logging.getLogger(__name__)
        self._log_path = log_path or self._resolve_log_path()
        self._username_fallback = username_fallback
        self._offset = 0
        if not self._log_path:
            self._logger.warning(
                "YouTubeLogAdapter disabled; set AURELIA_YOUTUBE_CHAT_LOG to enable file input."
            )
        else:
            self._logger.info("YouTubeLogAdapter watching %s", self._log_path)

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
            self._logger.warning("Failed to read YouTube chat log: %s", exc)
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
            source="youtube",
            user_id=username,
            username=username,
            text=text,
        )

    @staticmethod
    def _resolve_log_path() -> Path | None:
        value = os.getenv("AURELIA_YOUTUBE_CHAT_LOG", "").strip()
        return Path(value) if value else None
