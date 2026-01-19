# Aurelia / Princess AI

Aurelia is a modular AI companion runtime designed to run locally with optional
integrations (chat adapters, voice I/O, and a control API). The core runtime
combines a prompt builder, memory store, safety filters, a lightweight tool
router, and optional autonomous check-ins when idle.

## Quick start

### 1. Create and activate a virtual environment

Create the venv (from the repo root):
```bash
python -m venv .venv
```

Activate it:

**macOS/Linux**
```bash
source .venv/bin/activate
```

**Windows (PowerShell)**
```bash
.venv\Scripts\Activate.ps1
```

### 2. Install Python dependencies

Install the base package (required):
```bash
pip install -e .
```

Optional extras (install only what you need):
```bash
# API server (FastAPI + Uvicorn)
pip install -e .[api]

# Voice stack (Discord + TTS + Vosk)
pip install -e .[voice]

# Memory retrieval (FAISS + NumPy)
pip install -e .[memory]
```

### 3. Start your LLM backend

The runtime connects through OpenAI-compatible llama.cpp server endpoints at `/v1/chat/completions`.

Example `llama.cpp` server command (adjust model path to your GGUF file):
```bash
llama-server --model /path/to/llama-3.1-instruct.gguf --port 8080
```

### 4. Configure the runtime

By default, the runtime uses Ollama. To use `llama.cpp` server, set these environment variables:

```bash
export PRINCESS_ENGINE=llama_cpp_server
export PRINCESS_LLAMA_CPP_URL=http://127.0.0.1:8080
export PRINCESS_LLAMA_CPP_MODEL=llama-3.1-instruct
```

**Notes:**
- `PRINCESS_LLAMA_CPP_MODEL` is optional; it will be sent as the `model` field when provided.
- If you want to use Ollama instead, omit `PRINCESS_ENGINE` and use:

```bash
export PRINCESS_OLLAMA_URL=http://127.0.0.1:11434
export PRINCESS_OLLAMA_MODEL=llama3.1:8b-instruct-q4_K_M
```

To make these settings persistent, you can save them in a `.env` file in the root of the repository.

### 5. Start the Aurelia runtime

Run the main runtime loop:
```bash
python -m princess_ai.main
```

You should see a line like:
```
Selected profile: <profile-name>
```

### 6. Quick verification checklist

- ✅ Virtual environment created and activated.
- ✅ Dependencies installed (base + any extras).
- ✅ `llama.cpp` server running and reachable at `PRINCESS_LLAMA_CPP_URL`.
- ✅ `PRINCESS_ENGINE` set to `llama_cpp_server`.
- ✅ Runtime starts with `python -m princess_ai.main`.

## Runtime capabilities

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
- **API/Web UI**: FastAPI app with WebSocket streaming and a bundled web GUI (install the `api` extra and run via Uvicorn).

## API server example

This repo includes a FastAPI control surface in `princess_ai.api.server`, but it does not ship a standalone GUI. If your AI GUI expects a backend, wire it to the API endpoints exposed by `create_app(...)` and run it with Uvicorn, for example:

```bash
uvicorn princess_ai.api.server:create_app --factory --host 0.0.0.0 --port 8000
```

> Note: The API factory expects a session manager, memory store, and control hub to be provided by the caller if you want the API to reflect a running runtime session. See `princess_ai.api.server:create_app` for details.
