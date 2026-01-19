"""Output pipeline for TTS and playback."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class OutputPacket:
    text: str
    voice: str = "default"


class OutputPipeline:
    def speak(self, packet: OutputPacket) -> None:
        print(f"Princess says: {packet.text}")
