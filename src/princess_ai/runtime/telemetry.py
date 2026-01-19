"""Realtime telemetry snapshot and metrics tracking."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(slots=True)
class AdapterStatus:
    name: str
    connected: bool = False
    last_event_at: float | None = None
    reconnects: int = 0
    last_error: str | None = None


@dataclass(slots=True)
class VoiceTelemetry:
    listening: bool = False
    speaking: bool = False
    barge_in: bool = False
    tts_queue: List[str] = field(default_factory=list)
    last_transcript: str | None = None
    last_final_transcript: str | None = None


@dataclass(slots=True)
class QoSMetrics:
    time_to_first_token_ms: float | None = None
    end_to_end_latency_ms: float | None = None
    dropped_audio_frames: int = 0
    reconnect_count: int = 0


@dataclass(slots=True)
class EmotionTelemetry:
    mood: str = "warm"
    valence: float = 0.0
    arousal: float = 0.0


@dataclass(slots=True)
class TelemetrySnapshot:
    adapters: Dict[str, AdapterStatus]
    voice: VoiceTelemetry
    llm_tokens: List[str]
    qos: QoSMetrics
    emotion: EmotionTelemetry
    last_updated: float


class TelemetryHub:
    def __init__(self, token_limit: int = 200) -> None:
        self._adapters: Dict[str, AdapterStatus] = {}
        self._voice = VoiceTelemetry()
        self._llm_tokens: List[str] = []
        self._token_limit = token_limit
        self._qos = QoSMetrics()
        self._emotion = EmotionTelemetry()
        self._last_updated = time.time()

    def update_adapter(
        self,
        name: str,
        *,
        connected: Optional[bool] = None,
        last_event_at: Optional[float] = None,
        reconnects: Optional[int] = None,
        last_error: Optional[str] = None,
    ) -> None:
        status = self._adapters.get(name)
        if not status:
            status = AdapterStatus(name=name)
            self._adapters[name] = status
        if connected is not None:
            status.connected = connected
        if last_event_at is not None:
            status.last_event_at = last_event_at
        if reconnects is not None:
            status.reconnects = reconnects
        if last_error is not None:
            status.last_error = last_error
        self._touch()

    def update_voice(
        self,
        *,
        listening: Optional[bool] = None,
        speaking: Optional[bool] = None,
        barge_in: Optional[bool] = None,
        tts_queue: Optional[List[str]] = None,
        last_transcript: Optional[str] = None,
        last_final_transcript: Optional[str] = None,
    ) -> None:
        if listening is not None:
            self._voice.listening = listening
        if speaking is not None:
            self._voice.speaking = speaking
        if barge_in is not None:
            self._voice.barge_in = barge_in
        if tts_queue is not None:
            self._voice.tts_queue = list(tts_queue)
        if last_transcript is not None:
            self._voice.last_transcript = last_transcript
        if last_final_transcript is not None:
            self._voice.last_final_transcript = last_final_transcript
        self._touch()

    def add_llm_token(self, token: str) -> None:
        self._llm_tokens.append(token)
        if len(self._llm_tokens) > self._token_limit:
            self._llm_tokens.pop(0)
        self._touch()

    def reset_llm_tokens(self) -> None:
        self._llm_tokens.clear()
        self._touch()

    def update_qos(
        self,
        *,
        time_to_first_token_ms: Optional[float] = None,
        end_to_end_latency_ms: Optional[float] = None,
        dropped_audio_frames: Optional[int] = None,
        reconnect_count: Optional[int] = None,
    ) -> None:
        if time_to_first_token_ms is not None:
            self._qos.time_to_first_token_ms = time_to_first_token_ms
        if end_to_end_latency_ms is not None:
            self._qos.end_to_end_latency_ms = end_to_end_latency_ms
        if dropped_audio_frames is not None:
            self._qos.dropped_audio_frames = dropped_audio_frames
        if reconnect_count is not None:
            self._qos.reconnect_count = reconnect_count
        self._touch()

    def update_emotion(self, *, mood: str, valence: float, arousal: float) -> None:
        self._emotion.mood = mood
        self._emotion.valence = valence
        self._emotion.arousal = arousal
        self._touch()

    def snapshot(self) -> TelemetrySnapshot:
        return TelemetrySnapshot(
            adapters={name: status for name, status in self._adapters.items()},
            voice=self._voice,
            llm_tokens=list(self._llm_tokens),
            qos=self._qos,
            emotion=self._emotion,
            last_updated=self._last_updated,
        )

    def _touch(self) -> None:
        self._last_updated = time.time()
