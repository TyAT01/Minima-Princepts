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
from hardware.profiler import HardwareProfiler
from hardware.audio_player import LocalAudioPlayer
from orchestrator import AureliaOrchestrator
from adapters.twitch import TwitchChatAdapter
from adapters.youtube import YouTubeChatAdapter
import web_dashboard

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("aurelia_vale")

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

    # Extract personality traits to enrich the system prompt
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
        f"PERSONALITY TRAITS:\n{traits_str}\n\n"
        f"You possess the ability to understand auditory inputs and generate both text and speech.\n\n"
        f"AUTONOMOUS CADENCE CONTROL:\n"
        f"You can adjust your own speech timing parameters by including a tag in your thoughts or responses. "
        f"Use the format [CADENCE: min_gap_s=X, soft_gap_s=Y, max_silence_s=Z, burst_max_items=N].\n"
        f"- min_gap_s: Minimum seconds between responses (1.0 - 5.0).\n"
        f"- soft_gap_s: Typical gap when chat is active (2.0 - 10.0).\n"
        f"- max_silence_s: Maximum silence before you feel forced to speak (5.0 - 60.0).\n"
        f"- burst_max_items: Max messages in a quick burst (1 - 8).\n"
        f"Example: '[CADENCE: max_silence_s=10.0]' to be more talkative."
    )
    return system_prompt

async def run_discord(orchestrator: AureliaOrchestrator) -> None:
    if not (settings.discord_token and settings.discord_guild_id and settings.discord_voice_channel_id):
        logger.warning("Discord config missing. Skipping Discord bot.")
        return

    config = DiscordVoiceConfig(
        token=settings.discord_token,
        guild_id=settings.discord_guild_id,
        voice_channel_id=settings.discord_voice_channel_id,
        sample_rate=settings.sample_rate,
        discord_sample_rate=settings.discord_sample_rate,
    )
    bot = AlwaysListenBot(config=config, orchestrator=orchestrator)
    await bot.run()

async def main() -> None:
    web_dashboard.main_loop = asyncio.get_running_loop()
    parser = argparse.ArgumentParser(description="Aurelia Vale Companion")
    parser.add_argument("--discord", action="store_true", dest="discord", help="Run Discord always-listening bot")
    parser.add_argument("--no-discord", action="store_false", dest="discord", help="Do not run Discord always-listening bot")
    parser.set_defaults(discord=True)
    parser.add_argument("--twitch", action="store_true", default=False, help="Run Twitch chat interaction")
    parser.add_argument("--youtube", action="store_true", default=False, help="Run YouTube chat interaction")
    parser.add_argument("--web", action="store_true", default=True, help="Run web dashboard (default: True)")
    parser.add_argument("--no-web", action="store_false", dest="web", help="Do not run web dashboard")
    args = parser.parse_args()

    logger.info("Starting Aurelia Vale...")

    # Log hardware info
    try:
        profiler = HardwareProfiler()
        hw = profiler.detect()
        logger.info(f"Hardware Detected: CPU Cores: {hw.cpu_count}, RAM: {hw.total_ram_gb}GB, GPU: {hw.gpu_name} ({hw.vram_gb}GB VRAM)")
    except Exception as e:
        logger.warning(f"Could not detect hardware: {e}")

    # Load persona and initialize clients
    try:
        persona_prompt = load_persona_prompt()

        chroma_client = ChromaClient(
            model_id=settings.chroma_model_id,
            persona_prompt=persona_prompt,
            max_new_tokens=settings.max_new_tokens
        )
        chroma_client.load()

        whisper_client = WhisperClient()
        whisper_client.load()

        memory_store = ChromaMemoryStore(db_path=settings.data_dir / "chroma_db")

        # Initialize streamers if requested
        twitch_adapter = None
        if args.twitch:
            twitch_adapter = TwitchChatAdapter(
                username=settings.twitch_username,
                token=settings.twitch_token,
                channel=settings.twitch_channel
            )

        youtube_adapter = None
        if args.youtube:
            youtube_adapter = YouTubeChatAdapter(
                api_key=settings.youtube_api_key,
                token=settings.youtube_token,
                live_chat_id=settings.youtube_live_chat_id
            )

        local_audio_player = None
        if settings.enable_local_audio:
            local_audio_player = LocalAudioPlayer(sample_rate=settings.sample_rate)

        # Initialize Orchestrator
        orchestrator = AureliaOrchestrator(
            chroma_client=chroma_client,
            memory_store=memory_store,
            whisper_client=whisper_client,
            twitch_adapter=twitch_adapter,
            youtube_adapter=youtube_adapter,
            local_audio_player=local_audio_player
        )
        await orchestrator.start()

        tasks = []
        if args.discord:
            tasks.append(run_discord(orchestrator))

        if args.web:
            tasks.append(web_dashboard.run_dashboard())

        if tasks:
            await asyncio.gather(*tasks)
        else:
            logger.info("No UI selected. Exiting.")
    except Exception as e:
        logger.critical(f"Critical failure during startup: {e}")
        # In a real scenario, we might want to try to notify someone,
        # but here we just log and exit.

if __name__ == "__main__":
    asyncio.run(main())
