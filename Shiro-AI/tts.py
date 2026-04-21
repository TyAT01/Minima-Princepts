"""
tts.py — Shiro's Kokoro Voice Engine
==========================================
Sentence-streaming TTS using Kokoro-ONNX.
Feeds audio to sounddevice the moment each sentence is ready.
Fully interrupt-safe. Strips all non-speech artefacts from Shiro's
output before synthesis (asterisk actions, brackets, markdown, inner-mind leaks).

VRAM budget (RTX 3070 / 8GB):
  Ollama LLM 32L  ~4.1 GB  (all 32 layers on GPU — capped at 4GB via OLLAMA_MAX_VRAM)
  Whisper STT     ~0.2 GB  (on demand, CPU)
  Kokoro ONNX     ~0.1-0.2 GB (CPU/GPU depending on ONNX provider)
  Peak total      ~4.5 GB  ← Significant headroom gained over GPT-SoVITS

Config (config.yaml):
  tts:
    enabled:      true
    model_path:   "kokoro/kokoro-v0_19.onnx"
    voices_path:  "kokoro/voices.bin"
    voice:        "shiro"  # Reference to the 0.25*af_heart + 0.75*jf_alpha mix
    language:     "en-us"
    speed:        0.85
    volume:       1.0
    device:       "cuda"
"""

from __future__ import annotations

import io
import logging
import queue
import re
import threading
import time
from typing import Optional, Tuple

import numpy as np
import sounddevice as sd
import soundfile as sf
from kokoro_onnx import Kokoro

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
#  Text cleaning — strip everything that shouldn't be spoken aloud
# ──────────────────────────────────────────────────────────────────────────────

_STRIP_PATTERNS = re.compile(
    r'\*[^*]*\*'                        # *action blocks*
    r'|\[[^\]]*\]'                      # [system blocks]
    r'|\+--[^\n]*'                      # +-- inner mind headers
    r'|^\s*#{1,6}\s+.*$'               # ## markdown headers
    r'|`[^`]*`'                         # `inline code`
    r'|<[^>]+>'                         # <xml/html tags>
    r'|\([^)]*LOG[^)]*\)'              # (LOG: ...) artefacts
    r'|\(LOG:[^)]*\)'                   # (LOG:...) variant
    , re.MULTILINE
)

_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+(?=[A-Za-z\u3040-\u9fff])')
_WHITESPACE = re.compile(r'\s{2,}')
_MISSING_SPACE = re.compile(r'([a-z])([A-Z])')
_MISSING_SPACE_PUNCT = re.compile(r'([.!?…,])([a-zA-Z])')
_EMPHASIS_QUOTES = re.compile(r"(?<=\s)'([^'\n]{1,30})'(?=[\s.,!?;:]|$)")
_EMPHASIS_QUOTES_START = re.compile(r"^'([^'\n]{1,30})'(?=[\s.,!?;:])")
_EM_DASH = re.compile(r'\s*—\s*')

def clean_for_tts(text: str) -> str:
    text = _STRIP_PATTERNS.sub(' ', text)
    text = _EMPHASIS_QUOTES.sub(r'\1', text)
    text = _EMPHASIS_QUOTES_START.sub(r'\1', text)
    text = _EM_DASH.sub(', ', text)
    text = _MISSING_SPACE.sub(r'\1 \2', text)
    text = _MISSING_SPACE_PUNCT.sub(r'\1 \2', text)
    text = _WHITESPACE.sub(' ', text)
    return text.strip()

def split_sentences(text: str) -> list[str]:
    text = clean_for_tts(text)
    if not text:
        return []
    raw = _SENTENCE_SPLIT.split(text.strip())
    raw = [p.strip() for p in raw if p.strip()]
    if len(raw) <= 1:
        return raw
    result: list[str] = []
    for part in raw:
        wc = len(re.findall(r'\b\w+\b', part))
        if wc < 3 and result:
            result[-1] = result[-1] + " " + part
        else:
            result.append(part)
    return result

# ──────────────────────────────────────────────────────────────────────────────
#  ShiroTTS (Kokoro Edition)
# ──────────────────────────────────────────────────────────────────────────────

