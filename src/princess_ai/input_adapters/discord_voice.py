"""Discord voice adapter for realtime PCM streaming."""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
from pathlib import Path
from typing import Iterable, Optional

from princess_ai.audio.pipeline import AudioFrame, AudioPipeline
from princess_ai.audio.stt import DummyStreamingSTT, STTConfig, VoskStreamingSTT
from princess_ai.audio.tts import DummyTTS, PyTTSx3Engine, TTSConfig
from princess_ai.input_adapters.base import InputAdapter
from princess_ai.schemas.events import Event


class DiscordVoiceAdapter(InputAdapter):
    """Discord voice adapter that uses Discord voice API for PCM frames."""

    def __init__(
        self,
        token: str | None = None,
        guild_id: int | None = None,
        channel_id: int | None = None,
        username_fallback: str = "discord_user",
        stt_config: STTConfig | None = None,
        tts_config: TTSConfig | None = None,
    ) -> None:
        self._logger = logging.getLogger(__name__)
        self._username_fallback = username_fallback
        self._token = token or os.getenv("PRINCESS_DISCORD_TOKEN", "").strip()
        self._guild_id = guild_id or self._get_env_int("PRINCESS_DISCORD_GUILD_ID")
        self._channel_id = channel_id or self._get_env_int("PRINCESS_DISCORD_CHANNEL_ID")
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._loop = asyncio.get_event_loop()
        self._pipeline = AudioPipeline(
            stt=VoskStreamingSTT(stt_config) if self._has_vosk() else DummyStreamingSTT(),
            tts=PyTTSx3Engine(tts_config) if self._has_tts() else DummyTTS(),
            on_barge_in=self._handle_barge_in,
        )
        self._client = None
        self._voice_client = None
        self._ensure_client()

    def poll(self) -> Iterable[Event]:
        events: list[Event] = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return events

    def ingest_audio_frame(
        self,
        pcm_frame: bytes,
        sample_rate: int = 48000,
        channels: int = 2,
        user_id: str | None = None,
        username: str | None = None,
    ) -> None:
        """Accept PCM audio from a voice receiver and enqueue transcripts."""
        try:
            frame = AudioFrame(data=pcm_frame, sample_rate=sample_rate, channels=channels)
            for chunk in self._pipeline.ingest(frame):
                if not chunk.text:
                    continue
                event = Event(
                    source="discord",
                    user_id=user_id or self._username_fallback,
                    username=username or self._username_fallback,
                    text=chunk.text,
                    metadata={
                        "mode": "voice",
                        "final": chunk.is_final,
                        "channel": str(self._channel_id) if self._channel_id else None,
                    },
                )
                self._queue.put_nowait(event)
        except Exception as exc:  # noqa: BLE001 - keep audio ingestion resilient
            self._logger.exception("Failed to ingest Discord audio frame: %s", exc)

    def start(self) -> None:
        if not self._client or not self._token:
            self._logger.warning("Discord voice adapter not configured.")
            return
        if not self._loop.is_running():
            self._logger.warning("Event loop not running; Discord voice adapter idle.")
            return
        self._loop.create_task(self._client.start(self._token))

    def stop(self) -> None:
        if not self._client:
            return
        self._loop.create_task(self._client.close())

    @staticmethod
    def _get_env_int(key: str) -> int | None:
        value = os.getenv(key, "").strip()
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    @staticmethod
    def _has_vosk() -> bool:
        return importlib.util.find_spec("vosk") is not None

    @staticmethod
    def _has_tts() -> bool:
        return importlib.util.find_spec("pyttsx3") is not None

    def _ensure_client(self) -> None:
        if not importlib.util.find_spec("discord"):
            self._logger.warning("discord.py not installed; Discord voice adapter disabled.")
            return
        import discord  # type: ignore

        try:
            intents = discord.Intents.default()
            intents.message_content = True
            self._client = discord.Client(intents=intents)

            @self._client.event
            async def on_ready() -> None:  # type: ignore[override]
                self._logger.info("Discord voice adapter connected as %s", self._client.user)
                await self._join_voice()

            @self._client.event
            async def on_message(message: discord.Message) -> None:  # type: ignore[override]
                if message.author.bot:
                    return
                event = Event(
                    source="discord",
                    user_id=str(message.author.id),
                    username=message.author.display_name or self._username_fallback,
                    text=message.content,
                    metadata={"mode": "text"},
                )
                await self._queue.put(event)
        except Exception as exc:  # noqa: BLE001 - keep adapter resilient
            self._logger.exception("Failed to initialize Discord client: %s", exc)
            self._client = None

    async def _join_voice(self) -> None:
        if not self._client or not self._guild_id or not self._channel_id:
            self._logger.warning("Discord voice adapter missing guild/channel configuration.")
            return
        try:
            guild = self._client.get_guild(self._guild_id)
            if not guild:
                self._logger.warning("Discord guild %s not found.", self._guild_id)
                return
            channel = guild.get_channel(self._channel_id)
            if not channel:
                self._logger.warning("Discord channel %s not found.", self._channel_id)
                return
            if hasattr(channel, "connect"):
                self._voice_client = await channel.connect()
                self._logger.info("Joined Discord voice channel %s", self._channel_id)
        except Exception as exc:  # noqa: BLE001 - keep adapter resilient
            self._logger.exception("Failed to join Discord voice: %s", exc)

    def _handle_barge_in(self) -> None:
        self._logger.info("Barge-in detected; pausing TTS output.")


class DiscordTranscriptAdapter(InputAdapter):
    """Fallback adapter that tails transcript logs."""

    def __init__(self, transcript_path: Path | None = None, username_fallback: str = "discord_user") -> None:
        self._logger = logging.getLogger(__name__)
        self._transcript_path = transcript_path or self._resolve_log_path()
        self._username_fallback = username_fallback
        self._offset = 0
        if not self._transcript_path:
            self._logger.warning(
                "DiscordTranscriptAdapter disabled; set PRINCESS_DISCORD_TRANSCRIPT_LOG to enable file input."
            )
        else:
            self._logger.info("DiscordTranscriptAdapter watching %s", self._transcript_path)

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
