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
from llm.persona import load_persona_prompt
import web_dashboard

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("aurelia_vale")

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
            tasks.append(web_dashboard.run_dashboard(orch=orchestrator, port=settings.web_port))

        if tasks:
            await asyncio.gather(*tasks)
        else:
            logger.info("No UI selected. Exiting.")
    except Exception as e:
        import traceback
        logger.critical(f"Critical failure during startup: {e}")
        logger.critical(traceback.format_exc())
        # In a real scenario, we might want to try to notify someone,
        # but here we just log and exit.

if __name__ == "__main__":
    asyncio.run(main())
