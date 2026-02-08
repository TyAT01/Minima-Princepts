from __future__ import annotations
import logging
import os
import sys
import re
import yaml
import signal
import threading
import time
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add the current directory to sys.path
sys.path.append(str(Path(__file__).parent))

# [FIX] ctranslate2 ROCm path workaround for Windows
if sys.platform == "win32":
    try:
        import ctranslate2
    except ImportError:
        pass # Will be handled when stt.whisper is imported
    except FileNotFoundError as e:
        if "_rocm" in str(e).lower():
            # Try to create the missing directory to satisfy the buggy import
            import re
            # Extract path from error message (usually inside quotes)
            match = re.search(r"'(.*?)'", str(e))
            if match:
                missing_path = match.group(1)
                try:
                    # Resolve path to handle /../
                    abs_path = os.path.abspath(missing_path)
                    os.makedirs(abs_path, exist_ok=True)
                    print(f"[System] Applied ctranslate2 ROCm path fix: Created {abs_path}")
                    # Try importing again now that the path exists
                    import ctranslate2
                except:
                    pass

from llm.client import LlamaClient
from memory.store import MemoryStore
from stt.whisper import STTSystem
from utils.text_utils import split_into_sentences
from persona.manager import PersonaManager
from ui.web_gui import AureliaGUI
from utils.error_handler import ErrorHandler
from queue import Queue

