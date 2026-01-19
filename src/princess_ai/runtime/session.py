"""Session and runtime state management."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict


@dataclass(slots=True)
class SessionState:
    mode: str = "local"
    modules_enabled: Dict[str, bool] = field(default_factory=lambda: {"text": True})
    active_profile: str = "default"
    active_engine: str = "llama_cpp_server"


class SessionManager:
    def __init__(self, initial: SessionState | None = None) -> None:
        self._state = initial or SessionState()
        self._logger = logging.getLogger(__name__)

    def snapshot(self) -> SessionState:
        return self._state

    def set_mode(self, mode: str) -> None:
        try:
            self._state.mode = mode
        except Exception as exc:  # noqa: BLE001 - keep session updates resilient
            self._logger.exception("Failed to set mode: %s", exc)

    def set_module(self, name: str, enabled: bool) -> None:
        try:
            self._state.modules_enabled[name] = enabled
        except Exception as exc:  # noqa: BLE001 - keep session updates resilient
            self._logger.exception("Failed to set module: %s", exc)

    def set_profile(self, profile: str) -> None:
        try:
            self._state.active_profile = profile
        except Exception as exc:  # noqa: BLE001 - keep session updates resilient
            self._logger.exception("Failed to set profile: %s", exc)

    def set_engine(self, engine: str) -> None:
        try:
            self._state.active_engine = engine
        except Exception as exc:  # noqa: BLE001 - keep session updates resilient
            self._logger.exception("Failed to set engine: %s", exc)
