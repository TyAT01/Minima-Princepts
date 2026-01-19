"""Avatar animation bridge."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterable


@dataclass(slots=True)
class AvatarSignal:
    emotion: str
    viseme: str


class AvatarBridge:
    def __init__(self) -> None:
        self._listeners: list[Callable[[AvatarSignal], None]] = []
        self._logger = logging.getLogger(__name__)

    def register_listener(self, listener: Callable[[AvatarSignal], None]) -> None:
        try:
            if listener not in self._listeners:
                self._listeners.append(listener)
        except Exception as exc:  # noqa: BLE001 - keep avatar bridge resilient
            self._logger.exception("Failed to register listener: %s", exc)

    def send(self, signal: AvatarSignal) -> None:
        self._notify(signal)

    def _notify(self, signal: AvatarSignal) -> None:
        for listener in list(self._listeners):
            try:
                listener(signal)
            except Exception as exc:  # noqa: BLE001 - keep avatar bridge resilient
                self._logger.exception("Avatar listener failed: %s", exc)

    def listeners(self) -> Iterable[Callable[[AvatarSignal], None]]:
        return tuple(self._listeners)
