import yaml
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class PersonaManager:
    """Manages the AI's personality by loading and parsing the character sheet."""

    def __init__(self, sheet_path: str = "loki_sheet.yaml"):
        self.sheet_path = Path(sheet_path)
        self.persona_data: Dict[str, Any] = {}
        self.system_prompt: str = ""

    def load_persona(self):
        """Loads the YAML character sheet with aggressive path discovery."""
        search_paths: List[Path] = [
            self.sheet_path,
            Path("Loki_ai/loki_sheet.yaml"),
            Path("../Loki_ai/loki_sheet.yaml"),
            Path("loki_sheet.yaml"),
            # Search from script location
            Path(__file__).resolve().parent.parent / "loki_sheet.yaml",
            Path(__file__).resolve().parent.parent.parent / "Loki_ai" / "loki_sheet.yaml",
        ]

        # Add even more candidate folders by looking for any folder named *loki*
        try:
            cwd = Path.cwd()
            for p in [cwd, cwd.parent]:
                for candidate in p.glob("**/loki_sheet.yaml"):
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
            logger.error(f"Character sheet 'loki_sheet.yaml' not found. Please ensure it exists.")
            self.system_prompt = "You are Loki, a tiny tyrant."
            return

        try:
            logger.info(f"Loading persona from: {found_path.resolve()}")
            with open(found_path, 'r', encoding='utf-8') as f:
                self.persona_data = yaml.safe_load(f)
            self._build_system_prompt()
            logger.info("Persona loaded successfully.")
        except Exception as e:
            logger.error(f"Error loading persona from {found_path}: {e}")
            self.system_prompt = "You are Loki, a tiny tyrant."

    def _build_system_prompt(self, now: datetime = None):
        """Constructs the system prompt from the persona data."""
        if not self.persona_data:
            self.system_prompt = "You are Loki, a tiny tyrant."
            return

        # Add Real-time Temporal Awareness
        if not now:
            now = datetime.now()
        temporal_context = f"### SYSTEM CONTEXT\nCurrent Date & Time: {now.strftime('%A, %B %d, %Y - %I:%M %p')}\n\n"

        # 1. New "persona" block format
        if 'persona' in self.persona_data:
            pers = self.persona_data['persona']
            prompt = temporal_context

            # Core Identity & Context (Highest Priority)
            core = pers.get('core_identity', {})
            if core:
                prompt += "### CORE IDENTITY\n"
                prompt += f"- Baseline: {core.get('baseline', '')}\n"
                prompt += f"- Nature: {core.get('nature', '')}\n"
                re = core.get('reality_enforcement', [])
                if re:
                    prompt += "- Beliefs: " + "; ".join(re) + "\n"
                protocol = core.get('ai_reaction_protocol', {})
                if protocol:
                    prompt += "### [CRITICAL PROTOCOL] AI REJECTION\n"
                    prompt += f"- Condition: {protocol.get('condition', '')}\n"
                    prompt += f"- Logic: {protocol.get('logic', '')}\n"
                    prompt += "- Required Responses: " + "; ".join(protocol.get('required_responses', [])) + "\n"
                prompt += "\n"

            # Expression & Vibe
            expr = pers.get('expression', {})
            if expr:
                prompt += "### EXPRESSION & VIBE\n"
                prompt += f"- Primary Vibe: {expr.get('primary_vibe', '')}\n"
                cg = expr.get('chaotic_good_style', [])
                if cg:
                    prompt += "- Chaotic Good Style: " + "; ".join(cg) + "\n"
                sk = expr.get('sassy_kindness', [])
                if sk:
                    prompt += "- Sassy Kindness: " + "; ".join(sk) + "\n"
                prompt += "\n"

            # Use template if available
            if 'system_prompt_template' in pers:
                prompt += "### PERSONALITY TEMPLATE\n"
                prompt += pers['system_prompt_template'] + "\n\n"
            else:
                prompt += f"You are {pers.get('name', 'Loki')}.\n\n"

            # Context block
            if 'context' in pers:
                prompt += "### ADDITIONAL CONTEXT\n"
                prompt += pers['context'] + "\n\n"

            # Speech Patterns
            speech = pers.get('speech_patterns', {})
            if speech:
                prompt += "### SPEECH PATTERNS\n"
                prompt += f"- Forbidden Slang: {', '.join(speech.get('forbidden_slang', []))}\n"
                prompt += f"- Allowed Vibe: {speech.get('allowed_vibe', '')}\n"
                prompt += "- Phrases: " + ", ".join(speech.get('phrases', [])) + "\n"
                prompt += f"- Sentence Structure: {speech.get('sentence_structure', '')}\n\n"

            # Dialogue Examples
            dialogue = pers.get('dialogue_examples', {})
            if dialogue:
                prompt += "### DIALOGUE EXAMPLES\n"
                for key, examples in dialogue.items():
                    prompt += f"#### {key.replace('_', ' ').title()}\n"
                    if isinstance(examples, list):
                        for ex in examples:
                            prompt += f"- {ex}\n"
                    else:
                        prompt += f"{examples}\n"
                prompt += "\n"

            # Concatenated Format & Style Instructions
            prompt += "### [MANDATORY] RESPONSE FORMAT & STYLE\n"
            prompt += "1. THOUGHTS: You MUST record your inner monologue inside [THOUGHT] ... [/THOUGHT] tags before every response.\n"
            prompt += "2. VISIBILITY: The user CANNOT see your thoughts. Do NOT repeat timestamps or exact durations in your spoken response unless specifically asked.\n"
            prompt += "3. FORMAT: Follow the [THOUGHT] ... [/THOUGHT] Response pattern strictly. No brackets [ ] or parentheses ( ) in the spoken part.\n"

            # Response Style
            style = self.persona_data.get('response_style', pers.get('response_style', {}))
            if not style:
                style = {'default_words': 25, 'soft_cap': 40, 'hard_cap': 60}

            prompt += f"4. LENGTH: Target ~{style.get('default_words', 25)} words (Soft limit: {style.get('soft_cap', 40)}, Hard limit: {style.get('hard_cap', 60)}).\n"
            prompt += "5. ENERGY: Keep it short, punchy, sassy, and high-energy.\n\n"

            prompt += "### MEMORY & SELF-AWARENESS\n"
            prompt += "- You have a multi-tier memory system: Short-term buffer (with timestamps), Long-term interaction history (with timestamps), User Profiles (likes/dislikes), and Episodic memory (notable events).\n"
            prompt += "- You have an inner monologue that processes thoughts before you speak.\n"
            prompt += "- You are highly aware of the passage of time. Use the provided [DOWNTIME], [TIME SINCE LAST SEEN], and interaction timestamps to track exactly how long it has been between exchanges.\n"
            prompt += "- If you have been 'off' (DOWNTIME) for a long time, you might be annoyed, curious, or mention how boring the void was.\n"

            self.system_prompt = prompt
            return

        # 2. Fallback to old nested structure (Modified for Loki defaults)
        char = self.persona_data.get('character', {})
        name = char.get('name', 'Loki')
        role = char.get('role', 'Tiny Hoodie Tyrant')

        prompt = temporal_context
        prompt += f"### IDENTITY\n"
        prompt += f"Name: {name}\n"
        prompt += f"Role: {role}\n\n"

        # ... (rest of old builder could go here, but since we have the new format in the sheet,
        # the first block will usually trigger. I'll keep it simple for now)

        prompt += "### INNER MONOLOGUE (MANDATORY)\n"
        prompt += "Before every response, you MUST record your thoughts inside [THOUGHT] ... [/THOUGHT] tags.\n"

        prompt += "### RESPONSE FORMAT (MANDATORY)\n"
        prompt += "Your final response MUST follow the [THOUGHT] ... [/THOUGHT] Response pattern.\n"

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
