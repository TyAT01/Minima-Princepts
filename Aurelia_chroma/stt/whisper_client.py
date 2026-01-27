from __future__ import annotations

import logging
import torch
from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)

class WhisperClient:
    """A client for the faster-whisper speech-to-text model."""

    def __init__(self, model_size: str = "small"):
        self._model_size = model_size
        self._model = None

    def load(self):
        """Loads the model."""
        logger.info("Loading faster-whisper model: %s", self._model_size)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        self._model = WhisperModel(self._model_size, device=device, compute_type="float16" if device == "cuda" else "int8")
        logger.info("faster-whisper model loaded successfully.")

    def transcribe(self, audio_path: str) -> str:
        """
        Takes a path to an audio file and returns the transcribed text.
        """
        if not self._model:
            raise RuntimeError("Model is not loaded. Call .load() first.")

        segments, _ = self._model.transcribe(audio_path, beam_size=5)
        return " ".join([segment.text for segment in segments])
