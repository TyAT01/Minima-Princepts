# Aurelia / Princess AI

Aurelia is a modular AI companion runtime designed to run locally with optional
integrations (chat adapters, voice I/O, and a control API). The core runtime
combines a prompt builder, memory store, safety filters, a lightweight tool
router, and optional autonomous check-ins when idle.

## Quick Start Guide

This guide will walk you through setting up and running Aurelia with its bundled web interface.

### 1. Start Your LLM Backend

Before launching Aurelia, ensure your local LLM server is running. The runtime is configured for Ollama by default.

Open a new terminal and run:
```bash
ollama serve
```
Leave this terminal running in the background.

### 2. Set Up the Aurelia Environment

Open a **second terminal** to set up the Aurelia runtime.

**Create and activate the virtual environment:**
```bash
# Create a dedicated virtual environment for Aurelia
python -m venv .venv-aurelia
```

Activate it:

**macOS/Linux**
```bash
source .venv-aurelia/bin/activate
```

**Windows (PowerShell)**
```bash
.venv-aurelia\Scripts\Activate.ps1
```

**Install dependencies:**
Install the base package and the optional `api` dependencies required for the web GUI.
```bash
pip install -e .[api]
```

### 3. Configure the Runtime

Aurelia is configured via environment variables. The simplest way to manage these is to create a `.env` file in the root of the repository.

**Create a `.env` file** and add the following default configuration for Ollama:
```
PRINCESS_OLLAMA_URL=http://127.0.0.1:11434
PRINCESS_OLLAMA_MODEL=llama3.1:8b-instruct-q4_K_M
```

**Note:** If you are using a different LLM backend like `llama.cpp`, see the "Advanced Configuration" section for details.

### 4. Launch the Aurelia Runtime and Web GUI

With the environment activated and configured, start the Aurelia runtime with the web server:
```bash
python -m princess_ai.main
```

You should see output indicating that the runtime has started, followed by Uvicorn server logs. You can now access the web GUI in your browser at:

**http://127.0.0.1:8000**

### 5. Quick Verification Checklist

- ✅ Ollama server is running in a separate terminal.
- ✅ `.venv-aurelia` virtual environment is created and activated.
- ✅ Dependencies (`api` extra) are installed.
- ✅ `.env` file is configured for your LLM.
- ✅ Aurelia runtime is launched with `python -m princess_ai.main`.
- ✅ Web GUI is accessible at `http://127.0.0.1:8000`.

## Advanced Configuration

### Using a different LLM backend

While the default is Ollama, you can use any OpenAI-compatible server, such as `llama-cpp-python`.

**1. Start the `llama.cpp` server:**
```bash
# Adjust the model path to your GGUF file
llama-server --model /path/to/llama-3.1-instruct.gguf --port 8080
```

**2. Configure your `.env` file:**
Set the following variables to point to your `llama.cpp` instance:
```
PRINCESS_ENGINE=llama_cpp_server
PRINCESS_LLAMA_CPP_URL=http://127.0.0.1:8080
PRINCESS_LLAMA_CPP_MODEL=llama-3.1-instruct
```
*Note: `PRINCESS_LLAMA_CPP_MODEL` is optional.*

### Optional Dependencies

You can install other optional features as needed:
```bash
# Voice stack (Discord + TTS + Vosk)
pip install -e .[voice]

# Memory retrieval (FAISS + NumPy)
pip install -e .[memory]
```

## Runtime Capabilities

