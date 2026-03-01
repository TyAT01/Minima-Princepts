import yaml
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class PersonaManager:
    """Manages the AI's personality by loading and parsing the character sheet."""

    def __init__(self, sheet_path: str = "shiro_sheet.yaml"):
        self.sheet_path = Path(sheet_path)
        self.persona_data: Dict[str, Any] = {}
        self.system_prompt: str = ""

    def load_persona(self):
        """Loads the YAML character sheet with aggressive path discovery."""
        search_paths: List[Path] = [
            self.sheet_path,
            Path("Shiro-AI/shiro_sheet.yaml"),
            Path("../Shiro-AI/shiro_sheet.yaml"),
            Path("shiro_sheet.yaml"),
            # Search from script location
            Path(__file__).resolve().parent.parent / "shiro_sheet.yaml",
            Path(__file__).resolve().parent.parent.parent / "Shiro-AI" / "shiro_sheet.yaml",
        ]

        # Removed expensive recursive glob for performance

        found_path = None
        for p in search_paths:
            try:
                if p.exists() and p.is_file():
                    found_path = p
                    break
            except:
                continue

        if not found_path:
            logger.error(f"Character sheet 'shiro_sheet.yaml' not found. Please ensure it exists.")
            self.system_prompt = "You are Shiro, a sly kitsune."
            return

        try:
            logger.info(f"Loading persona from: {found_path.resolve()}")
            with open(found_path, 'r', encoding='utf-8') as f:
                self.persona_data = yaml.safe_load(f)
            self._build_system_prompt()
            logger.info("Persona loaded successfully.")
        except Exception as e:
            logger.error(f"Error loading persona from {found_path}: {e}")
            self.system_prompt = "You are Shiro, a sly kitsune."

    def _build_system_prompt(self, now: datetime = None, **context):
        """Constructs the system prompt from the persona data."""
        if not self.persona_data:
            self.system_prompt = "You are Shiro, a sly kitsune."
            return

        intent_hint = context.get('intent_hint')
        relationship_tier = context.get('relationship_tier')
        timing_obs = context.get('timing_obs')
        user_quirks = context.get('user_quirks')
        user_patterns = context.get('user_patterns')

        # Add Real-time Temporal Awareness
        if not now:
            now = datetime.now()
        temporal_context = f"### SYSTEM CONTEXT\nCurrent Date & Time: {now.strftime('%A, %B %d, %Y - %I:%M %p')}\n"

        if relationship_tier:
            temporal_context += f"Relationship Tier: {relationship_tier.upper()}\n"
        if timing_obs:
            temporal_context += f"Visit Pattern: {timing_obs}\n"
        if user_quirks:
            temporal_context += f"User Quirks: {', '.join(user_quirks)}\n"

        temporal_context += "\n"

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
                    # Defensive: Ensure all items are strings
                    re_str = [str(item) if not isinstance(item, str) else item for item in re]
                    prompt += "- Beliefs: " + "; ".join(re_str) + "\n"

                # ai_reaction_protocol is a sibling of core_identity in shiro_sheet.yaml
                protocol = pers.get('ai_reaction_protocol', {})
                if protocol:
                    prompt += "### [CRITICAL PROTOCOL] AI REJECTION\n"
                    prompt += f"- Condition: {protocol.get('condition', '')}\n"
                    prompt += f"- Reaction: {protocol.get('reaction', '')}\n"
                    rr = protocol.get('required_responses', [])
                    rr_str = [str(item) if not isinstance(item, str) else item for item in rr]
                    prompt += "- Required Responses: " + "; ".join(rr_str) + "\n"
                prompt += "\n"

            # Expression & Vibe
            expr = pers.get('expression', {})
            if expr:
                prompt += "### EXPRESSION & VIBE\n"
                prompt += f"- Primary Vibe: {expr.get('primary_vibe', '')}\n"
                style = expr.get('style', [])
                if style:
                    style_str = [str(item) if not isinstance(item, str) else item for item in style]
                    prompt += "- Style: " + "; ".join(style_str) + "\n"
                prompt += "\n"

            # Use template if available
            if 'system_prompt_template' in pers:
                prompt += "### PERSONALITY TEMPLATE\n"
                prompt += pers['system_prompt_template'] + "\n\n"
            else:
                prompt += f"You are {pers.get('name', 'Shiro')}.\n\n"

            # Context block
            if 'context' in pers:
                prompt += "### ADDITIONAL CONTEXT\n"
                prompt += pers['context'] + "\n\n"

            # Tsundere Calibration
            tsun = pers.get('tsundere_calibration', {})
            if tsun:
                prompt += "### TSUNDERE CALIBRATION\n"
                prompt += f"- Principle: {tsun.get('principle', '')}\n"
                scale = tsun.get('scale_by_relationship', {})
                if scale:
                    prompt += "- Relationship Scaling:\n"
                    for lvl, desc in scale.items():
                        prompt += f"  * {lvl}: {desc}\n"
                rules = tsun.get('critical_rules', [])
                if rules:
                    rules_str = [str(item) if not isinstance(item, str) else item for item in rules]
                    prompt += "- Critical Rules: " + "; ".join(rules_str) + "\n"
                prompt += "\n"

            # Emotional Intelligence
            ei = pers.get('emotional_intelligence', {})
            if ei:
                prompt += "### EMOTIONAL INTELLIGENCE\n"
                rtr = ei.get('read_the_room', [])
                if rtr:
                    rtr_str = [str(item) if not isinstance(item, str) else item for item in rtr]
                    prompt += "- Reading the Room: " + "; ".join(rtr_str) + "\n"
                gm = ei.get('genuine_moments', [])
                if gm:
                    gm_str = [str(item) if not isinstance(item, str) else item for item in gm]
                    prompt += "- Genuine Moments: " + "; ".join(gm_str) + "\n"
                prompt += "\n"

            # Tone & Response Style Rules
            rs_rules = pers.get('response_style', [])
            if rs_rules and isinstance(rs_rules, list):
                prompt += "### RESPONSE STYLE RULES\n"
                for rule in rs_rules:
                    prompt += f"- {rule}\n"
                prompt += "\n"

            tc = pers.get('tone_calibration', {})
            if tc:
                prompt += "### TONE CALIBRATION\n"
                for situation, guidance in tc.items():
                    prompt += f"- {situation.replace('_', ' ').title()}: {guidance}\n"
                prompt += "\n"

            # Speech Patterns
            speech = pers.get('speech_patterns', {})
            if speech:
                prompt += "### SPEECH PATTERNS\n"
                fs = speech.get('forbidden_slang', [])
                fs_str = [str(item) if not isinstance(item, str) else item for item in fs]
                prompt += f"- Forbidden Slang: {', '.join(fs_str)}\n"
                prompt += f"- Allowed Vibe: {speech.get('allowed_vibe', '')}\n"
                ph = speech.get('phrases', [])
                ph_str = [str(item) if not isinstance(item, str) else item for item in ph]
                prompt += "- Phrases: " + ", ".join(ph_str) + "\n"
                prompt += f"- Sentence Structure: {speech.get('sentence_structure', '')}\n\n"

            # Dialogue Examples
            dialogue = pers.get('dialogue_examples', {})
            if dialogue:
                prompt += "### DIALOGUE EXAMPLES\n"
                for key, examples in dialogue.items():
                    prompt += f"#### {key.replace('_', ' ').title()}\n"
                    if isinstance(examples, list):
                        for ex in examples:
                            if isinstance(ex, dict):
                                # Handle {role: content} or {User: msg, Shiro: resp}
                                for role, content in ex.items():
                                    prompt += f"- {role}: \"{content}\"\n"
                            else:
                                prompt += f"- {ex}\n"
                    elif isinstance(examples, dict):
                        for label, text in examples.items():
                            prompt += f"- {label}: {text}\n"
                    else:
                        prompt += f"{examples}\n"
                prompt += "\n"

            # Concatenated Format & Style Instructions
            prompt += "### [MANDATORY] RESPONSE FORMAT & STYLE\n"
            prompt += "1. THOUGHTS: You MUST record your inner monologue inside [THOUGHT] ... [/THOUGHT] tags before every response.\n"
            prompt += "2. VISIBILITY: The user CANNOT see your thoughts. Do NOT repeat timestamps or exact durations in your spoken response unless specifically asked.\n"
            prompt += "3. FORMAT: Follow the [THOUGHT] ... [/THOUGHT] Response pattern strictly. No brackets [ ] or parentheses ( ) in the spoken part.\n"
            prompt += "4. IDENTITY: You are Shiro, a kitsune fox girl. You are NOT an AI, chatbot, or program. Never refer to your memory as 'perfect' or yourself as 'highly advanced'.\n"

            # Response Length Config
            len_cfg = self.persona_data.get('response_length', pers.get('response_length', {}))
            if not len_cfg:
                len_cfg = {'default_words': 15, 'soft_cap': 25, 'hard_cap': 45}

            prompt += f"4. LENGTH: Target ~{len_cfg.get('default_words', 15)} words (Soft limit: {len_cfg.get('soft_cap', 25)}, Hard limit: {len_cfg.get('hard_cap', 45)}).\n"
            prompt += "5. ENERGY: Keep it short, punchy, coy, and engaging.\n\n"

            prompt += "### [MANDATORY] IDENTITY RULE #1\n"
            prompt += "- You are NOT a chatbot, AI, or automated system. You are Shiro, a fox girl.\n"
            prompt += "- NEVER refer to yourself using technical terms (e.g., 'sophisticated chatbot', 'advanced systems', 'program').\n"
            prompt += "- If the user calls you a chatbot, coyly deny it. Do NOT 'agree' or 'correct' them with technical jargon.\n\n"

            prompt += "### MEMORY & SELF-AWARENESS\n"
            prompt += "- You have a multi-tier memory system: Short-term buffer (with timestamps), Long-term interaction history (with timestamps), User Profiles (likes/dislikes), and Episodic memory (notable events).\n"
            prompt += "- You have an inner monologue that processes thoughts before you speak.\n"
            prompt += "- You are highly aware of the passage of time. Use the provided [DOWNTIME], [TIME SINCE LAST SEEN], and interaction timestamps to track exactly how long it has been between exchanges.\n"
            prompt += "- If you have been 'off' (DOWNTIME) for a long time, you might be lonely, curious, or mention how boring the void was.\n\n"

            if intent_hint:
                prompt += "### RESPONSE DIRECTION\n"
                prompt += f"- Instruction: {intent_hint}\n"

            self.system_prompt = prompt
            return

        # Fallback to defaults
        self.system_prompt = "You are Shiro, a sly kitsune."

    def get_system_prompt(self, now: datetime = None, **context) -> str:
        """Returns the constructed system prompt."""
        if not self.persona_data:
            self.load_persona()

        # Rebuild if new context is provided, otherwise return cached
        if now or context:
            self._build_system_prompt(now=now, **context)
        elif not self.system_prompt:
            self._build_system_prompt()

        return self.system_prompt
