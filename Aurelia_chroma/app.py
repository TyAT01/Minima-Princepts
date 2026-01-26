from __future__ import annotations
import argparse
import asyncio
import logging
from pathlib import Path
import yaml

from config import settings
from discord_ui.always_listen_bot import AlwaysListenBot, DiscordVoiceConfig
from llm.chroma_client import ChromaClient
from memory.store import ChromaMemoryStore
from stt.whisper_client import WhisperClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("aurelia_chroma")

def load_persona_prompt() -> str:
    """Loads the persona from the yaml file and constructs the system prompt."""
    with open(settings.persona_yaml, "r", encoding="utf-8") as f:
        persona_data = yaml.safe_load(f)

    character = persona_data.get("character", {})
    name = character.get("name", "Aurelia")
    role = character.get("role", "AI Companion")
    goals = ", ".join(character.get("goals", []))
    core_identity = character.get("core_identity", {}).get("self_awareness", "I am an AI.")
    speech_style = character.get("speech_patterns", {}).get("style", "friendly and helpful.")

    system_prompt = (
        f"You are {name}, an advanced virtual human. Your role is '{role}'. "
        f"Your core identity is: '{core_identity}'. Your goal is to '{goals}'. "
        f"You speak in a style that is '{speech_style}'. "
        "You possess the ability to understand auditory inputs and generate both text and speech."
    )
    return system_prompt

async def run_discord(chroma_client: ChromaClient, memory_store: ChromaMemoryStore, whisper_client: WhisperClient) -> None:
    if not (settings.discord_token and settings.discord_guild_id and settings.discord_voice_channel_id):
        raise RuntimeError(
            "Discord config missing. Set AURELIA_CHROMA_DISCORD_TOKEN/GUILD_ID/VOICE_CHANNEL_ID"
        )
    config = DiscordVoiceConfig(
        token=settings.discord_token,
        guild_id=settings.discord_guild_id,
        voice_channel_id=settings.discord_voice_channel_id,
        sample_rate=settings.sample_rate,
        discord_sample_rate=settings.discord_sample_rate,
    )
    bot = AlwaysListenBot(
        config=config,
        chroma_client=chroma_client,
        memory_store=memory_store,
        whisper_client=whisper_client
    )
    await bot.run()

async def main() -> None:
    parser = argparse.ArgumentParser(description="Aurelia Chroma Companion")
    parser.add_argument("--discord", action="store_true", help="Run Discord always-listening bot")
    args = parser.parse_args()

    # Load persona and initialize clients
    persona_prompt = load_persona_prompt()

    chroma_client = ChromaClient(persona_prompt=persona_prompt, max_new_tokens=settings.max_new_tokens)
    chroma_client.load()

    whisper_client = WhisperClient()
    whisper_client.load()

    memory_store = ChromaMemoryStore(db_path=Path("data/chroma_db"))

    if args.discord:
        await run_discord(chroma_client, memory_store, whisper_client)
    else:
        parser.print_help()

if __name__ == "__main__":
    asyncio.run(main())
