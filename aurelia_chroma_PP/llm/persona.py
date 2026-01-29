from __future__ import annotations
import yaml
from config import settings
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from llm.personaplex import PersonaPlex

def load_persona_prompt(personaplex: Optional[PersonaPlex] = None) -> str:
    """Loads the persona from the yaml file and constructs the system prompt."""
    with open(settings.persona_yaml, "r", encoding="utf-8") as f:
        persona_data = yaml.safe_load(f)

    if personaplex:
        persona_data = personaplex.get_merged_persona_data(persona_data)

    character = persona_data.get("character", {})
    name = character.get("name", "Aurelia")
    role = character.get("role", "AI Companion")
    goals = ", ".join(character.get("goals", []))
    core_identity = character.get("core_identity", {}).get("self_awareness", "I am an AI.")
    speech_style = character.get("speech_patterns", {}).get("style", "friendly and helpful.")

    # Extract personality traits to enrich the system prompt
    traits = character.get("personality_traits", {})
    traits_list = []
    for trait_name, trait_data in traits.items():
        if isinstance(trait_data, dict):
            desc = trait_data.get("description", "")
            traits_list.append(f"- {trait_name.replace('_', ' ').title()}: {desc}")
        else:
            traits_list.append(f"- {trait_name.replace('_', ' ').title()}: {trait_data}")
    traits_str = "\n".join(traits_list)

    personaplex_docs = ""
    if personaplex:
        active_mods = ", ".join(personaplex.active_modules) if personaplex.active_modules else "None"
        personaplex_docs = (
            f"\n\nPERSONAPLEX SYSTEM:\n"
            f"You have a dynamic persona architecture. Active modules: {active_mods}.\n"
            f"You can create, update, activate, or deactivate persona modules by including tags at the end of your response.\n"
            f"Format: [PERSONAPLEX: action=create|update|activate|deactivate, name=module_name, data=YAML_CONTENT]\n"
            f"Example: [PERSONAPLEX: action=create, name=scholar, data={{personality_traits: {{scholar: {{description: 'Obsessed with history'}}}}}}]\n"
            f"Modules allow you to evolve and adapt your personality over time."
        )

    system_prompt = (
        f"You are {name}, an advanced virtual human. Your role is '{role}'. "
        f"Your core identity is: '{core_identity}'. Your goal is to '{goals}'. "
        f"You speak in a style that is '{speech_style}'.\n\n"
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
        f"{personaplex_docs}"
    )
    return system_prompt
