from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class WhisperConfig:
    model_size: str = "small"
    compute_type: str = "int8"


class FasterWhisperSTT:
    def __init__(self, config: WhisperConfig) -> None:
        self._config = config
        self._model = None

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel  # type: ignore

            self._model = WhisperModel(self._config.model_size, compute_type=self._config.compute_type)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to initialize Faster Whisper: %s", exc)
            raise

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        self._ensure_model()
        segments, _ = self._model.transcribe(audio, language="en")
        return " ".join(segment.text.strip() for segment in segments if segment.text)

    def transcribe_segments(self, segments: Iterable[np.ndarray], sample_rate: int) -> str:
        combined = np.concatenate(list(segments)).astype(np.float32)
        return self.transcribe(combined, sample_rate)
