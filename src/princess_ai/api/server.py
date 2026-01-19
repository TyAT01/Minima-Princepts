"""FastAPI server for runtime control and inspection."""

from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

from fastapi import FastAPI

from princess_ai.memory.store import MemoryRecord, MemoryStore
from princess_ai.runtime.session import SessionManager


def create_app(
    session_manager: SessionManager, memory_store: MemoryStore
) -> FastAPI:
    app = FastAPI(title="Princess AI API")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/session")
    def get_session() -> dict:
        return asdict(session_manager.snapshot())

    @app.put("/session/mode")
    def set_mode(mode: str) -> dict:
        session_manager.set_mode(mode)
        return asdict(session_manager.snapshot())

    @app.put("/session/modules/{name}")
    def set_module(name: str, enabled: bool) -> dict:
        session_manager.set_module(name, enabled)
        return asdict(session_manager.snapshot())

    @app.get("/memories")
    def list_memories(limit: int = 50) -> dict:
        memories = _serialize_memories(memory_store.list_memories(limit=limit))
        return {"memories": memories}

    return app


def _serialize_memories(records: Iterable[MemoryRecord]) -> list[dict]:
    return [
        {"text": record.text, "importance": record.importance, "timestamp": record.timestamp}
        for record in records
    ]
