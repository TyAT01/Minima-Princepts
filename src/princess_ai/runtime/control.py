"""Runtime control hub for WebUI and automation."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(slots=True)
class ControlState:
    adapters: Dict[str, bool] = field(default_factory=dict)
    active_model: str | None = None
    persona: str | None = None
    stream_mode: bool = False
    muted: bool = False
    manual_messages: List[str] = field(default_factory=list)


class ControlHub:
    def __init__(self) -> None:
        self._state = ControlState()
        self._logger = logging.getLogger(__name__)

    def snapshot(self) -> ControlState:
        return self._state

    def set_adapter(self, name: str, enabled: bool) -> None:
        try:
            self._state.adapters[name] = enabled
        except Exception as exc:  # noqa: BLE001 - keep control resilient
            self._logger.exception("Failed to set adapter: %s", exc)

    def set_model(self, model: str) -> None:
        try:
            self._state.active_model = model
        except Exception as exc:  # noqa: BLE001 - keep control resilient
            self._logger.exception("Failed to set model: %s", exc)

    def set_persona(self, persona: str) -> None:
        try:
            self._state.persona = persona
        except Exception as exc:  # noqa: BLE001 - keep control resilient
            self._logger.exception("Failed to set persona: %s", exc)

    def set_stream_mode(self, enabled: bool) -> None:
        try:
            self._state.stream_mode = enabled
        except Exception as exc:  # noqa: BLE001 - keep control resilient
            self._logger.exception("Failed to set stream mode: %s", exc)

    def set_muted(self, muted: bool) -> None:
        try:
            self._state.muted = muted
        except Exception as exc:  # noqa: BLE001 - keep control resilient
            self._logger.exception("Failed to set muted: %s", exc)

    def push_manual(self, message: str) -> None:
        try:
            self._state.manual_messages.append(message)
        except Exception as exc:  # noqa: BLE001 - keep control resilient
            self._logger.exception("Failed to queue manual message: %s", exc)

    def drain_manual(self) -> List[str]:
        try:
            messages = list(self._state.manual_messages)
            self._state.manual_messages.clear()
            return messages
        except Exception as exc:  # noqa: BLE001 - keep control resilient
            self._logger.exception("Failed to drain manual messages: %s", exc)
            return []
