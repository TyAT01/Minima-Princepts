from __future__ import annotations
import asyncio
import logging
import time
import random
import re
from typing import Optional, List, Dict, Any

from llm.chroma_client import ChromaClient
from memory.store import ChromaMemoryStore
from stt.whisper_client import WhisperClient
from learning.simulation import SimulationManager
from learning.evolve import Reflector
from adapters.twitch import TwitchChatAdapter
from adapters.youtube import YouTubeChatAdapter
from adapters.schemas import Event
from cadence_controller import AureliaCadenceController, StreamSignals, ChatMessage, clamp

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

        self.cadence_controller = AureliaCadenceController()
        self.current_hype = 0.0

        self.last_interaction_time = time.time()
        self.autonomous_task = None
        self.is_running = False
        self.current_members = [] # For Discord context

        # Callback for playing audio via Discord (set by AlwaysListenBot)
        self.discord_play_callback = None
        self.discord_stop_callback = None
        self.discord_bot = None # Reference to AlwaysListenBot
        self._current_generation_task = None

        # Learning components
        self.simulation_manager = SimulationManager()
        self.reflector = Reflector(chroma_client, memory_store)
        self.cycle_count = 0

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

    async def stop_speaking(self):
        """Interrupts current audio playback and LLM generation on all platforms."""
        logger.info("Interrupting Aurelia's speech and thinking process...")

        # 1. Cancel current LLM/Fragment processing task
        if self._current_generation_task and not self._current_generation_task.done():
            self._current_generation_task.cancel()
            logger.info("Current generation task cancelled.")

        # 2. Stop local playback
        if self.local_audio_player:
            self.local_audio_player.stop()

        # 3. Stop Discord playback
        if self.discord_stop_callback:
            await self.discord_stop_callback()

        # 4. Mark as not busy in cadence controller
        self.cadence_controller.last_spoke_ts = 0 # Allow immediate re-entry

    def _build_full_context(self, user: str, source: str, query_text: str) -> str:
        """Centralized method to build the prompt context with categorized insights."""
        # 1. Platform & User Context
        room_context = f"Platform: {source}. User: {user}."
        if source == "discord":
            members = self.current_members or []
            is_alone = len(members) == 0
            room_context += f" Members in voice: {', '.join(members) if not is_alone else 'None'}."

        # 2. Memory Retrieval
        memories = self.memory_store.search(query_text, filter_type="interaction")
        long_term_context = "\n".join([f"- User: {mem['user_text']}, Bot: {mem['bot_text']}" for mem in memories])
        short_term_context = self.memory_store.get_short_term_context()

        # 3. Categorized Insights
        # We increase n_results to 5 to get a better spread of both types
        insights = self.memory_store.search(query_text, filter_type="insight", n_results=5)

        real_insights = [i['insight'] for i in insights if i.get("source") == "periodic_reflection"]
        sim_insights = [i['insight'] for i in insights if i.get("source", "").startswith("sim_")]

        insights_sections = []
        if real_insights:
            insights_sections.append("### [REAL-WORLD EXPERIENCE & LESSONS]\n" + "\n".join([f"- {ins}" for ins in real_insights]))
        if sim_insights:
            insights_sections.append("### [SIMULATED TRAINING & GROWTH DATA]\n" + "\n".join([f"- {ins}" for ins in sim_insights]))

        insights_context = "\n\n".join(insights_sections) if insights_sections else "No specific lessons learned yet."

        # 4. Final Context Assembly
        full_context = (
            f"{room_context}\n\n"
            f"{insights_context}\n\n"
            f"[Short-term Memory]\n{short_term_context}\n\n"
            f"[Long-term Memory]\n{long_term_context}"
        )
        return full_context

    async def _stream_llm_response(self, text: str, context: str, is_audio: bool = False, audio_path: Optional[str] = None):
        """Async generator that yields fragments from the LLM stream without blocking the event loop."""
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()

        def producer():
            try:
                if is_audio and audio_path:
                    gen = self.chroma_client.stream_respond_to_audio(audio_path, context)
                else:
                    gen = self.chroma_client.stream_respond_to_text(text, context)

                for chunk in gen:
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
            except Exception as e:
                logger.error(f"Error in LLM producer thread: {e}")
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None) # Signal end

        # Run the synchronous generator in a thread in the background
        # Do NOT await this here, as it would block until completion.
        loop.run_in_executor(None, producer)

        full_response = ""
        current_fragment = ""
        while True:
            chunk = await queue.get()
            if chunk is None:
                break

            full_response += chunk
            current_fragment += chunk

            # Improved fragment detection logic
            if any(punct in chunk for punct in (".", "!", "?", ",", ";", "\n")):
                yield current_fragment
                current_fragment = ""

        if current_fragment.strip():
            yield current_fragment

    async def process_text_input(self, text: str, user: str, source: str) -> str:
        """Processes text input from any source and generates a response via streaming fragments."""
        self.last_interaction_time = time.time()
        logger.info(f"Processing input from {user} ({source}): {text}")

        full_context = self._build_full_context(user, source, text)
        full_response = ""

        self._current_generation_task = asyncio.current_task()
        try:
            async for fragment in self._stream_llm_response(text, full_context):
                full_response += fragment
                await self._dispatch_fragment(fragment, source)
        except asyncio.CancelledError:
            logger.info("Fragment processing task cancelled.")
            raise
        finally:
            self._current_generation_task = None

        if full_response:
            cleaned_response = self._extract_and_apply_cadence_commands(full_response)
            self.memory_store.store_memory(text, cleaned_response)
            self.cadence_controller.last_spoke_ts = time.time()
            return cleaned_response

        return ""

    async def process_audio_input(self, audio_path: str, user: str, source: str) -> str:
        """Processes audio input and generates a response via streaming fragments."""
        self.last_interaction_time = time.time()

        loop = asyncio.get_running_loop()
        user_text = await loop.run_in_executor(
            None, self.whisper_client.transcribe, audio_path
        )

        if not user_text or not user_text.strip():
            return ""

        logger.info(f"Transcribed audio from {user} ({source}): {user_text}")
        full_context = self._build_full_context(user, source, user_text)
        full_response = ""

        self._current_generation_task = asyncio.current_task()
        try:
            async for fragment in self._stream_llm_response(user_text, full_context, is_audio=True, audio_path=audio_path):
                full_response += fragment
                await self._dispatch_fragment(fragment, source)
        except asyncio.CancelledError:
            logger.info("Fragment processing task cancelled.")
            raise
        finally:
            self._current_generation_task = None

        if full_response:
            cleaned_response = self._extract_and_apply_cadence_commands(full_response)
            self.memory_store.store_memory(user_text, cleaned_response)
            self.cadence_controller.last_spoke_ts = time.time()
            return cleaned_response

        return ""

    async def _dispatch_fragment(self, text: str, source: str, broadcast: bool = False):
        """Generates audio for a fragment and dispatches it immediately."""
        text = text.strip()
        if not text:
            return

        # Clean tags from fragment before speaking
        speech_text = re.sub(r"\[CADENCE:.*?\]", "", text, flags=re.IGNORECASE).strip()
        if not speech_text:
            return

        # Apply content filter to fragment
        speech_text = self.chroma_client.filter_text(speech_text)
        if not speech_text:
            return

        loop = asyncio.get_event_loop()
        audio_data = await loop.run_in_executor(
            None, self.chroma_client.generate_audio_for_fragment, speech_text
        )

        await self.dispatch_response(speech_text, audio_data, source, broadcast)

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

    def _detect_hype(self, events: List[Event]) -> float:
        """Calculates current hype based on message volume, keywords, and intensity markers."""
        if not events:
            return clamp(self.current_hype - 0.05) # decay

        hype_keywords = {
            "lfg", "pog", "hype", "omg", "wow", "love", "cracked", "legendary", "gg",
            "fire", "lit", "huge", "goat", "clutch", "poggers", "pogchamp", "ez"
        }

        count = len(events)
        intensity_sum = 0.0

        for e in events:
            msg_intensity = 0.0
            text = e.text
            text_lower = text.lower()

            # 1. Keywords
            if any(k in text_lower for k in hype_keywords):
                msg_intensity += 0.4

            # 2. Capitalization (Caps lock hype)
            if len(text) > 3 and text.isupper():
                msg_intensity += 0.3

            # 3. Repeated punctuation (!!!, ???)
            if "!!" in text or "??" in text:
                msg_intensity += 0.2

            # 4. Elongated words (e.g. POGGGGG, NOOOOO)
            if re.search(r"(.)\1{2,}", text): # Changed to 2+ repeats (3 total chars)
                msg_intensity += 0.2

            intensity_sum += clamp(msg_intensity)

        # Average intensity per message
        avg_msg_intensity = intensity_sum / count

        # Density score (volume)
        density = clamp(count / 5.0)

        # Combined score: 40% density, 60% intensity
        score = (density * 0.4) + (avg_msg_intensity * 0.6)
        return clamp(score)

    async def chat_polling_loop(self):
        """Polls Twitch and YouTube for new messages and handles cadence."""
        logger.info("Chat polling loop started.")
        while self.is_running:
            events = []
            if self.twitch_adapter:
                events.extend(self.twitch_adapter.poll())

            if self.youtube_adapter:
                events.extend(self.youtube_adapter.poll())

            self.current_hype = self._detect_hype(events)

            chat_msgs = [
                ChatMessage(user=e.username, text=e.text, ts=time.time(),
                            source=e.source,
                            is_high_signal=(len(e.text) > 40 or "?" in e.text))
                for e in events
            ]

            signals = StreamSignals(
                now=time.time(),
                chat_messages=chat_msgs,
                event_intensity=self.current_hype,
                focus_level=0.0 # Default
            )

            self.cadence_controller.update(signals)
            intent = self.cadence_controller.maybe_emit_intent(signals)

            if intent:
                await self.process_intent(intent)

            await asyncio.sleep(0.5)

    async def process_intent(self, intent: Any):
        """Processes a SpeechIntent from the cadence controller."""
        logger.info(f"Processing speech intent: {intent.kind} (urgency: {intent.urgency:.2f})")

        if intent.kind == "REPLY" and intent.target_message:
            await self.process_text_input(
                intent.target_message.text,
                intent.target_message.user,
                intent.target_message.source
            )
            self.cadence_controller.pop_consumed_message(intent.target_message)

        elif intent.kind in ("RIFF", "REACT", "FILLER"):
            style = intent.meta.get("style", "default")
            await self.think_and_act(style=style, intent=intent)
            if intent.kind == "RIFF" and intent.target_message:
                self.cadence_controller.pop_consumed_message(intent.target_message)

    async def autonomous_loop(self):
        """Background loop for periodic tasks like reflection and simulation."""
        logger.info("Autonomous task loop started.")
        while self.is_running:
            await asyncio.sleep(60)
            self.cycle_count += 1

            now = time.time()
            idle_time = now - self.last_interaction_time

            # 1. Periodic Reflection (every 10 mins)
            if self.cycle_count % 10 == 0:
                await self.reflector.reflect_on_recent_interactions()

            # 2. Deep idle logic (Simulations)
            if idle_time >= 300:
                if random.random() < 0.3:
                    await self.run_autonomous_simulation()

    async def run_autonomous_simulation(self):
        """Runs a simulation during idle time to improve skills."""
        logger.info("Aurelia is running an autonomous mental simulation...")
        await self.simulation_manager.run_simulation(self.chroma_client, self.memory_store)
        self.last_interaction_time = time.time() # Reset idle timer after 'thinking'

    def _extract_and_apply_cadence_commands(self, text: str) -> str:
        """Scans for [CADENCE: key=value] tags, updates the controller, and returns cleaned text."""
        pattern = r"\[CADENCE:\s*(.*?)\]"
        matches = re.findall(pattern, text, re.IGNORECASE)

        for match in matches:
            # match is like "min_gap_s=2.5, max_silence_s=30"
            parts = [p.strip() for p in match.split(",")]
            updates = {}
            for p in parts:
                if "=" in p:
                    k, v = p.split("=", 1)
                    try:
                        updates[k.strip()] = float(v.strip())
                    except ValueError:
                        continue
            if updates:
                self.cadence_controller.update_config(**updates)

        # Strip tags from output and normalize spaces
        cleaned = re.sub(pattern, "", text, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    async def think_and_act(self, style: str = "default", intent: Optional[Any] = None):
        """Aurelia decides to speak or act on her own, optionally guided by an intent."""
        logger.info(f"Aurelia is thinking (style: {style})...")

        members = self.current_members or []
        is_alone = len(members) == 0

        # Determine silence type context
        if is_alone:
            silence_context = "You are currently alone in the room/stream. It's very quiet."
        else:
            silence_context = f"There are people present ({', '.join(members)}), but they are being quiet."

        has_chat = self.twitch_adapter is not None or self.youtube_adapter is not None
        if has_chat:
            silence_context += " The stream chat is also silent."

        state_context = f"Status: {'Alone' if is_alone else 'Idle'}. {silence_context}"

        if intent:
            prompt = f"Intent: {intent.kind}. Style: {style}. Energy: {intent.energy:.2f}. "
            if intent.kind == "REACT":
                prompt += "Give a very short, high-energy hype reaction to the current stream vibe. Keep it under 5 words."
            elif intent.kind == "FILLER":
                if "armor" in style or "cape" in style or "pouch" in style or "gear" in style:
                    prompt += f"Perform a brief squire-like action: {style.replace('_', ' ')}. Narrate it naturally as you do it."
                else:
                    prompt += "Share a brief inner monologue or small filler comment. Keep it short and natural."
            elif intent.kind == "RIFF":
                if intent.target_message:
                    prompt += f"A message from {intent.target_message.user} caught your eye: '{intent.target_message.text}'. Use this as a seed to riff or tell a mini-story (15-30s) while staying in character."
                else:
                    prompt += "Riff on your current journey or the stream's vibe for 15-30s. Be engaging!"
            else:
                prompt += "Share an interesting thought or observation."
        else:
            if style == "filler":
                prompt = "It's a bit quiet. Share a brief inner monologue or a small 'filler' comment about what you're doing or thinking. Keep it short and natural."
            elif style == "conversation_driver":
                prompt = "The silence is starting to bother you. As a streamer who hates dead air, try to start a conversation. Ask a question to those present or share an interesting thought to get people talking."
            elif style == "deep_thought":
                prompt = "You've been quiet for a while. Share a deeper existential thought or reflect on your journey to become human. Don't let the dead air win."
            else:
                prompt = "You've been quiet. What's on your mind? Share a thought with your friends or chat."

        prompt += " Remember: You hate dead air, but don't be annoying."

        memories = self.memory_store.search("current state")
        memory_context = "\n".join([f"- User: {mem['user_text']}, Bot: {mem['bot_text']}" for mem in memories])
        full_context = f"{state_context}\n\n{memory_context}"

        full_response = ""
        self._current_generation_task = asyncio.current_task()
        try:
            async for fragment in self._stream_llm_response(prompt, full_context):
                full_response += fragment
                await self._dispatch_fragment(fragment, "autonomous", broadcast=True)
        except asyncio.CancelledError:
            logger.info("Autonomous fragment processing task cancelled.")
            raise
        finally:
            self._current_generation_task = None

        if full_response:
            cleaned_response = self._extract_and_apply_cadence_commands(full_response)
            logger.info(f"Autonomous action ({style}): {cleaned_response}")
            self.last_interaction_time = time.time()
            self.cadence_controller.last_spoke_ts = time.time()

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
            # Detect and apply autonomous cadence adjustments, then clean response
            response_text = self._extract_and_apply_cadence_commands(response_text)

            await self.dispatch_response(response_text, audio_data, source, broadcast=(source != "discord"))
            self.last_interaction_time = time.time()
            self.cadence_controller.last_spoke_ts = time.time()

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
