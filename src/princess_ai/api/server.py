"""FastAPI server for runtime control and inspection."""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Iterable

from fastapi import FastAPI

from princess_ai.memory.store import MemoryRecord, MemoryStore
from princess_ai.runtime.session import SessionManager


def create_app(
    session_manager: SessionManager, memory_store: MemoryStore
) -> FastAPI:
    app = FastAPI(title="Aurelia Vale AI API")
    logger = logging.getLogger(__name__)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/session")
    def get_session() -> dict:
        try:
            return asdict(session_manager.snapshot())
        except Exception as exc:  # noqa: BLE001 - keep API resilient
            logger.exception("Failed to fetch session snapshot: %s", exc)
            return {"error": "Unable to fetch session snapshot"}

    @app.put("/session/mode")
    def set_mode(mode: str) -> dict:
        try:
            session_manager.set_mode(mode)
            return asdict(session_manager.snapshot())
        except Exception as exc:  # noqa: BLE001 - keep API resilient
            logger.exception("Failed to set mode: %s", exc)
            return {"error": "Unable to set mode"}

    @app.put("/session/modules/{name}")
    def set_module(name: str, enabled: bool) -> dict:
        try:
            session_manager.set_module(name, enabled)
            return asdict(session_manager.snapshot())
        except Exception as exc:  # noqa: BLE001 - keep API resilient
            logger.exception("Failed to set module: %s", exc)
            return {"error": "Unable to set module"}

    @app.get("/memories")
    def list_memories(limit: int = 50) -> dict:
        try:
            memories = _serialize_memories(memory_store.list_memories(limit=limit))
            return {"memories": memories}
        except Exception as exc:  # noqa: BLE001 - keep API resilient
            logger.exception("Failed to list memories: %s", exc)
            return {"memories": []}

    return app


def _serialize_memories(records: Iterable[MemoryRecord]) -> list[dict]:
    try:
        return [
            {
                "text": record.text,
                "importance": record.importance,
                "timestamp": record.timestamp,
            }
            for record in records
        ]
    except Exception:
        return []
