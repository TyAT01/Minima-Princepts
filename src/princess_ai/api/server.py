"""FastAPI server for runtime control and inspection."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import Iterable, List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from princess_ai.logging.telemetry import InMemoryLogStore, LogEntry
from princess_ai.memory.store import MemoryRecord, MemoryStore
from princess_ai.runtime.control import ControlHub
from princess_ai.runtime.session import SessionManager


def create_app(
    session_manager: SessionManager,
    memory_store: MemoryStore,
    control_hub: ControlHub | None = None,
    log_store: InMemoryLogStore | None = None,
) -> FastAPI:
    app = FastAPI(title="Aurelia Vale AI API")
    logger = logging.getLogger(__name__)
    control = control_hub or ControlHub()
    logs = log_store or InMemoryLogStore()

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

    @app.get("/logs")
    def list_logs(limit: int = 200) -> dict:
        try:
            entries = logs.snapshot()[-limit:]
            return {"logs": _serialize_logs(entries)}
        except Exception as exc:  # noqa: BLE001 - keep API resilient
            logger.exception("Failed to list logs: %s", exc)
            return {"logs": []}

    @app.post("/controls/adapters/{name}")
    def set_adapter(name: str, enabled: bool) -> dict:
        control.set_adapter(name, enabled)
        return asdict(control.snapshot())

    @app.post("/controls/model")
    def set_model(model: str) -> dict:
        control.set_model(model)
        return asdict(control.snapshot())

    @app.post("/controls/persona")
    def set_persona(persona: str) -> dict:
        control.set_persona(persona)
        return asdict(control.snapshot())

    @app.post("/controls/stream")
    def set_stream_mode(enabled: bool) -> dict:
        control.set_stream_mode(enabled)
        return asdict(control.snapshot())

    @app.post("/controls/mute")
    def set_mute(enabled: bool) -> dict:
        control.set_muted(enabled)
        session_manager.set_muted(enabled)
        return asdict(control.snapshot())

    @app.post("/controls/manual")
    def push_manual(message: str) -> dict:
        control.push_manual(message)
        return {"queued": True}

    @app.websocket("/ws/stream")
    async def websocket_stream(socket: WebSocket) -> None:
        await socket.accept()
        try:
            while True:
                payload = {"logs": _serialize_logs(logs.snapshot())}
                await socket.send_json(payload)
                await asyncio.sleep(0.5)
        except WebSocketDisconnect:
            return
        except Exception as exc:  # noqa: BLE001 - keep websocket resilient
            logger.exception("Websocket stream error: %s", exc)
            await socket.close()

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


def _serialize_logs(entries: Iterable[LogEntry]) -> List[dict]:
    try:
        return [
            {"name": entry.name, "payload": entry.payload, "timestamp": entry.timestamp.isoformat()}
            for entry in entries
        ]
    except Exception:
        return []
