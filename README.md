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
