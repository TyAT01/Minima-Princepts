from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import webrtcvad


@dataclass
class VADConfig:
    aggressiveness: int
    sample_rate: int
    frame_ms: int = 30


class VADSegmenter:
    def __init__(self, config: VADConfig) -> None:
        self._config = config
        self._vad = webrtcvad.Vad(config.aggressiveness)

    def is_speech(self, frame: bytes) -> bool:
        return self._vad.is_speech(frame, self._config.sample_rate)

    def frame_bytes(self, audio: np.ndarray) -> list[bytes]:
        frame_size = int(self._config.sample_rate * self._config.frame_ms / 1000)
        if frame_size <= 0:
            return []
        frames = []
        for start in range(0, len(audio), frame_size):
            chunk = audio[start : start + frame_size]
            if len(chunk) < frame_size:
                break
            frames.append(chunk.astype(np.int16).tobytes())
        return frames
