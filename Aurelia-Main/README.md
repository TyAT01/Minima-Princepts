# Aurelia Vale - AI Streamer

Aurelia Vale is an anime-focused AI Streamer project. She listens, remembers your conversations, and aspires to become human. This project combines OpenAI-compatible LLMs (like Llama 3.1 8B), GPT-SoVITS voice synthesis, and Faster-Whisper ASR into a fully configurable conversational pipeline optimized for local hardware.

**Tested with Python 3.10+, Windows 10/11 (RTX 3070), and Linux Ubuntu**

## ✨ Features

- 💬 **Llama 3.1 8B dialogue** via local OpenAI-compatible API (Ollama/LM Studio)
- 🧠 **Conversation memory** to keep context during interactions
- 🔊 **Voice generation** via GPT-SoVITS API (Aurelia's custom voice)
- 🎧 **Speech recognition** using Faster-Whisper (GPU Optimized)
- 📁 Clean YAML-based config for personality configuration

## ⚙️ Configuration

All prompts and parameters are stored in `character_config.yaml`.

```yaml
OPENAI_API_KEY: sk-local
OPENAI_BASE_URL: "http://localhost:11434/v1" # Point to your local Ollama/LM Studio
model: "llama 3.1 8b instruct q4 k m"
presets:
  default:
    system_prompt: |
      # Aurelia Vale's detailed personality goes here...
```

You can define personalities by modifying the config file.

## 🛠️ Setup

### Windows (Recommended)
Run the automated setup script to create a virtual environment and install all dependencies:
1. Double-click `setup_venv.bat`
2. Wait for the installation to finish.

### Manual / Linux
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows use: .venv\Scripts\activate
pip install -r requirements.txt
pip install -r extra-req.txt
```

**GPU Support (RTX 3070 optimized):**
Make sure you have:
* CUDA & cuDNN installed correctly (for Faster-Whisper GPU support)
* `ffmpeg` installed (for audio processing)

## 🧪 Usage

### 1. Launch the GPT-SoVITS API
### 2. Start your local LLM server (e.g., Ollama with Llama 3.1 8B)
### 3. Run the main script from the root folder:

```bash
python main_chat.py
```

The flow:
1. Aurelia listens to your voice via microphone (push to talk)
2. Transcribes it with Faster-Whisper (GPU accelerated)
3. Passes it to the local LLM (with history and personality)
4. Generates a response as Aurelia Vale
5. Synthesizes Aurelia's voice using GPT-SoVITS
6. Plays the output back to you

## 🧑‍🎤 Credits

* Voice synthesis powered by [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)
* ASR via [Faster-Whisper](https://github.com/SYSTRAN/faster-whisper)
* Language model via [Meta Llama 3.1](https://llama.meta.com/)

## 📜 License

MIT
