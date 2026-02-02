from __future__ import annotations
import asyncio
import logging
import os
import tempfile
from dataclasses import dataclass
import soundfile as sf
import nextcord
from nextcord.ext import commands
from nextcord.ext.listening import AudioSink, VoiceClient as ListenVoiceClient
import numpy as np
import librosa

from llm.chroma_client import ChromaClient
from memory.store import ChromaMemoryStore
from stt.whisper_client import WhisperClient
from orchestrator import AureliaOrchestrator
from discord_ui.vad_segmenter import VADSegmenter, VADConfig

logger = logging.getLogger(__name__)

class AureliaAudioSink(AudioSink):
    def __init__(self, bot: "AlwaysListenBot", vad_config: VADConfig):
        super().__init__()
        self.bot = bot
        self.vad = VADSegmenter(vad_config)
        self.buffers = {} # Accumulates raw stereo audio for processing
        self.vad_buffers = {} # Accumulates mono audio for VAD check
        self.silence_count = {}
        self.speaking = {}
        # VAD expects 10, 20, or 30ms. At 48kHz, 20ms is 960 samples.
        self.vad_frame_samples = int(vad_config.sample_rate * 0.02) # 20ms
        self.vad_frame_bytes = self.vad_frame_samples * 2

        # Pre-roll to avoid clipping start of speech
        self.pre_roll_buffers = {}
        self.pre_roll_ms = 500
        self.bytes_per_ms = (vad_config.sample_rate * 2 * 2) // 1000
        self.pre_roll_max_bytes = self.pre_roll_ms * self.bytes_per_ms

    def write(self, user, data):
        # nextcord-ext-listening: data might be VoiceData or raw bytes
        if hasattr(data, "pcm"):
            raw_data = data.pcm
        elif hasattr(data, "data"):
            raw_data = data.data
        else:
            raw_data = data

        if user not in self.buffers:
            self.buffers[user] = bytearray()
            self.vad_buffers[user] = bytearray()
            self.pre_roll_buffers[user] = bytearray()
            self.silence_count[user] = 0
            self.speaking[user] = False

        # Convert to mono for VAD check
        pcm_data = np.frombuffer(raw_data, dtype=np.int16)
        if pcm_data.size % 2 == 0:
            # Stereo to Mono: average L and R
            mono_data = pcm_data.reshape(-1, 2).mean(axis=1).astype(np.int16)
            self.vad_buffers[user].extend(mono_data.tobytes())
        else:
            # Fallback if already mono or corrupted
            self.vad_buffers[user].extend(raw_data)

        # Process VAD in fixed chunks
        while len(self.vad_buffers[user]) >= self.vad_frame_bytes:
            frame = bytes(self.vad_buffers[user][:self.vad_frame_bytes])
            del self.vad_buffers[user][:self.vad_frame_bytes]

            is_speech = self.vad.is_speech(frame)

            if is_speech:
                if not self.speaking[user]:
                    logger.info(f"User {user} started speaking.")
                    self.speaking[user] = True
                    # Prepend pre-roll to main buffer
                    self.buffers[user].extend(self.pre_roll_buffers[user])

                    # Interrupt Aurelia if she is speaking
                    if self.bot.bot and self.bot.bot.loop:
                        self.bot.bot.loop.call_soon_threadsafe(
                            lambda: asyncio.create_task(self.bot._orchestrator.stop_speaking())
                        )

                self.silence_count[user] = 0
            elif self.speaking[user]:
                self.silence_count[user] += 1

            # If speech is detected, reset silence counter.
            # If transitioning from silence to speech, pre-roll is already handled.
            pass

        # Always extend full buffer if speaking to ensure we capture the audio for processing.
        if self.speaking[user]:
            self.buffers[user].extend(raw_data)

        # Always update pre-roll buffer
        self.pre_roll_buffers[user].extend(raw_data)
        if len(self.pre_roll_buffers[user]) > self.pre_roll_max_bytes:
            del self.pre_roll_buffers[user][:-self.pre_roll_max_bytes]

        if self.speaking[user]:
            # 50 frames of 20ms = 1 second of silence
            if self.silence_count[user] > 50:
                logger.info(f"User {user} finished speaking. Processing segment...")
                audio_data = bytes(self.buffers[user])
                self.buffers[user].clear()
                self.vad_buffers[user].clear()
                self.pre_roll_buffers[user].clear()
                self.speaking[user] = False
                self.silence_count[user] = 0

                # Trigger bot processing
                if self.bot.bot and self.bot.bot.loop:
                    self.bot.bot.loop.call_soon_threadsafe(
                        lambda: asyncio.create_task(self.bot.process_audio_data(user, audio_data))
                    )

    def cleanup(self):
        self.buffers.clear()
        self.vad_buffers.clear()

