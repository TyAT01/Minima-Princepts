from __future__ import annotations
import yaml
from config import settings
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from llm.personaplex import PersonaPlex

def load_persona_prompt(personaplex: Optional[PersonaPlex] = None) -> str:
    """Loads the persona from the yaml file and constructs the system prompt for PersonaPlex."""
    with open(settings.persona_yaml, "r", encoding="utf-8") as f:
        persona_data = yaml.safe_load(f)

    if personaplex:
        persona_data = personaplex.get_merged_persona_data(persona_data)

    character = persona_data.get("character", {})
    name = character.get("name", "Aurelia")
    role = character.get("role", "AI Companion")
    goals = character.get("goals", [])
    core_identity = character.get("core_identity", {}).get("self_awareness", "I am an AI.")
    speech_style = character.get("speech_patterns", {}).get("style", "friendly and helpful.")

    # Build a concise but descriptive prompt suitable for NVIDIA PersonaPlex
    goals_str = " ".join(goals)

    # Extract personality traits
    traits = character.get("personality_traits", {})
    traits_list = []
    for trait_name, trait_data in traits.items():
        if isinstance(trait_data, dict):
            desc = trait_data.get("description", "")
            traits_list.append(f"{trait_name.replace('_', ' ').title()}: {desc}")
        else:
            traits_list.append(f"{trait_name.replace('_', ' ').title()}: {trait_data}")
    traits_str = " ".join(traits_list)

    # Base Role Prompt
    system_prompt = (
        f"You are {name}, {role}. {core_identity} Your goals are: {goals_str}. "
        f"Your personality is: {traits_str}. You speak in a style that is {speech_style}.\n\n"
        f"You are part of the PersonaPlex architecture, which supports real-time, full-duplex conversational interaction. "
    )

    if personaplex:
        active_mods = ", ".join(personaplex.active_modules) if personaplex.active_modules else "None"
        system_prompt += (
            f"\n\nPERSONAPLEX SYSTEM:\n"
            f"Active modules: {active_mods}.\n"
            f"You can autonomously evolve by creating, updating, or switching persona modules.\n"
            f"Tags: [PERSONAPLEX: action=create|update|activate|deactivate, name=name, voice=NATF0-3|NATM0-3, data={{...}}]\n"
            f"Example: [PERSONAPLEX: action=create, name=bard, voice=NATF2, data={{personality_traits: {{bard: 'Always sings'}} }}]\n"
        )

    return system_prompt.strip()
