"""Voice output routing for adapters that support TTS playback."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Protocol


class VoiceCapable(Protocol):
    def speak(self, text: str) -> None:
        ...

    def interrupt(self) -> None:
        ...


@dataclass(slots=True)
class VoiceOutputPacket:
    text: str
    source: str
    channel: str | None = None


class VoiceOutputManager:
    def __init__(self, adapters: Iterable[VoiceCapable]) -> None:
        self._adapters = list(adapters)
        self._logger = logging.getLogger(__name__)

    def speak(self, packet: VoiceOutputPacket) -> None:
        for adapter in self._adapters:
            try:
                adapter.speak(packet.text)
            except Exception as exc:  # noqa: BLE001 - keep voice output resilient
                self._logger.exception("Voice output failed: %s", exc)

    def interrupt(self) -> None:
        for adapter in self._adapters:
            try:
                adapter.interrupt()
            except Exception as exc:  # noqa: BLE001 - keep voice output resilient
                self._logger.exception("Voice interrupt failed: %s", exc)
