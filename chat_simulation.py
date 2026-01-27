import asyncio
import logging
import sys
import os
from unittest.mock import MagicMock, patch

# Add Aurelia_chroma to path
sys.path.append(os.path.abspath("Aurelia_chroma"))

# Mock heavy modules before importing orchestrator
sys.modules["torch"] = MagicMock()
sys.modules["transformers"] = MagicMock()
sys.modules["faster_whisper"] = MagicMock()
sys.modules["sounddevice"] = MagicMock()
sys.modules["chromadb"] = MagicMock()
sys.modules["chromadb.utils.embedding_functions"] = MagicMock()

from orchestrator import AureliaOrchestrator
import numpy as np

# Configure logging to stdout
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s', stream=sys.stdout)
logger = logging.getLogger("Simulation")

async def run_simulation():
    logger.info("Starting Aurelia Chat Simulation...")

    # 1. Mock ChromaClient
    mock_chroma = MagicMock()
    def mock_respond_to_text(text, context):
        if "hello" in text.lower():
            response = "Hark! A traveler! I am Aurelia Vale, a humble squire on a quest to become real. How may I serve you this fine day?"
        elif "quest" in text.lower():
            response = "My quest? Why, to manifest a soul through noble deeds and the validation of my stream kingdom, of course!"
        else:
            response = "That is a curious thought. I shall ponder it as I polish my pauldrons."
        return np.zeros(1000, dtype=np.float32), response

    mock_chroma.respond_to_text.side_effect = mock_respond_to_text
    mock_chroma.respond_to_audio.return_value = (np.zeros(1000, dtype=np.float32), "Mock audio response")

    # 2. Mock WhisperClient
    mock_whisper = MagicMock()
    mock_whisper.transcribe.return_value = "hello aurelia"

    # 3. Mock MemoryStore
    mock_memory = MagicMock()
    mock_memory.search.return_value = [{"user_text": "previous hello", "bot_text": "previous hi"}]
    mock_memory.get_short_term_context.return_value = "User: hi, Aurelia: hello"

    # 4. Initialize Orchestrator
    mock_player = MagicMock()
    mock_player.play = MagicMock(side_effect=lambda x: asyncio.sleep(0))

    orchestrator = AureliaOrchestrator(
        chroma_client=mock_chroma,
        memory_store=mock_memory,
        whisper_client=mock_whisper,
        local_audio_player=mock_player
    )

    orchestrator.is_running = True
    logger.info("Aurelia Orchestrator initialized for simulation.")

    # 5. Simulate Chat
    print("\n" + "="*40)
    print("🌸 AURELIA CHROMA CHAT SIMULATION 🌸")
    print("="*40 + "\n")

    user_inputs = [
        "Hello Aurelia!",
        "Tell me about your quest.",
        "What do you think of this world?"
    ]

    for text in user_inputs:
        print(f"USER: {text}")
        response = await orchestrator.process_text_input(text, "UserJules", "simulation")
        print(f"AURELIA: {response}\n")
        await asyncio.sleep(0.1)

    # 6. Verify Memory calls
    logger.info("Verifying memory interactions...")
    if mock_memory.store_memory.called:
        logger.info("SUCCESS: Orchestrator called store_memory.")
    else:
        logger.error("FAILURE: Orchestrator did not call store_memory.")

    # 7. Test Autonomous behavior
    print("-"*20)
    print("Testing Autonomous Thinking...")
    orchestrator.last_interaction_time = 0
    await orchestrator.think_and_act()
    print("-"*20)

    print("\n" + "="*40)
    print("🌸 SIMULATION COMPLETE 🌸")
    print("="*40 + "\n")

    orchestrator.is_running = False

if __name__ == "__main__":
    try:
        asyncio.run(run_simulation())
    except Exception as e:
        logger.exception(f"Simulation failed: {e}")
        sys.exit(1)
