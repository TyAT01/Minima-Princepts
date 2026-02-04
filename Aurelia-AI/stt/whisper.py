from __future__ import annotations
import logging
import os
import sys
import torch
import collections
import threading
import time
import tempfile
import wave
import numpy as np
import sounddevice as sd

# [FIX] ctranslate2 ROCm path workaround for Windows
if sys.platform == "win32":
    try:
        from faster_whisper import WhisperModel
    except FileNotFoundError as e:
        if "_rocm" in str(e).lower():
            import re
            match = re.search(r"'(.*?)'", str(e))
            if match:
                missing_path = match.group(1)
                try:
                    abs_path = os.path.abspath(missing_path)
                    os.makedirs(abs_path, exist_ok=True)
                    from faster_whisper import WhisperModel
                except:
                    raise e
            else:
                raise e
        else:
            raise e
else:
    from faster_whisper import WhisperModel

# Optional import for VAD
try:
    import webrtcvad
except ImportError:
    webrtcvad = None

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

    def transcribe(self, audio_source: str | np.ndarray) -> str:
        """Transcribes an audio file or numpy array to text."""
        if self.model is None:
            self.load_model()

        segments, info = self.model.transcribe(audio_source, beam_size=5)

        full_text = ""
        for segment in segments:
            full_text += segment.text + " "

        return full_text.strip()

class VoiceMonitor:
    """Background monitor that captures audio from the default mic and segments speech."""

    def __init__(self, callback, sample_rate=16000, frame_duration_ms=30):
        self.callback = callback
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.frame_size = int(sample_rate * frame_duration_ms / 1000)

        if webrtcvad:
            self.vad = webrtcvad.Vad(3) # Aggressiveness 3
        else:
            self.vad = None
            logger.warning("webrtcvad not found. Hands-free mic will not work correctly.")

        self.buffer = collections.deque(maxlen=20) # 600ms pre-roll
        self.triggered = False
        self.voiced_frames = []
        self.stop_event = threading.Event()
        self.is_listening = False
        self.thread = None

    def start(self):
        if self.is_listening: return
        self.is_listening = True
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._listen_loop, daemon=True)
        self.thread.start()
        logger.info("Voice Monitor started.")

    def stop(self):
        self.is_listening = False
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
        logger.info("Voice Monitor stopped.")

    def _listen_loop(self):
        if not self.vad:
            logger.error("VAD not initialized. Cannot start listen loop.")
            return

        # Diagnostics: log default device
        try:
            device_info = sd.query_devices(kind='input')
            logger.info(f"Using default input device: {device_info.get('name')} (SR: {device_info.get('default_samplerate')})")
        except Exception as e:
            logger.error(f"Could not query audio devices: {e}")
            self.is_listening = False
            return

        # Open default input stream
        try:
            logger.info(f"Opening audio stream: {self.sample_rate}Hz, 1 channel...")
            with sd.RawInputStream(samplerate=self.sample_rate, channels=1, dtype='int16',
                                  blocksize=self.frame_size) as stream:

                num_silent_frames = 0
                max_silent_frames = int(1000 / self.frame_duration_ms) # 1 second of silence to trigger

                # Counter for logging periodically
                frame_count = 0

                while not self.stop_event.is_set() and self.is_listening:
                    frame, overflowed = stream.read(self.frame_size)
                    frame_count += 1

                    if overflowed:
                        logger.debug("Audio input overflowed.")

                    is_speech = self.vad.is_speech(frame, self.sample_rate)

                    # Periodic heartbeat log
                    if frame_count % 100 == 0:
                        logger.debug(f"Monitor heartbeat: frames={frame_count}, is_speech={is_speech}, triggered={self.triggered}")

                    if not self.triggered:
                        self.buffer.append(frame)
                        if is_speech:
                            logger.info("Speech detected! Recording...")
                            self.triggered = True
                            self.voiced_frames.extend(list(self.buffer))
                            self.buffer.clear()
                            num_silent_frames = 0
                    else:
                        self.voiced_frames.append(frame)
                        if not is_speech:
                            num_silent_frames += 1
                        else:
                            num_silent_frames = 0

                        if num_silent_frames > max_silent_frames:
                            # User stopped speaking
                            logger.info("Speech ended. Processing segment...")
                            self.triggered = False
                            full_audio = b"".join(self.voiced_frames)
                            self.voiced_frames = []

                            # Process the segment
                            if len(full_audio) > self.sample_rate * 0.5: # Min 0.5s of audio
                                self._process_segment(full_audio)
                            else:
                                logger.info("Segment too short, skipping.")

        except Exception as e:
            logger.error(f"Error in Voice Monitor loop: {e}")
            self.is_listening = False

    def _process_segment(self, audio_bytes):
        # Convert bytes to float32 numpy array as expected by faster-whisper
        audio_int16 = np.frombuffer(audio_bytes, dtype=np.int16)
        audio_float32 = audio_int16.astype(np.float32) / 32768.0

        # Call the callback (which should handle STT and AI response)
        # We run this in a separate thread to not block the listener
        threading.Thread(target=self.callback, args=(audio_float32,), daemon=True).start()
