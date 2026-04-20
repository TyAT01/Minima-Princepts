"""
tts.py — Shiro's GPT-SoVITS Voice Engine
==========================================
Sentence-streaming TTS. Feeds audio to sounddevice the moment each sentence
is ready. Fully interrupt-safe. Strips all non-speech artefacts from Shiro's
output before synthesis (asterisk actions, brackets, markdown, inner-mind leaks).

VRAM budget (RTX 3070 / 8GB):
  Ollama LLM 32L  ~4.1 GB  (all 32 layers on GPU — capped at 4GB via OLLAMA_MAX_VRAM)
  Whisper STT     ~0.2 GB  (on demand, CPU)
  GPT-SoVITS fp16 ~0.8-1.1 GB  (only while actively synthesising)
  Peak total      ~5.1-5.3 GB  ← comfortable headroom on 8GB

GPT-SoVITS runs as a separate process (api_v2.py server on port 9880).
This module calls it over HTTP — zero coupling to Ollama's VRAM context.

Config (config.yaml):
  tts:
    enabled:      true
    api_url:      "http://127.0.0.1:9880"
    ref_audio:    "tts_reference/shiro_reference.wav"
    ref_text:     "The exact text spoken in the reference clip."
    language:     "en"
    speed:        1.0
    volume:       1.0
    device:       "cuda"    # "cpu" to save all VRAM if needed
    timeout:      15
"""

from __future__ import annotations

import io
import logging
import queue
import re
import threading
import time
from typing import Optional

import numpy as np
import requests
import sounddevice as sd
import soundfile as sf
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
#  Text cleaning — strip everything that shouldn't be spoken aloud
# ──────────────────────────────────────────────────────────────────────────────

# Patterns stripped before synthesis
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

# Sentence boundary: split on hard sentence-ending punctuation (.!?) followed
# by whitespace + any letter (upper OR lower — Shiro writes mostly lowercase).
# NOTE: Do NOT split on … (ellipsis) — Shiro uses it for dramatic pauses mid-clause.
# GPT-SoVITS already handles … internally; splitting here creates tiny fragments
# like "well…" (0.3s) that sound like audio glitches.
# After splitting, split_sentences() merges short chunks (< 5 words) with neighbours
# to prevent GPT-SoVITS receiving micro-fragments that sound clipped.
_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+(?=[A-Za-z\u3040-\u9fff])')

# Collapse multiple spaces/newlines left after stripping
_WHITESPACE = re.compile(r'\s{2,}')

# Missing space between word boundary and letter after punctuation e.g. "andactually"
_MISSING_SPACE = re.compile(r'([a-z])([A-Z])')
# Missing space after punctuation before a letter e.g. "fast!she"
_MISSING_SPACE_PUNCT = re.compile(r'([.!?…,])([a-zA-Z])')

# Emphasis single-quotes: 'word' or 'phrase' used for scare quotes / air quotes
# GPT-SoVITS treats the closing ' as a sentence boundary, causing an audio cut.
# Strip the quotes but keep the word. Preserves contractions (don't, it's, I'll)
# because those have no space before the opening quote.
# Pattern matches: space/start + ' + 1-30 chars (no newline) + ' + space/punct/end
_EMPHASIS_QUOTES = re.compile(r"(?<=\s)'([^'\n]{1,30})'(?=[\s.,!?;:]|$)")
_EMPHASIS_QUOTES_START = re.compile(r"^'([^'\n]{1,30})'(?=[\s.,!?;:])")

# Em-dash — replace with a comma+space so GPT-SoVITS gets a natural breath
_EM_DASH = re.compile(r'\s*—\s*')


def clean_for_tts(text: str) -> str:
    """Remove non-speech artefacts from Shiro's LLM output."""
    text = _STRIP_PATTERNS.sub(' ', text)
    # Strip emphasis single-quotes: 'spiritual' → spiritual, 'deep' → deep
    # This prevents GPT-SoVITS from treating the closing apostrophe as a sentence split.
    text = _EMPHASIS_QUOTES.sub(r'\1', text)
    text = _EMPHASIS_QUOTES_START.sub(r'\1', text)
    # Replace em-dash with comma pause — sounds more natural than a hard stop
    text = _EM_DASH.sub(', ', text)
    # Fix missing spaces at word/punctuation boundaries (engine streaming glitch)
    text = _MISSING_SPACE.sub(r'\1 \2', text)
    text = _MISSING_SPACE_PUNCT.sub(r'\1 \2', text)
    # NOTE: Do NOT replace commas with … here.
    # GPT-SoVITS internally splits on … as a sentence boundary, which causes
    # mid-clause audio cuts (e.g. "well…" becomes a separate ~0.3s fragment).
    # Commas are handled naturally by GPT-SoVITS prosody — leave them alone.
    text = _WHITESPACE.sub(' ', text)
    return text.strip()


