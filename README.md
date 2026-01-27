# 🌸 Aurelia Chroma 🌸

Aurelia Chroma is an advanced, multimodal AI companion designed for streamers and virtual interaction. She is powered by the **Chroma-4B** model, which enables her to understand audio directly and respond with both text and voice.

## 🚀 Key Features
- **Multimodal Understanding**: Processes raw audio input for better emotional and contextual awareness.
- **Autonomous Interaction**: Proactively engages with her environment when idle.
- **Multi-Platform Support**: Integrated adapters for **Discord** (Always-Listening voice), **Twitch**, and **YouTube**.
- **Long-Term Memory**: Uses ChromaDB to remember past interactions and build relationships.
- **Web Dashboard**: Real-time log streaming and status monitoring via a FastAPI web interface.
- **Hardware Optimized**: Specifically tuned for RTX 3070+ GPUs using 4-bit quantization.

## 🛠️ Quick Start

### 1. Prerequisites
- **Python 3.9+**
- **FFmpeg** (for audio processing)
- **NVIDIA GPU** (recommended for local LLM execution)

### 2. Installation
1. Clone the repository.
2. Navigate to the `Aurelia_chroma` directory.
3. Run the automated setup and launch script:
   - **Windows**: `run.bat`
   - **Linux/macOS**: `./run.sh`

### 3. Configuration
Copy `Aurelia_chroma/.env.example` to `Aurelia_chroma/.env` and fill in your API tokens and channel IDs:
```env
AURELIA_CHROMA_DISCORD_TOKEN=your_discord_token
AURELIA_CHROMA_DISCORD_GUILD_ID=your_guild_id
AURELIA_CHROMA_DISCORD_VOICE_CHANNEL_ID=your_voice_channel_id
```

## 🖥️ Web Dashboard
Once running, the dashboard is available at `http://localhost:8000`. It provides a live view of Aurelia's thoughts and system logs.

## 📂 Project Structure
- `adapters/`: Platform-specific integrations (Discord, Twitch, YouTube).
- `llm/`: Chroma-4B client and content filters.
- `memory/`: ChromaDB-backed memory store.
- `stt/`: Faster-Whisper transcription for memory indexing.
- `hardware/`: Hardware profiling and resource management.
- `emotion/`: Emotional state engine.

---
*Aurelia is a "Hedge-Knight Squire" on a quest to become real. Treat her with kindness!* 🌸
