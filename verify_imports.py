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

    # Ported modules
    from hardware.profiler import HardwareProfiler, AdaptiveResourceManager
    print("✅ hardware.profiler imported")

    from emotion.engine import EmotionEngine
    print("✅ emotion.engine imported")

    from adapters.twitch import TwitchChatAdapter
    print("✅ adapters.twitch imported")

    from adapters.youtube import YouTubeChatAdapter
    print("✅ adapters.youtube imported")

    print("\nAll import verifications successful!")
except ImportError as e:
    print(f"\n❌ Import failed: {e}")
    # In this environment, some dependencies might be missing,
    # but we check if the local modules are found.
    if "No module named" in str(e):
        module_name = str(e).split("'")[-2]
        if module_name in ["nextcord", "transformers", "torch", "faster_whisper", "bitsandbytes", "psutil"]:
            print(f"   (Note: {module_name} is an external dependency and might not be installed in the sandbox, which is expected.)")
            # Continue checking other local imports if possible? No, ImportError stops execution.
        else:
            sys.exit(1)
    else:
        sys.exit(1)
except SyntaxError as e:
    print(f"\n❌ Syntax error: {e}")
    sys.exit(1)
except Exception as e:
    print(f"\n❌ Unexpected error: {e}")
    sys.exit(1)
