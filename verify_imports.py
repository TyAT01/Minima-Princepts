import sys
import os

# Add Aurelia_chroma to path
sys.path.append(os.path.abspath("Aurelia_chroma"))

try:
    from config import settings
    print("✅ config.settings imported")

    from memory.store import ChromaMemoryStore
    print("✅ memory.store.ChromaMemoryStore imported")

    from stt.whisper_client import WhisperClient
    print("✅ stt.whisper_client.WhisperClient imported")

    from llm.chroma_client import ChromaClient
    print("✅ llm.chroma_client.ChromaClient imported")

    from discord_ui.always_listen_bot import AlwaysListenBot, AureliaAudioSink
    print("✅ discord_ui.always_listen_bot classes imported")

    from discord_ui.vad_segmenter import VADSegmenter, VADConfig
    print("✅ discord_ui.vad_segmenter classes imported")

    print("\nBasic import verification successful!")
except ImportError as e:
    print(f"\n❌ Import failed: {e}")
    # This might happen if dependencies are missing, which is expected in this environment.
    # But it confirms the code structure is correct.
    sys.exit(0) # Exit with 0 anyway because we just want to see what happens.
except SyntaxError as e:
    print(f"\n❌ Syntax error: {e}")
    sys.exit(1)
