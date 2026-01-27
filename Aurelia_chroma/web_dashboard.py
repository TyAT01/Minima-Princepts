from __future__ import annotations
import logging
import asyncio
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pathlib import Path
from config import settings

logger = logging.getLogger(__name__)

app = FastAPI(title="Aurelia Chroma Dashboard")

subscribers: set[asyncio.Queue] = set()
main_loop: asyncio.AbstractEventLoop | None = None

class QueueHandler(logging.Handler):
    def emit(self, record):
        try:
            if main_loop and main_loop.is_running():
                msg = self.format(record)
                main_loop.call_soon_threadsafe(self.broadcast, msg)
        except Exception:
            self.handleError(record)

    def broadcast(self, msg: str):
        for q in subscribers:
            q.put_nowait(msg)

# Configure logging to use our QueueHandler
queue_handler = QueueHandler()
queue_handler.setFormatter(logging.Formatter('[%(levelname)s] %(message)s'))
logging.getLogger().addHandler(queue_handler)

# Simplified dashboard
templates_html = """
<!DOCTYPE html>
<html>
<head>
    <title>Aurelia Chroma Dashboard</title>
    <style>
        body { font-family: sans-serif; background: #1a1a1a; color: #e0e0e0; margin: 20px; }
        .container { max-width: 800px; margin: auto; background: #2a2a2a; padding: 20px; border-radius: 8px; }
        h1 { color: #bb86fc; }
        .status { padding: 10px; border-radius: 4px; background: #333; margin-bottom: 20px; }
        .logs { background: #000; padding: 10px; height: 300px; overflow-y: scroll; font-family: monospace; font-size: 0.9em; line-height: 1.4; }
        .persona { border-left: 4px solid #bb86fc; padding-left: 10px; margin-top: 20px; }
        .log-entry { margin-bottom: 2px; border-bottom: 1px solid #222; white-space: pre-wrap; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Aurelia Chroma Companion</h1>
        <div class="status">
            <strong>Status:</strong> Online<br>
            <strong>Model:</strong> {{ model_id }}<br>
            <strong>Data Directory:</strong> {{ data_dir }}
        </div>
        <div class="persona">
            <h3>Persona</h3>
            <p>{{ persona_name }} - {{ persona_role }}</p>
            <p><i>{{ persona_archetype }}</i></p>
        </div>
        <h3>System Logs</h3>
        <div class="logs" id="logs">
            <div class="log-entry">[System] Dashboard initialized.</div>
        </div>
    </div>
    <script>
        const logContainer = document.getElementById('logs');
        const eventSource = new EventSource('/logs-stream');

        eventSource.onmessage = function(event) {
            const newLog = document.createElement('div');
            newLog.className = 'log-entry';
            newLog.textContent = event.data;
            logContainer.appendChild(newLog);
            logContainer.scrollTop = logContainer.scrollHeight;
        };

        eventSource.onerror = function(err) {
            console.error("EventSource failed:", err);
        };
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    from jinja2 import Template

    # Load persona info
    try:
        with open(settings.persona_yaml, "r", encoding="utf-8") as f:
            persona_data = yaml.safe_load(f)
        char = persona_data.get("character", {})
        persona_name = char.get("name", "Aurelia")
        persona_role = char.get("role", "AI Companion")
        persona_archetype = char.get("archetype", "")
    except Exception:
        persona_name = "Aurelia"
        persona_role = "AI Companion"
        persona_archetype = ""

    template = Template(templates_html)
    return template.render(
        model_id=settings.chroma_model_id,
        data_dir=str(settings.data_dir),
        persona_name=persona_name,
        persona_role=persona_role,
        persona_archetype=persona_archetype
    )

@app.get("/logs-stream")
async def logs_stream(request: Request):
    q = asyncio.Queue()
    subscribers.add(q)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    # Use wait_for to periodically check for disconnection even if no logs
                    log_msg = await asyncio.wait_for(q.get(), timeout=1.0)
                    # Handle multi-line logs for SSE
                    lines = log_msg.splitlines()
                    sse_msg = "".join([f"data: {line}\n" for line in lines])
                    yield f"{sse_msg}\n"
                except asyncio.TimeoutError:
                    continue
        finally:
            subscribers.remove(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

async def run_dashboard(host: str = "0.0.0.0", port: int = 8000):
    import uvicorn
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()
