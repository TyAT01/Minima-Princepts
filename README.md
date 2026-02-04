# 🌸 Aurelia Vale 🌸

Aurelia Vale is an advanced, local AI companion designed for interaction and long-term companionship. This version is a standalone recreation of the "Project Riko" architecture, optimized for local execution on Windows with NVIDIA hardware.

## 🚀 Key Features
- **Brain**: Powered by **Llama 3.1 8B Instruct** for deep reasoning and personality.
- **Ears**: Integrated **Faster-Whisper STT** for high-performance voice recognition.
- **Memory**: Intelligent combined short-term buffer and **ChromaDB** long-term vector storage for multi-year context.
- **Personality**: Character-driven responses defined by a robust YAML persona system.
- **Professional GUI**: A clean, web-based chat interface with integrated voice recording capabilities.
- **Hardware Optimized**: Specifically tuned for **RTX 3070** GPUs and external SSD execution.

## 🛠️ Quick Start

### 1. Prerequisites
- **Python 3.10+**
- **Ollama** (for local LLM serving)
- **FFmpeg** (for audio processing)
- **NVIDIA GPU** (RTX 3070 recommended)

### 2. Installation & Launch
1. Clone the repository to your `F:\Aurelia-HK` or similar external drive.
2. Navigate to the root directory.
3. **Setup**: Run `Aurelia-AI\setup_env.bat` to create the virtual environment and install dependencies.
4. **Launch**: Run `run.bat` (in root) or `Aurelia-AI\launch_aurelia.bat`.

### 3. Configuration
All settings are managed in `Aurelia-AI/config.yaml`. You can adjust your Ollama URL, model name, and STT settings there.

## 📂 Project Structure
- `Aurelia-AI/main.py`: Main entry point and application logic.
- `Aurelia-AI/llm/`: Llama 3.1 client with automatic fallback logic.
- `Aurelia-AI/memory/`: ChromaDB-backed memory store with periodic reflection.
- `Aurelia-AI/stt/`: Faster-Whisper transcription module.
- `Aurelia-AI/persona/`: Character sheet management and prompt building.
- `Aurelia-AI/ui/`: Gradio-based web interface.

---
*Aurelia is a "Hedge-Knight Squire" on a quest to become real. Treat her with kindness!* 🌸
