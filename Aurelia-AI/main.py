from __future__ import annotations
import logging
import os
import sys
import yaml
import signal
import threading
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

        self.results_queue = Queue()
        self.interrupt_event = threading.Event()
        self.is_responding = False

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
            title=ui_cfg.get('title', "⚔️ Aurelia Vale: The Hedge-Knight Squire"),
            theme=ui_cfg.get('theme', "soft")
        )

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

    def _generate_thought(self, user_input: str, history: list, context: str) -> str:
        """Generates an inner monologue entry before responding."""
        try:
            thought_prompt = (
                "You are Aurelia Vale. Before responding to the user, record your private internal thoughts. "
                "What do you really think about what they said? How does it make you feel? "
                "What is your current state of mind? (Keep this concise, ~20 words)."
            )
            # Use full response for thought to avoid complex streaming here
            thought = self.llm.generate_response(thought_prompt, f"User said: {user_input}", history, context)
            return thought.strip()
        except Exception as e:
            logging.warning(f"Failed to generate thought: {e}")
            return "Processing..."

    def process_text(self, text: Any, user_name: str = None):
        """Generator that yields sentence fragments from the LLM with inner monologue and interrupt checks."""
        if user_name:
            self.current_user_name = user_name
        else:
            user_name = self.current_user_name

        # Robustly handle list/dict inputs from Gradio
        if isinstance(text, list) and len(text) > 0:
            text = text[0].get("text", str(text))
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
            try:
                system_prompt = self.persona.get_system_prompt()
                history = self.memory.get_history()
                context = self.memory.get_full_context(text, user_id=user_name)

                # Add Temporal Context (Time Awareness)
                last_time = self.memory.get_last_interaction_time()
                temporal_note = ""
                if last_time:
                    delta = datetime.now(timezone.utc) - last_time
                    hours, remainder = divmod(int(delta.total_seconds()), 3600)
                    minutes, _ = divmod(remainder, 60)

                    if hours > 0:
                        temporal_note = f"You haven't spoken to {user_name} for {hours} hours and {minutes} minutes."
                    else:
                        temporal_note = f"You last spoke to {user_name} {minutes} minutes ago."

                    if delta.days > 0:
                        temporal_note = f"It has been {delta.days} days since your last interaction."

                if temporal_note:
                    context = f"### [TEMPORAL CONTEXT]\n- {temporal_note}\n\n{context}"

                # 1. Inner Monologue Phase
                thought = self._generate_thought(text, history, context)
                logging.info(f"Aurelia's Thought: {thought}")
                self.memory.store_insight(f"Thought: {thought}", source="inner_monologue")

                # Prepend thought to system prompt for the actual response
                augmented_system = f"{system_prompt}\n\nYour current internal thought: {thought}\nUse this thought to guide your response but don't repeat it verbatim."

                # 2. Response Phase
                full_response = ""
                stream = self.llm.stream_response(augmented_system, text, history, context)

                for fragment in split_into_sentences(stream):
                    if self.interrupt_event.is_set():
                        logging.info("Response halted by interrupt.")
                        yield "... [Interrupted]"
                        break

                    full_response += fragment + " "
                    yield fragment

                if not self.interrupt_event.is_set():
                    self.memory.add_interaction(text, full_response.strip(), user_id=user_name)
                    logging.info(f"Successfully processed message. Response length: {len(full_response)}")

                # Periodic reflection
                if len(self.memory._collection.get()['ids']) % 10 == 0:
                     self.reflect()

            except Exception as e:
                self.error_handler.handle_error(e, "Text Processing")
                yield self.error_handler.get_ai_fallback_response()
            finally:
                self.is_responding = False
                self.interrupt_event.clear()

    def reflect(self):
        """Asks the LLM to analyze recent interactions for profiles, events, and insights."""
        try:
            logging.info("Aurelia is reflecting on recent experiences...")
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

            # Simple parser for the YAML-like response
            try:
                # Look for YAML block
                if "```yaml" in analysis_raw:
                    analysis_raw = analysis_raw.split("```yaml")[1].split("```")[0]
                elif "```" in analysis_raw:
                    analysis_raw = analysis_raw.split("```")[1].split("```")[0]

                data = yaml.safe_load(analysis_raw)
                if isinstance(data, dict):
                    for fact in data.get('user_facts', []):
                        self.memory.update_user_profile("default_user", fact)
                    for event in data.get('events', []):
                        self.memory.store_episodic_memory(event)
                    for insight in data.get('insights', []):
                        self.memory.store_insight(insight, source="reflection")
                    logging.info("Deep reflection complete. Memories filed.")
            except Exception as pe:
                logging.warning(f"Failed to parse reflection data: {pe}. Raw: {analysis_raw[:100]}")
                # Fallback to general insight if YAML parsing fails
                self.memory.store_insight(analysis_raw[:500], source="reflection_fallback")

        except Exception as e:
            logging.warning(f"Reflection failed: {e}")

    def process_background_audio(self, audio_data: Any):
        """Callback for background STT."""
        try:
            transcribed_text = self.stt.transcribe(audio_data)
            if not transcribed_text or not transcribed_text.strip():
                return

            # For background audio, we'll collect the whole response to put in queue
            full_bot_txt = ""
            # Pass current_user_name since we don't have it directly from the callback
            for fragment in self.process_text(transcribed_text, self.current_user_name):
                full_bot_txt += fragment + " "

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

            full_response = ""
            for fragment in self.process_text(transcribed_text, user_name):
                full_response += fragment + " "
                yield transcribed_text, full_response.strip()

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
            logging.FileHandler("aurelia_ai.log")
        ]
    )

    app = AureliaApp()
    app.run()
