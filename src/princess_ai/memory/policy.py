"""Memory write policy, decay, and reinforcement logic."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from princess_ai.memory.store import MemoryRecord, MemoryStore


@dataclass(slots=True)
class MemoryPolicyConfig:
    min_importance: float = 0.5
    decay_rate: float = 0.01
    reinforce_boost: float = 0.2


class MemoryPolicy:
    def __init__(self, store: MemoryStore, config: MemoryPolicyConfig | None = None) -> None:
        self._store = store
        self._config = config or MemoryPolicyConfig()
        self._logger = logging.getLogger(__name__)

    def should_store(self, text: str, importance: float) -> bool:
        try:
            return bool(text.strip()) and importance >= self._config.min_importance
        except Exception as exc:  # noqa: BLE001 - keep policy resilient
            self._logger.exception("Failed to evaluate memory policy: %s", exc)
            return False

    def store_memory(self, text: str, importance: float) -> None:
        if not self.should_store(text, importance):
            return
        try:
            record = MemoryRecord(text=text, importance=importance, timestamp=time.time())
            self._store.add_memory(record)
        except Exception as exc:  # noqa: BLE001 - keep memory storage resilient
            self._logger.exception("Failed to store memory: %s", exc)

    def decay_memories(self) -> None:
        """Decay memory importance over time (no-op for SQLite store)."""
        try:
            # Placeholder: implement importance decay if store supports updates.
            return None
        except Exception as exc:  # noqa: BLE001 - keep decay resilient
            self._logger.exception("Failed to decay memories: %s", exc)

    def reinforce(self, text: str, base_importance: float) -> float:
        try:
            return base_importance + self._config.reinforce_boost
        except Exception as exc:  # noqa: BLE001 - keep reinforcement resilient
            self._logger.exception("Failed to reinforce memory: %s", exc)
            return base_importance
