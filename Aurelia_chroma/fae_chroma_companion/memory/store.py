from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class MemoryEntry:
    id: str
    source: str
    user_text: str
    created_at: str


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.write_text("[]", encoding="utf-8")

    def store_entry(self, source: str, user_text: str) -> str:
        entry = MemoryEntry(
            id=str(uuid.uuid4()),
            source=source,
            user_text=user_text,
            created_at=datetime.utcnow().isoformat(),
        )
        data = self._read()
        data.append(entry.__dict__)
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return entry.id

    def list_entries(self) -> list[dict[str, Any]]:
        return list(self._read())

    def _read(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
