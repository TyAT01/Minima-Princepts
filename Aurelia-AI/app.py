from __future__ import annotations
import logging
import os
import sys
from pathlib import Path

# Add the current directory to sys.path to allow absolute imports if needed
sys.path.append(str(Path(__file__).parent))

from llm.client import LlamaClient
from memory.store import MemoryStore
from stt.whisper import STTSystem
from persona.manager import PersonaManager
from ui.web_gui import AureliaGUI
from utils.error_handler import ErrorHandler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("aurelia_ai.log")
    ]
)
logger = logging.getLogger("AureliaAI")

class AureliaApp:
    def __init__(self):
        self.error_handler = ErrorHandler(ai_comment_callback=self.ai_comment_on_error)
        self.persona = PersonaManager()
        self.memory = MemoryStore()
        self.llm = LlamaClient()
        self.stt = STTSystem()

        self.gui = AureliaGUI(
            process_text_cb=self.process_text,
            process_audio_cb=self.process_audio
        )

    def initialize(self):
        try:
            logger.info("Initializing Aurelia AI...")
            self.persona.load_persona()
            # self.stt.load_model() # We'll lazy load this to save startup time
            logger.info("Initialization complete.")
        except Exception as e:
            self.error_handler.handle_error(e, "Initialization")

    def process_text(self, text: str) -> str:
        """Processes text input and returns the AI response."""
        try:
            system_prompt = self.persona.get_system_prompt()
            history = self.memory.get_history()
            context = self.memory.get_full_context(text)

            response = self.llm.generate_response(system_prompt, text, history, context)

            self.memory.add_interaction(text, response)
            return response
        except Exception as e:
            self.error_handler.handle_error(e, "Text Processing")
            return self.error_handler.get_ai_fallback_response()

    def process_audio(self, audio_path: str) -> tuple[str, str]:
        """Processes audio input, transcribes it, and returns (transcribed_text, AI_response)."""
        try:
            transcribed_text = self.stt.transcribe(audio_path)
            if not transcribed_text.strip():
                return "[Inaudible]", "I'm sorry, I couldn't quite hear you. Could you repeat that?"

            response = self.process_text(transcribed_text)
            return transcribed_text, response
        except Exception as e:
            self.error_handler.handle_error(e, "Audio Processing")
            return "[Audio Error]", self.error_handler.get_ai_fallback_response()

    def ai_comment_on_error(self, error_details: str):
        """Callback for the error handler to let the AI comment on a glitch."""
        # In a more complex system, this might trigger an autonomous speech event.
        # For now, we'll log it. The next response will likely be a fallback or influenced by the error.
        logger.warning(f"AI noticing error: {error_details}")

    def run(self):
        self.initialize()
        self.gui.build_ui()
        self.gui.launch()

if __name__ == "__main__":
    app = AureliaApp()
    app.run()