def split_sentences(text: str) -> list[str]:
    """
    Split cleaned text into TTS-ready sentence chunks.

    Used by speak() and flush() for pre-built complete strings.
    Splits on .!? + any letter, then merges short trailing chunks
    upward so GPT-SoVITS never receives micro-fragments alone.
    """
    text = clean_for_tts(text)
    if not text:
        return []
    raw = _SENTENCE_SPLIT.split(text.strip())
    raw = [p.strip() for p in raw if p.strip()]
    if len(raw) <= 1:
        return raw
    # Merge short final chunks up into their predecessor
    result: list[str] = []
    for part in raw:
        wc = len(re.findall(r'\b\w+\b', part))
        if wc < 3 and result:
            result[-1] = result[-1] + " " + part
        else:
            result.append(part)
    return result


# ──────────────────────────────────────────────────────────────────────────────
#  ShiroTTS
# ──────────────────────────────────────────────────────────────────────────────

class ShiroTTS:
    """
    Real-time TTS for Shiro via a local GPT-SoVITS API server.

    Lifecycle
    ---------
    tts = ShiroTTS(config_dict)
    tts.start()                  # launch worker thread

    # Stream LLM fragments as they arrive:
    tts.feed("hey,")
    tts.feed(" you're back.")
    tts.feed(" I was thinking about you.")
    tts.flush()                  # push any remaining partial sentence

    # Or queue a full string at once:
    tts.speak("hey. what took you so long?")

    # Interrupt mid-playback (user speaks):
    tts.interrupt()

    tts.stop()                   # clean shutdown
    """

    DEFAULTS = {
        "enabled":   True,
        "api_url":   "http://127.0.0.1:9880",
        "ref_audio": "tts_reference/shiro_reference.wav",
        "ref_text":  "",
        "language":  "en",
        "speed":     1.0,
        "volume":    1.0,
        "device":    "cuda",
        "timeout":   40,  # raised from 15s — SoVITS under VRAM pressure can take 20-30s
    }

    def __init__(self, config: dict):
        cfg = {**self.DEFAULTS, **config}
        self.enabled   = bool(cfg["enabled"])
        self.api_url   = cfg["api_url"].rstrip("/")
        self.ref_audio = cfg["ref_audio"]
        self.ref_text  = cfg["ref_text"]
        self.language  = cfg["language"]
        self.speed     = float(cfg["speed"])
        self.volume    = float(cfg["volume"])
        self.device    = cfg["device"]
        self.timeout   = int(cfg["timeout"])

        self._queue: queue.Queue[Optional[str]] = queue.Queue()
        self._interrupt  = threading.Event()
        self._is_playing = threading.Event()
        self._running    = False
        self._worker: Optional[threading.Thread] = None

        # Partial buffer — accumulates LLM token fragments until a sentence boundary
        self._buf = ""
        # Short-fragment hold — last-resort merge buffer in _enqueue (separate from _buf)
        self._short_hold = ""
        # Minimum word count for a sentence split during streaming (prevents micro-fragments)
        self._MIN_STREAM_WORDS = 4
        self._muted = False  # set True by Discord bridge when in voice channel

        # Mood-responsive prosody — speed varies with emotional state
        # Base speed comes from config; mood offsets are applied on top
        self._base_speed = self.speed
        self._mood_speed_offset = 0.0  # set by set_mood_speed()

        # Cancellable HTTP session — replaced on each interrupt
        self._session = requests.Session()

    # ── Mood-responsive prosody ───────────────────────────────────────────────

    # Maps mood names to speed offsets relative to base speed
    _MOOD_SPEED_MAP = {
        "excited":    +0.14,
        "playful":    +0.10,
        "engaged":    +0.06,
        "curious":    +0.04,
        "attentive":  +0.03,
        "motivated":  +0.05,
        "bold":       +0.06,
        "content":    +0.00,
        "neutral":    +0.00,
        "reflective": -0.05,
        "quiet":      -0.08,
        "pensive":    -0.09,
        "melancholy": -0.12,
        "uncertain":  -0.06,
        "anxious":    +0.08,   # anxiety = faster, not slower
        "withdrawn":  -0.10,
        "fatigued":   -0.08,
    }

    # Volume micro-offsets — subtle variation so voice doesn't feel robotic
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
        """
        Adjust TTS playback speed and volume based on Shiro's current mood.
        Uses exponential smoothing so transitions feel natural, not jarring.
        Clamps speed to [0.72, 1.28] and volume to [0.75, 1.15].
        """
        target_offset = self._MOOD_SPEED_MAP.get(mood_label.lower(), 0.0)
        target_speed  = max(0.72, min(1.28, self._base_speed + target_offset))

        # Exponential smoothing — 30% toward target per call (gradual transitions)
        smoothed = self.speed + 0.30 * (target_speed - self.speed)
        if abs(smoothed - self.speed) > 0.005:
            self.speed = round(smoothed, 3)
            logger.debug(f"[TTS] Prosody: mood={mood_label!r} → speed={self.speed:.3f}")

        # Volume micro-variation
        _vol_offset = self._MOOD_VOLUME_MAP.get(mood_label.lower(), 0.0)
        _base_vol   = getattr(self, '_base_volume', self.volume)
        if not hasattr(self, '_base_volume'):
            self._base_volume = self.volume
        _target_vol = max(0.75, min(1.15, _base_vol + _vol_offset))
        self.volume = round(self.volume + 0.20 * (_target_vol - self.volume), 3)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        if not self.enabled or self._running:
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
        self._queue.put(None)  # poison pill
        if self._worker:
            self._worker.join(timeout=4)
        logger.info("ShiroTTS stopped.")

    # ── Public interface ──────────────────────────────────────────────────────

    def feed(self, fragment: str):
        """
        Feed a streaming LLM fragment and synthesize as fast as possible.

        Fast path: fragment already ends with .!? — enqueue immediately so
        synthesis starts on sentence 1 while the LLM generates sentence 2.

        Slow path: accumulate partial tokens. Only split off a completed sentence
        if it has enough words (>= MIN_STREAM_WORDS) — this prevents the splitter
        from emitting a short first sentence ("I see.") while the rest is still
        streaming, which would cause GPT-SoVITS to synthesise a tiny fragment
        before the main reply.
        """
        if not self.enabled:
            return

        stripped = fragment.strip()
        if not stripped:
            return

        # Fast path: already a sentence boundary
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

        # Slow path: accumulate
        self._buf += fragment
        parts = _SENTENCE_SPLIT.split(self._buf.strip())
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) > 1:
            # Only emit completed parts that are substantial enough
            emitted_any = False
            for p in parts[:-1]:
                wc = len(re.findall(r'\b\w+\b', p))
                if wc >= self._MIN_STREAM_WORDS:
                    self._enqueue(p)
                    emitted_any = True
                else:
                    # Short completed part — keep in buf merged with remainder
                    self._buf = p + " " + parts[-1]
                    return
            if emitted_any:
                self._buf = parts[-1]

    def flush(self):
        """
        Push any remaining buffered text to the synthesis queue.
        This is the end of a response — force-enqueue whatever remains,
        even if it's short (e.g. a lone "Yes." or "Hmm.").
        """
        if not self.enabled:
            return
        # Collect everything: _buf + any held short fragment
        tail = self._buf.strip()
        if self._short_hold:
            tail = (tail + " " + self._short_hold).strip() if tail else self._short_hold
            self._short_hold = ""
        self._buf = ""
        if not tail:
            return
        tail = clean_for_tts(tail)
        if not tail:
            return
        # Force-queue directly — bypass the short-hold guard since this is
        # the final flush and there is nothing more coming after it.
        if re.search(r'[a-zA-Z0-9\u3040-\u9fff]', tail):
            self._queue.put(tail)

    def speak(self, text: str):
        """Queue an entire pre-built string for synthesis (used for autonomous speech)."""
        if not self.enabled:
            return
        for sentence in split_sentences(text):
            self._enqueue(sentence)

    def interrupt(self):
        """Stop playback immediately, cancel any in-flight HTTP request, drain queue."""
        self._interrupt.set()
        # Close current session — this aborts any in-flight requests.get() call
        try:
            self._session.close()
        except Exception:
            pass
        self._session = requests.Session()
        # Drain queue
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._buf = ""
        self._short_hold = ""
        sd.stop()
        logger.debug("TTS interrupted.")

    @property
    def is_speaking(self) -> bool:
        return self._is_playing.is_set()

    @property
    def is_active(self) -> bool:
        """True if currently playing OR has items queued for synthesis."""
        return self._is_playing.is_set() or not self._queue.empty()

    def wait_until_done(self, timeout: float = 8.0):
        """
        Block until all queued audio has finished playing.
        Used before starting a new response synthesis to prevent GPT-SoVITS
        from receiving concurrent HTTP requests (causes robotic audio artifacts).
        Returns when queue is empty AND is_playing is False, or timeout expires.
        """
        if not self.enabled or self._muted:
            return  # Nothing to wait for
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.is_active:
                return
            time.sleep(0.05)
        # Timeout — log but don't block forever
        logger.debug("[TTS] wait_until_done timed out after %.1fs", timeout)

    def set_muted(self, muted: bool):
        """
        Mute or unmute local audio output.
        When muted=True: TTS still synthesizes (for Discord voice) but does
        NOT play through local speakers. The feed/flush/speak pipeline still
        queues items — they just get discarded at play time.
        Discord bridge calls set_muted(True) on join, set_muted(False) on leave.
        """
        self._muted = muted
        if muted:
            # Interrupt any currently-playing local audio immediately
            sd.stop()
            logger.info("[ShiroTTS] Local audio muted.")
        else:
            logger.info("[ShiroTTS] Local audio unmuted.")

    # ── Discord TTS: synthesize full reply to WAV bytes ───────────────────────

    # Discord voice requires exactly this format:
    DISCORD_SAMPLE_RATE = 48000
    DISCORD_CHANNELS    = 2      # stereo
    DISCORD_SAMPWIDTH   = 2      # 16-bit

    def synthesize_to_bytes(self, text: str) -> Optional[bytes]:
        """
        Synthesize text to RAW PCM bytes for discord.PCMAudio.
        Discord requires: 48000 Hz, stereo, 16-bit signed LE — no WAV header.
        GPT-SoVITS returns: ~32000 Hz mono float32 — we resample here.
        Returns raw PCM bytes or None on failure.
        """
        if not self.enabled:
            return None

        # Synthesise each sentence and collect raw float32 audio
        all_audio = []
        sr_out = None
        for sentence in split_sentences(text):
            result = self._synthesise(sentence)
            if result and result[0] is not None:
                audio, sr = result
                if sr_out is None:
                    sr_out = sr
                # Ensure mono
                if audio.ndim == 2:
                    audio = audio.mean(axis=1)
                all_audio.append(audio)

        if not all_audio or sr_out is None:
            logger.warning('[Discord TTS] synthesize_to_bytes: no audio produced')
            return None

        combined = np.concatenate(all_audio)  # mono float32 at sr_out Hz

        # Resample to Discord's required 48000 Hz if needed.
        # FIX: prefer soxr (in requirements.txt) over scipy (not required).
        # Fallback chain: soxr → scipy → crude index resample.
        if sr_out != self.DISCORD_SAMPLE_RATE:
            try:
                import soxr
                combined = soxr.resample(combined, sr_out, self.DISCORD_SAMPLE_RATE)
            except ImportError:
                try:
                    import scipy.signal as _sig
                    num_samples = int(len(combined) * self.DISCORD_SAMPLE_RATE / sr_out)
                    combined = _sig.resample(combined, num_samples).astype(np.float32)
                except ImportError:
                    # Last resort — crude integer resampling (lower quality)
                    ratio = self.DISCORD_SAMPLE_RATE / sr_out
                    indices = (np.arange(int(len(combined) * ratio)) / ratio).astype(int)
                    indices = np.clip(indices, 0, len(combined) - 1)
                    combined = combined[indices]

        # Mono → stereo (duplicate channel)
        stereo = np.stack([combined, combined], axis=1)  # shape: (N, 2)

        # float32 → int16 PCM
        pcm = (stereo * 32767).clip(-32768, 32767).astype(np.int16)

        # Return raw bytes — NO WAV header — discord.PCMAudio reads raw PCM
        return pcm.tobytes()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _enqueue(self, text: str):
        """
        Enqueue a cleaned sentence for synthesis.

        Short-fragment handling: split_sentences() already merges short chunks
        before they reach here, so most short-fragment cases are already handled
        upstream. This guard is a last-resort safety net for any fragment that
        slips through (e.g. a lone "Oh." from the fast-path).

        IMPORTANT: do NOT write back to self._buf here. self._buf belongs to the
        streaming accumulator in feed() — writing to it mid-stream corrupts ordering.
        Short fragments are held in self._short_hold and prepended to the next call.
        """
        text = text.strip()
        if not text:
            return
        # Merge with any held short fragment from a previous call
        if self._short_hold:
            text = self._short_hold + " " + text
            self._short_hold = ""

        # Reject pure-punctuation / symbol-only strings (GPT-SoVITS 400 error)
        if not re.search(r'[a-zA-Z0-9\u3040-\u9fff]', text):
            logger.debug(f'[TTS] Skipping punct-only fragment: {text!r}')
            return

        # Last-resort short-fragment guard — hold and merge with the next sentence.
        # Uses ALL word tokens (including 1-char words like 'I', 'a') so that
        # 'I did not!' (3 tokens) goes through, while 'Oh.' or 'well?' (1-2 tokens)
        # are held. split_sentences() and the streaming slow-path handle most cases;
        # this catches edge-cases from the fast-path.
        word_count = len(re.findall(r'\b\w+\b', text))
        if word_count < 3 and len(text) < 14:
            self._short_hold = text
            logger.debug(f'[TTS] Short fragment held for merge: {text!r}')
            return

        self._queue.put(text)

    def _loop(self):
        """Pipeline: synth thread feeds audio_queue while play thread drains it."""
        # maxsize=2: keep only 2 synthesised sentences buffered ahead of playback.
        # Prevents synth thread from blocking indefinitely on a full queue while
        # the play thread is busy — old maxsize=4 caused stalls and dropped sentences.
        audio_queue: queue.Queue = queue.Queue(maxsize=2)

        def synth_worker():
            while self._running:
                try:
                    sentence = self._queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                if sentence is None:
                    break  # stop() poison pill
                if self._interrupt.is_set():
                    self._queue.task_done() if hasattr(self._queue, 'task_done') else None
                    continue
                try:
                    result = self._synthesise(sentence)
                    if result and not self._interrupt.is_set():
                        try:
                            audio_queue.put(result, timeout=60)
                        except queue.Full:
                            logger.warning("[TTS] audio_queue full — dropping sentence to avoid stall")
                except Exception as e:
                    logger.error(f"TTS synth error: {e}")

        synth_thread = threading.Thread(
            target=synth_worker, daemon=True, name="ShiroTTS-synth"
        )
        synth_thread.start()

        while self._running:
            try:
                item = audio_queue.get(timeout=0.2)
            except queue.Empty:
                # After interrupt: clear flag here so next speak() works
                if self._interrupt.is_set() and self._queue.empty():
                    self._interrupt.clear()
                continue
            if item is None:
                continue  # skip stale None items — don't break
            audio, sr = item
            if audio is not None and not self._interrupt.is_set():
                try:
                    if not self._muted:
                        self._play(audio, sr)
                except Exception as e:
                    logger.error(f"TTS play error: {e}")
            # After interrupt, drain leftover audio and clear flag
            if self._interrupt.is_set():
                while not audio_queue.empty():
                    try: audio_queue.get_nowait()
                    except queue.Empty: break
                self._interrupt.clear()

        synth_thread.join(timeout=2)
        logger.info("ShiroTTS worker exited.")

    def _synthesise(self, text: str) -> tuple[Optional[np.ndarray], int]:
        """Call GPT-SoVITS API, return (float32_audio, sample_rate) or (None, 0)."""
        if self._interrupt.is_set():
            return None, 0

        params = {
            "text":           text,
            "text_lang":      self.language,
            "ref_audio_path": self.ref_audio,
            "prompt_text":    self.ref_text,
            "prompt_lang":    self.language,
            "speed_factor":   self.speed,
            "streaming_mode": False,
        }

        label = text[:50] + "…" if len(text) > 50 else text
        logger.debug(f"TTS synth → '{label}'")
        t0 = time.perf_counter()

        try:
            r = self._session.get(
                f"{self.api_url}/tts",
                params=params,
                timeout=self.timeout,
            )
            r.raise_for_status()
        except requests.exceptions.ConnectionError:
            logger.warning(
                "GPT-SoVITS server unreachable at %s — TTS silent. "
                "Is GPT-SoVITS\\start_api.bat running?", self.api_url
            )
            return None, 0
        except requests.exceptions.Timeout:
            logger.warning("GPT-SoVITS timed out for: '%s' — retrying once with extended timeout", label)
            try:
                r = self._session.get(
                    f"{self.api_url}/tts",
                    params=params,
                    timeout=self.timeout * 2,  # double timeout for retry
                )
                r.raise_for_status()
            except Exception as _retry_err:
                logger.warning("GPT-SoVITS retry also failed: %s", _retry_err)
                return None, 0
        except requests.exceptions.HTTPError as e:
            logger.warning("GPT-SoVITS HTTP error: %s", e)
            return None, 0

        logger.debug("TTS synthesis: %.2fs for %d chars", time.perf_counter() - t0, len(text))

        try:
            audio, sr = sf.read(io.BytesIO(r.content), dtype="float32")
        except Exception as e:
            logger.error("Failed to decode TTS audio: %s", e)
            return None, 0

        if self.volume != 1.0:
            audio = np.clip(audio * self.volume, -1.0, 1.0)

        return audio, sr

    # Short fade applied only to the END of each chunk to smooth inter-chunk joins.
    # Fade-IN is intentionally omitted: GPT-SoVITS already starts its audio with
    # a few ms of natural lead-in, and any additional fade-in attenuates the first
    # phoneme of the first word, which the ear perceives as the word being "cut off".
    _FADE_MS = 12  # milliseconds fade-out only

    def _apply_fade(self, audio: np.ndarray, sr: int) -> np.ndarray:
        """Apply a short linear fade-OUT only to smooth chunk boundaries.

        No fade-in: GPT-SoVITS includes natural lead-in silence.
        Adding fade-in causes perceived first-word clipping.
        """
        fade_samples = min(int(sr * self._FADE_MS / 1000), len(audio) // 4)
        if fade_samples < 2:
            return audio
        fade_out = np.linspace(1.0, 0.0, fade_samples, dtype=np.float32)
        result = audio.copy()
        if audio.ndim == 1:
            result[-fade_samples:] *= fade_out
        else:
            result[-fade_samples:] *= fade_out[:, np.newaxis]
        return result

    def _play(self, audio: np.ndarray, sr: int):
        """Play audio with fade-in/out to eliminate inter-chunk clicks.

        Uses sd.wait() as the primary completion signal rather than a manual
        elapsed-time loop — this prevents the next sentence from cutting off
        the tail of the current one when synthesis is faster than playback.
        A small post-play gap (20ms) ensures the speaker has fully settled
        before the next chunk starts.
        """
        if self._interrupt.is_set():
            return
        audio = self._apply_fade(audio, sr)
        self._is_playing.set()
        try:
            sd.play(audio, samplerate=sr)
            # Poll for interrupt at 50ms intervals rather than blocking on sd.wait()
            # so we can respond to interrupt() within ~50ms.
            duration = len(audio) / sr
            elapsed = 0.0
            interval = 0.05
            while elapsed < duration + 0.02:  # +20ms tail guard
                if self._interrupt.is_set():
                    sd.stop()
                    return
                time.sleep(interval)
                elapsed += interval
            # Final hard wait — ensures audio device has fully drained
            # before the next sd.play() call (prevents tail-clip on fast replies)
            sd.wait()
            # Brief inter-sentence gap — 25ms silence lets speaker settle
            # and prevents perceptual clipping between consecutive sentences
            if not self._interrupt.is_set():
                time.sleep(0.025)
        finally:
            self._is_playing.clear()


# ──────────────────────────────────────────────────────────────────────────────
#  Utility
# ──────────────────────────────────────────────────────────────────────────────

def check_tts_server(api_url: str = "http://127.0.0.1:9880") -> bool:
    """Returns True if the GPT-SoVITS API server is reachable."""
    try:
        r = requests.get(api_url.rstrip("/") + "/", timeout=2)
        return r.status_code < 500
    except Exception:
        return False