@dataclass
class DiscordVoiceConfig:
    token: str
    guild_id: int
    voice_channel_id: int
    sample_rate: int
    discord_sample_rate: int

import time
import random

class AlwaysListenBot:
    def __init__(self, config: DiscordVoiceConfig, orchestrator: AureliaOrchestrator):
        self._config = config
        self._orchestrator = orchestrator
        self._orchestrator.discord_play_callback = self._play_response
        self._orchestrator.discord_stop_callback = self._stop_response
        self._voice_client = None
        self._is_listening = False
        self.bot = None
        self._response_queue = asyncio.Queue()
        self._worker_task = None

    async def run(self) -> None:
        self._orchestrator.discord_bot = self
        self._worker_task = asyncio.create_task(self._response_worker())
        intents = nextcord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        self.bot = commands.Bot(command_prefix="!", intents=intents)
        bot = self.bot

        @bot.command(name="stop")
        async def stop(ctx):
            """Stops the bot and disconnects from voice."""
            if self._voice_client:
                await self._voice_client.disconnect()
                self._voice_client = None
                self._is_listening = False
                await ctx.send("Disconnected from voice. Farewell!")
            else:
                await ctx.send("I'm not in a voice channel.")

        @bot.event
        async def on_ready():
            logger.info("Discord bot connected as %s", bot.user)
            await self._connect_to_voice(bot)
            self._update_members()

        @bot.event
        async def on_voice_state_update(member, before, after):
            if member == bot.user:
                return

            if self._voice_client:
                # Member joined our channel
                if after.channel == self._voice_client.channel and before.channel != self._voice_client.channel:
                    self._update_members()
                    await self._orchestrator.handle_event("member_join", {"user": member.display_name, "source": "discord"})

                # Member left our channel
                elif before.channel == self._voice_client.channel and after.channel != self._voice_client.channel:
                    self._update_members()
                    await self._orchestrator.handle_event("member_leave", {"user": member.display_name, "source": "discord"})

        @bot.event
        async def on_message(message: nextcord.Message):
            try:
                if message.author == bot.user or not self._voice_client:
                    return

                thinking_message = await message.channel.send("Thinking...")
                response_text = await self._orchestrator.process_text_input(message.content, message.author.display_name, "discord")
                await thinking_message.edit(content=response_text)
            except Exception as e:
                logger.exception("Error in on_message")
                await self._orchestrator.report_error(str(e))

        await bot.start(self._config.token)

    def _update_members(self):
        if self._voice_client and self._voice_client.channel:
            members = [
                m.display_name for m in self._voice_client.channel.members if not m.bot
            ]
            self._orchestrator.current_members = members
            logger.info(f"Current members in voice: {members}")

    async def _connect_to_voice(self, bot):
        await self.join_voice(self._config.voice_channel_id)

    async def join_voice(self, channel_id: int):
        if not self.bot:
            logger.error("Bot not initialized.")
            return

        channel = self.bot.get_channel(channel_id)
        if not channel:
            # Try to fetch it if it's not in cache
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception as e:
                logger.error(f"Could not find or fetch channel {channel_id}: {e}")
                return

        if self._voice_client:
            await self._voice_client.move_to(channel)
        else:
            self._voice_client = await channel.connect(cls=ListenVoiceClient)
            asyncio.create_task(self.start_listening())
        logger.info(f"Connected to voice channel: {channel.name}")

    async def leave_voice(self):
        if self._voice_client:
            await self._voice_client.disconnect()
            self._voice_client = None
            self._is_listening = False
            logger.info("Disconnected from voice channel.")

    async def _response_worker(self):
        """Processes the response queue and plays audio sequentially."""
        logger.info("Response worker started.")
        while True:
            audio_data, text_response, source_label = await self._response_queue.get()
            try:
                await self._actually_play_response(audio_data, text_response, source_label)
            except Exception as e:
                logger.exception(f"Error in response worker during {source_label}: {e}")
            finally:
                self._response_queue.task_done()

    async def _play_response(self, audio_data, text_response, source_label):
        """Puts a response into the queue."""
        await self._response_queue.put((audio_data, text_response, source_label))

    async def _stop_response(self):
        """Stops current audio playback and clears the response queue."""
        if self._voice_client and self._voice_client.is_playing():
            self._voice_client.stop()

        # Clear the queue to prevent further fragments from playing
        while not self._response_queue.empty():
            try:
                self._response_queue.get_nowait()
                self._response_queue.task_done()
            except asyncio.QueueEmpty:
                break
        logger.info("Discord audio playback stopped and queue cleared.")

    async def _actually_play_response(self, audio_data, text_response, source_label):
        """Helper to play audio response."""
        if audio_data is None:
            logger.warning(f"No audio data to play for {source_label}")
            return

        response_fp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        sf.write(response_fp.name, audio_data, self._config.sample_rate)
        response_fp.close()

        play_done = asyncio.Event()

        def cleanup_response(error):
            if error:
                logger.error(f"Error during playback ({source_label}): {error}")
            self._safe_delete(response_fp.name)
            play_done.set()

        if self._voice_client and self._voice_client.is_connected():
            # Ensure we don't start playing while something else is playing
            # (though the queue should handle this, autonomous actions or errors might skip the queue)
            while self._voice_client.is_playing():
                await asyncio.sleep(0.1)

            try:
                self._voice_client.play(nextcord.FFmpegPCMAudio(response_fp.name), after=cleanup_response)
                await play_done.wait()
            except Exception as e:
                logger.error(f"Failed to play audio: {e}")
                self._safe_delete(response_fp.name)
                play_done.set()
        else:
             self._safe_delete(response_fp.name)

    async def start_listening(self):
        if self._voice_client and not self._is_listening:
            self._is_listening = True
            # VAD config for Discord's 48kHz audio
            vad_config = VADConfig(aggressiveness=3, sample_rate=self._config.discord_sample_rate)
            sink = AureliaAudioSink(self, vad_config)
            self._voice_client.listen(sink)
            logger.info("Started listening in voice channel.")

    async def process_audio_data(self, user, data):
        fp_name = None
        try:
            fp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            fp_name = fp.name
            fp.close()

            audio_np = np.frombuffer(data, dtype=np.int16)
            if audio_np.size % 2 == 0:
                audio_mono = audio_np.reshape(-1, 2).mean(axis=1).astype(np.float32) / 32768.0
            else:
                audio_mono = audio_np.astype(np.float32) / 32768.0

            resampled_audio = librosa.resample(audio_mono, orig_sr=self._config.discord_sample_rate, target_sr=self._config.sample_rate)
            sf.write(fp_name, resampled_audio, self._config.sample_rate)

            await self._orchestrator.process_audio_input(fp_name, str(user), "discord")
        except Exception as e:
            logger.exception("Error in process_audio_data")
            await self._orchestrator.report_error(str(e))
        finally:
            if fp_name:
                self._safe_delete(fp_name)

    def _safe_delete(self, filepath: str):
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
                logger.info(f"Deleted temporary file: {filepath}")
        except Exception as e:
            logger.warning(f"Failed to delete temporary file {filepath}: {e}")