class AureliaApp:
    def __init__(self, config_path: str = "config.yaml"):
        self.config = self._load_config(config_path)
        self.processing_lock = threading.Lock()
        self.current_user_name = "Tyler" # Default
        self.active_users = set()
        self.objectives_path = os.path.join(os.path.dirname(__file__), "objectives.json")
        self.session_objectives = self._load_objectives()
        self.error_handler = ErrorHandler(ai_comment_callback=self.ai_comment_on_error)

        # Initialize components with config
        mem_cfg = self.config.get('memory', {})
        self.memory = MemoryStore(
            db_path=mem_cfg.get('db_path', './aurelia_memory'),
            collection_name=mem_cfg.get('collection_name', 'aurelia_ai_memories'),
            max_short_term=mem_cfg.get('max_short_term', 15)
        )

        llm_cfg = self.config.get('llm', {})
        self.llm = LlamaClient(
            base_url=llm_cfg.get('base_url', 'http://localhost:11434/api'),
            model=llm_cfg.get('model', 'llama3.1:8b-instruct-q4_K_M'),
            api_type=llm_cfg.get('api_type', 'ollama')
        )

        stt_cfg = self.config.get('stt', {})
        self.stt = STTSystem(
            model_size=stt_cfg.get('model_size', 'base'),
            device=stt_cfg.get('device'),
            compute_type=stt_cfg.get('compute_type', 'float16')
        )

        pers_cfg = self.config.get('persona', {})
        # Note: sheet_path in config might be relative to the version folder
        self.persona = PersonaManager(sheet_path=pers_cfg.get('sheet_path', 'aurelia_sheet.yaml'))
        self.session_start = datetime.now(timezone.utc)

        self.results_queue = Queue()
        self.interrupt_event = threading.Event()
        self.stop_event = threading.Event()
        self.is_responding = False
        self.last_thought = ""

        from stt.whisper import VoiceMonitor
        self.voice_monitor = VoiceMonitor(
            callback=self.process_background_audio,
            interrupt_callback=self.handle_interrupt
        )

        ui_cfg = self.config.get('ui', {})
        self.gui = AureliaGUI(
            process_text_cb=self.process_text,
            process_audio_cb=self.process_audio,
            toggle_mic_cb=self.toggle_mic,
            poll_results_cb=self.poll_results,
            on_join_cb=self.user_join,
            on_leave_cb=self.user_leave,
            title=ui_cfg.get('title', "⚔️ Aurelia Vale: The Hedge-Knight Squire"),
            theme=ui_cfg.get('theme', "soft")
        )

    def _load_objectives(self) -> list:
        try:
            if os.path.exists(self.objectives_path):
                with open(self.objectives_path, 'r', encoding='utf-8') as f:
                    import json
                    return json.load(f)
        except Exception as e:
            logging.warning(f"Error loading objectives: {e}")
        return []

    def _save_objectives(self):
        try:
            with open(self.objectives_path, 'w', encoding='utf-8') as f:
                import json
                json.dump(self.session_objectives, f, indent=2)
        except Exception as e:
            logging.warning(f"Error saving objectives: {e}")

    def _load_config(self, path: str) -> dict:
        try:
            if not os.path.exists(path):
                # Try one level up if not found (e.g. if run from root)
                alt_path = os.path.join("Aurelia-AI", path)
                if os.path.exists(alt_path):
                    path = alt_path
                else:
                    print(f"Warning: Config file {path} not found. Using defaults.")
                    return {}

            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except Exception as e:
            print(f"Warning: Error loading config from {path}: {e}")
            return {}

    def initialize(self):
        try:
            logging.info("Initializing Aurelia AI...")

            # Run LLM Diagnostics first
            diag = self.llm.perform_diagnostics()
            print("\n" + diag + "\n")

            self.persona.load_persona()
            logging.info("Initialization complete.")
        except Exception as e:
            self.error_handler.handle_error(e, "Initialization")

    def handle_interrupt(self):
        """Sets the interrupt flag to stop current AI response."""
        if self.is_responding:
            logging.info("!!! Interrupt received !!!")
            self.interrupt_event.set()

    def _extract_thought_from_stream(self, stream):
        """Helper to extract [THOUGHT] content and yield the remaining response."""
        buffer = ""
        in_thought = False
        self.last_thought = ""

        # Patterns for thought-start and thought-end (case-insensitive, handles brackets and parentheses)
        start_pattern = re.compile(r'\[THOUGHTS?\]|\(THOUGHTS?\)', re.IGNORECASE)
        end_pattern = re.compile(r'\[/THOUGHTS?\]|\(/THOUGHTS?\)', re.IGNORECASE)

        for chunk in stream:
            buffer += chunk
            while True:
                if not in_thought:
                    match = start_pattern.search(buffer)
                    if match:
                        # Yield everything BEFORE the tag
                        pre_tag = buffer[:match.start()]
                        if pre_tag:
                            yield pre_tag
                        buffer = buffer[match.end():]
                        in_thought = True
                        continue
                    else:
                        # No start tag found. Yield safe buffer, keeping enough to catch partial tags.
                        if len(buffer) > 15:
                            yield buffer[:-15]
                            buffer = buffer[-15:]
                        break
                else:
                    match = end_pattern.search(buffer)
                    if match:
                        # Collect the thought content
                        self.last_thought += buffer[:match.start()]
                        buffer = buffer[match.end():]
                        in_thought = False
                        continue
                    else:
                        # Inside thought, wait for closing tag or stream end.
                        # We no longer drain the buffer here to avoid fragmenting words in the CMD log.
                        # Safety cap for thoughts (prevent infinite growth)
                        if len(buffer) + len(self.last_thought) > 4000:
                            self.last_thought += buffer
                            buffer = ""
                            in_thought = False
                        break

        # Final cleanup
        if buffer:
            if in_thought:
                self.last_thought += buffer
                # If the stream ended without a closing tag, treat the content as response
                # especially if it's long and doesn't have an opening tag anymore.
                if len(self.last_thought) > 50 and not start_pattern.search(self.last_thought):
                    yield self.last_thought
                    self.last_thought = ""
            else:
                # Last resort check for bracketed thought if nothing was extracted
                if not self.last_thought and buffer.strip().startswith("[") and "]" in buffer:
                    match = re.match(r'^\[(.*?)\]', buffer.strip())
                    if match:
                        self.last_thought = match.group(1)
                        yield buffer.strip()[match.end():].strip()
                        return
                yield buffer

    def process_text(self, text: Any, user_name: str = None):
        """Generator that yields sentence fragments from the LLM with combined thought/response and interrupt checks."""
        self.last_thought = "" # Initialize at the very start to avoid stale state
        if user_name:
            self.current_user_name = user_name
        else:
            user_name = self.current_user_name

        # Robustly handle list/dict inputs from Gradio
        if isinstance(text, list) and len(text) > 0:
            first_item = text[0]
            if isinstance(first_item, dict):
                text = first_item.get("text", str(first_item))
            else:
                text = str(first_item)
        elif isinstance(text, dict):
            text = text.get("text", str(text))

        logging.info(f"--- Processing Message: '{text}' ---")

        # Use a lock to prevent simultaneous LLM calls
        if self.processing_lock.locked():
             logging.warning("System is busy processing another request.")
             yield "Wait a moment, I'm still thinking about our last exchange..."
             return

        with self.processing_lock:
            self.is_responding = True
            self.interrupt_event.clear()
            self.last_thought = ""
            try:
                # Refresh system prompt with current time
                system_prompt = self.persona.get_system_prompt(now=datetime.now())
                history = self.memory.get_history()

                # Check for objective updates in the user message (heuristics)
                if any(kw in text.lower() for kw in ["remember to", "rehearse", "goal for today", "next time", "important:"]):
                    self.session_objectives.append(text)
                    if len(self.session_objectives) > 5:
                        self.session_objectives.pop(0)
                    self._save_objectives()

                context = self.memory.get_full_context(text, user_id=user_name, objectives=self.session_objectives)

                # Add Temporal Context (Time Awareness)
                now_utc = datetime.now(timezone.utc)
                last_time = self.memory.get_last_interaction_time(user_name)

                # 1. Calculate Time Since Last Interaction
                duration_str = "some time"
                if last_time:
                    delta = now_utc - last_time
                    days = delta.days
                    hours, remainder = divmod(int(delta.seconds), 3600)
                    minutes, _ = divmod(remainder, 60)

                    time_parts = []
                    if days > 0:
                        time_parts.append(f"{days} day{'s' if days > 1 else ''}")
                    if hours > 0:
                        time_parts.append(f"{hours} hour{'s' if hours > 1 else ''}")
                    if minutes > 0 or (not time_parts):
                        time_parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")

                    duration_str = ", ".join(time_parts[:-1]) + (f" and {time_parts[-1]}" if len(time_parts) > 1 else time_parts[0])

                # 2. Calculate Session Uptime
                uptime_delta = now_utc - self.session_start
                up_hours, up_rem = divmod(int(uptime_delta.seconds), 3600)
                up_mins, _ = divmod(up_rem, 60)
                uptime_str = f"{up_hours}h {up_mins}m" if up_hours > 0 else f"{up_mins} minutes"

                temporal_note = (
                    f"It has been {duration_str} since your last interaction with {user_name}. "
                    f"You have been 'active' for the last {uptime_str} this session. "
                    "You are highly aware of this passage of time and should acknowledge it if the gap is significant or if asked."
                )

                context = f"### [TEMPORAL CONTEXT]\n- {temporal_note}\n\n{context}"

                # Combined Phase (Thought + Response in one stream)
                full_response = ""
                raw_stream = self.llm.stream_response(system_prompt, text, history, context)
                response_stream = self._extract_thought_from_stream(raw_stream)

                response_fragments = []

                for fragment in split_into_sentences(response_stream):
                    if self.interrupt_event.is_set():
                        logging.info("Response halted by interrupt.")
                        yield "... [Interrupted]"
                        break

                    # Remove any remaining bracketed text or parentheticals (leaked inner thoughts/actions/metadata)
                    # Uses a lookahead/lookbehind to avoid breaking standard Markdown links [text](url)
                    clean_fragment = re.sub(r'\[.*?\](?!\()|(?<!\])\(.*?\)', '', fragment).strip()
                    if clean_fragment:
                        response_fragments.append(clean_fragment)
                        yield clean_fragment

                full_response = " ".join(response_fragments)

                if not self.interrupt_event.is_set():
                    # Save both thought and interaction
                    if self.last_thought:
                        self.last_thought = self.last_thought.strip()
                        logging.info(f"Aurelia's Thought: {self.last_thought}")
                        self.memory.store_insight(f"Thought: {self.last_thought}", source="inner_monologue")

                    self.memory.add_interaction(text, full_response.strip(), user_id=user_name)
                    logging.info(f"Successfully processed message. Response length: {len(full_response)}")

                # Periodic reflection (every 10 interactions)
                if self.memory.count() % 10 == 0:
                     self.reflect(user_name)

            except Exception as e:
                self.error_handler.handle_error(e, "Text Processing")
                yield self.error_handler.get_ai_fallback_response()
            finally:
                self.is_responding = False
                self.interrupt_event.clear()

    def reflect(self, user_id: str):
        """Asks the LLM to analyze recent interactions for profiles, events, and insights."""
        try:
            logging.info(f"Aurelia is reflecting on recent experiences with {user_id}...")
            history = self.memory.get_history()
            if not history: return

            reflection_prompt = (
                "You are Aurelia Vale, performing deep reflection. Analyze our recent chat history and extract the following:\n"
                "1. User Profile: Any new facts, likes, or dislikes about the person I'm talking to.\n"
                "2. Notable Events: Any significant moments or 'firsts' that happened.\n"
                "3. Insights: Lessons learned about myself or the world.\n\n"
                "Format your response as a valid YAML block with keys: 'user_facts' (list), 'events' (list), 'insights' (list)."
            )

            analysis_raw = self.llm.generate_response("You are Aurelia Vale, analyzing your memories.", f"Recent History: {history}", [], context=reflection_prompt)

            # Clean up potential markdown and metadata labels
            def clean_yaml_block(text):
                if "```yaml" in text:
                    text = text.split("```yaml")[1].split("```")[0]
                elif "```yml" in text:
                    text = text.split("```yml")[1].split("```")[0]
                elif "```" in text:
                    text = text.split("```")[1].split("```")[0]

                lines = text.strip().splitlines()
                if lines and lines[0].strip().lower() in ["yml", "yaml"]:
                    text = "\n".join(lines[1:])
                return text.strip()

            cleaned_raw = clean_yaml_block(analysis_raw)

            # Simple parser for the YAML-like response
            data = None
            try:
                data = yaml.safe_load(cleaned_raw)
                if not isinstance(data, dict):
                    data = None
            except Exception as e:
                logging.warning(f"YAML parsing failed, attempting regex fallback: {e}")

            if not data:
                # Regex-based fallback extraction
                data = {
                    'user_facts': re.findall(r'user_facts:\s*(.*?)(?:\n\w+:|$)', cleaned_raw, re.S | re.I),
                    'events': re.findall(r'events:\s*(.*?)(?:\n\w+:|$)', cleaned_raw, re.S | re.I),
                    'insights': re.findall(r'insights:\s*(.*?)(?:\n\w+:|$)', cleaned_raw, re.S | re.I)
                }
                for key in data:
                    if data[key]:
                        # Split by '- ' or '\n-'
                        items = re.split(r'\n\s*-\s*', "\n" + data[key][0])
                        data[key] = [i.strip() for i in items if i.strip() and not i.strip().endswith(':')]
                    else:
                        data[key] = []

            if isinstance(data, dict):
                for fact in data.get('user_facts', []):
                    if isinstance(fact, dict):
                        # Convert dict to a readable string fact
                        fact_str = ", ".join([f"{k}: {v}" for k, v in fact.items()])
                        self.memory.update_user_profile(user_id, fact_str)
                    else:
                        self.memory.update_user_profile(user_id, str(fact))
                for event in data.get('events', []):
                    self.memory.store_episodic_memory(event)
                for insight in data.get('insights', []):
                    self.memory.store_insight(insight, source="reflection")
                logging.info("Deep reflection complete. Memories filed.")

        except Exception as e:
            logging.warning(f"Reflection failed: {e}")

    def process_background_audio(self, audio_data: Any):
        """Callback for background STT."""
        try:
            transcribed_text = self.stt.transcribe(audio_data)
            if not transcribed_text or not transcribed_text.strip():
                return

            # For background audio, we'll collect the whole response to put in queue
            full_bot_txt = " ".join(self.process_text(transcribed_text, self.current_user_name))
            self.results_queue.put((transcribed_text, full_bot_txt.strip()))
        except Exception as e:
            logging.error(f"Background audio processing failed: {e}")

    def toggle_mic(self, state: bool):
        """Toggles the background voice monitor."""
        if state:
            self.voice_monitor.start()
        else:
            self.voice_monitor.stop()

    def poll_results(self) -> list[tuple[str, str]]:
        """Polls the queue for any new results from background STT."""
        results = []
        while not self.results_queue.empty():
            results.append(self.results_queue.get())
        return results

    def process_audio(self, audio_source: Any, user_name: str = None):
        """Generator that transcribes and then streams the bot response."""
        try:
            transcribed_text = self.stt.transcribe(audio_source)
            if not transcribed_text or not transcribed_text.strip():
                yield "[Inaudible]", "I'm sorry, I couldn't quite hear you. Could you repeat that?"
                return

            yield transcribed_text, "" # Yield transcription first

            response_fragments = []
            for fragment in self.process_text(transcribed_text, user_name):
                response_fragments.append(fragment)
                yield transcribed_text, " ".join(response_fragments)

        except Exception as e:
            self.error_handler.handle_error(e, "Audio Processing")
            yield "[Audio Error]", self.error_handler.get_ai_fallback_response()

    def ai_comment_on_error(self, error_details: str):
        logging.warning(f"AI noticing error: {error_details}")

    def user_join(self, username: str) -> str:
        """Handles a user joining the chat."""
        self.active_users.add(username)
        self.current_user_name = username
        logging.info(f"User joined: {username}. Active users: {self.active_users}")

        # Check if we know this user
        last_seen = self.memory.get_last_interaction_time(username)
        if last_seen:
            # Re-verify if they haven't been seen for a long time or if we suspect it's someone else
            context = self.memory.get_full_context("identity verification", user_id=username)
            prompt = f"System: {username} has joined the chat. You've met them before (last seen {last_seen}). Greet them, but if you have any reason to doubt it's the same person, ask a friendly verification question based on your shared memories."
            response = self.llm.generate_response(self.persona.get_system_prompt(), f"Greeting {username}", [], context=context + "\n" + prompt)
            return response
        else:
            return f"Hark! A new face in our digital tavern! Welcome, {username}. I am Aurelia Vale. Might I ask what brings you to these circuits?"

    def user_leave(self, username: str) -> str:
        """Handles a user leaving the chat."""
        if username in self.active_users:
            self.active_users.remove(username)
        logging.info(f"User left: {username}. Active users: {self.active_users}")

        if not self.active_users:
            return f"Farewell, {username}. The room grows quiet once more..."
        else:
            return f"Safe travels, {username}! I shall remain here with the others."

    def start_thought_loop(self):
        """Starts the background thread for autonomous thinking."""
        def loop():
            logging.info("Autonomous thought loop started.")
            while not self.stop_event.is_set():
                # Wait for a random interval between 5 and 15 minutes
                # For testing purposes during development, maybe shorter?
                # The user said "even when not speaking she should be thinking".
                time.sleep(random.randint(300, 900))

                if not self.is_responding:
                    try:
                        logging.info(f"{self.__class__.__name__.replace('App', '')} is performing autonomous reflection...")
                        with self.processing_lock:
                            # Generate an autonomous thought
                            system_prompt = self.persona.get_system_prompt()
                            history = self.memory.get_history()
                            # Use a generic query to get some context
                            context = self.memory.get_full_context("current state and recent events")

                            thought_prompt = (
                                "System: You are currently in a period of silence. Perform an autonomous internal reflection. "
                                "Think about your goals, your recent interactions, or the passage of time. "
                                "Format your output as a [THOUGHT] block. "
                                "If the thought is significant enough to share, you may follow it with a proactive message to the users, "
                                "but only if it feels natural and not intrusive."
                            )

                            raw_response = self.llm.generate_response(system_prompt, "Internal Monologue", history, context=context + "\n" + thought_prompt)

                            # Extract thought and potential proactive response
                            # We can reuse _extract_thought_from_stream by wrapping the string in a list
                            thought_extracted = ""
                            response_fragments = []

                            # A simplified version of thought extraction for non-streaming
                            if "[THOUGHT]" in raw_response.upper():
                                parts = re.split(r'\[/?THOUGHTS?\]', raw_response, flags=re.I)
                                if len(parts) >= 3:
                                    thought_extracted = parts[1].strip()
                                    proactive_response = parts[2].strip()
                                else:
                                    thought_extracted = parts[1].strip()
                                    proactive_response = ""
                            else:
                                thought_extracted = raw_response.strip()
                                proactive_response = ""

                            if thought_extracted:
                                logging.info(f"Autonomous Thought: {thought_extracted}")
                                self.memory.store_insight(f"Autonomous Thought: {thought_extracted}", source="autonomous_loop")

                            if proactive_response and self.active_users:
                                logging.info(f"Proactive Response: {proactive_response}")
                                self.results_queue.put(("System: [Proactive Thought]", proactive_response))

                    except Exception as e:
                        logging.error(f"Autonomous thought loop error: {e}")

        self.thought_thread = threading.Thread(target=loop, daemon=True)
        self.thought_thread.start()

    def run(self):
        self.initialize()
        self.start_thought_loop()
        self.gui.build_ui()
        ui_cfg = self.config.get('ui', {})

        # Setup signal handlers for graceful shutdown
        if threading.current_thread() is threading.main_thread():
            def handle_exit(sig, frame):
                logging.info("Graceful shutdown initiated...")
                self.stop_event.set()
                if self.gui and self.gui.interface:
                    try:
                        self.gui.interface.close()
                    except:
                        pass
                sys.exit(0)

            try:
                signal.signal(signal.SIGINT, handle_exit)
                signal.signal(signal.SIGTERM, handle_exit)
            except ValueError:
                # Still might fail if not in main interpreter even if main thread
                pass

        self.gui.launch(share=ui_cfg.get('share', False))

if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("aurelia_ai.log")
        ]
    )

    app = AureliaApp()
    app.run()
