from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from chroma.chroma_voice import ChromaVoice
from core.orchestrator import Orchestrator
from discord_ui.vad_segmenter import VADConfig, VADSegmenter

logger = logging.getLogger(__name__)


@dataclass
class DiscordVoiceConfig:
    token: str
    guild_id: int
    voice_channel_id: int
    sample_rate: int
    vad_aggressiveness: int


class AlwaysListenBot:
    def __init__(
        self,
        config: DiscordVoiceConfig,
        orchestrator: Orchestrator,
        chroma_voice: ChromaVoice,
        on_reply: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._config = config
        self._orchestrator = orchestrator
        self._chroma_voice = chroma_voice
        self._on_reply = on_reply
        self._vad = VADSegmenter(
            VADConfig(aggressiveness=config.vad_aggressiveness, sample_rate=config.sample_rate)
        )

    async def run(self) -> None:
        try:
            import nextcord
            from nextcord.ext import commands
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("nextcord is required to run the Discord bot") from exc

        intents = nextcord.Intents.default()
        intents.message_content = True
        bot = commands.Bot(command_prefix="!", intents=intents)

        @bot.event
        async def on_ready() -> None:
            logger.info("Discord bot connected as %s", bot.user)
            await self._connect_voice(bot)

        @bot.event
        async def on_message(message: nextcord.Message) -> None:  # type: ignore[override]
            if message.author == bot.user:
                return
            response = self._orchestrator.respond(message.content, source="discord-text")
            await message.channel.send(response.text)

        await bot.start(self._config.token)

    async def _connect_voice(self, bot) -> None:
        guild = bot.get_guild(self._config.guild_id)
        if guild is None:
            logger.error("Guild not found: %s", self._config.guild_id)
            return
        channel = guild.get_channel(self._config.voice_channel_id)
        if channel is None:
            logger.error("Voice channel not found: %s", self._config.voice_channel_id)
            return
        try:
            await channel.connect()
            logger.info("Connected to voice channel %s", channel.name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unable to connect to voice channel: %s", exc)

    def handle_audio(self, audio: np.ndarray) -> str:
        frames = self._vad.frame_bytes(audio)
        if not frames:
            return ""
        speech_frames = [frame for frame in frames if self._vad.is_speech(frame)]
        if not speech_frames:
            return ""
        transcript = self._chroma_voice.transcribe(audio)
        response = self._orchestrator.respond(transcript, source="discord-voice")
        if self._on_reply:
            self._on_reply(response.text)
        return response.text
