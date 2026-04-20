"""
youtube_chat.py — Shiro's YouTube Live Chat Reader
===================================================
Reads live chat from a YouTube stream and feeds messages into Shiro's engine
as if they were typed in Discord or the GUI.

STATUS: DISABLED — not used until Shiro streams on YouTube.
To activate: set enabled: true in config.yaml under youtube_chat,
and provide the stream URL or video ID.

Dependencies (install when needed):
  pip install pytchat

Config (config.yaml):
  youtube_chat:
    enabled: false
    video_id: ""          # YouTube video ID or full URL
    poll_interval: 2.0    # seconds between chat polls
    prefix_username: true # prepend "[YT] username: " to messages
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional, Callable

logger = logging.getLogger("shiro.youtube_chat")


class YouTubeChatReader:
    """
    Polls a YouTube live stream's chat and calls on_message(username, text)
    for each new message.

    Usage (when ready to stream):
        reader = YouTubeChatReader(config.get('youtube_chat', {}))
        reader.on_message = lambda user, text: engine.process_text(
            text, user_name=f"[YT] {user}"
        )
        reader.start("dQw4w9WgXcQ")   # pass video ID
        # ...
        reader.stop()
    """

    def __init__(self, config: dict, stream_profiles=None):
        """
        Parameters
        ----------
        config         : dict  — youtube_chat section from config.yaml
        stream_profiles: StreamProfileManager | None
            Pass the engine's stream_profiles instance so YouTube viewers
            are routed through the identity cross-reference layer.
            If None, falls back to raw usernames (old behaviour).
        """
        cfg = config or {}
        self.enabled        = bool(cfg.get("enabled", False))
        self.video_id       = cfg.get("video_id", "")
        self.poll_interval  = float(cfg.get("poll_interval", 2.0))
        self.prefix_user    = bool(cfg.get("prefix_username", True))

        self.on_message: Optional[Callable[[str, str], None]] = None

        # Stream identity layer — resolves viewer names and gates profiling
        self._stream_profiles = stream_profiles  # StreamProfileManager or None

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running    = False

    def start(self, video_id: str = ""):
        """Start reading chat. Pass video_id to override config."""
        if not self.enabled:
            logger.info("[YouTube] Chat reader disabled in config — not starting.")
            return
        vid = video_id or self.video_id
        if not vid:
            logger.warning("[YouTube] No video_id set — cannot start chat reader.")
            return
        self.video_id = vid
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._read_loop, daemon=True, name="ShiroYTChat"
        )
        self._thread.start()
        logger.info(f"[YouTube] Chat reader started for video: {self.video_id}")

    def stop(self):
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("[YouTube] Chat reader stopped.")

    def _read_loop(self):
        try:
            import pytchat
        except ImportError:
            logger.error(
                "[YouTube] pytchat not installed. "
                "Run: pip install pytchat  — then restart."
            )
            return

        try:
            chat = pytchat.create(video_id=self.video_id)
            logger.info(f"[YouTube] Connected to live chat for: {self.video_id}")
            while self._running and not self._stop_event.is_set() and chat.is_alive():
                for item in chat.get().sync_items():
                    raw_username = item.author.name or "Viewer"
                    message      = (item.message or "").strip()
                    if not message:
                        continue

                    # ── Stream identity resolution ────────────────────────────
                    # Route through StreamProfileManager if available.
                    # This cross-references stream usernames against known_users
                    # aliases so e.g. "TyAT01" resolves to "Tyler" automatically.
                    # Viewers who haven't hit the message threshold are not profiled.
                    resolved_username = raw_username
                    if self._stream_profiles is not None:
                        identity = self._stream_profiles.record_message(
                            raw_username, message, platform="youtube"
                        )
                        if identity["is_known_user"]:
                            resolved_username = identity["resolved_name"]
                            logger.info(
                                f"[YouTube] Cross-ref: '{raw_username}' → '{resolved_username}'"
                            )
                        elif not identity["should_profile"]:
                            # Below threshold and not a known user — log but don't feed to engine
                            logger.debug(
                                f"[YouTube] {raw_username}: below profile threshold "
                                f"({identity}), skipping engine call."
                            )
                            continue
                        else:
                            resolved_username = raw_username  # stream-only viewer, use as-is
                    # ─────────────────────────────────────────────────────────

                    logger.info(f"[YouTube] {raw_username} (→{resolved_username}): {message}")
                    if self.on_message:
                        try:
                            self.on_message(resolved_username, message)
                        except Exception as cb_err:
                            logger.warning(f"[YouTube] on_message error: {cb_err}")
                self._stop_event.wait(timeout=self.poll_interval)
        except Exception as e:
            logger.error(f"[YouTube] Chat reader error: {e}")