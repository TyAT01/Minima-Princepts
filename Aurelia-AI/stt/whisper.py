from __future__ import annotations
import logging
import os
import torch
from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)

class STTSystem:
    """Speech-to-Text system using Faster-Whisper."""

    def __init__(self, model_size: str = "base", device: str = None, compute_type: str = "float16"):
        self.model_size = model_size
        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device

        # On CPU, float16 is not supported, use int8
        if self.device == "cpu":
            self.compute_type = "int8"
        else:
            self.compute_type = compute_type

        self.model = None

    def load_model(self):
        """Lazy loads the Whisper model."""
        if self.model is None:
            logger.info(f"Loading Faster-Whisper model: {self.model_size} on {self.device}...")
            self.model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
            logger.info("Faster-Whisper model loaded.")

    def transcribe(self, audio_path: str) -> str:
        """Transcribes an audio file to text."""
        if self.model is None:
            self.load_model()

        segments, info = self.model.transcribe(audio_path, beam_size=5)

        full_text = ""
        for segment in segments:
            full_text += segment.text + " "

        return full_text.strip()
