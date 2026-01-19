"""Discord voice adapter for realtime PCM streaming."""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import time
from pathlib import Path
from queue import Empty, Queue
from typing import Iterable

from princess_ai.audio.pcm import chunk_pcm, ensure_pcm_format
from princess_ai.audio.pipeline import AudioFrame, AudioPipeline
from princess_ai.audio.stt import DummyStreamingSTT, STTConfig, VoskStreamingSTT
from princess_ai.audio.tts import DummyTTS, PyTTSx3Engine, TTSConfig
from princess_ai.input_adapters.base import InputAdapter
from princess_ai.runtime.telemetry import TelemetryHub
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
        telemetry: TelemetryHub | None = None,
    ) -> None:
        self._logger = logging.getLogger(__name__)
        self._username_fallback = username_fallback
        self._token = token or os.getenv("PRINCESS_DISCORD_TOKEN", "").strip()
        self._guild_id = guild_id or self._get_env_int("PRINCESS_DISCORD_GUILD_ID")
        self._channel_id = channel_id or self._get_env_int("PRINCESS_DISCORD_CHANNEL_ID")
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._loop = asyncio.get_event_loop()
        self._telemetry = telemetry
        self._pipeline = AudioPipeline(
            stt=VoskStreamingSTT(stt_config) if self._has_vosk() else DummyStreamingSTT(),
            tts=PyTTSx3Engine(tts_config) if self._has_tts() else DummyTTS(),
            on_barge_in=self._handle_barge_in,
        )
        self._client = None
        self._voice_client = None
        self._audio_queue: Queue[bytes] = Queue(maxsize=400)
        self._audio_source = None
        self._dropped_audio_frames = 0
        self._tts_lock = asyncio.Lock()
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
                if self._telemetry:
                    if chunk.is_final:
                        self._telemetry.update_voice(last_final_transcript=chunk.text)
                    else:
                        self._telemetry.update_voice(last_transcript=chunk.text)
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
        finally:
            if self._telemetry:
                self._telemetry.update_voice(
                    listening=self._pipeline.state.listening,
                    speaking=self._pipeline.state.speaking,
                    barge_in=self._pipeline.state.barge_in_detected,
                    tts_queue=list(self._pipeline.state.tts_queue),
                )

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

    def speak(self, text: str) -> None:
        if not text.strip():
            return
        if not self._loop.is_running():
            self._logger.warning("Event loop not running; unable to speak.")
            return
        self._loop.create_task(self._enqueue_tts(text))

    def interrupt(self) -> None:
        self._pipeline.stop_tts()
        self._clear_audio_queue()
        if self._voice_client and getattr(self._voice_client, "is_playing", lambda: False)():
            try:
                self._voice_client.stop()
            except Exception as exc:  # noqa: BLE001 - keep stop resilient
                self._logger.exception("Failed to stop Discord playback: %s", exc)

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
                if self._telemetry:
                    self._telemetry.update_adapter("discord", connected=True)
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
                if self._telemetry:
                    self._telemetry.update_adapter("discord", last_event_at=time.time())
        except Exception as exc:  # noqa: BLE001 - keep adapter resilient
            self._logger.exception("Failed to initialize Discord client: %s", exc)
            self._client = None
            if self._telemetry:
                self._telemetry.update_adapter("discord", connected=False, last_error=str(exc))

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
                voice_client = await self._connect_voice(channel)
                self._voice_client = voice_client
                self._logger.info("Joined Discord voice channel %s", self._channel_id)
        except Exception as exc:  # noqa: BLE001 - keep adapter resilient
            self._logger.exception("Failed to join Discord voice: %s", exc)
            if self._telemetry:
                self._telemetry.update_adapter("discord", last_error=str(exc))

    def _handle_barge_in(self) -> None:
        self._logger.info("Barge-in detected; pausing TTS output.")
        if self._telemetry:
            self._telemetry.update_voice(barge_in=True)
        self.interrupt()

    async def _enqueue_tts(self, text: str) -> None:
        async with self._tts_lock:
            for frame in self._pipeline.enqueue_tts(text):
                pcm = ensure_pcm_format(frame, target_rate=48000, target_channels=2)
                for chunk in chunk_pcm(pcm, 3840):
                    if not self._try_enqueue_audio(chunk):
                        self._dropped_audio_frames += 1
                        if self._telemetry:
                            self._telemetry.update_qos(dropped_audio_frames=self._dropped_audio_frames)
                        break
            self._ensure_playback()
            if self._telemetry:
                self._telemetry.update_voice(
                    speaking=self._pipeline.state.speaking,
                    tts_queue=list(self._pipeline.state.tts_queue),
                )

    def _ensure_playback(self) -> None:
        if not self._voice_client:
            return
        is_playing = getattr(self._voice_client, "is_playing", lambda: False)()
        if is_playing:
            return
        source = self._get_audio_source()
        try:
            self._voice_client.play(source)
        except Exception as exc:  # noqa: BLE001 - keep playback resilient
            self._logger.exception("Failed to start Discord playback: %s", exc)

    def _get_audio_source(self) -> "DiscordPCMSource":
        if self._audio_source is None:
            self._audio_source = DiscordPCMSource(self._audio_queue)
        return self._audio_source

    def _try_enqueue_audio(self, data: bytes) -> bool:
        try:
            self._audio_queue.put_nowait(data)
            return True
        except Exception:
            return False

    def _clear_audio_queue(self) -> None:
        try:
            while True:
                self._audio_queue.get_nowait()
        except Empty:
            return

    async def _connect_voice(self, channel) -> object:
        voice_recv_spec = importlib.util.find_spec("discord.ext.voice_recv")
        if voice_recv_spec:
            from discord.ext import voice_recv  # type: ignore

            voice_client = await channel.connect(cls=voice_recv.VoiceRecvClient)
            try:
                voice_client.listen(DiscordAudioSink(self))
            except Exception as exc:  # noqa: BLE001 - keep receiver resilient
                self._logger.exception("Failed to attach voice receiver: %s", exc)
            return voice_client
        return await channel.connect()


class DiscordAudioSink:
    """Voice receiver sink for discord.ext.voice_recv."""

    def __init__(self, adapter: DiscordVoiceAdapter) -> None:
        self._adapter = adapter

    def wants_opus(self) -> bool:
        return False

    def write(self, user, data) -> None:
        try:
            pcm = getattr(data, "pcm", data)
            if not pcm:
                return
            username = getattr(user, "display_name", None) or getattr(user, "name", None)
            user_id = str(getattr(user, "id", ""))
            self._adapter.ingest_audio_frame(
                pcm,
                sample_rate=48000,
                channels=2,
                user_id=user_id or None,
                username=username or None,
            )
            if self._adapter._telemetry:
                self._adapter._telemetry.update_adapter(
                    "discord",
                    last_event_at=time.time(),
                )
        except Exception as exc:  # noqa: BLE001 - keep sink resilient
            self._adapter._logger.exception("Discord audio sink failed: %s", exc)
            if self._adapter._telemetry:
                self._adapter._telemetry.update_adapter("discord", last_error=str(exc))


class DiscordPCMSource:
    """Audio source reading PCM frames from a queue."""

    def __init__(self, queue: Queue[bytes], frame_size: int = 3840) -> None:
        self._queue = queue
        self._frame_size = frame_size
        self._silence = b"\x00" * frame_size

    def read(self) -> bytes:
        try:
            return self._queue.get_nowait()
        except Empty:
            return self._silence

    def is_opus(self) -> bool:
        return False


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
