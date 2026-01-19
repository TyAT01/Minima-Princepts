"""SQLite-based long-term memory store."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(slots=True)
class MemoryRecord:
    text: str
    importance: float
    timestamp: float


class MemoryStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    importance REAL NOT NULL,
                    timestamp REAL NOT NULL
                )
                """
            )

    def add_memory(self, record: MemoryRecord) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO memories (text, importance, timestamp) VALUES (?, ?, ?)",
                (record.text, record.importance, record.timestamp),
            )

    def list_memories(self, limit: int = 50) -> Iterable[MemoryRecord]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT text, importance, timestamp FROM memories ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [MemoryRecord(text=row[0], importance=row[1], timestamp=row[2]) for row in rows]
