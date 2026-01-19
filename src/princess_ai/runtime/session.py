"""Session and runtime state management."""

from __future__ import annotations

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

    def snapshot(self) -> SessionState:
        return self._state

    def set_mode(self, mode: str) -> None:
        self._state.mode = mode

    def set_module(self, name: str, enabled: bool) -> None:
        self._state.modules_enabled[name] = enabled

    def set_profile(self, profile: str) -> None:
        self._state.active_profile = profile

    def set_engine(self, engine: str) -> None:
        self._state.active_engine = engine
