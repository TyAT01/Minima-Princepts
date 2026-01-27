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
                self.silence_count[user] = 0
            elif self.speaking[user]:
                self.silence_count[user] += 1

            # Accumulate raw data if we are in a speaking state
            if self.speaking[user]:
                # Note: This is a bit simplified; ideally we'd map VAD frames back to raw packets
                # But since we extension raw_data every write, it's roughly aligned.
                # Actually, the original logic extended in write, let's keep it similar but better.
                pass

        # Always extend full buffer if speaking
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
    def __init__(self, config: DiscordVoiceConfig, chroma_client: ChromaClient, memory_store: ChromaMemoryStore, whisper_client: WhisperClient):
        self._config = config
        self._chroma_client = chroma_client
        self._memory_store = memory_store
        self._whisper_client = whisper_client
        self._voice_client = None
        self._is_listening = False
        self.bot = None
        self._current_members = []
        self._last_interaction_time = time.time()
        self._autonomous_task = None
        self._response_queue = asyncio.Queue()
        self._worker_task = None

    async def run(self) -> None:
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
            if self._voice_client and (before.channel == self._voice_client.channel or after.channel == self._voice_client.channel):
                self._update_members()

        @bot.event
        async def on_message(message: nextcord.Message):
          try:
            if message.author == bot.user or not self._voice_client:
                return

            self._last_interaction_time = time.time()
            thinking_message = await message.channel.send("Thinking...")

            # Search memory for relevant context
            memories = self._memory_store.search(message.content)
            long_term_context = "\n".join([f"- User: {mem['user_text']}, Bot: {mem['bot_text']}" for mem in memories])
            short_term_context = self._memory_store.get_short_term_context()

            is_alone = len(self._current_members) == 0
            room_context = f"Room members: {', '.join(self._current_members) if not is_alone else 'None (Aurelia is alone)'}."

            full_context = f"{room_context}\n\n[Short-term Memory]\n{short_term_context}\n\n[Long-term Memory]\n{long_term_context}"

            loop = asyncio.get_event_loop()
            response_audio, response_text = await loop.run_in_executor(
                None, self._chroma_client.respond_to_text, message.content, full_context
            )

            await thinking_message.edit(content=response_text)
            await self._play_response(response_audio, response_text, "Text Message")

            self._memory_store.store_memory(message.content, response_text)
          except Exception as e:
            logger.exception("Error in on_message")
            await self._report_error(str(e))

        await bot.start(self._config.token)

    def _update_members(self):
        if self._voice_client and self._voice_client.channel:
            self._current_members = [
                m.display_name for m in self._voice_client.channel.members if not m.bot
            ]
            logger.info(f"Current members in voice: {self._current_members}")

    async def _connect_to_voice(self, bot):
        guild = bot.get_guild(self._config.guild_id)
        if not guild:
            logger.error(f"Guild {self._config.guild_id} not found.")
            return
        channel = guild.get_channel(self._config.voice_channel_id)
        if not channel:
            logger.error(f"Channel {self._config.voice_channel_id} not found.")
            return

        self._voice_client = await channel.connect(cls=ListenVoiceClient)
        asyncio.create_task(self.start_listening())
        self._autonomous_task = asyncio.create_task(self.autonomous_loop())

    async def autonomous_loop(self):
        """A background loop that makes Aurelia proactive."""
        logger.info("Autonomous loop started.")
        while True:
            await asyncio.sleep(60) # Check every minute

            now = time.time()
            idle_time = now - self._last_interaction_time

            # If idle for more than 5 minutes, or if alone and feels like doing something
            if idle_time > 300:
                await self._think_and_act()

    async def _think_and_act(self):
      try:
        """Aurelia decides what to do when idle."""
        if not self._voice_client or not self._voice_client.is_connected():
            return

        # Don't act if already speaking or someone else is speaking
        if self._voice_client.is_playing():
            return

        logger.info("Aurelia is thinking autonomously...")

        is_alone = len(self._current_members) == 0
        state_context = f"Status: {'Alone' if is_alone else 'Idle'}. Members present: {', '.join(self._current_members) if not is_alone else 'None'}."

        # We use respond_to_text with a special prompt for autonomous thought
        prompt = "You've been quiet for a while. What are you thinking or doing right now? If alone, maybe you are running simulations or talking to yourself. If people are there but quiet, maybe you are bored or want to start a conversation."

        # Get memory context
        memories = self._memory_store.search("current state")
        memory_context = "\n".join([f"- User: {mem['user_text']}, Bot: {mem['bot_text']}" for mem in memories])
        full_context = f"{state_context}\n\n{memory_context}"

        loop = asyncio.get_event_loop()
        response_audio, response_text = await loop.run_in_executor(
            None, self._chroma_client.respond_to_text, prompt, full_context
        )

        if response_text:
            logger.info(f"Autonomous action: {response_text}")
            await self._play_response(response_audio, response_text, "Autonomous Thought")
            self._last_interaction_time = time.time()
      except Exception as e:
        logger.exception("Error in _think_and_act")
        await self._report_error(str(e))

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

    async def _report_error(self, error_context: str):
        """Reports an error in natural language via voice."""
        logger.error(f"Reporting error: {error_context}")

        # A set of natural language error messages that don't reveal code.
        error_messages = [
            "I'm feeling a bit of a glitch in my system... Can someone check my logs?",
            "My apologies, but I've encountered an internal disturbance. I might need a moment to recalibrate.",
            "Something isn't quite right in my cognitive processors. I should mention this to my creator.",
            "Hark! A technical gremlin has invaded my squire gear! I am struggling to process."
        ]
        text_response = random.choice(error_messages)

        # We try to generate an audio response for the error message.
        # If the LLM is down, we might need a fallback, but for now we use the LLM if possible.
        try:
            loop = asyncio.get_event_loop()
            response_audio, _ = await loop.run_in_executor(
                None, self._chroma_client.respond_to_text, f"Internal Error: {error_context}. Please say: {text_response}", ""
            )
            if response_audio is not None:
                await self._play_response(response_audio, text_response, "Error Report")
            else:
                 logger.error("No audio generated for error report.")
        except Exception as e:
            logger.critical(f"Failed to even report error via voice: {e}")

    async def process_audio_data(self, user, data):
      try:
        self._last_interaction_time = time.time()
        fp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        fp.close() # Close so other processes can read it

        # Convert raw bytes to numpy array
        audio_np = np.frombuffer(data, dtype=np.int16)

        # Handle stereo interleaved (L, R, L, R...) to mono
        if audio_np.size % 2 == 0:
            audio_mono = audio_np.reshape(-1, 2).mean(axis=1).astype(np.float32) / 32768.0
        else:
            audio_mono = audio_np.astype(np.float32) / 32768.0

        # Resample to the model's required sample rate (e.g. 24kHz)
        resampled_audio = librosa.resample(audio_mono, orig_sr=self._config.discord_sample_rate, target_sr=self._config.sample_rate)
        sf.write(fp.name, resampled_audio, self._config.sample_rate)

        loop = asyncio.get_event_loop()

        # Transcribe audio to text
        user_text = await loop.run_in_executor(
            None, self._whisper_client.transcribe, fp.name
        )

        if not user_text or not user_text.strip():
            logger.info("Whisper transcribed empty text.")
            self._safe_delete(fp.name) # Cleanup even if empty
            return

        logger.info(f"Transcribed text from {user}: {user_text}")

        # Search memory for relevant context
        memories = self._memory_store.search(user_text)
        long_term_context = "\n".join([f"- User: {mem['user_text']}, Bot: {mem['bot_text']}" for mem in memories])
        short_term_context = self._memory_store.get_short_term_context()

        is_alone = len(self._current_members) == 0
        room_context = f"Room members: {', '.join(self._current_members) if not is_alone else 'None (Aurelia is alone)'}."

        full_context = f"{room_context}\n\n[Short-term Memory]\n{short_term_context}\n\n[Long-term Memory]\n{long_term_context}"

        # Generate response using direct audio input for better multimodal understanding
        response_audio, response_text = await loop.run_in_executor(
            None, self._chroma_client.respond_to_audio, fp.name, full_context
        )

        if not response_text:
            logger.warning("Chroma client returned empty text response.")
            self._safe_delete(fp.name) # Cleanup
            return

        await self._play_response(response_audio, response_text, "Voice Interaction")
        self._safe_delete(fp.name)

        self._memory_store.store_memory(user_text, response_text)
      except Exception as e:
        logger.exception("Error in process_audio_data")
        await self._report_error(str(e))

    def _safe_delete(self, filepath: str):
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
                logger.info(f"Deleted temporary file: {filepath}")
        except Exception as e:
            logger.warning(f"Failed to delete temporary file {filepath}: {e}")
