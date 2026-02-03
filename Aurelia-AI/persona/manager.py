import yaml
import logging
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger(__name__)

class PersonaManager:
    """Manages the AI's personality by loading and parsing the character sheet."""

    def __init__(self, sheet_path: str = "Aurelia_chroma/aurelia_sheet.yaml"):
        self.sheet_path = Path(sheet_path)
        self.persona_data: Dict[str, Any] = {}
        self.system_prompt: str = ""

    def load_persona(self):
        """Loads the YAML character sheet."""
        if not self.sheet_path.exists():
            logger.error(f"Character sheet not found at {self.sheet_path}")
            # Fallback to a basic persona if file is missing
            self.system_prompt = "You are Aurelia Vale, a helpful AI companion."
            return

        try:
            with open(self.sheet_path, 'r', encoding='utf-8') as f:
                self.persona_data = yaml.safe_load(f)
            self._build_system_prompt()
            logger.info("Persona loaded successfully.")
        except Exception as e:
            logger.error(f"Error loading persona: {e}")
            self.system_prompt = "You are Aurelia Vale, a helpful AI companion."

    def _build_system_prompt(self):
        """Constructs the system prompt from the persona data."""
        char = self.persona_data.get('character', {})
        name = char.get('name', 'Aurelia Vale')
        role = char.get('role', 'AI Companion')
        goals = char.get('goals', [])
        identity = char.get('core_identity', {}).get('self_awareness', '')
        traits = char.get('personality_traits', {})
        speech = char.get('speech_patterns', {})
        constraints = char.get('llm_logic_constraints', {}).get('chroma_4b', '')

        prompt = f"### IDENTITY\n"
        prompt += f"Name: {name}\n"
        prompt += f"Role: {role}\n"
        prompt += f"Background: {identity}\n\n"

        prompt += "### GOALS\n"
        for goal in goals:
            prompt += f"- {goal}\n"
        prompt += "\n"

        prompt += "### PERSONALITY TRAITS\n"
        for trait_name, trait_data in traits.items():
            desc = trait_data.get('description', '')
            prompt += f"- {trait_name.replace('_', ' ').title()}: {desc}\n"
        prompt += "\n"

        prompt += "### SPEECH PATTERNS\n"
        prompt += f"Style: {speech.get('style', 'Natural')}\n"
        prompt += "Examples:\n"
        for example in speech.get('examples', []):
            prompt += f"  - \"{example}\"\n"
        prompt += "\n"

        prompt += "### LOGIC CONSTRAINTS\n"
        prompt += f"{constraints}\n\n"

        prompt += "### RESPONSE GUIDELINES\n"
        prompt += "1. Stay in character at all times.\n"
        prompt += "2. Be concise but engaging.\n"
        prompt += "3. Use filler words (uhm, ah, so) occasionally for a more natural feel.\n"
        prompt += "4. If an error occurs, acknowledge it in-character as a 'glitch' or 'technical gremlin'.\n"

        self.system_prompt = prompt

    def get_system_prompt(self) -> str:
        """Returns the constructed system prompt."""
        if not self.system_prompt:
            self.load_persona()
        return self.system_prompt
