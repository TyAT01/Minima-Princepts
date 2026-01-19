"""Streaming speech-to-text implementations."""

from __future__ import annotations

import importlib.util
import json
import logging
from dataclasses import dataclass
from typing import Iterable, List

from princess_ai.audio.pipeline import AudioFrame, TranscriptChunk, SpeechToText


@dataclass(slots=True)
class STTConfig:
    model_path: str = "models/vosk"
    sample_rate: int = 16000


class VoskStreamingSTT(SpeechToText):
    """Vosk streaming STT implementation with partials + finals."""

    def __init__(self, config: STTConfig | None = None) -> None:
        self._config = config or STTConfig()
        self._logger = logging.getLogger(__name__)
        self._ready = False
        self._partials: List[TranscriptChunk] = []
        self._init_vosk()

    def _init_vosk(self) -> None:
        if not importlib.util.find_spec("vosk"):
            self._logger.warning("Vosk not installed; STT disabled.")
            return
        from vosk import KaldiRecognizer, Model  # type: ignore

        try:
            self._model = Model(self._config.model_path)
            self._recognizer = KaldiRecognizer(self._model, self._config.sample_rate)
            self._ready = True
        except Exception as exc:  # noqa: BLE001 - keep STT resilient
            self._logger.exception("Failed to initialize Vosk: %s", exc)
            self._ready = False

    def accept_audio(self, frame: AudioFrame) -> None:
        if not self._ready:
            return
        try:
            if self._recognizer.AcceptWaveform(frame.data):
                result = json.loads(self._recognizer.Result())
                text = result.get("text", "").strip()
                if text:
                    self._partials.append(TranscriptChunk(text=text, is_final=True))
            else:
                partial = json.loads(self._recognizer.PartialResult()).get("partial", "").strip()
                if partial:
                    self._partials.append(TranscriptChunk(text=partial, is_final=False))
        except Exception as exc:  # noqa: BLE001 - keep STT resilient
            self._logger.exception("Failed to accept audio: %s", exc)

    def partials(self) -> Iterable[TranscriptChunk]:
        chunks = list(self._partials)
        self._partials.clear()
        return chunks


class DummyStreamingSTT(SpeechToText):
    """Fallback STT that yields no transcript."""

    def accept_audio(self, frame: AudioFrame) -> None:
        return None

    def partials(self) -> Iterable[TranscriptChunk]:
        return []
