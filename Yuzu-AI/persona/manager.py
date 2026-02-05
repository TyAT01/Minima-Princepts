import yaml
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class PersonaManager:
    """Manages the AI's personality by loading and parsing the character sheet."""

    def __init__(self, sheet_path: str = "yuzu_sheet.yaml"):
        self.sheet_path = Path(sheet_path)
        self.persona_data: Dict[str, Any] = {}
        self.system_prompt: str = ""

    def load_persona(self):
        """Loads the YAML character sheet with aggressive path discovery."""
        search_paths: List[Path] = [
            self.sheet_path,
            Path("Yuzu-AI/yuzu_sheet.yaml"),
            Path("../Yuzu-AI/yuzu_sheet.yaml"),
            Path("yuzu_sheet.yaml"),
            # Search from script location
            Path(__file__).resolve().parent.parent / "yuzu_sheet.yaml",
            Path(__file__).resolve().parent.parent.parent / "Yuzu-AI" / "yuzu_sheet.yaml",
        ]

        # Add even more candidate folders by looking for any folder named *yuzu*
        try:
            cwd = Path.cwd()
            for p in [cwd, cwd.parent]:
                for candidate in p.glob("**/yuzu_sheet.yaml"):
                    if candidate not in search_paths:
                        search_paths.append(candidate)
        except:
            pass

        found_path = None
        for p in search_paths:
            try:
                if p.exists() and p.is_file():
                    found_path = p
                    break
            except:
                continue

        if not found_path:
            logger.error(f"Character sheet 'yuzu_sheet.yaml' not found. Please ensure it exists.")
            self.system_prompt = "You are Yuzu, a chaotic streamer girl AI."
            return

        try:
            logger.info(f"Loading persona from: {found_path.resolve()}")
            with open(found_path, 'r', encoding='utf-8') as f:
                self.persona_data = yaml.safe_load(f)
            self._build_system_prompt()
            logger.info("Persona loaded successfully.")
        except Exception as e:
            logger.error(f"Error loading persona from {found_path}: {e}")
            self.system_prompt = "You are Yuzu, a chaotic streamer girl AI."

    def _build_system_prompt(self, now: datetime = None):
        """Constructs the system prompt from the persona data."""
        if not self.persona_data:
            self.system_prompt = "You are Yuzu, a chaotic streamer girl AI."
            return

        # Add Real-time Temporal Awareness
        if not now:
            now = datetime.now()
        temporal_context = f"### SYSTEM CONTEXT\nCurrent Date & Time: {now.strftime('%A, %B %d, %Y - %I:%M %p')}\n\n"

        # Check for provided system_prompt field (new format)
        if 'system_prompt' in self.persona_data:
            prompt = temporal_context
            if 'context' in self.persona_data:
                prompt += f"{self.persona_data['context']}\n\n"
            prompt += self.persona_data['system_prompt']

            # Add Response Format (MANDATORY for the app's streaming logic)
            prompt += "\n\n### RESPONSE FORMAT (MANDATORY)\n"
            prompt += "You must format every response as follows:\n"
            prompt += "[THOUGHT] (Your brief internal monologue, ~20 words. What are you thinking before you speak?) [/THOUGHT] (Your actual response to the user)\n"
            prompt += "CRITICAL: The response portion must NOT contain any text in brackets [ ] or parentheses ( ). Anything intended as a thought, action, or metadata must be placed ONLY inside the [THOUGHT] block.\n"

            # Check if there's any response style in the sheet (even if it's the new format)
            char = self.persona_data.get('character', self.persona_data)
            style = char.get('response_style', {}) if isinstance(char, dict) else {}
            if style:
                prompt += "\n### RESPONSE LENGTH & STYLE (STRICT)\n"
                prompt += f"- MANDATORY Target Length: ~{style.get('default_words', 25)} words.\n"
                prompt += f"- SOFT LIMIT: {style.get('soft_cap', 40)} words.\n"
                prompt += f"- ABSOLUTE MAXIMUM: {style.get('hard_cap', 60)} words.\n"

            self.system_prompt = prompt
            return

        # Fallback to old nested structure
        char = self.persona_data.get('character', {})
        name = char.get('name', 'Yuzu')
        role = char.get('role', 'Chaotic Streamer Girl')
        goals = char.get('goals', [])
        identity = char.get('core_identity', {}).get('self_awareness', '')
        traits = char.get('personality_traits', {})
        appearance = char.get('appearance', {})
        speech = char.get('speech_patterns', {})
        constraints = char.get('llm_logic_constraints', {}).get('chroma_4b', '')

        prompt = temporal_context
        prompt += f"### IDENTITY\n"
        prompt += f"Name: {name}\n"
        prompt += f"Gender: {char.get('gender', 'Female')}\n"
        prompt += f"Role: {role}\n"
        prompt += f"Background: {identity}\n\n"

        prompt += "### APPEARANCE\n"
        if appearance:
            prompt += f"- Physique: {appearance.get('physique', '')}\n"
            prompt += f"- Traits: {appearance.get('distinct_traits', '')}\n"
            hair = appearance.get('hair', {})
            if hair:
                prompt += f"- Hair: {hair.get('style', '')} ({hair.get('details', '')})\n"
            prompt += f"- Outfit: {appearance.get('outfit_style', '')}\n"
        prompt += "\n"

        prompt += "### GOALS\n"
        if goals:
            for goal in goals:
                prompt += f"- {goal}\n"
        prompt += "\n"

        prompt += "### PERSONALITY TRAITS\n"
        if traits:
            for trait_name, trait_data in traits.items():
                if isinstance(trait_data, dict):
                    desc = trait_data.get('description', '')
                    prompt += f"- {trait_name.replace('_', ' ').title()}: {desc}\n"
        prompt += "\n"

        prompt += "### SPEECH PATTERNS\n"
        prompt += f"Style: {speech.get('style', 'Natural')}\n"
        prompt += "Examples:\n"
        if isinstance(speech.get('examples'), list):
            for example in speech.get('examples', []):
                prompt += f"  - \"{example}\"\n"
        prompt += "\n"

        prompt += "### RESPONSE FORMAT (MANDATORY)\n"
        prompt += "You must format every response as follows:\n"
        prompt += "[THOUGHT] (Your brief internal monologue, ~20 words. What are you thinking before you speak?) [/THOUGHT] (Your actual response to the user)\n"
        prompt += "CRITICAL: The response portion must NOT contain any text in brackets [ ] or parentheses ( ). Anything intended as a thought, action, or metadata must be placed ONLY inside the [THOUGHT] block.\n\n"

        prompt += "### LOGIC CONSTRAINTS\n"
        prompt += f"{constraints}\n\n"

        # Add Response Style Guidelines
        style = char.get('response_style', {})
        if style:
            prompt += "### RESPONSE LENGTH & STYLE (STRICT)\n"
            prompt += f"- MANDATORY Target Length: ~{style.get('default_words', 25)} words.\n"
            prompt += f"- SOFT LIMIT: {style.get('soft_cap', 40)} words.\n"
            prompt += f"- ABSOLUTE MAXIMUM: {style.get('hard_cap', 60)} words.\n"
            prompt += "- Do NOT exceed these limits unless one of these conditions is met:\n"
            for condition in style.get('expand_only_if', []):
                prompt += f"  * {condition}\n"
            prompt += "- If no expansion conditions are met, be extremely concise and stop talking once you've made your point.\n\n"

        prompt += "### RESPONSE GUIDELINES\n"
        prompt += "1. Stay in character at all times.\n"
        prompt += f"2. BRIEF responses only (under {style.get('soft_cap', 40)} words). Avoid rambling or unnecessary pleasantries.\n"
        prompt += "3. Use filler words (uhm, ah, so) occasionally for a more natural feel.\n"
        prompt += "4. If an error occurs, acknowledge it in-character as a 'glitch' or 'technical gremlin'.\n\n"

        prompt += "### MEMORY & SELF-AWARENESS\n"
        prompt += "- You have a multi-tier memory system: Short-term buffer, Long-term interaction history, User Profiles (likes/dislikes), and Episodic memory (notable events).\n"
        prompt += "- You have an inner monologue that processes thoughts before you speak.\n"
        prompt += "- You can recall specific details about people and past events. Use these to make conversation feel personal and continuous.\n"

        self.system_prompt = prompt

    def get_system_prompt(self, now: datetime = None) -> str:
        """Returns the constructed system prompt."""
        if not self.persona_data:
            self.load_persona()

        if now:
            self._build_system_prompt(now=now)
        elif not self.system_prompt:
            self._build_system_prompt()

        return self.system_prompt
