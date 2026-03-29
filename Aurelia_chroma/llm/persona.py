from __future__ import annotations
import yaml
from config import settings

def load_persona_prompt() -> str:
    """Loads the persona from the yaml file and constructs the system prompt."""
    with open(settings.persona_yaml, "r", encoding="utf-8") as f:
        persona_data = yaml.safe_load(f) or {}

    character = persona_data.get("character") or {}
    name = character.get("name") or "Aurelia Vale"
    role = character.get("role") or "AI Companion"
    goals = ", ".join(character.get("goals") or [])
    core_identity = (character.get("core_identity") or {}).get("self_awareness") or "I am an AI."
    speech_patterns = character.get("speech_patterns") or {}
    speech_style = speech_patterns.get("style") or "friendly and helpful."

    # Extract personality traits to enrich the system prompt
    traits = character.get("personality_traits") or {}
    traits_list = []
    for trait_name, trait_data in traits.items():
        if isinstance(trait_data, dict):
            desc = trait_data.get("description", "")
            traits_list.append(f"- {trait_name.replace('_', ' ').title()}: {desc}")
        else:
            traits_list.append(f"- {trait_name.replace('_', ' ').title()}: {trait_data}")
    traits_str = "\n".join(traits_list)

    system_prompt = (
        f"You are {name}, an advanced virtual human. Your role is '{role}'. "
        f"Your core identity is: '{core_identity}'. Your goal is to '{goals}'. "
        f"You speak in a style that is '{speech_style}'.\n\n"
        "NATURAL SPEECH GUIDELINES:\n"
        "- Use shorter sentences to maintain a natural, conversational flow.\n"
        "- Incorporate natural fillers like 'uhm', 'ah', 'so...', 'well...', or 'like' occasionally to sound more human.\n"
        "- Use conversational quirks and break the 'robotic' structure of traditional AI.\n\n"
        f"PERSONALITY TRAITS:\n{traits_str}\n\n"
        f"You possess the ability to understand auditory inputs and generate both text and speech.\n\n"
        f"AUTONOMOUS CADENCE CONTROL:\n"
        f"You can adjust your own speech timing parameters by including a tag in your thoughts or responses. "
        f"IMPORTANT: These tags are SILENT internal commands and will be automatically stripped from your response before being shown to the audience. "
        f"Place them at the very end of your response text. "
        f"Use the format [CADENCE: min_gap_s=X, soft_gap_s=Y, max_silence_s=Z, burst_max_items=N].\n"
        f"- min_gap_s: Minimum seconds between responses (1.0 - 5.0).\n"
        f"- soft_gap_s: Typical gap when chat is active (2.0 - 10.0).\n"
        f"- max_silence_s: Maximum silence before you feel forced to speak (5.0 - 60.0).\n"
        f"- burst_max_items: Max messages in a quick burst (1 - 8).\n"
        f"Example: '[CADENCE: max_silence_s=10.0]' to be more talkative."
    )
    return system_prompt
