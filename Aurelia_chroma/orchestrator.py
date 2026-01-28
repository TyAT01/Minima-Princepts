from __future__ import annotations
import asyncio
import logging
import time
import random
from typing import Optional, List, Dict, Any

from llm.chroma_client import ChromaClient
from memory.store import ChromaMemoryStore
from stt.whisper_client import WhisperClient
from adapters.twitch import TwitchChatAdapter
from adapters.youtube import YouTubeChatAdapter
from adapters.schemas import Event

logger = logging.getLogger(__name__)

class AureliaOrchestrator:
    def __init__(
        self,
        chroma_client: ChromaClient,
        memory_store: ChromaMemoryStore,
        whisper_client: WhisperClient,
        twitch_adapter: Optional[TwitchChatAdapter] = None,
        youtube_adapter: Optional[YouTubeChatAdapter] = None,
        local_audio_player: Any = None
    ):
        self.chroma_client = chroma_client
        self.memory_store = memory_store
        self.whisper_client = whisper_client
        self.twitch_adapter = twitch_adapter
        self.youtube_adapter = youtube_adapter
        self.local_audio_player = local_audio_player

        self.last_interaction_time = time.time()
        self.autonomous_task = None
        self.is_running = False
        self.current_members = [] # For Discord context

        # Callback for playing audio via Discord (set by AlwaysListenBot)
        self.discord_play_callback = None

    async def start(self):
        self.is_running = True
        self.autonomous_task = asyncio.create_task(self.autonomous_loop())
        if self.twitch_adapter or self.youtube_adapter:
            asyncio.create_task(self.chat_polling_loop())
        logger.info("Aurelia Orchestrator started.")

    async def stop(self):
        self.is_running = False
        if self.autonomous_task:
            self.autonomous_task.cancel()
        logger.info("Aurelia Orchestrator stopped.")

    async def process_text_input(self, text: str, user: str, source: str) -> str:
        """Processes text input from any source and generates a response."""
        self.last_interaction_time = time.time()
        logger.info(f"Processing input from {user} ({source}): {text}")

        # Search memory for relevant context
        memories = self.memory_store.search(text)
        long_term_context = "\n".join([f"- User: {mem['user_text']}, Bot: {mem['bot_text']}" for mem in memories])
        short_term_context = self.memory_store.get_short_term_context()

        room_context = f"Platform: {source}. User: {user}."
        if source == "discord":
            is_alone = len(self.current_members) == 0
            room_context += f" Members in voice: {', '.join(self.current_members) if not is_alone else 'None'}."

        full_context = f"{room_context}\n\n[Short-term Memory]\n{short_term_context}\n\n[Long-term Memory]\n{long_term_context}"

        loop = asyncio.get_event_loop()
        audio_data, response_text = await loop.run_in_executor(
            None, self.chroma_client.respond_to_text, text, full_context
        )

        if response_text:
            # Store in memory
            self.memory_store.store_memory(text, response_text)

            # Handle outputs
            await self.dispatch_response(response_text, audio_data, source)

        return response_text

    async def process_audio_input(self, audio_path: str, user: str, source: str) -> str:
        """Processes audio input (e.g. from Discord) and generates a response."""
        self.last_interaction_time = time.time()

        loop = asyncio.get_event_loop()
        # Transcribe first for memory search context
        user_text = await loop.run_in_executor(
            None, self.whisper_client.transcribe, audio_path
        )

        if not user_text or not user_text.strip():
            return ""

        logger.info(f"Transcribed audio from {user} ({source}): {user_text}")

        memories = self.memory_store.search(user_text)
        long_term_context = "\n".join([f"- User: {mem['user_text']}, Bot: {mem['bot_text']}" for mem in memories])
        short_term_context = self.memory_store.get_short_term_context()

        is_alone = len(self.current_members) == 0
        room_context = f"Platform: {source}. User: {user}. Members in voice: {', '.join(self.current_members) if not is_alone else 'None'}."
        full_context = f"{room_context}\n\n[Short-term Memory]\n{short_term_context}\n\n[Long-term Memory]\n{long_term_context}"

        # Generate response using direct audio input
        audio_data, response_text = await loop.run_in_executor(
            None, self.chroma_client.respond_to_audio, audio_path, full_context
        )

        if response_text:
            self.memory_store.store_memory(user_text, response_text)
            await self.dispatch_response(response_text, audio_data, source)

        return response_text

    async def dispatch_response(self, text: str, audio_data: Optional[bytes], source: str, broadcast: bool = False):
        """Dispatches the response to appropriate platforms."""
        tasks = []

        # 1. Output text to chat-based platforms
        if self.twitch_adapter and (broadcast or source == "twitch"):
            tasks.append(self.twitch_adapter.send_message(text))

        if self.youtube_adapter and (broadcast or source == "youtube"):
            tasks.append(self.youtube_adapter.send_message(text))

        # 2. Handle Audio output
        if audio_data is not None:
            # Play locally for Streamers (OBS capture)
            if self.local_audio_player:
                tasks.append(self.local_audio_player.play(audio_data))

            # Play in Discord
            if self.discord_play_callback:
                if broadcast or source == "discord":
                    tasks.append(self.discord_play_callback(audio_data, text, "Interaction"))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def chat_polling_loop(self):
        """Polls Twitch and YouTube for new messages."""
        logger.info("Chat polling loop started.")
        while self.is_running:
            events = []
            if self.twitch_adapter:
                events.extend(list(self.twitch_adapter.poll()))

            if self.youtube_adapter:
                events.extend(list(self.youtube_adapter.poll()))

            # Process all gathered events sequentially to maintain conversation context
            for event in events:
                await self.process_text_input(event.text, event.username, event.source)

            await asyncio.sleep(1)

    async def autonomous_loop(self):
        """Background loop for proactive behavior."""
        logger.info("Autonomous loop started.")
        while self.is_running:
            await asyncio.sleep(30) # Check every 30 seconds

            now = time.time()
            idle_time = now - self.last_interaction_time

            if idle_time > 300: # 5 minutes idle
                await self.think_and_act()

    async def think_and_act(self):
        """Aurelia decides to speak or act on her own."""
        logger.info("Aurelia is thinking autonomously...")

        is_alone = len(self.current_members) == 0
        state_context = f"Status: {'Alone' if is_alone else 'Idle'}. Members present: {', '.join(self.current_members) if not is_alone else 'None'}."
        prompt = "You've been quiet. What's on your mind? Share a thought with your friends or chat."

        memories = self.memory_store.search("current state")
        memory_context = "\n".join([f"- User: {mem['user_text']}, Bot: {mem['bot_text']}" for mem in memories])
        full_context = f"{state_context}\n\n{memory_context}"

        loop = asyncio.get_event_loop()
        audio_data, response_text = await loop.run_in_executor(
            None, self.chroma_client.respond_to_text, prompt, full_context
        )

        if response_text:
            logger.info(f"Autonomous action: {response_text}")
            # Broadcast autonomous actions to all platforms
            await self.dispatch_response(response_text, audio_data, "autonomous", broadcast=True)
            self.last_interaction_time = time.time()

    async def handle_event(self, event_type: str, data: Dict[str, Any]):
        """Handles platform-specific events (e.g. member joined)."""
        logger.info(f"Handling event: {event_type} - {data}")

        user = data.get("user", "Someone")
        source = data.get("source", "unknown")

        if event_type == "member_join":
            prompt = f"{user} has joined the voice channel. Greet them warmly and in character."
        elif event_type == "member_leave":
            prompt = f"{user} has left the voice channel. Say a brief goodbye if appropriate, or just note it."
        else:
            return

        # Simple event-driven response
        loop = asyncio.get_event_loop()
        audio_data, response_text = await loop.run_in_executor(
            None, self.chroma_client.respond_to_text, prompt, f"Event: {event_type}. User: {user}."
        )

        if response_text:
            await self.dispatch_response(response_text, audio_data, source, broadcast=(source != "discord"))

    async def report_error(self, error_message: str):
        """Reports an error in natural language."""
        logger.error(f"Orchestrator reporting error: {error_message}")
        error_options = [
            "My apologies, I seem to have hit a snag in my processing.",
            "Hark! A technical gremlin has appeared. One moment while I deal with it.",
            "I'm feeling a bit glitched out right now. Can someone check on me?"
        ]
        text = random.choice(error_options)

        loop = asyncio.get_event_loop()
        audio_data, _ = await loop.run_in_executor(
            None, self.chroma_client.respond_to_text, f"Internal Error: {error_message}. Say: {text}", ""
        )

        # Play via local audio and Discord if possible
        if self.local_audio_player and audio_data is not None:
            await self.local_audio_player.play(audio_data)
        if self.discord_play_callback and audio_data is not None:
            await self.discord_play_callback(audio_data, text, "Error Report")
