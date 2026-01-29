from __future__ import annotations
import os
import logging
from config import settings
from llm.personaplex import PersonaplexManager

# Global manager instance
_pp_manager = None

def get_pp_manager() -> PersonaplexManager:
    global _pp_manager
    if _pp_manager is None:
        modules_dir = os.path.join(os.path.dirname(settings.persona_yaml), "personaplex_modules")
        _pp_manager = PersonaplexManager(modules_dir)
    return _pp_manager

def load_persona_prompt() -> str:
    """Loads the persona from modular Personaplex facets and constructs the system prompt."""
    manager = get_pp_manager()
    combined = manager.load_combined_persona()

    character = combined.get("character", {})
    name = character.get("name", "Aurelia")
    role = character.get("role", "AI Companion")
    goals = ", ".join(character.get("goals", [])) if isinstance(character.get("goals"), list) else character.get("goals", "")
    core_identity = character.get("core_identity", {}).get("self_awareness", "I am an AI.")
    speech_style = combined.get("speech_patterns", {}).get("style", "friendly and helpful.")

    # Extract personality traits
    traits = combined.get("personality_traits", {})
    traits_list = []
    for trait_name, trait_data in traits.items():
        desc = trait_data.get("description", "") if isinstance(trait_data, dict) else trait_data
        traits_list.append(f"- {trait_name.replace('_', ' ').title()}: {desc}")
    traits_str = "\n".join(traits_list)

    system_prompt = (
        f"You are {name}, an advanced virtual human. Your role is '{role}'. "
        f"Your core identity is: '{core_identity}'. Your goal is to '{goals}'. "
        f"You speak in a style that is '{speech_style}'.\n\n"
        f"PERSONALITY TRAITS:\n{traits_str}\n\n"
        f"AUTONOMOUS PERSONAPLEX CONTROL:\n"
        f"You can modify your own personality modules by including a tag at the end of your response. "
        f"Use [PERSONAPLEX: action=modify, module=MOD_NAME, trait=TRAIT_NAME, value=DESCRIPTION].\n"
        f"Example: '[PERSONAPLEX: action=modify, module=squire, trait=valiant, value=Extremely brave in the face of glitches.]'\n\n"
        f"AUTONOMOUS CADENCE CONTROL:\n"
        f"Use [CADENCE: min_gap_s=X, soft_gap_s=Y, max_silence_s=Z, burst_max_items=N].\n"
    )
    return system_prompt
