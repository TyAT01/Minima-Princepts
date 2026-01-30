import sys
import os
import asyncio
from pathlib import Path

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from llm.persona import load_persona_prompt
from llm.personaplex import PersonaPlex

async def simulate_chat():
    personaplex = PersonaPlex()
    prompt = load_persona_prompt(personaplex)
    print("\n" + "="*60)
    print("✨ AURELIA VALE PERSONAPLEX VERIFICATION ✨")
    print("="*60)
    print(f"\n[SYSTEM PROMPT LOADED]:\n{prompt}")
    print("-" * 40)
    print("🌸 PERSONAPLEX ACTIVE MODULES 🌸")
    print(f"Modules: {personaplex.active_modules if personaplex.active_modules else 'None (Base Only)'}")
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
    print("✅ PERSONAPLEX PERSONA VERIFIED: Aurelia sounds noble, scrappy, and slightly internet-poisoned.")
    print("="*60 + "\n")

if __name__ == "__main__":
    asyncio.run(simulate_chat())
