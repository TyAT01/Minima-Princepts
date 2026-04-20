"""
screen_vision.py — Shiro's Screen Vision Module (EasyOCR edition)
=================================================================
Replaces moondream2 with EasyOCR — fast, lightweight, no VRAM required.

Why EasyOCR over moondream2:
  - No VRAM required (CPU by default, optional GPU)
  - ~200ms first read after warm; <50ms per frame thereafter
  - Reads any on-screen text: browser tabs, chat, game UI, code, titles
  - Fully offline, no API key, free forever
  - moondream2 was broken on this setup anyway

Limitation: reads TEXT only — cannot describe images/scenes.
For scene description, a separate image captioner could be added later.

Install deps (once in venv):
  pip install easyocr mss Pillow

Config (config.yaml):
  vision:
    enabled: false
    mode: on_demand         # "on_demand" or "passive"
    passive_interval_secs: 60
    idle_unload_secs: 300
    device: cpu             # "cpu" or "cuda"
    max_dim: 1280           # resize before OCR
    min_confidence: 0.4     # discard OCR results below this
    languages: ["en"]       # EasyOCR language list
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional, List

logger = logging.getLogger("shiro.vision")

_mss       = None
_PIL_Image = None
_easyocr   = None


def _lazy_import_capture():
    global _mss, _PIL_Image
    if _mss is None:
        import mss
        _mss = mss
    if _PIL_Image is None:
        from PIL import Image
        _PIL_Image = Image


def _lazy_import_ocr():
    global _easyocr
    if _easyocr is None:
        import easyocr
        _easyocr = easyocr


class ScreenVision:
    """
    Shiro's screen awareness — reads on-screen text via EasyOCR.

    Public API (identical to old moondream version):
        vision = ScreenVision(config.get('vision', {}))
        vision.start()
        ctx = vision.get_screen_context()   # returns "[Screen: ...]" or ""
        vision.stop()
    """

    DEFAULTS = {
        "enabled":               False,
        "mode":                  "on_demand",
        "passive_interval_secs": 60,
        "idle_unload_secs":      300,
        "device":                "cpu",
        "max_dim":               1280,
        "min_confidence":        0.4,
        "languages":             ["en"],
    }

    def __init__(self, config: dict):
        cfg = {**self.DEFAULTS, **config}
        self.enabled          = bool(cfg["enabled"])
        self.mode             = cfg["mode"]
        self.passive_interval = int(cfg["passive_interval_secs"])
        self.idle_unload_secs = int(cfg["idle_unload_secs"])
        self.device           = cfg.get("device", "cpu")
        self.max_dim          = int(cfg["max_dim"])
        self.min_confidence   = float(cfg["min_confidence"])
        self.languages: List[str] = cfg.get("languages") or ["en"]

        self._reader           = None
        self._reader_lock      = threading.Lock()
        self._last_used        = 0.0
        self._last_description = ""
        self._last_capture_ts  = 0.0

        self._passive_thread: Optional[threading.Thread] = None
        self._stop_event       = threading.Event()
        self._running          = False

    def start(self):
        if not self.enabled:
            logger.info("[Vision] Disabled — EasyOCR not loaded.")
            return
        self._running = True
        self._stop_event.clear()
        threading.Thread(target=self._idle_watchdog, daemon=True,
                         name="ShiroVision-watchdog").start()
        if self.mode == "passive":
            self._passive_thread = threading.Thread(
                target=self._passive_loop, daemon=True,
                name="ShiroVision-passive"
            )
            self._passive_thread.start()
            logger.info(
                f"[Vision] EasyOCR passive mode "
                f"(interval={self.passive_interval}s, device={self.device})"
            )
        else:
            logger.info(f"[Vision] EasyOCR on-demand mode (device={self.device})")

    def stop(self):
        self._running = False
        self._stop_event.set()
        self._unload_reader()
        logger.info("[Vision] Stopped.")

    def enable(self):
        if self.enabled:
            return
        self.enabled = True
        self.start()
        logger.info("[Vision] Enabled at runtime.")

    def disable(self):
        if not self.enabled:
            return
        self.enabled = False
        self.stop()
        self._last_description = ""
        logger.info("[Vision] Disabled at runtime.")

    @property
    def is_enabled(self) -> bool:
        return self.enabled

    def get_screen_context(self, force: bool = False) -> str:
        """Returns '[Screen: ...]' string for Shiro's prompt. Returns '' on failure."""
        if not self.enabled:
            return ""
        if self.mode == "passive" and not force:
            if self._last_description:
                age = time.time() - self._last_capture_ts
                age_str = f"{int(age)}s ago" if age < 60 else f"{int(age / 60)}m ago"
                return f"[Screen ({age_str}): {self._last_description}]"
            return ""
        return self._capture_and_read()

    def get_status(self) -> dict:
        return {
            "enabled":      self.enabled,
            "mode":         self.mode,
            "model_loaded": self._reader is not None,
            "last_capture": self._last_capture_ts,
            "last_desc":    (
                self._last_description[:80] + "..."
                if len(self._last_description) > 80
                else self._last_description
            ),
        }

    def _capture_and_read(self) -> str:
        try:
            image = self._capture_screen()
            if image is None:
                return ""
            description = self._ocr_image(image)
            if description:
                self._last_description = description
                self._last_capture_ts  = time.time()
                logger.info(f"[Vision] Screen text: {description[:80]}")
                return f"[Screen: {description}]"
            return ""
        except Exception as e:
            logger.warning(f"[Vision] Capture/OCR error: {e}")
            return ""

    def _capture_screen(self):
        """Grab primary monitor as PIL Image, resized to max_dim."""
        try:
            _lazy_import_capture()
            with _mss.mss() as sct:
                monitor = sct.monitors[1]
                raw = sct.grab(monitor)
                img = _PIL_Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            w, h = img.size
            if max(w, h) > self.max_dim:
                scale = self.max_dim / max(w, h)
                img = img.resize(
                    (int(w * scale), int(h * scale)),
                    _PIL_Image.LANCZOS
                )
            return img
        except Exception as e:
            logger.warning(f"[Vision] Screen capture failed: {e}")
            return None

    def _ocr_image(self, image) -> str:
        """Run EasyOCR on PIL Image, return cleaned text summary."""
        with self._reader_lock:
            reader = self._ensure_reader()
            if reader is None:
                return ""
            t0 = time.perf_counter()
            try:
                import numpy as np
                img_array = np.array(image)
                results = reader.readtext(img_array, detail=1, paragraph=False)

                seen = set()
                lines = []
                for (_bbox, text, conf) in results:
                    text = text.strip()
                    if conf < self.min_confidence or not text:
                        continue
                    key = text.lower()
                    if key not in seen:
                        seen.add(key)
                        lines.append(text)

                elapsed = time.perf_counter() - t0
                logger.debug(f"[Vision] OCR: {len(lines)} blocks in {elapsed:.2f}s")
                self._last_used = time.time()

                if not lines:
                    return ""
                summary = " | ".join(lines[:20])
                if len(summary) > 400:
                    summary = summary[:397] + "..."
                return summary
            except Exception as e:
                logger.warning(f"[Vision] OCR error: {e}")
                return ""

    def _ensure_reader(self):
        """Lazy-load EasyOCR Reader. Returns reader or None on failure."""
        if self._reader is not None:
            return self._reader
        try:
            _lazy_import_ocr()
            gpu = self.device.lower() == "cuda"
            logger.info(
                f"[Vision] Loading EasyOCR "
                f"(langs={self.languages}, gpu={gpu}) — first load ~5-15s..."
            )
            t0 = time.perf_counter()
            self._reader = _easyocr.Reader(
                self.languages, gpu=gpu, verbose=False,
            )
            elapsed = time.perf_counter() - t0
            logger.info(f"[Vision] EasyOCR ready in {elapsed:.1f}s")
            self._last_used = time.time()
            return self._reader
        except Exception as e:
            logger.error(
                f"[Vision] Failed to load EasyOCR: {e}\n"
                "Fix: pip install easyocr mss Pillow"
            )
            self._reader = None
            return None

    def _unload_reader(self):
        with self._reader_lock:
            if self._reader is not None:
                try:
                    del self._reader
                    self._reader = None
                    try:
                        import torch
                        if torch.cuda.is_available():
                            torch.cuda.empty_cache()
                    except ImportError:
                        pass
                    logger.info("[Vision] EasyOCR unloaded.")
                except Exception as e:
                    logger.warning(f"[Vision] Unload error: {e}")

    def _passive_loop(self):
        while self._running and not self._stop_event.is_set():
            try:
                self._capture_and_read()
            except Exception as e:
                logger.warning(f"[Vision] Passive loop error: {e}")
            for _ in range(self.passive_interval // 5 + 1):
                if self._stop_event.wait(timeout=5):
                    return

    def _idle_watchdog(self):
        while self._running and not self._stop_event.is_set():
            if self._stop_event.wait(timeout=60):
                return
            if (self._reader is not None
                    and self._last_used > 0
                    and time.time() - self._last_used > self.idle_unload_secs):
                logger.info(
                    f"[Vision] EasyOCR idle {self.idle_unload_secs}s — unloading."
                )
                self._unload_reader()