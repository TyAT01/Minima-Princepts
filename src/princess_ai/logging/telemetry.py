"""Logging, telemetry, and replay."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List


@dataclass(slots=True)
class TelemetryEvent:
    name: str
    payload: Dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.utcnow)


class TelemetryLogger:
    def __init__(self, log_path: Path) -> None:
        self._logger = logging.getLogger("princess_ai")
        self._logger.setLevel(logging.INFO)
        try:
            self._handler = logging.FileHandler(log_path, encoding="utf-8")
            self._logger.addHandler(self._handler)
        except OSError as exc:
            self._logger.error("Failed to configure telemetry handler: %s", exc)
            self._handler = None

    def log(self, event: TelemetryEvent) -> None:
        try:
            record = {
                "name": event.name,
                "payload": event.payload,
                "timestamp": event.timestamp.isoformat(),
            }
            self._logger.info(json.dumps(record))
        except Exception as exc:  # noqa: BLE001 - keep telemetry resilient
            self._logger.error("Failed to log telemetry event: %s", exc)


@dataclass(slots=True)
class LogEntry:
    name: str
    payload: Dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.utcnow)


class InMemoryLogStore:
    def __init__(self, limit: int = 500) -> None:
        self._limit = limit
        self._events: List[LogEntry] = []

    def add(self, entry: LogEntry) -> None:
        self._events.append(entry)
        if len(self._events) > self._limit:
            self._events.pop(0)

    def search(self, term: str) -> Iterable[LogEntry]:
        lowered = term.lower()
        return [event for event in self._events if lowered in json.dumps(event.payload).lower()]

    def snapshot(self) -> List[LogEntry]:
        return list(self._events)
