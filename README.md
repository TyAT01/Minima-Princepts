# Aurelia / Princess AI

Aurelia is a modular AI companion runtime designed to run locally with optional
integrations (chat adapters, voice I/O, and a control API). The core runtime
combines a prompt builder, memory store, safety filters, a lightweight tool
router, and optional autonomous check-ins when idle.

## Quick start

1. **Create and activate a virtual environment**

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```

2. **Install the base package**

   ```bash
   pip install -e .
   ```

3. **(Optional) Install extras**

   ```bash
   # API server (FastAPI + Uvicorn)
   pip install -e .[api]

   # Voice stack (Discord + Vosk + pyttsx3)
   pip install -e .[voice]

   # Memory retrieval (FAISS + NumPy)
   pip install -e .[memory]
   ```

4. **Run a local model backend**

   - **Ollama (default)**

     ```bash
     export PRINCESS_OLLAMA_URL=http://127.0.0.1:11434
     export PRINCESS_OLLAMA_MODEL=llama3.1:8b-instruct-q4_K_M
     ```

   - **llama.cpp server**

     ```bash
     export PRINCESS_ENGINE=llama_cpp_server
     export PRINCESS_LLAMA_CPP_URL=http://127.0.0.1:8080
     export PRINCESS_LLAMA_CPP_MODEL=llama-3.1-instruct
     ```

5. **Start the runtime**

   ```bash
   python -m princess_ai.main
   ```

## Runtime capabilities

- **LLM backends**: Ollama, llama.cpp server (OpenAI-compatible), and a heuristic
  fallback engine for offline responses.
- **Autonomous idle prompts**: When no input arrives, the runtime periodically
  emits self-driven prompts to keep the agent active.
- **Input adapters**: stdin text, Discord (voice + text), Discord transcript log
  tailing, Twitch IRC chat, Twitch log tailing, YouTube live chat polling, and
  YouTube log tailing.
- **Memory system**: SQLite-backed long-term memory, scoped per channel, with
  importance tracking, decay, and reinforcement.
- **Memory retrieval**: keyword overlap scoring with an optional FAISS + NumPy
  vector retriever for semantic recall.
- **Safety filter**: input/output rewrite, term blocking, and stream-safe
  rephrasing when categories are detected.
- **Personality layer**: persona rules, safety framing, and response markers
  loaded from `aurelia_sheet.yaml`.
- **Emotion engine**: simple mood/valence state with expressive output styling.
- **Tool routing**: lightweight tool calls for storing memories, reporting time,
  and summarizing recent conversation.
- **Session management**: mode, module toggles, persona mode, and channel-level
  tracking.
- **Telemetry/logging**: in-memory event logs plus QoS metrics and adapter
  status for UI or API surfacing.
- **Voice output**: optional TTS output routed to voice-capable adapters.
- **Auto-tuning**: runtime profile selection based on telemetry latency.
- **API/Web UI**: FastAPI app with WebSocket streaming and a bundled web GUI
  (install the `api` extra and run via Uvicorn).

## API server example

```bash
uvicorn princess_ai.api.server:create_app --factory --host 0.0.0.0 --port 8000
```

> Note: The API factory expects a session manager, memory store, and control hub
> to be provided by the caller if you want the API to reflect a running runtime
> session. See `princess_ai.api.server:create_app` for details.