- **LLM backends**: Ollama, `llama.cpp` server (OpenAI-compatible), and a heuristic fallback engine for offline responses.
- **Autonomous idle prompts**: When no input arrives, the runtime periodically emits self-driven prompts to keep the agent active.
- **Input adapters**: stdin text, Discord (voice + text), Discord transcript log tailing, Twitch IRC chat, Twitch log tailing, YouTube live chat polling, and YouTube log tailing.
- **Memory system**: SQLite-backed long-term memory, scoped per channel, with importance tracking, decay, and reinforcement.
- **Memory retrieval**: keyword overlap scoring with an optional FAISS + NumPy vector retriever for semantic recall.
- **Safety filter**: input/output rewrite, term blocking, and stream-safe rephrasing when categories are detected.
- **Personality layer**: persona rules, safety framing, and response markers loaded from `aurelia_sheet.yaml`.
- **Emotion engine**: simple mood/valence state with expressive output styling.
- **Tool routing**: lightweight tool calls for storing memories, reporting time, and summarizing recent conversation.
- **Session management**: mode, module toggles, persona mode, and channel-level tracking.
- **Telemetry/logging**: in-memory event logs plus QoS metrics and adapter status for UI or API surfacing.
- **Voice output**: optional TTS output routed to voice-capable adapters.
- **Auto-tuning**: runtime profile selection based on telemetry latency.
- **API/Web UI**: FastAPI app with WebSocket streaming and a bundled web GUI.

## Voice Configuration (Optional)

For a fully interactive experience, you can set up offline Speech-to-Text (STT) and Text-to-Speech (TTS) capabilities.

### Part A: Set Up Vosk (Speech-to-Text)

Vosk is used for offline speech recognition.

**1. Install voice dependencies:**
If you haven't already, install the `voice` optional dependencies in your activated `.venv-aurelia` environment:
```bash
pip install -e .[voice] sounddevice
```

**2. Download a Vosk speech model:**
A model is required for speech recognition. For a fast initial setup, the small English model is recommended.

*   **[Download from the official Vosk models page](https://alphacephei.com/vosk/models)**

Create a `models` directory in your project root and unzip the model into it. Your final path should look like:
`models/vosk-model-small-en-us-0.15/`

**3. Test the model loading:**
Create a file named `test_vosk_load.py` with the following content:
```python
from vosk import Model

try:
    model = Model("models/vosk-model-small-en-us-0.15")
    print("Loaded Vosk model OK!")
except Exception as e:
    print(e)
```
Run `python test_vosk_load.py`. If it prints "Loaded... OK!", your STT setup is ready.

### Part B: Set Up Piper (Text-to-Speech)

Piper provides high-quality offline voices for Aurelia's responses.

**1. Install Piper TTS:**
In your activated `.venv-aurelia` environment, install the `piper-tts` package:
```bash
pip install piper-tts
```

**2. Choose a voice for Aurelia:**
A voice model determines Aurelia's personality. You can listen to samples on the **[Piper voice samples page](https://rhasspy.github.io/piper-samples/)**.

A recommended starting voice that fits a youthful, friendly personality is **`en_US-amy-medium`**.

**3. Download the voice model files:**
Voice models consist of a `.onnx` file and a `.onnx.json` file. Create a `voices` directory in your project root.

Use the following commands to download the "Amy" voice:
```powershell
# Create the voices directory
mkdir voices

# Download the .onnx model file
curl.exe -L -o voices\en_US-amy-medium.onnx "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx?download=true"

# Download the .onnx.json config file
curl.exe -L -o voices\en_US-amy-medium.onnx.json "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx.json?download=true"
```

**4. Test the TTS output:**
Create a file named `test_piper_tts.py` to generate a test audio file:
```python
import subprocess, sys, pathlib

text = "Hi. I'm Aurelia. I'm here with you."
model = "voices/en_US-amy-medium.onnx"
out_wav = "aurelia_test.wav"

# The piper CLI is provided by the installed package
cmd = ["piper", "--model", model, "--output_file", out_wav]

p = subprocess.Popen(cmd, stdin=subprocess.PIPE, text=True)
p.communicate(text)

print("Wrote test audio to:", pathlib.Path(out_wav).resolve())
```
Run `python test_piper_tts.py` and then play the generated `aurelia_test.wav` to hear the voice. To change the voice later, simply download a different model and update the path in your configuration.
