"""Integration smoke tests for adapters and core services."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from princess_ai.input_adapters.base import InputAdapter
from princess_ai.llm.engine import LLMEngine, GenerationConfig
from princess_ai.memory.store import MemoryRecord, MemoryStore


@dataclass(slots=True)
class DiagnosticResult:
    name: str
    success: bool
    detail: str


class Diagnostics:
    def __init__(self) -> None:
        self._logger = logging.getLogger(__name__)

    def check_llm(self, llm: LLMEngine) -> DiagnosticResult:
        try:
            response = llm.generate("Ping", GenerationConfig())
            return DiagnosticResult("llm", bool(response), "LLM responded")
        except Exception as exc:  # noqa: BLE001 - keep diagnostics resilient
            self._logger.exception("LLM check failed: %s", exc)
            return DiagnosticResult("llm", False, "LLM check failed")

    def check_memory(self, store: MemoryStore) -> DiagnosticResult:
        try:
            store.add_memory(MemoryRecord(text="diagnostic", importance=0.5, timestamp=0.0))
            return DiagnosticResult("memory", True, "Memory write ok")
        except Exception as exc:  # noqa: BLE001 - keep diagnostics resilient
            self._logger.exception("Memory check failed: %s", exc)
            return DiagnosticResult("memory", False, "Memory check failed")

    def check_adapter(self, adapter: InputAdapter) -> DiagnosticResult:
        try:
            list(adapter.poll())
            return DiagnosticResult("adapter", True, "Adapter poll ok")
        except Exception as exc:  # noqa: BLE001 - keep diagnostics resilient
            self._logger.exception("Adapter check failed: %s", exc)
            return DiagnosticResult("adapter", False, "Adapter check failed")
