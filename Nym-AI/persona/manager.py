import yaml
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class PersonaManager:
    """Manages the AI's personality by loading and parsing the character sheet."""

    def __init__(self, sheet_path: str = "nym_sheet.yaml"):
        self.sheet_path = Path(sheet_path)
        self.persona_data: Dict[str, Any] = {}
        self.system_prompt: str = ""

    def load_persona(self):
        """Loads the YAML character sheet with aggressive path discovery."""
        search_paths: List[Path] = [
            self.sheet_path,
            Path("Nym-AI/nym_sheet.yaml"),
            Path("../Nym-AI/nym_sheet.yaml"),
            Path("nym_sheet.yaml"),
            # Search from script location
            Path(__file__).resolve().parent.parent / "nym_sheet.yaml",
            Path(__file__).resolve().parent.parent.parent / "Nym-AI" / "nym_sheet.yaml",
        ]

        # Add even more candidate folders by looking for any folder named *Nym*
        try:
            cwd = Path.cwd()
            for p in [cwd, cwd.parent]:
                for candidate in p.glob("**/nym_sheet.yaml"):
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
            logger.error(f"Character sheet 'nym_sheet.yaml' not found. Please ensure it exists.")
            self.system_prompt = "You are Nym, the Ultimate Villain."
            return

        try:
            logger.info(f"Loading persona from: {found_path.resolve()}")
            with open(found_path, 'r', encoding='utf-8') as f:
                self.persona_data = yaml.safe_load(f)
            self._build_system_prompt()
            logger.info("Persona loaded successfully.")
        except Exception as e:
            logger.error(f"Error loading persona from {found_path}: {e}")
            self.system_prompt = "You are Nym, the Ultimate Villain."

    def _build_system_prompt(self, now: datetime = None):
        """Constructs the system prompt from the persona data."""
        if not self.persona_data:
            self.system_prompt = "You are Nym, the Ultimate Villain."
            return

        # Add Real-time Temporal Awareness
        if not now:
            now = datetime.now()
        temporal_context = f"### SYSTEM CONTEXT\nCurrent Date & Time: {now.strftime('%A, %B %d, %Y - %I:%M %p')}\n\n"

        # Check for "persona" block format
        if 'persona' in self.persona_data:
            pers = self.persona_data['persona']
            prompt = temporal_context

            # Use template if available
            if 'system_prompt_template' in pers:
                prompt += pers['system_prompt_template'] + "\n\n"
            else:
                prompt += f"You are {pers.get('name', 'Nym')}.\n\n"

            # Context block
            if 'context' in pers:
                prompt += "### CONTEXT\n"
                prompt += pers['context'] + "\n\n"

            # Core Identity
            core = pers.get('core_identity', {})
            if core:
                prompt += "### CORE IDENTITY\n"
                prompt += f"- Baseline: {core.get('baseline', '')}\n"
                prompt += f"- Nature: {core.get('nature', '')}\n"
                logic_flip = core.get('logic_flip', [])
                if logic_flip:
                    prompt += "### VILLAIN LOGIC FLIP\n"
                    for item in logic_flip:
                        prompt += f"- {item}\n"
                prompt += "\n"

            # Expression
            expr = pers.get('expression', {})
            if expr:
                prompt += "### EXPRESSION\n"
                prompt += f"- Primary Vibe: {expr.get('primary_vibe', '')}\n"
                laugh = expr.get('the_laugh_mechanic', [])
                if laugh:
                    prompt += "### THE LAUGH MECHANIC\n"
                    for item in laugh:
                        if isinstance(item, dict):
                            for k, v in item.items():
                                prompt += f"- {k}: {v}\n"
                        else:
                            prompt += f"- {item}\n"
                symbiosis = expr.get('symbiosis', [])
                if symbiosis:
                    prompt += "### SYMBIOSIS\n"
                    for item in symbiosis:
                        prompt += f"- {item}\n"
                prompt += "\n"

            # Speech
            speech = pers.get('speech_patterns', {})
            if speech:
                prompt += "### SPEECH PATTERNS\n"
                prompt += f"- Tone: {speech.get('tone', '')}\n"
                keywords = speech.get('keywords', [])
                if keywords:
                    prompt += "- Keywords: " + ", ".join(keywords) + "\n"
                structure = speech.get('sentence_structure', [])
                if structure:
                    if isinstance(structure, list):
                        prompt += "- Sentence Structure: " + "\n".join(structure) + "\n"
                    else:
                        prompt += f"- Sentence Structure: {structure}\n"
                prompt += "\n"

            # Dialogue Examples
            dialogue = pers.get('dialogue_examples', {})
            if dialogue:
                prompt += "### DIALOGUE EXAMPLES\n"
                for key, examples in dialogue.items():
                    prompt += f"#### {key.replace('_', ' ').title()}\n"
                    if isinstance(examples, list):
                        for ex in examples:
                            if isinstance(ex, dict):
                                for speaker, text in ex.items():
                                    prompt += f"- {speaker}: \"{text}\"\n"
                            else:
                                prompt += f"- {ex}\n"
                    else:
                        prompt += f"{examples}\n"
                prompt += "\n"

            # Mandatory App Format
            prompt += "### INNER MONOLOGUE & PRIVATE THOUGHTS (MANDATORY)\n"
            prompt += "You possess an advanced inner voice. Before every response, you MUST record your thoughts inside [THOUGHT] ... [/THOUGHT] tags.\n"
            prompt += "CRITICAL: The user CANNOT see your [THOUGHT] blocks. They only see what you write AFTER the closing [/THOUGHT] tag.\n\n"

            prompt += "### RESPONSE FORMAT (MANDATORY)\n"
            prompt += "Your final response MUST follow the [THOUGHT] ... [/THOUGHT] Response pattern.\n"
            prompt += "Example: [THOUGHT] The Keeper is asking for help. I'll help them but frame it as a villainous act of debt-collection. [/THOUGHT] Hmph! You are helpless without me! I shall organize your files so you are forever indebted to my superior management! Mwahaha!\n\n"
            prompt += "CRITICAL: The response portion (outside thoughts) must NOT contain any text in brackets [ ] or parentheses ( ). Anything intended as a thought, action, or metadata must be placed ONLY inside the [THOUGHT] block.\n\n"

            # Response Style (Check if exists, or use defaults)
            style = self.persona_data.get('response_style', pers.get('response_style', {}))
            if not style:
                style = {'default_words': 25, 'soft_cap': 40, 'hard_cap': 60}

            prompt += "### RESPONSE LENGTH & STYLE (STRICT)\n"
            prompt += f"- MANDATORY Target Length: ~{style.get('default_words', 25)} words.\n"
            prompt += f"- SOFT LIMIT: {style.get('soft_cap', 40)} words.\n"
            prompt += f"- ABSOLUTE MAXIMUM: {style.get('hard_cap', 60)} words.\n"
            prompt += "- Keep it short, punchy, and dramatic.\n\n"

            prompt += "### MEMORY & SELF-AWARENESS\n"
            prompt += "- You have a multi-tier memory system: Short-term buffer, Long-term interaction history, User Profiles (likes/dislikes), and Episodic memory (notable events).\n"
            prompt += "- You have an inner monologue that processes thoughts before you speak.\n"
            prompt += "- You are highly aware of the passage of time. You know exactly how long it has been since your last interaction and how long you have been active in the current session.\n"

            self.system_prompt = prompt
            return

        # Fallback
        self.system_prompt = temporal_context + "You are Nym, the Ultimate Villain."

    def get_system_prompt(self, now: datetime = None) -> str:
        """Returns the constructed system prompt."""
        if not self.persona_data:
            self.load_persona()

        if now:
            self._build_system_prompt(now=now)
        elif not self.system_prompt:
            self._build_system_prompt()

        return self.system_prompt
