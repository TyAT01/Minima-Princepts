"""Logging, telemetry, and replay."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


@dataclass(slots=True)
class TelemetryEvent:
    name: str
    payload: Dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.utcnow)


class TelemetryLogger:
    def __init__(self, log_path: Path) -> None:
        self._logger = logging.getLogger("princess_ai")
        self._logger.setLevel(logging.INFO)
        self._handler = logging.FileHandler(log_path, encoding="utf-8")
        self._logger.addHandler(self._handler)

    def log(self, event: TelemetryEvent) -> None:
        record = {
            "name": event.name,
            "payload": event.payload,
            "timestamp": event.timestamp.isoformat(),
        }
        self._logger.info(json.dumps(record))
