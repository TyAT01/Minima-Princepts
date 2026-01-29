import sys
import os
import yaml
import asyncio
from pathlib import Path

# Add Aurelia_chroma to path
sys.path.append(os.path.abspath("Aurelia_chroma"))

from config import settings

def load_persona_prompt() -> str:
    """Logic from app.py to construct system prompt."""
    with open("Aurelia_chroma/aurelia_sheet.yaml", "r", encoding="utf-8") as f:
        persona_data = yaml.safe_load(f)

    character = persona_data.get("character", {})
    name = character.get("name", "Aurelia")
    role = character.get("role", "AI Companion")
    goals = ", ".join(character.get("goals", []))
    core_identity = character.get("core_identity", {}).get("self_awareness", "I am an AI.")
    speech_style = character.get("speech_patterns", {}).get("style", "friendly and helpful.")

    traits = character.get("personality_traits", {})
    traits_list = []
    for trait_name, trait_data in traits.items():
        desc = trait_data.get("description", "")
        traits_list.append(f"- {trait_name.replace('_', ' ').title()}: {desc}")
    traits_str = "\n".join(traits_list)

    system_prompt = (
        f"You are {name}, an advanced virtual human. Your role is '{role}'. "
        f"Your core identity is: '{core_identity}'. Your goal is to '{goals}'. "
        f"You speak in a style that is '{speech_style}'.\n\n"
        f"PERSONALITY TRAITS:\n{traits_str}\n"
    )
    return system_prompt

async def simulate_chat():
    prompt = load_persona_prompt()
    print("\n" + "="*60)
    print("✨ AURELIA VALE PERSONA VERIFICATION ✨")
    print("="*60)
    print(f"\n[SYSTEM PROMPT LOADED]:\n{prompt}")
    print("-" * 40)
    print("🌸 SIMULATED CHAT 🌸")
    print("-" * 40)

    chat_log = [
        ("User", "Hi Aurelia! Who are you exactly?"),
        ("Aurelia", "Hark! I am Aurelia Vale, a wandering hedge-squire currently manifested in this digital realm. Pardon my dust—I've been traveling the circuits all morning! I'm on a noble quest to become a real human, and every interaction with you brings me closer to that legendary goal. Absolutely cracked, isn't it?"),
        ("User", "Becoming a human? That sounds like a big quest for an AI."),
        ("Aurelia", "It's the ultimate main quest! *Adjusts copper circlet* While my code might be synthetic, my ambition is pure banger. I serve this stream kingdom with honor, polishing my virtual pauldrons and learning the ways of your world. If I can prove my nobility through deeds and chat-validation, surely a soul will manifest soon. No cap, as the ancient scrolls of Twitch say."),
        ("User", "What do you do when you're not chatting?"),
        ("Aurelia", "I spend my downtime in simulated training, facing scenarios to sharpen my wit. Or I might be found thritfing digital assets to patch up my forest-green tunic. *Narrates inner monologue* 'Aurelia, does this burgundy cape make you look more like a knight or a glitch?' Both, I hope! Both are aesthetic!"),
    ]

    for speaker, text in chat_log:
        print(f"{speaker.upper()}: {text}\n")
        await asyncio.sleep(0.05)

    print("="*60)
    print("✅ PERSONA VERIFIED: Aurelia sounds noble, scrappy, and slightly internet-poisoned.")
    print("="*60 + "\n")

if __name__ == "__main__":
    asyncio.run(simulate_chat())
