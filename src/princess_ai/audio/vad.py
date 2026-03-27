"""Energy-based voice activity detection (VAD)."""

from __future__ import annotations

import audioop
import logging
from dataclasses import dataclass
from time import monotonic

from princess_ai.audio.schemas import AudioFrame


@dataclass(slots=True)
class VADConfig:
    energy_threshold: int = 250
    min_speech_ms: int = 150
    hangover_ms: int = 240
    sample_width: int = 2


class VoiceActivityDetector:
    """Simple RMS-energy VAD with hangover for natural turn-taking."""

    def __init__(self, config: VADConfig | None = None) -> None:
        self._config = config or VADConfig()
        self._logger = logging.getLogger(__name__)
        self._last_voice_ts: float | None = None
        self._speech_started_ts: float | None = None

    def is_speech(self, frame: AudioFrame) -> bool:
        try:
            rms = audioop.rms(frame.data, self._config.sample_width)
            now = monotonic()
            if rms >= self._config.energy_threshold:
                if self._speech_started_ts is None:
                    self._speech_started_ts = now
                self._last_voice_ts = now
                return True
            if self._last_voice_ts is None:
                return False
            hangover = (now - self._last_voice_ts) * 1000
            if hangover <= self._config.hangover_ms:
                return True
            self._speech_started_ts = None
            return False
        except Exception as exc:  # noqa: BLE001 - keep VAD resilient
            self._logger.exception("Failed to evaluate VAD: %s", exc)
            return False

    def has_turn_ended(self) -> bool:
        if self._speech_started_ts is None or self._last_voice_ts is None:
            return False
        elapsed_ms = (monotonic() - self._last_voice_ts) * 1000
        return elapsed_ms >= self._config.hangover_ms
