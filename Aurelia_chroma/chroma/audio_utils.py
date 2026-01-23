from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
import soundfile as sf


def normalize_audio(audio: np.ndarray) -> np.ndarray:
    if audio.size == 0:
        return audio
    peak = np.max(np.abs(audio))
    if peak <= 0:
        return audio
    return audio / peak


def write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, audio, sample_rate)


def combine_segments(segments: Iterable[np.ndarray]) -> np.ndarray:
    segments_list = list(segments)
    if not segments_list:
        return np.array([], dtype=np.float32)
    return np.concatenate(segments_list).astype(np.float32)
