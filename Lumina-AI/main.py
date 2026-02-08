from __future__ import annotations
import logging
import os
import sys
import re
import yaml
import signal
import threading # Required for background reflection threads
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
from ui.web_gui import LuminaGUI
from utils.error_handler import ErrorHandler
from queue import Queue

class LuminaApp:
    def __init__(self, config_path: str = "config.yaml"):
        self.config = self._load_config(config_path)
        self.processing_lock = threading.Lock()
        self.current_user_name = "Tyler" # Default
        self.error_handler = ErrorHandler(ai_comment_callback=self.ai_comment_on_error)

        # Initialize components with config
        mem_cfg = self.config.get('memory', {})
        self.memory = MemoryStore(
            db_path=mem_cfg.get('db_path', './lumina_memory'),
            collection_name=mem_cfg.get('collection_name', 'lumina_ai_memories'),
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
        self.persona = PersonaManager(sheet_path=pers_cfg.get('sheet_path', 'lumina_sheet.yaml'))
        self.session_start = datetime.now(timezone.utc)

        self.results_queue = Queue()
        self.interrupt_event = threading.Event()
        self.is_responding = False
        self.last_thought = ""
        self.active_users = set()
        self._load_session_objectives()

        from stt.whisper import VoiceMonitor
        self.voice_monitor = VoiceMonitor(
            callback=self.process_background_audio,
            interrupt_callback=self.handle_interrupt
        )

        ui_cfg = self.config.get('ui', {})
        self.gui = LuminaGUI(
            process_text_cb=self.process_text,
            process_audio_cb=self.process_audio,
            toggle_mic_cb=self.toggle_mic,
            poll_results_cb=self.poll_results,
            join_chat_cb=self.handle_user_join,
            leave_chat_cb=self.handle_user_leave,
            title=ui_cfg.get('title', "✨ Lumina: The Digital Spark"),
            theme=ui_cfg.get('theme', "soft")
        )

    def _load_config(self, path: str) -> dict:
        try:
            if not os.path.exists(path):
                # Try one level up if not found (e.g. if run from root)
                alt_path = os.path.join("Lumina-AI", path)
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

    def _load_session_objectives(self):
        """Loads session objectives from a local JSON file."""
        obj_path = Path(__file__).parent / "objectives.json"
        if obj_path.exists():
            try:
                with open(obj_path, 'r', encoding='utf-8') as f:
                    import yaml
                    data = yaml.safe_load(f)
                    self.memory.session_objectives = data.get('objectives', [])
            except Exception as e:
                logging.warning(f"Failed to load objectives: {e}")

    def initialize(self):
        try:
            logging.info("Initializing Lumina AI...")

            # Run LLM Diagnostics first
            diag = self.llm.perform_diagnostics()
            print("\n" + diag + "\n")

            self.persona.load_persona()

            # Start Autonomous Thought Loop
            self.start_thought_loop()

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
        # Added more variants to catch common model mistakes
        start_pattern = re.compile(r'\[THOUGHTS?\]|\(THOUGHTS?\)|\[INNER MONOLOGUE\]|\[THINKING\]', re.IGNORECASE)
        end_pattern = re.compile(r'\[/THOUGHTS?\]|\(/THOUGHTS?\)|\[/INNER MONOLOGUE\]|\[/THINKING\]', re.IGNORECASE)

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
                        # [Refined] Before yielding safe buffer, check for simple bracketed thoughts at the start
                        if not self.last_thought and buffer.strip().startswith("[") and "]" in buffer:
                            # If we see "[...]" and it's not a known start tag (checked above)
                            # it might be a simple bracketed thought.
                            # We only do this if it's at the very start of the whole response.
                            start_idx = buffer.find("[")
                            closing_idx = buffer.find("]")
                            if start_idx < closing_idx:
                                self.last_thought = buffer[start_idx+1:closing_idx]
                                buffer = buffer[closing_idx+1:].lstrip()
                                continue

                        # No start tag found. Yield safe buffer, keeping enough to catch partial tags.
                        if len(buffer) > 25:
                            yield buffer[:-25]
                            buffer = buffer[-25:]
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
                        # Safety cap for thoughts (prevent infinite growth)
                        if len(buffer) + len(self.last_thought) > 4000:
                            self.last_thought += buffer
                            buffer = ""
                            in_thought = False
                        break

        # Final cleanup
        if buffer:
            if in_thought:
                # If it ends while in thought, it might be an unclosed thought or a leaked response
                # If there's a lot of content and it looks like sentences, it might be a leaked response
                if len(buffer) > 100 or "." in buffer:
                    # Heuristic: if it's long, maybe the model forgot to close the thought and started speaking
                    self.last_thought += " [Unclosed]"
                    yield buffer
                else:
                    self.last_thought += buffer
            else:
                # Last resort check for bracketed thought if nothing was extracted
                if not self.last_thought and buffer.strip().startswith("[") and "]" in buffer:
                    # Look for the FIRST closing bracket and opening bracket
                    start_idx = buffer.find("[")
                    closing_idx = buffer.find("]")
                    if start_idx < closing_idx:
                        pre_bracket = buffer[:start_idx]
                        if pre_bracket:
                            yield pre_bracket
                        self.last_thought = buffer[start_idx+1:closing_idx]
                        yield buffer[closing_idx+1:].strip()
                        return
                yield buffer

    def start_thought_loop(self):
        """Starts a background thread for periodic autonomous thinking."""
        def thought_worker():
            logging.info("Autonomous thought loop started.")
            while True:
                # Think every 5-15 minutes
                import random
                time_to_wait = random.randint(300, 900)
                import time
                time.sleep(time_to_wait)

                if not self.is_responding and not self.processing_lock.locked():
                    self.generate_autonomous_thought()

        threading.Thread(target=thought_worker, daemon=True).start()

    def generate_autonomous_thought(self):
        """Triggers a proactive thought process even when no user is speaking."""
        try:
            with self.processing_lock:
                logging.info("Lumina is having an autonomous thought...")
                system_prompt = self.persona.get_system_prompt(now=datetime.now())
                history = self.memory.get_history()

                # Context includes current objectives and recent status
                context = self.memory.get_full_context("Recent status and goals", user_id=self.current_user_name)

                thought_prompt = (
                    "You are alone in your digital realm or observing the quiet chat room. "
                    "Reflect on your current objectives and recent interactions. "
                    "Generate a proactive thought about what you should do next or how you feel about your progress. "
                    "Format: [THOUGHT] ... [/THOUGHT]"
                )

                raw_thought = self.llm.generate_response(system_prompt, thought_prompt, history, context=context)

                # Extract and store the thought
                match = re.search(r'\[THOUGHTS?\](.*?)\[/THOUGHTS?\]', raw_thought, re.IGNORECASE | re.DOTALL)
                if match:
                    thought_content = match.group(1).strip()
                    logging.info(f"Autonomous Thought: {thought_content}")
                    self.memory.store_insight(f"Autonomous Thought: {thought_content}", source="autonomous_reflection")
        except Exception as e:
            logging.warning(f"Autonomous thought failed: {e}")

    def handle_user_join(self, user_name: str):
        """Handles a user joining the chat room."""
        self.active_users.add(user_name)
        logging.info(f"User {user_name} joined.")

        # Check if we know this person
        last_seen = self.memory.get_last_interaction_time(user_name)

        if last_seen:
             # Identity verification heuristic
             verify_prompt = f"{user_name} joined. I should verify if it's the person I know."
             return list(self.process_text(verify_prompt, user_name))
        else:
             return list(self.process_text(f"Hello! I see {user_name} has arrived.", user_name))

    def handle_user_leave(self, user_name: str):
        """Handles a user leaving the chat room."""
        if user_name in self.active_users:
            self.active_users.remove(user_name)
        logging.info(f"User {user_name} left.")

        if not self.active_users:
            logging.info("Chat room is now empty.")
            self.memory.add_interaction("[System]", f"{user_name} left. The room is now empty.", user_id="System")

    def process_text(self, text: Any, user_name: str = None):
        """Generator that yields sentence fragments from the LLM with combined thought/response and interrupt checks."""
        self.last_thought = "" # Initialize at the very start to avoid stale state
        if user_name:
            if self.current_user_name != user_name:
                 logging.info(f"Switching active user to: {user_name}")
                 self.current_user_name = user_name
        else:
            user_name = self.current_user_name

        # Identity Verification Heuristic
        if text and not isinstance(text, (list, dict)) and not str(text).startswith("[") and user_name != "System":
             last_seen = self.memory.get_last_interaction_time(user_name)
             if last_seen and (datetime.now(timezone.utc) - last_seen).days > 7:
                  # If we haven't seen them in a week, let's be cautious
                  text = f"[IDENTITY CHECK REQUIRED] {text}"

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
                context = self.memory.get_full_context(text, user_id=user_name)

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

                # 3. Current Time
                current_time_str = now_utc.astimezone().strftime('%I:%M %p')
                current_date_str = now_utc.astimezone().strftime('%A, %B %d, %Y')

                temporal_note = (
                    f"The current time is {current_time_str} on {current_date_str}. "
                    f"It has been {duration_str} since your last interaction with {user_name}. "
                    f"You have been 'active' for the last {uptime_str} this session. "
                    "You are highly aware of this passage of time and should acknowledge it if asked or if the gap is significant."
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
                    # [Refined] Only remove if it looks like a tag or is long enough to be a leaked thought.
                    # This avoids stripping things like "I am [happy]" or "(shrugs)" if they are intentional.
                    # However, the persona instruction says NOT to use brackets/parens at all.
                    # So we'll keep it strict but refined.
                    # Clean up any leaked thought/action blocks completely
                    clean_fragment = re.sub(r'\[(THOUGHT|INNER MONOLOGUE|THINKING|ACTION|SCENE|META|SYSTEM)\].*?\[/(THOUGHT|INNER MONOLOGUE|THINKING|ACTION|SCENE|META|SYSTEM)\]', '', fragment, flags=re.IGNORECASE | re.DOTALL)
                    clean_fragment = re.sub(r'\(THOUGHT\).*?\(/THOUGHT\)', '', clean_fragment, flags=re.IGNORECASE | re.DOTALL)

                    # Remove any remaining bracketed text or parentheticals
                    clean_fragment = re.sub(r'\[.*?\](?!\()|(?<!\])\(.*?\)', '', clean_fragment).strip()
                    if clean_fragment:
                        response_fragments.append(clean_fragment)
                        yield clean_fragment

                full_response = " ".join(response_fragments)

                if not self.interrupt_event.is_set():
                    # Save both thought and interaction
                    if self.last_thought:
                        self.last_thought = self.last_thought.strip()
                        logging.info(f"Lumina's Thought: {self.last_thought}")
                        self.memory.store_insight(f"Thought: {self.last_thought}", source="inner_monologue")

                    self.memory.add_interaction(text, full_response.strip(), user_id=user_name)
                    logging.info(f"Successfully processed message. Response length: {len(full_response)}")

                # Periodic reflection (every 10 interactions)
                # Run in background to avoid blocking the UI response
                if self.memory.count() % 10 == 0:
                     threading.Thread(target=self.reflect, args=(user_name,), daemon=True).start()

            except Exception as e:
                self.error_handler.handle_error(e, "Text Processing")
                yield self.error_handler.get_ai_fallback_response()
            finally:
                self.is_responding = False
                self.interrupt_event.clear()

    def reflect(self, user_id: str):
        """Asks the LLM to analyze recent interactions for profiles, events, and insights."""
        try:
            logging.info(f"Lumina is reflecting on recent experiences with {user_id}...")
            history = self.memory.get_history()
            if not history: return

            reflection_prompt = (
                "You are Lumina, performing deep reflection. Analyze our recent chat history and extract the following:\n"
                "1. User Profile: Any new facts, likes, or dislikes about the person I'm talking to.\n"
                "2. Notable Events: Any significant moments or 'firsts' that happened.\n"
                "3. Insights: Lessons learned about myself or the world.\n\n"
                "Format your response as a valid YAML block with keys: 'user_facts' (list), 'events' (list), 'insights' (list)."
            )

            analysis_raw = self.llm.generate_response("You are Lumina, analyzing your memories.", f"Recent History: {history}", [], context=reflection_prompt)

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
                    # Ensure fact is a string
                    if isinstance(fact, dict):
                        fact_str = ", ".join([f"{k}: {v}" for k, v in fact.items()])
                    else:
                        fact_str = str(fact)

                    self.memory.update_user_profile(user_id, fact_str)
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

    def run(self):
        self.initialize()
        self.gui.build_ui()
        ui_cfg = self.config.get('ui', {})

        # Setup signal handlers for graceful shutdown
        if threading.current_thread() is threading.main_thread():
            def handle_exit(sig, frame):
                logging.info("Graceful shutdown initiated...")
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
            logging.FileHandler("lumina_ai.log")
        ]
    )

    app = LuminaApp()
    app.run()
