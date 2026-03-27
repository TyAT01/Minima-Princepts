"""Audio pipeline interfaces for STT/TTS streaming."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Protocol

from princess_ai.audio.schemas import AudioFrame
from princess_ai.audio.vad import VoiceActivityDetector, VADConfig


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
class AudioPipelineConfig:
    vad: VADConfig = field(default_factory=VADConfig)
    allow_barge_in: bool = True
    echo_suppression: bool = True
    min_interrupt_ms: int = 200


@dataclass(slots=True)
class AudioPipelineState:
    listening: bool = False
    speaking: bool = False
    tts_queue: List[str] = field(default_factory=list)
    barge_in_detected: bool = False
    last_partial: str | None = None


class AudioPipeline:
    """Audio pipeline coordinating STT/TTS with VAD and barge-in handling."""

    def __init__(
        self,
        stt: SpeechToText,
        tts: TextToSpeech,
        config: AudioPipelineConfig | None = None,
        on_barge_in: Callable[[], None] | None = None,
    ) -> None:
        self._stt = stt
        self._tts = tts
        self._config = config or AudioPipelineConfig()
        self._state = AudioPipelineState()
        self._logger = logging.getLogger(__name__)
        self._vad = VoiceActivityDetector(self._config.vad)
        self._on_barge_in = on_barge_in

    @property
    def state(self) -> AudioPipelineState:
        return self._state

    def ingest(self, frame: AudioFrame) -> Iterable[TranscriptChunk]:
        self._state.listening = True
        try:
            if self._config.echo_suppression and self._state.speaking:
                if not self._vad.is_speech(frame):
                    return []
            if self._config.allow_barge_in and self._state.speaking:
                if self._vad.is_speech(frame):
                    self._trigger_barge_in()
            self._stt.accept_audio(frame)
            chunks = list(self._stt.partials())
            for chunk in chunks:
                if not chunk.text:
                    continue
                self._state.last_partial = chunk.text
                if chunk.is_final:
                    self._state.last_partial = None
            if self._state.last_partial and self._vad.has_turn_ended():
                final_text = self._state.last_partial
                self._state.last_partial = None
                chunks.append(TranscriptChunk(text=final_text, is_final=True))
            return chunks
        except Exception as exc:  # noqa: BLE001 - keep audio pipeline resilient
            self._logger.exception("Failed to ingest audio frame: %s", exc)
            return []

    def enqueue_tts(self, text: str) -> Iterable[AudioFrame]:
        self._state.speaking = True
        try:
            self._state.tts_queue.append(text)
            if self._state.barge_in_detected:
                return []
            return self._tts.synthesize(text)
        except Exception as exc:  # noqa: BLE001 - keep audio pipeline resilient
            self._logger.exception("Failed to synthesize TTS audio: %s", exc)
            return []

    def stop_tts(self) -> None:
        try:
            self._state.speaking = False
            self._state.tts_queue.clear()
            self._state.last_partial = None
        except Exception as exc:  # noqa: BLE001 - keep audio pipeline resilient
            self._logger.exception("Failed to stop TTS: %s", exc)

    def reset_barge_in(self) -> None:
        self._state.barge_in_detected = False

    def _trigger_barge_in(self) -> None:
        if not self._state.barge_in_detected:
            self._state.barge_in_detected = True
            self.stop_tts()
            if self._on_barge_in:
                try:
                    self._on_barge_in()
                except Exception as exc:  # noqa: BLE001 - keep callback resilient
                    self._logger.exception("Barge-in callback failed: %s", exc)
