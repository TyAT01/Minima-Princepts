from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

from chroma.audio_utils import normalize_audio

logger = logging.getLogger(__name__)


@dataclass
class ChromaVoiceConfig:
    model_name: str
    tts_model: str
    sample_rate: int


class ChromaVoice:
    """Handles speech-to-text and text-to-speech via Chroma-compatible models."""

    def __init__(self, config: ChromaVoiceConfig) -> None:
        self._config = config
        self._stt_pipeline = None
        self._tts_pipeline = None

    def _lazy_load(self) -> None:
        if self._stt_pipeline and self._tts_pipeline:
            return
        try:
            from transformers import pipeline  # type: ignore

            if self._stt_pipeline is None:
                self._stt_pipeline = pipeline(
                    "automatic-speech-recognition",
                    model=self._config.model_name,
                )
            if self._tts_pipeline is None:
                self._tts_pipeline = pipeline(
                    "text-to-speech",
                    model=self._config.tts_model,
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to load Chroma pipelines: %s", exc)
            raise

    def transcribe(self, audio: np.ndarray) -> str:
        self._lazy_load()
        result = self._stt_pipeline(audio, sampling_rate=self._config.sample_rate)
        if isinstance(result, dict):
            return str(result.get("text", "")).strip()
        return str(result).strip()

    def synthesize(self, text: str) -> Optional[np.ndarray]:
        self._lazy_load()
        result = self._tts_pipeline(text)
        if isinstance(result, dict):
            audio = result.get("audio")
            if audio is None:
                return None
            return normalize_audio(np.asarray(audio, dtype=np.float32))
        return None
