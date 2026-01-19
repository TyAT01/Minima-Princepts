"""Data schemas for audio processing."""

from __future__ import annotations
from dataclasses import dataclass


@dataclass(slots=True)
class AudioFrame:
    """A chunk of audio data."""

    data: bytes
    sample_rate: int
    channels: int
