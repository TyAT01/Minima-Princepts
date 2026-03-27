"""FastAPI server for runtime control and inspection."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from princess_ai.app_logging.telemetry import (
    InMemoryLogStore,
    LogEntry,
    attach_error_log_handler,
)
from princess_ai.memory.store import MemoryRecord, MemoryStore
from princess_ai.runtime.presence import PresenceTracker
from princess_ai.runtime.control import ControlHub
from princess_ai.runtime.session import SessionManager
from princess_ai.runtime.telemetry import TelemetryHub


def create_app(
    session_manager: SessionManager,
    memory_store: MemoryStore,
    control_hub: ControlHub | None = None,
    log_store: InMemoryLogStore | None = None,
    telemetry: TelemetryHub | None = None,
) -> FastAPI:
    app = FastAPI(title="Aurelia Vale AI API")
    logger = logging.getLogger(__name__)
    control = control_hub or ControlHub()
    logs = log_store or InMemoryLogStore()
    telemetry_hub = telemetry or TelemetryHub()
    attach_error_log_handler(logs)
    presence = PresenceTracker()
    web_root = Path(__file__).parent / "webgui"
    if web_root.exists():
        app.mount("/static", StaticFiles(directory=web_root), name="static")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/")
    def web_gui() -> HTMLResponse:
        index_path = web_root / "index.html"
        if not index_path.exists():
            return HTMLResponse("<h1>Aurelia Web GUI not installed.</h1>", status_code=404)
        return HTMLResponse(index_path.read_text(encoding="utf-8"))

    @app.get("/styles.css")
    def web_styles() -> FileResponse:
        return FileResponse(web_root / "styles.css")

    @app.get("/app.js")
    def web_script() -> FileResponse:
        return FileResponse(web_root / "app.js")

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
    def list_memories(limit: int = 50, scope: str | None = None) -> dict:
        try:
            memories = _serialize_memories(memory_store.list_memories(limit=limit, scope=scope))
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

    @app.get("/telemetry")
    def get_telemetry() -> dict:
        try:
            snapshot = telemetry_hub.snapshot()
            return {
                "adapters": {name: asdict(status) for name, status in snapshot.adapters.items()},
                "voice": asdict(snapshot.voice),
                "llm_tokens": snapshot.llm_tokens,
                "qos": asdict(snapshot.qos),
                "emotion": asdict(snapshot.emotion),
                "last_updated": snapshot.last_updated,
            }
        except Exception as exc:  # noqa: BLE001 - keep API resilient
            logger.exception("Failed to fetch telemetry: %s", exc)
            return {"adapters": {}, "voice": {}, "llm_tokens": [], "qos": {}}

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

    @app.get("/presence")
    def list_presence() -> dict:
        snapshots = presence.snapshot()
        return {
            "channels": [
                {
                    "channel": item.channel,
                    "participants": item.participants,
                    "last_updated": item.last_updated.isoformat(),
                }
                for item in snapshots
            ]
        }

    @app.post("/presence/join")
    def join_presence(channel: str, user: str) -> dict:
        snapshot = presence.join(channel, user)
        logs.add(
            LogEntry(
                name="presence",
                payload={"event": "join", "channel": channel, "user": user},
            )
        )
        control.push_manual(f"{user} joined the {channel} channel.")
        return {
            "channel": snapshot.channel,
            "participants": snapshot.participants,
            "last_updated": snapshot.last_updated.isoformat(),
        }

    @app.post("/presence/leave")
    def leave_presence(channel: str, user: str) -> dict:
        snapshot = presence.leave(channel, user)
        logs.add(
            LogEntry(
                name="presence",
                payload={"event": "leave", "channel": channel, "user": user},
            )
        )
        control.push_manual(f"{user} left the {channel} channel.")
        if not snapshot.participants:
            control.push_manual(
                f"The {channel} channel is empty, but the creator may still be monitoring."
            )
        return {
            "channel": snapshot.channel,
            "participants": snapshot.participants,
            "last_updated": snapshot.last_updated.isoformat(),
        }

    @app.websocket("/ws/stream")
    async def websocket_stream(socket: WebSocket) -> None:
        await socket.accept()
        try:
            while True:
                snapshot = telemetry_hub.snapshot()
                payload = {
                    "logs": _serialize_logs(logs.snapshot()),
                    "presence": [
                        {
                            "channel": item.channel,
                            "participants": item.participants,
                            "last_updated": item.last_updated.isoformat(),
                        }
                        for item in presence.snapshot()
                    ],
                    "telemetry": {
                        "adapters": {name: asdict(status) for name, status in snapshot.adapters.items()},
                        "voice": asdict(snapshot.voice),
                        "llm_tokens": snapshot.llm_tokens,
                        "qos": asdict(snapshot.qos),
                        "emotion": asdict(snapshot.emotion),
                        "last_updated": snapshot.last_updated,
                    },
                    "session": asdict(session_manager.snapshot()),
                    "controls": asdict(control.snapshot()),
                }
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
                "scope": record.scope,
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
