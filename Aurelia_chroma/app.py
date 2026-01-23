from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
from typing import Optional

import uvicorn

from chroma.chroma_voice import ChromaVoice, ChromaVoiceConfig
from config import settings
from core.orchestrator import Orchestrator
from core.persona import Persona
from desktop_ui.dashboard import build_dashboard
from discord_ui.always_listen_bot import AlwaysListenBot, DiscordVoiceConfig
from llm.llama_cpp_client import LlamaCppClient
from memory.store import MemoryStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("aurelia_chroma")


def build_orchestrator() -> Orchestrator:
    memory_store = MemoryStore(Path(settings.data_dir) / "memory.json")
    persona = Persona.from_yaml(settings.persona_yaml)
    llm_client = LlamaCppClient(settings.llama_cpp_url)
    return Orchestrator(persona=persona, memory_store=memory_store, llm_client=llm_client)


def build_chroma_voice() -> ChromaVoice:
    config = ChromaVoiceConfig(
        model_name=settings.chroma_voice_model,
        tts_model=settings.chroma_tts_model,
        sample_rate=settings.sample_rate,
    )
    return ChromaVoice(config)


async def run_discord(orchestrator: Orchestrator, chroma_voice: ChromaVoice) -> None:
    if not (settings.discord_token and settings.discord_guild_id and settings.discord_voice_channel_id):
        raise RuntimeError(
            "Discord config missing. Set AURELIA_CHROMA_DISCORD_TOKEN/GUILD_ID/VOICE_CHANNEL_ID"
        )
    config = DiscordVoiceConfig(
        token=settings.discord_token,
        guild_id=settings.discord_guild_id,
        voice_channel_id=settings.discord_voice_channel_id,
        sample_rate=settings.sample_rate,
        vad_aggressiveness=settings.vad_aggressiveness,
    )
    bot = AlwaysListenBot(config=config, orchestrator=orchestrator, chroma_voice=chroma_voice)
    await bot.run()


def run_dashboard(orchestrator: Orchestrator, host: Optional[str], port: Optional[int]) -> None:
    memory_store = orchestrator.memory_store
    app = build_dashboard(memory_store, Path(__file__).parent / "desktop_ui" / "templates")
    uvicorn.run(app, host=host or settings.dashboard_host, port=port or settings.dashboard_port)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Aurelia Chroma Companion")
    parser.add_argument("--discord", action="store_true", help="Run Discord always-listening bot")
    parser.add_argument("--dashboard", action="store_true", help="Run lightweight desktop dashboard")
    parser.add_argument("--prompt", type=str, help="Send a single prompt and exit")
    parser.add_argument("--dashboard-host", type=str)
    parser.add_argument("--dashboard-port", type=int)
    args = parser.parse_args()

    orchestrator = build_orchestrator()
    chroma_voice = build_chroma_voice()

    if args.prompt:
        response = orchestrator.respond(args.prompt, source="cli")
        print(response.text)
        return

    tasks = []
    if args.discord:
        tasks.append(run_discord(orchestrator, chroma_voice))
    if args.dashboard:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, run_dashboard, orchestrator, args.dashboard_host, args.dashboard_port)
        return

    if tasks:
        await asyncio.gather(*tasks)
    else:
        parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())
