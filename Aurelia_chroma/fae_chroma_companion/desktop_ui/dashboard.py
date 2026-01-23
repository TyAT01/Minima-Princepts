from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from memory.store import MemoryStore


def build_dashboard(memory_store: MemoryStore, templates_dir: Path) -> FastAPI:
    app = FastAPI()
    templates = Jinja2Templates(directory=str(templates_dir))

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> Any:
        entries = memory_store.list_entries()[-20:]
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "entries": entries},
        )

    return app
