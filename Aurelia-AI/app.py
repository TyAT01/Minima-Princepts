from __future__ import annotations
import logging
import os
import sys
import yaml
from pathlib import Path

# Add the current directory to sys.path
sys.path.append(str(Path(__file__).parent))

from llm.client import LlamaClient
from memory.store import MemoryStore
from stt.whisper import STTSystem
from persona.manager import PersonaManager
from ui.web_gui import AureliaGUI
from utils.error_handler import ErrorHandler

class AureliaApp:
    def __init__(self, config_path: str = "Aurelia-AI/config.yaml"):
        self.config = self._load_config(config_path)

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
            model=llm_cfg.get('model', 'llama3.1:8b'),
            api_type=llm_cfg.get('api_type', 'ollama')
        )

        stt_cfg = self.config.get('stt', {})
        self.stt = STTSystem(
            model_size=stt_cfg.get('model_size', 'base'),
            device=stt_cfg.get('device'),
            compute_type=stt_cfg.get('compute_type', 'float16')
        )

        pers_cfg = self.config.get('persona', {})
        self.persona = PersonaManager(sheet_path=pers_cfg.get('sheet_path', 'Aurelia_chroma/aurelia_sheet.yaml'))

        ui_cfg = self.config.get('ui', {})
        self.gui = AureliaGUI(
            process_text_cb=self.process_text,
            process_audio_cb=self.process_audio,
            title=ui_cfg.get('title', "⚔️ Aurelia Vale: The Hedge-Knight Squire"),
            theme=ui_cfg.get('theme', "soft")
        )

    def _load_config(self, path: str) -> dict:
        try:
            with open(path, 'r') as f:
                return yaml.safe_load(f)
        except Exception as e:
            print(f"Warning: Could not load config from {path}, using defaults. Error: {e}")
            return {}

    def initialize(self):
        try:
            logging.info("Initializing Aurelia AI...")
            self.persona.load_persona()
            logging.info("Initialization complete.")
        except Exception as e:
            self.error_handler.handle_error(e, "Initialization")

    def process_text(self, text: str) -> str:
        try:
            system_prompt = self.persona.get_system_prompt()
            history = self.memory.get_history()
            context = self.memory.get_full_context(text)

            response = self.llm.generate_response(system_prompt, text, history, context)

            self.memory.add_interaction(text, response)

            # Intelligent background: check if we should "reflect" (every 10 interactions for example)
            # This is a simple hook for "years of storage" intelligence
            if len(self.memory._collection.get()['ids']) % 10 == 0:
                 self.reflect()

            return response
        except Exception as e:
            self.error_handler.handle_error(e, "Text Processing")
            return self.error_handler.get_ai_fallback_response()

    def reflect(self):
        """Asks the LLM to summarize recent interactions into a long-term insight."""
        try:
            logging.info("Aurelia is reflecting on recent experiences...")
            history = self.memory.get_history()
            if not history: return

            prompt = "Please summarize our recent conversation into one or two significant lessons or facts about the user or our journey. Format it as a concise insight for long-term memory."
            reflection_prompt = f"System: You are reflecting on your journey.\nRecent History: {history}\n\nTask: {prompt}"

            insight = self.llm.generate_response("You are Aurelia Vale, reflecting on your experiences.", reflection_prompt, [])
            if insight:
                self.memory.store_insight(insight, source="automatic_reflection")
        except Exception as e:
            logging.warning(f"Reflection failed: {e}")

    def process_audio(self, audio_path: str) -> tuple[str, str]:
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
        logging.warning(f"AI noticing error: {error_details}")

    def run(self):
        self.initialize()
        self.gui.build_ui()
        ui_cfg = self.config.get('ui', {})
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
