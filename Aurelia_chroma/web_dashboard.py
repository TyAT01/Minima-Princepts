from __future__ import annotations
import logging
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from config import settings

logger = logging.getLogger(__name__)

app = FastAPI(title="Aurelia Chroma Dashboard")

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
        .logs { background: #000; padding: 10px; height: 300px; overflow-y: scroll; font-family: monospace; }
        .persona { border-left: 4px solid #bb86fc; padding-left: 10px; margin-top: 20px; }
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
            <p>Aurelia Vale - The Hedge-Knight Squire</p>
        </div>
        <h3>System Logs</h3>
        <div class="logs" id="logs">
            [System] Dashboard initialized.<br>
            [Info] Bot is running in Discord always-listening mode.
        </div>
    </div>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    # In a real app, we would use a template file, but here we use a string for simplicity
    from jinja2 import Template
    template = Template(templates_html)
    return template.render(model_id=settings.chroma_model_id, data_dir=str(settings.data_dir))

def run_dashboard(host: str = "0.0.0.0", port: int = 8000):
    import uvicorn
    uvicorn.run(app, host=host, port=port)