class ShiroTTS:
    DEFAULTS = {
        "enabled":     True,
        "model_path":  "kokoro/kokoro-v0_19.onnx",
        "voices_path": "kokoro/voices.bin",
        "voice":       "shiro",
        "language":    "en-us",
        "speed":       0.85,
        "volume":      1.0,
    }

    def __init__(self, config: dict):
        cfg = {**self.DEFAULTS, **config}
        self.enabled     = bool(cfg["enabled"])
        self.model_path  = cfg["model_path"]
        self.voices_path = cfg["voices_path"]
        self.voice_name  = cfg["voice"]
        self.language    = cfg["language"]
        self.speed       = float(cfg["speed"])
        self.volume      = float(cfg["volume"])

        self._kokoro: Optional[Kokoro] = None
        self._voice_style = None

        self._queue: queue.Queue[Optional[str]] = queue.Queue()
        self._interrupt  = threading.Event()
        self._is_playing = threading.Event()
        self._running    = False
        self._worker: Optional[threading.Thread] = None

        self._buf = ""
        self._short_hold = ""
        self._MIN_STREAM_WORDS = 4
        self._muted = False

        self._base_speed = self.speed
        self._mood_speed_offset = 0.0

    @property
    def is_playing(self) -> bool:
        """Alias for is_speaking used by some modules."""
        return self._is_playing.is_set()

    @property
    def is_speaking(self) -> bool:
        """Compatibility property for ShiroEngine/DiscordBridge."""
        return self._is_playing.is_set()

    # ── Mood-responsive prosody ───────────────────────────────────────────────

    _MOOD_SPEED_MAP = {
        "excited":    +0.12,
        "playful":    +0.08,
        "engaged":    +0.05,
        "curious":    +0.03,
        "attentive":  +0.02,
        "motivated":  +0.04,
        "bold":       +0.05,
        "content":    +0.00,
        "neutral":    +0.00,
        "reflective": -0.04,
        "quiet":      -0.06,
        "pensive":    -0.07,
        "melancholy": -0.10,
        "uncertain":  -0.05,
        "anxious":    +0.06,
        "withdrawn":  -0.08,
        "fatigued":   -0.06,
    }

    _MOOD_VOLUME_MAP = {
        "excited":    +0.05,
        "playful":    +0.03,
        "engaged":    +0.02,
        "melancholy": -0.05,
        "withdrawn":  -0.08,
        "quiet":      -0.04,
        "neutral":    +0.00,
    }

    def set_mood_speed(self, mood_label: str) -> None:
        target_offset = self._MOOD_SPEED_MAP.get(mood_label.lower(), 0.0)
        target_speed  = max(0.65, min(1.35, self._base_speed + target_offset))

        smoothed = self.speed + 0.30 * (target_speed - self.speed)
        if abs(smoothed - self.speed) > 0.005:
            self.speed = round(smoothed, 3)
            logger.debug(f"[TTS] Prosody: mood={mood_label!r} → speed={self.speed:.3f}")

        _vol_offset = self._MOOD_VOLUME_MAP.get(mood_label.lower(), 0.0)
        _base_vol   = getattr(self, '_base_volume', self.volume)
        if not hasattr(self, '_base_volume'):
            self._base_volume = self.volume
        _target_vol = max(0.70, min(1.20, _base_vol + _vol_offset))
        self.volume = round(self.volume + 0.20 * (_target_vol - self.volume), 3)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def _init_kokoro(self):
        """Lazy init Kokoro and set up Shiro's voice style."""
        if self._kokoro is not None:
            return True
        try:
            logger.info(f"Initializing Kokoro TTS (Model: {self.model_path})")
            self._kokoro = Kokoro(self.model_path, self.voices_path)

            # Setup Shiro's custom voice mix: 0.25*af_heart + 0.75*jf_alpha
            v1 = self._kokoro.get_voice_style("af_heart")
            v2 = self._kokoro.get_voice_style("jf_alpha")
            self._voice_style = (0.25 * v1) + (0.75 * v2)

            logger.info("Kokoro TTS ready. Shiro voice mix initialized.")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize Kokoro TTS: {e}")
            self.enabled = False
            return False

    def start(self):
        if not self.enabled or self._running:
            return
        if not self._init_kokoro():
            return

        self._running = True
        self._worker = threading.Thread(
            target=self._loop, daemon=True, name="ShiroTTS"
        )
        self._worker.start()
        logger.info("ShiroTTS worker started.")

    def stop(self):
        if not self._running:
            return
        self._running = False
        self.interrupt()
        self._queue.put(None)
        if self._worker:
            self._worker.join(timeout=4)
        logger.info("ShiroTTS stopped.")

    # ── Public interface ──────────────────────────────────────────────────────

    def feed(self, fragment: str):
        if not self.enabled: return
        stripped = fragment.strip()
        if not stripped: return

        if stripped[-1] in '.!?':
            if self._buf.strip():
                combined = self._buf + fragment
                parts = _SENTENCE_SPLIT.split(combined.strip())
                parts = [p.strip() for p in parts if p.strip()]
                for p in parts[:-1]:
                    self._enqueue(p)
                last = parts[-1] if parts else ""
                if last and last[-1] in '.!?':
                    self._enqueue(last)
                    self._buf = ""
                else:
                    self._buf = last
            else:
                self._enqueue(clean_for_tts(stripped))
            return

        self._buf += fragment
        parts = _SENTENCE_SPLIT.split(self._buf.strip())
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) > 1:
            emitted_any = False
            for p in parts[:-1]:
                wc = len(re.findall(r'\b\w+\b', p))
                if wc >= self._MIN_STREAM_WORDS:
                    self._enqueue(p)
                    emitted_any = True
                else:
                    self._buf = p + " " + parts[-1]
                    return
            if emitted_any:
                self._buf = parts[-1]

    def flush(self):
        if not self.enabled: return
        tail = self._buf.strip()
        if self._short_hold:
            tail = (tail + " " + self._short_hold).strip() if tail else self._short_hold
            self._short_hold = ""
        self._buf = ""
        if not tail: return
        tail = clean_for_tts(tail)
        if not tail: return
        if re.search(r'[a-zA-Z0-9\u3040-\u9fff]', tail):
            self._queue.put(tail)

    def speak(self, text: str):
        if not self.enabled: return
        for sentence in split_sentences(text):
            self._enqueue(sentence)

    def interrupt(self):
        self._interrupt.set()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._buf = ""
        self._short_hold = ""
        sd.stop()

    def synthesize_to_bytes(self, text: str) -> Optional[bytes]:
        """Synthesize text to raw PCM bytes for Discord."""
        if not self.enabled: return None
        if not self._init_kokoro(): return None

        text = clean_for_tts(text)
        if not text: return None

        try:
            audio, sr = self._kokoro.create(
                text,
                voice=self._voice_style,
                speed=self.speed,
                lang='a'
            )
            # Kokoro returns float32, Discord wants int16 PCM
            # Assuming sr is 24000
            pcm16 = (audio * 32767).astype(np.int16)
            return pcm16.tobytes()
        except Exception as e:
            logger.error(f"synthesize_to_bytes failed: {e}")
            return None

    # ── Internal ──────────────────────────────────────────────────────────────

    def _enqueue(self, text: str):
        text = text.strip()
        if not text: return
        if self._short_hold:
            text = self._short_hold + " " + text
            self._short_hold = ""

        if not re.search(r'[a-zA-Z0-9\u3040-\u9fff]', text):
            return

        word_count = len(re.findall(r'\b\w+\b', text))
        if word_count < 3 and len(text) < 14:
            self._short_hold = text
            return

        self._queue.put(text)

    def _loop(self):
        audio_queue: queue.Queue = queue.Queue(maxsize=2)

        def synth_worker():
            while self._running:
                try:
                    sentence = self._queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                if sentence is None: break
                if self._interrupt.is_set(): continue
                try:
                    result = self._synthesise(sentence)
                    if result and not self._interrupt.is_set():
                        audio_queue.put(result, timeout=60)
                except Exception as e:
                    logger.error(f"Kokoro synth error: {e}")

        synth_thread = threading.Thread(
            target=synth_worker, daemon=True, name="ShiroTTS-synth"
        )
        synth_thread.start()

        while self._running:
            try:
                item = audio_queue.get(timeout=0.2)
            except queue.Empty:
                if self._interrupt.is_set() and self._queue.empty():
                    self._interrupt.clear()
                continue
            if item is None: continue
            audio, sr = item
            if audio is not None and not self._interrupt.is_set():
                try:
                    if not self._muted:
                        self._play(audio, sr)
                except Exception as e:
                    logger.error(f"TTS play error: {e}")
            if self._interrupt.is_set():
                while not audio_queue.empty():
                    try: audio_queue.get_nowait()
                    except queue.Empty: break
                self._interrupt.clear()

        synth_thread.join(timeout=2)

    def _synthesise(self, text: str) -> tuple[Optional[np.ndarray], int]:
        if self._interrupt.is_set() or not self._kokoro:
            return None, 0

        logger.debug(f"Kokoro synth → '{text[:50]}...'")
        t0 = time.perf_counter()

        try:
            # lang='a' for American English (consistent with af_heart base)
            # speed=self.speed
            audio, sr = self._kokoro.create(
                text,
                voice=self._voice_style,
                speed=self.speed,
                lang='a'
            )
        except Exception as e:
            logger.error(f"Kokoro synthesis failed: {e}")
            return None, 0

        logger.debug("Kokoro synthesis: %.2fs for %d chars", time.perf_counter() - t0, len(text))

        if self.volume != 1.0:
            audio = np.clip(audio * self.volume, -1.0, 1.0)

        return audio, sr

    _FADE_MS = 15

    def _apply_fade(self, audio: np.ndarray, sr: int) -> np.ndarray:
        fade_samples = min(int(sr * self._FADE_MS / 1000), len(audio) // 4)
        if fade_samples < 2: return audio
        fade_out = np.linspace(1.0, 0.0, fade_samples, dtype=np.float32)
        result = audio.copy()
        if audio.ndim == 1:
            result[-fade_samples:] *= fade_out
        else:
            result[-fade_samples:] *= fade_out[:, np.newaxis]
        return result

    def _play(self, audio: np.ndarray, sr: int):
        if self._interrupt.is_set(): return
        audio = self._apply_fade(audio, sr)
        self._is_playing.set()
        try:
            sd.play(audio, samplerate=sr)
            duration = len(audio) / sr
            elapsed = 0.0
            interval = 0.05
            while elapsed < duration + 0.02:
                if self._interrupt.is_set():
                    sd.stop()
                    return
                time.sleep(interval)
                elapsed += interval
            sd.wait()
            if not self._interrupt.is_set():
                time.sleep(0.02)
        finally:
            self._is_playing.clear()


def check_tts_server(model_path: str) -> bool:
    """Check if Kokoro model exists on disk."""
    import os
    return os.path.exists(model_path)
