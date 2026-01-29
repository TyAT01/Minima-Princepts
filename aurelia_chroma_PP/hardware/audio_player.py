from __future__ import annotations
import logging
import asyncio
import numpy as np
import sounddevice as sd
from config import settings

logger = logging.getLogger(__name__)

class LocalAudioPlayer:
    """Handles playing audio through the system's default output device."""

    def __init__(self, sample_rate: int = 24000):
        self.sample_rate = sample_rate
        self._lock = asyncio.Lock()

    async def play(self, audio_data: np.ndarray | bytes):
        """Plays the given audio data."""
        if audio_data is None:
            return

        async with self._lock:
            try:
                # Convert to numpy if it's bytes
                if isinstance(audio_data, bytes):
                    data_np = np.frombuffer(audio_data, dtype=np.float32)
                else:
                    data_np = audio_data

                logger.info("Playing audio through local output...")

                # sd.play is non-blocking, but we want to wait for it to finish
                # within this locked section to avoid overlapping local audio.
                sd.play(data_np, self.sample_rate)

                # Calculate duration to sleep
                duration = len(data_np) / self.sample_rate
                await asyncio.sleep(duration + 0.1)
                sd.stop()
            except Exception as e:
                logger.error(f"Error during local audio playback: {e}")

    def stop(self):
        """Stops any currently playing audio."""
        try:
            sd.stop()
        except Exception as e:
            logger.error(f"Error stopping audio: {e}")
