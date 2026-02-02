import asyncio
import logging
import sys
import os
from pathlib import Path
import yaml

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import settings
from llm.chroma_client import ChromaClient
from memory.store import ChromaMemoryStore
from learning.simulation import SimulationManager

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("StandaloneSim")

def load_persona_prompt() -> str:
    try:
        with open(settings.persona_yaml, "r", encoding="utf-8") as f:
            persona_data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        persona_data = {}

    character = persona_data.get("character", {})
    name = character.get("name", "Aurelia Vale")
    core_identity = (character.get("core_identity") or {}).get("self_awareness") or "I am Aurelia Vale, an AI companion."
    system_prompt = f"You are {name}. Your core identity is: '{core_identity}'."
    return system_prompt

async def main():
    logger.info("Starting Aurelia Standalone Simulation...")

    persona_prompt = load_persona_prompt()

    # Initialize components
    # NOTE: We use a high max_new_tokens for simulations to allow deep reflection
    chroma_client = ChromaClient(
        model_id=settings.chroma_model_id,
        persona_prompt=persona_prompt,
        max_new_tokens=200
    )

    try:
        chroma_client.load()
    except Exception as e:
        logger.error(f"Failed to load model: {e}")
        return

    memory_store = ChromaMemoryStore(db_path=settings.data_dir / "chroma_db")
    sim_manager = SimulationManager()

    logger.info("="*50)
    logger.info("🌸 AURELIA VALE: STANDALONE EVOLUTION SIM 🌸")
    logger.info("="*50)

    # Run 3 simulations
    for i in range(3):
        logger.info(f"--- Simulation {i+1}/3 ---")
        await sim_manager.run_simulation(chroma_client, memory_store)
        logger.info("-" * 30)

    logger.info("="*50)
    logger.info("🌸 SIMULATIONS COMPLETE: Aurelia has evolved. 🌸")
    logger.info("="*50)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.exception(f"Simulation script failed: {e}")
