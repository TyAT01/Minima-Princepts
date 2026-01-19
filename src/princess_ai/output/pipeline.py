"""Output pipeline for TTS and playback."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class OutputPacket:
    text: str
    voice: str = "default"
    speaker: str = "Aurelia Vale"


class OutputPipeline:
    def speak(self, packet: OutputPacket) -> None:
        try:
            print(f"{packet.speaker} says: {packet.text}")
        except Exception:  # noqa: BLE001 - stdout failures should not crash runtime
            return
