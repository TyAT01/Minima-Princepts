import sys
import os

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from config import settings
    print("✅ config.settings imported")

    from memory.store import ChromaMemoryStore
    print("✅ memory.store.ChromaMemoryStore imported")

    from stt.whisper_client import WhisperClient
    print("✅ stt.whisper_client.WhisperClient imported")

    from llm.nvidia_personaplex_client import NVIDIAPersonaPlexClient
    print("✅ llm.nvidia_personaplex_client.NVIDIAPersonaPlexClient imported")

    from llm.personaplex import PersonaPlex
    print("✅ llm.personaplex.PersonaPlex imported")

    from discord_ui.always_listen_bot import AlwaysListenBot, AureliaAudioSink
    print("✅ discord_ui.always_listen_bot classes imported")

    from discord_ui.vad_segmenter import VADSegmenter, VADConfig
    print("✅ discord_ui.vad_segmenter classes imported")

    from hardware.profiler import HardwareProfiler
    print("✅ hardware.profiler imported")

    from adapters.twitch import TwitchChatAdapter
    print("✅ adapters.twitch imported")

    from adapters.youtube import YouTubeChatAdapter
    print("✅ adapters.youtube imported")

    print("\nAll Personaplex Edition import verifications successful!")
except ImportError as e:
    print(f"\n❌ Import failed: {e}")
    if "No module named" in str(e):
        module_name = str(e).split("'")[-2]
        external_deps = [
            "nextcord", "transformers", "torch", "faster_whisper", "bitsandbytes",
            "psutil", "pydantic_settings", "pydantic", "yaml", "numpy", "soundfile",
            "librosa", "webrtcvad", "chromadb", "sentence_transformers", "fastapi",
            "uvicorn", "jinja2", "sounddevice", "moshi", "pyopus"
        ]
        if module_name in external_deps:
            print(f"   (Note: {module_name} is an external dependency and might not be installed in this environment.)")
        else:
            print(f"   (Potential local module missing or misnamed: {module_name})")
            sys.exit(1)
    else:
        sys.exit(1)
except Exception as e:
    print(f"\n❌ Unexpected error: {e}")
    sys.exit(1)
