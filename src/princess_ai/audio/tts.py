"""Text-to-speech implementations with streaming chunking."""

from __future__ import annotations

import importlib.util
import logging
import tempfile
import wave
from dataclasses import dataclass
from typing import Iterable

from princess_ai.audio.pipeline import AudioFrame, TextToSpeech


@dataclass(slots=True)
class TTSConfig:
    sample_rate: int = 22050
    channels: int = 1


class PyTTSx3Engine(TextToSpeech):
    """pyttsx3-based TTS with WAV chunking."""

    def __init__(self, config: TTSConfig | None = None) -> None:
        self._config = config or TTSConfig()
        self._logger = logging.getLogger(__name__)
        self._engine = None
        self._ready = False
        self._init_engine()

    def _init_engine(self) -> None:
        if not importlib.util.find_spec("pyttsx3"):
            self._logger.warning("pyttsx3 not installed; TTS disabled.")
            return
        import pyttsx3  # type: ignore

        try:
            self._engine = pyttsx3.init()
            self._engine.setProperty("rate", 175)
            self._ready = True
        except Exception as exc:  # noqa: BLE001 - keep TTS resilient
            self._logger.exception("Failed to initialize pyttsx3: %s", exc)
            self._ready = False

    def synthesize(self, text: str) -> Iterable[AudioFrame]:
        if not self._ready:
            return []
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav") as temp_wav:
                self._engine.save_to_file(text, temp_wav.name)
                self._engine.runAndWait()
                with wave.open(temp_wav.name, "rb") as wav:
                    frames = wav.readframes(wav.getnframes())
                    return [
                        AudioFrame(
                            data=frames,
                            sample_rate=wav.getframerate(),
                            channels=wav.getnchannels(),
                        )
                    ]
        except Exception as exc:  # noqa: BLE001 - keep TTS resilient
            self._logger.exception("Failed to synthesize audio: %s", exc)
            return []


class DummyTTS(TextToSpeech):
    """Fallback TTS that yields no audio."""

    def synthesize(self, text: str) -> Iterable[AudioFrame]:
        return []
