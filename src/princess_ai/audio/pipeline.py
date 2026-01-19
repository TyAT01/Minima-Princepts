"""Audio pipeline interfaces for STT/TTS streaming."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, List, Protocol


@dataclass(slots=True)
class AudioFrame:
    data: bytes
    sample_rate: int
    channels: int


@dataclass(slots=True)
class TranscriptChunk:
    text: str
    is_final: bool = False


class SpeechToText(Protocol):
    def accept_audio(self, frame: AudioFrame) -> None:
        ...

    def partials(self) -> Iterable[TranscriptChunk]:
        ...


class TextToSpeech(Protocol):
    def synthesize(self, text: str) -> Iterable[AudioFrame]:
        ...


@dataclass(slots=True)
class AudioPipelineState:
    listening: bool = False
    speaking: bool = False
    tts_queue: List[str] = field(default_factory=list)


class AudioPipeline:
    """Minimal audio pipeline for coordinating STT/TTS."""

    def __init__(self, stt: SpeechToText, tts: TextToSpeech) -> None:
        self._stt = stt
        self._tts = tts
        self._state = AudioPipelineState()
        self._logger = logging.getLogger(__name__)

    @property
    def state(self) -> AudioPipelineState:
        return self._state

    def ingest(self, frame: AudioFrame) -> Iterable[TranscriptChunk]:
        self._state.listening = True
        try:
            self._stt.accept_audio(frame)
            return self._stt.partials()
        except Exception as exc:  # noqa: BLE001 - keep audio pipeline resilient
            self._logger.exception("Failed to ingest audio frame: %s", exc)
            return []

    def enqueue_tts(self, text: str) -> Iterable[AudioFrame]:
        self._state.speaking = True
        try:
            self._state.tts_queue.append(text)
            return self._tts.synthesize(text)
        except Exception as exc:  # noqa: BLE001 - keep audio pipeline resilient
            self._logger.exception("Failed to synthesize TTS audio: %s", exc)
            return []
