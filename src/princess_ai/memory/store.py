"""SQLite-based long-term memory store."""

from __future__ import annotations

import logging
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
        self._logger = logging.getLogger(__name__)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        try:
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
        except sqlite3.Error as exc:
            self._logger.error("Failed to ensure memory schema: %s", exc)

    def add_memory(self, record: MemoryRecord) -> None:
        try:
            if not record.text.strip():
                self._logger.warning("Skipping empty memory record.")
                return
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT INTO memories (text, importance, timestamp) VALUES (?, ?, ?)",
                    (record.text, record.importance, record.timestamp),
                )
        except sqlite3.Error as exc:
            self._logger.error("Failed to store memory: %s", exc)

    def list_memories(self, limit: int = 50) -> Iterable[MemoryRecord]:
        try:
            if limit <= 0:
                self._logger.warning("Memory list limit must be positive. Got %s.", limit)
                return []
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT text, importance, timestamp FROM memories ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        except sqlite3.Error as exc:
            self._logger.error("Failed to list memories: %s", exc)
            return []
        return [MemoryRecord(text=row[0], importance=row[1], timestamp=row[2]) for row in rows]

    def decay_importance(self, amount: float) -> int:
        try:
            if amount <= 0:
                self._logger.warning("Decay amount must be positive. Got %s.", amount)
                return 0
            with sqlite3.connect(self._db_path) as conn:
                cursor = conn.execute(
                    """
                    UPDATE memories
                    SET importance = CASE
                        WHEN importance - ? < 0 THEN 0
                        ELSE importance - ?
                    END
                    """,
                    (amount, amount),
                )
                return cursor.rowcount
        except sqlite3.Error as exc:
            self._logger.error("Failed to decay memory importance: %s", exc)
            return 0
