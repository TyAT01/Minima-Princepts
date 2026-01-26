from __future__ import annotations
import asyncio
import logging
import tempfile
from dataclasses import dataclass
import soundfile as sf
import nextcord
from nextcord.ext import commands, listening
import numpy as np
import librosa

from llm.chroma_client import ChromaClient
from memory.store import ChromaMemoryStore
from stt.whisper_client import WhisperClient

logger = logging.getLogger(__name__)

@dataclass
class DiscordVoiceConfig:
    token: str
    guild_id: int
    voice_channel_id: int
    sample_rate: int
    discord_sample_rate: int

class AlwaysListenBot:
    def __init__(self, config: DiscordVoiceConfig, chroma_client: ChromaClient, memory_store: ChromaMemoryStore, whisper_client: WhisperClient):
        self._config = config
        self._chroma_client = chroma_client
        self._memory_store = memory_store
        self._whisper_client = whisper_client
        self._voice_client = None
        self._is_listening = False
        self._user_audio_data = {}

    async def run(self) -> None:
        intents = nextcord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        bot = commands.Bot(command_prefix="!", intents=intents)

        @bot.event
        async def on_ready():
            logger.info("Discord bot connected as %s", bot.user)
            await self._connect_to_voice(bot)

        @bot.event
        async def on_message(message: nextcord.Message):
            if message.author == bot.user or not self._voice_client:
                return

            thinking_message = await message.channel.send("Thinking...")

            # Search memory for relevant context
            memories = self._memory_store.search(message.content)
            context = "\n".join([f"- User: {mem['user_text']}, Bot: {mem['bot_text']}" for mem in memories])

            loop = asyncio.get_event_loop()
            response_audio, response_text = await loop.run_in_executor(
                None, self._chroma_client.respond_to_text, message.content, context
            )

            await thinking_message.edit(content=response_text)

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as fp:
                sf.write(fp.name, response_audio, self._config.sample_rate)
                self._voice_client.play(nextcord.FFmpegPCMAudio(fp.name), after=lambda e: logger.info("Finished playing audio."))

            self._memory_store.store_memory(message.content, response_text)

        await bot.start(self._config.token)

    async def _connect_to_voice(self, bot):
        guild = bot.get_guild(self._config.guild_id)
        if not guild:
            logger.error(f"Guild {self._config.guild_id} not found.")
            return
        channel = guild.get_channel(self._config.voice_channel_id)
        if not channel:
            logger.error(f"Channel {self._config.voice_channel_id} not found.")
            return

        self._voice_client = await channel.connect(cls=listening.ListenVoiceClient)
        asyncio.create_task(self.start_listening())

    async def start_listening(self):
        if self._voice_client and not self._is_listening:
            self._is_listening = True
            self._voice_client.listen(self.process_user_audio, after=self.after_listening)

    def process_user_audio(self, user, data):
        if user not in self._user_audio_data:
            self._user_audio_data[user] = bytearray()
        self._user_audio_data[user].extend(data)

    def after_listening(self, error):
        self._is_listening = False
        if error:
            logger.error(f"Error in listening: {error}")
            return

        loop = asyncio.get_event_loop()
        for user, data in self._user_audio_data.items():
            if len(data) > 0:
                asyncio.run_coroutine_threadsafe(self.process_audio_data(user, data), loop)
        self._user_audio_data.clear()

        asyncio.run_coroutine_threadsafe(self.start_listening(), loop)

    async def process_audio_data(self, user, data):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as fp:
            audio_data = np.frombuffer(data, dtype=np.int16)
            resampled_audio = librosa.resample(audio_data.astype(np.float32), orig_sr=self._config.discord_sample_rate, target_sr=self._config.sample_rate)
            sf.write(fp.name, resampled_audio, self._config.sample_rate)

            loop = asyncio.get_event_loop()

            # Transcribe audio to text
            user_text = await loop.run_in_executor(
                None, self._whisper_client.transcribe, fp.name
            )

            if not user_text or not user_text.strip():
                logger.info("Whisper transcribed empty text.")
                return

            logger.info(f"Transcribed text from {user}: {user_text}")

            # Search memory for relevant context
            memories = self._memory_store.search(user_text)
            context = "\n".join([f"- User: {mem['user_text']}, Bot: {mem['bot_text']}" for mem in memories])

            # Generate response using direct audio input for better multimodal understanding
            response_audio, response_text = await loop.run_in_executor(
                None, self._chroma_client.respond_to_audio, fp.name, context
            )

            if not response_text:
                logger.warning("Chroma client returned empty text response.")
                return

            # Play response and store memory
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as response_fp:
                sf.write(response_fp.name, response_audio, self._config.sample_rate)
                self._voice_client.play(nextcord.FFmpegPCMAudio(response_fp.name), after=lambda e: logger.info("Finished playing audio."))

            self._memory_store.store_memory(user_text, response_text)
