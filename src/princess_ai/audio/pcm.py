"""PCM conversion helpers for voice transport."""

from __future__ import annotations

import audioop

from princess_ai.audio.pipeline import AudioFrame


def ensure_pcm_format(
    frame: AudioFrame,
    *,
    target_rate: int = 48000,
    target_channels: int = 2,
    sample_width: int = 2,
) -> bytes:
    data = frame.data
    channels = frame.channels
    rate = frame.sample_rate

    if channels != target_channels:
        if target_channels == 1:
            data = audioop.tomono(data, sample_width, 0.5, 0.5)
        else:
            data = audioop.tostereo(data, sample_width, 1.0, 1.0)
        channels = target_channels

    if rate != target_rate:
        data, _ = audioop.ratecv(data, sample_width, channels, rate, target_rate, None)
        rate = target_rate

    return data


def chunk_pcm(data: bytes, frame_size: int) -> list[bytes]:
    return [data[i : i + frame_size] for i in range(0, len(data), frame_size)]
