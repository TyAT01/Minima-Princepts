from __future__ import annotations
import logging
import os
import sys
import re
import yaml
import signal
import threading # Required for background reflection threads
import random
import time
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

from stt.whisper import STTSystem, VoiceMonitor
from ui.web_gui import ShiroGUI
from utils.error_handler import ErrorHandler
from shiro_engine import ShiroEngine
from queue import Queue

class ShiroApp:
    def __init__(self, config_path: str = "config.yaml"):
        self.config = self._load_config(config_path)
        self.engine = ShiroEngine(self.config, on_autonomous_speak=self.handle_autonomous_speak)
        self.error_handler = ErrorHandler(ai_comment_callback=self.ai_comment_on_error)

        stt_cfg = self.config.get('stt', {})
        self.stt = STTSystem(
            model_size=stt_cfg.get('model_size', 'base'),
            device=stt_cfg.get('device'),
            compute_type=stt_cfg.get('compute_type', 'float16')
        )

        self.results_queue = Queue()
        self.interrupt_event = threading.Event()
        self.is_responding = False
        self.active_users = set()

        self.voice_monitor = VoiceMonitor(
            callback=self.process_background_audio,
            interrupt_callback=self.handle_interrupt
        )

        ui_cfg = self.config.get('ui', {})
        self.gui = ShiroGUI(
            process_text_cb=self.process_text,
            process_audio_cb=self.process_audio,
            toggle_mic_cb=self.toggle_mic,
            poll_results_cb=self.poll_results,
            join_chat_cb=self.handle_user_join,
            leave_chat_cb=self.handle_user_leave,
            title=ui_cfg.get('title', "🦊 Shiro: The Sly Kitsune Yaoguai"),
            theme=ui_cfg.get('theme', "soft")
        )

    def _load_config(self, path: str) -> dict:
        try:
            if not os.path.exists(path):
                # Try one level up if not found (e.g. if run from root)
                alt_path = os.path.join("Shiro-AI", path)
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
            logging.info("Initializing Shiro AI...")
            self.engine.initialize()

            logging.info("Initialization complete.")
        except Exception as e:
            self.error_handler.handle_error(e, "Initialization")

    def handle_interrupt(self):
        """Sets the interrupt flag to stop current AI response."""
        if self.is_responding:
            logging.info("!!! Interrupt received !!!")
            self.interrupt_event.set()

    def handle_autonomous_speak(self, text: str, speech_type: str):
        """Bridge for autonomous speech from engine to GUI."""
        # For now, put it into the results queue so the GUI picks it up
        # Use None as user tag to indicate autonomous speech (no user involved)
        self.results_queue.put((None, text))

    def handle_user_join(self, user_name: str):
        """Handles a user joining the chat room."""
        if user_name in self.active_users:
            logging.info(f"User {user_name} already joined. Skipping greeting.")
            return []

        self.active_users.add(user_name)
        logging.info(f"User {user_name} joined.")

        # [V4 UPGRADE] Sync join to engine
        self.engine.on_user_join(user_name)

        # Determine greeting mode based on last interaction time
        last_seen = self.engine.memory.get_last_interaction_time(user_name)

        if last_seen:
            delta = datetime.now(timezone.utc) - last_seen
            if delta.total_seconds() < 7200:  # Returned within 2 hours
                mode = "returning_soon"
            else:  # Long absence — guarded, slightly suspicious
                mode = "returning_long"
        else:
            mode = "new"  # First time ever

        # Each call picks a fresh variant from the pool — Shiro never sounds identical
        prompt = self.engine.get_greeting_prompt(user_name, mode)
        resp = list(self.process_text(prompt, user_name))

        # Fallback: if LLM returns nothing, use a randomised in-character string
        if not resp or not any(r.strip() for r in resp):
            return [self.engine.get_fallback_greeting(user_name, mode)]

        return resp

    def handle_user_leave(self, user_name: str):
        """Handles a user leaving the chat room."""
        if user_name in self.active_users:
            self.active_users.remove(user_name)
        logging.info(f"User {user_name} left.")

        # [V4 UPGRADE] Sync leave to engine
        self.engine.on_user_leave(user_name)

        if not self.active_users:
            logging.info("Chat room is now empty.")
            self.engine.memory.add_interaction("[System]", f"{user_name} left. The room is now empty.", user_id="System")

    def process_text(self, text: Any, user_name: str = None):
        """Generator that yields sentence fragments from the LLM."""
        # Robustly handle list/dict inputs from Gradio
        processed_text = ""
        if isinstance(text, list) and len(text) > 0:
            first_item = text[0]
            if isinstance(first_item, dict):
                processed_text = first_item.get("text", str(first_item))
            else:
                processed_text = str(first_item)
        elif isinstance(text, dict):
            processed_text = text.get("text", str(text))
        else:
            processed_text = str(text)

        if self.engine.processing_lock.locked():
            logging.warning("System is busy processing another request.")
            yield "Wait a moment, I'm thinking..."
            return

        self.is_responding = True
        self.interrupt_event.clear()
        try:
            for fragment in self.engine.process_text(processed_text, user_name, self.interrupt_event):
                yield fragment
        except Exception as e:
            self.error_handler.handle_error(e, "Text Processing")
            yield self.error_handler.get_ai_fallback_response()
        finally:
            self.is_responding = False
            self.interrupt_event.clear()

    def process_background_audio(self, audio_data: Any):
        """Callback for background STT."""
        try:
            transcribed_text = self.stt.transcribe(audio_data)
            if not transcribed_text or not transcribed_text.strip():
                return

            # For background audio, we'll collect the whole response to put in queue
            full_bot_txt = " ".join(self.process_text(transcribed_text, self.engine.current_user_name))
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
        """Asks the AI to make a quick, in-character comment about the error."""
        try:
            system_prompt = self.engine.persona.get_system_prompt()
            comment_prompt = f"The system just encountered a technical error: {error_details}. Make a very short (1 sentence), in-character comment about it, like the coy kitsune you are."
            comment = self.engine.llm.generate_response(system_prompt, comment_prompt, [])
            logging.info(f"AI Error Commentary: {comment}")
        except:
            pass

    def run(self):
        self.initialize()
        self.gui.build_ui()
        ui_cfg = self.config.get('ui', {})

        # Setup signal handlers for graceful shutdown
        if threading.current_thread() is threading.main_thread():
            def handle_exit(sig, frame):
                logging.info("Graceful shutdown signal received...")
                if self.gui and self.gui.interface:
                    try:
                        self.gui.interface.close()
                    except:
                        pass
                # Signal Gradio to stop, but don't exit immediately if we want run() to finish finally

            try:
                signal.signal(signal.SIGINT, handle_exit)
                signal.signal(signal.SIGTERM, handle_exit)
            except ValueError:
                # Still might fail if not in main interpreter even if main thread
                pass

        try:
            self.gui.launch(share=ui_cfg.get('share', False))
        finally:
            logging.info("Performing Reflective Shutdown...")
            try:
                self.engine.shutdown()
            except Exception as e:
                logging.error(f"Error during engine shutdown: {e}")

if __name__ == "__main__":
    # Handle Windows console encoding for emojis
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except (AttributeError, IOError):
            pass

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("shiro_ai.log", encoding="utf-8")
        ]
    )

    app = ShiroApp()
    app.run()
