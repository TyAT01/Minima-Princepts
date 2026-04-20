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
                except Exception:
                    raise e
            else:
                raise e
        else:
            raise e
else:
    from faster_whisper import WhisperModel

# Silero VAD — lightweight neural VAD, runs <1ms/chunk on CPU
# Replaces webrtcvad: better accuracy, no false positives on background noise
try:
    import torch as _silero_torch
    _silero_model, _silero_utils = _silero_torch.hub.load(
        repo_or_dir='snakers4/silero-vad',
        model='silero_vad',
        force_reload=False,
        verbose=False,
    )
    _silero_get_speech_ts = _silero_utils[0]  # get_speech_timestamps helper
    _SILERO_AVAILABLE = True
except Exception:
    _SILERO_AVAILABLE = False
    _silero_model = None

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

        segments, info = self.model.transcribe(
            audio_source,
            beam_size=5,
            language="en",
            vad_filter=False,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()

        # Block non-latin scripts (Georgian, Cyrillic, etc.) — Whisper hallucination
        import unicodedata
        letter_chars = [c for c in text if unicodedata.category(c).startswith('L')]
        if letter_chars:
            latin_ratio = sum(1 for c in letter_chars if ord(c) < 0x0590) / len(letter_chars)
            if latin_ratio < 0.7:
                logger.warning(f"[STT] Non-latin script filtered: {text!r}")
                return ""

        # Filter known Whisper hallucinations
        HALLUCINATIONS = {
            "thank you for watching", "thanks for watching",
            "please subscribe", "like and subscribe", ".", "..", "...",
        }
        if text.lower().strip(".! ") in HALLUCINATIONS:
            return ""

        # Filter suspiciously repetitive output
        words = text.split()
        if len(words) >= 4 and len(set(w.lower() for w in words)) <= 2:
            return ""

        # Filter superscript/subscript garbage (ᶦᶦᶦᶦ)
        if text and all(unicodedata.category(c) in ('Lm', 'Sk', 'So', 'Lo') or ord(c) > 0x2000 for c in text.replace(' ','')):
            logger.warning(f"[STT] Garbage script filtered: {text!r}")
            return ""

        return text

class VoiceMonitor:
    """Background monitor that captures audio from the default mic and segments speech."""

    def __init__(self, callback, sample_rate=16000, frame_duration_ms=30, interrupt_callback=None,
                 energy_threshold=300):
        self.callback = callback
        self.interrupt_callback = interrupt_callback
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.frame_size = int(sample_rate * frame_duration_ms / 1000)
        # FIX: Energy VAD threshold is now configurable. Default 300 RMS works
        # well in a quiet room. Increase (500-800) for noisy environments,
        # decrease (100-200) for very quiet speakers.
        self.energy_threshold = energy_threshold

        if _SILERO_AVAILABLE:
            self.vad = _silero_model
            logger.info("Silero VAD initialized — neural speech detection active.")
        else:
            self.vad = None
            logger.warning("Silero VAD not available. Using Energy-based fallback VAD.")

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

    def _is_speech(self, frame_bytes):
        """Detects speech using Silero VAD (neural) or Energy-based fallback."""
        if self.vad is not None and _SILERO_AVAILABLE:
            try:
                # Silero expects float32 tensor normalised to [-1, 1]
                audio_int16 = np.frombuffer(frame_bytes, dtype=np.int16)
                audio_f32 = _silero_torch.from_numpy(
                    audio_int16.astype(np.float32) / 32768.0
                )
                confidence = self.vad(audio_f32, self.sample_rate).item()
                return confidence > 0.5
            except Exception as e:
                logger.debug(f"Silero VAD error: {e}")
                # Fall through to energy detection

        # Energy-based VAD (RMS) fallback
        audio_data = np.frombuffer(frame_bytes, dtype=np.int16)
        rms = np.sqrt(np.mean(audio_data.astype(np.float32)**2))
        return rms > self.energy_threshold

    def _listen_loop(self):
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

                    is_speech = self._is_speech(frame)

                    # Periodic heartbeat log
                    if frame_count % 100 == 0:
                        logger.debug(f"Monitor heartbeat: frames={frame_count}, is_speech={is_speech}, triggered={self.triggered}")

                    if not self.triggered:
                        self.buffer.append(frame)
                        if is_speech:
                            logger.info("Speech detected! Recording...")
                            if self.interrupt_callback:
                                self.interrupt_callback()
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
        # Dedup guard — skip if this exact audio was already sent
        # (can happen if VAD triggers twice on the same utterance boundary)
        import hashlib
        seg_hash = hashlib.md5(audio_bytes[:512]).hexdigest()
        if getattr(self, '_last_seg_hash', None) == seg_hash:
            logger.debug("[VoiceMonitor] Duplicate segment skipped")
            return
        self._last_seg_hash = seg_hash

        # Convert bytes to float32 numpy array as expected by faster-whisper
        audio_int16 = np.frombuffer(audio_bytes, dtype=np.int16)
        audio_float32 = audio_int16.astype(np.float32) / 32768.0

        # Call the callback (which should handle STT and AI response)
        # We run this in a separate thread to not block the listener
        threading.Thread(target=self.callback, args=(audio_float32,), daemon=True).start()