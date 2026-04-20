"""
stream_profile_manager.py — Shiro Stream Identity Layer v1.0
=============================================================
Manages viewer identities during a live stream SEPARATELY from the main
WebUI/Discord user_profiles. Prevents stream lurkers from polluting Shiro's
main memory while still connecting known people across platforms.

HOW IT WORKS
------------
1. Stream messages come in with a platform ("twitch", "youtube") and username.
2. StreamProfileManager checks if that username is a known alias of someone
   in known_users (from config.yaml). If yes → resolve to their real identity.
3. If not a known person, a lightweight ViewerProfile is built in stream_profiles
   (NOT in user_profiles) only after min_messages_to_profile threshold is met.
4. The engine can call get_identity(username, platform) at any point to get
   the resolved display_name and whether they are a known trusted user.

CROSS-REFERENCE CHAIN
---------------------
  stream username  →  config known_users aliases  →  real_name
  e.g. "TyAT01"   →  Tyler's youtube alias        →  "Tyler"
  e.g. "TyAT001"  →  Tyler's twitch alias          →  "Tyler"
  e.g. "rando123" →  no match                      →  "rando123" (stream-only)

INTEGRATION
-----------
In shiro_engine.py, when a stream message arrives:
    identity = self.stream_profiles.get_identity(username, platform)
    user_name = identity["resolved_name"]   # use this for process_text()
    is_known  = identity["is_known_user"]   # True = cross-ref hit (trusted)

In youtube_chat.py / twitch_chat.py (when built), replace:
    engine.process_text(text, user_name=f"[YT] {user}")
with:
    identity = engine.stream_profiles.get_identity(user, "youtube")
    engine.process_text(text, user_name=identity["resolved_name"],
                        user_id=identity["resolved_name"])
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from stream_viewers import ViewerRoster, ViewerProfile

logger = logging.getLogger("shiro.stream_profiles")


class StreamProfileManager:
    """
    Owns all stream viewer state and cross-reference logic.

    Parameters
    ----------
    config : dict
        The full Shiro config dict (from config.yaml). Reads:
          - config["stream_profiles"]  — stream profile settings
          - config["known_users"]      — identity cross-reference table
    """

    def __init__(self, config: dict):
        cfg = config.get("stream_profiles", {})
        self._profile_file   = Path(cfg.get("file", "./stream_profiles.json"))
        self._min_msgs       = int(cfg.get("min_messages_to_profile", 3))

        # Platform-specific identity maps from config
        # e.g. twitch_identity_map: {"TyAT001": "Tyler"}
        self._twitch_map: dict[str, str]  = cfg.get("twitch_identity_map", {}) or {}
        self._youtube_map: dict[str, str] = cfg.get("youtube_identity_map", {}) or {}

        # Build alias lookup from known_users list
        # Maps any alias (any case) → display_name of that known user
        self._alias_lookup: dict[str, str] = {}
        self._known_users: list[dict]       = config.get("known_users", []) or []
        self._build_alias_lookup()

        # Live session viewer tracking (stream_viewers.py)
        self.roster = ViewerRoster()

        # Persistent stream profiles across sessions
        # Only stored for viewers who hit the min_messages threshold
        # Structure: { "platform:username": { ...facts... } }
        self._stream_profiles: dict[str, dict] = self._load_stream_profiles()
        self._lock = threading.RLock()

        logger.info(
            f"[StreamProfiles] Ready. "
            f"Known aliases: {len(self._alias_lookup)} | "
            f"Saved stream profiles: {len(self._stream_profiles)}"
        )

    # ── Alias lookup ──────────────────────────────────────────────────────────

    def _build_alias_lookup(self):
        """
        Build a case-insensitive alias → display_name map from known_users.
        Also indexes platform-specific usernames from the platforms dict.
        """
        self._alias_lookup = {}
        for user in self._known_users:
            dn = user.get("display_name", "")
            if not dn:
                continue
            # The display_name itself maps to itself
            self._alias_lookup[dn.lower()] = dn
            # All listed aliases
            for alias in user.get("aliases", []):
                self._alias_lookup[alias.lower()] = dn
            # Platform-specific usernames
            for platform, uname in (user.get("platforms", {}) or {}).items():
                if uname:
                    self._alias_lookup[uname.lower()] = dn
        logger.debug(f"[StreamProfiles] Alias table: {self._alias_lookup}")

    def reload_known_users(self, known_users: list):
        """Call this if known_users config changes at runtime."""
        with self._lock:
            self._known_users = known_users
            self._build_alias_lookup()

    # ── Core identity resolution ──────────────────────────────────────────────

    def get_identity(self, username: str, platform: str = "unknown") -> dict:
        """
        Resolve a stream username to a Shiro identity.

        Returns
        -------
        dict with keys:
          resolved_name : str   — the name to pass to process_text()
          real_name     : str   — human-readable name (may equal resolved_name)
          is_known_user : bool  — True if this person is in known_users
          platform      : str   — original platform
          original_name : str   — original stream username (unmodified)
          should_profile: bool  — True if this viewer has hit the msg threshold
        """
        original = username
        resolved = self._resolve(username, platform)
        is_known = resolved != username  # name changed = we found a cross-ref

        # Record the message in the roster regardless
        viewer = self.roster.get_or_create(username, platform)

        # Determine if we should expose a profile for this viewer
        should_profile = (
            is_known  # always profile known people
            or viewer.message_count >= self._min_msgs
        )

        return {
            "resolved_name": resolved,
            "real_name":     resolved,
            "is_known_user": is_known,
            "platform":      platform,
            "original_name": original,
            "should_profile": should_profile,
        }

    def _resolve(self, username: str, platform: str) -> str:
        """
        Attempt to resolve username → known display_name.
        Priority:
          1. Per-platform identity maps (twitch_identity_map / youtube_identity_map)
          2. Alias lookup (known_users aliases + platform usernames)
          3. Return original username unchanged
        """
        # 1. Explicit platform maps in config
        if platform == "twitch":
            hit = self._twitch_map.get(username) or self._twitch_map.get(username.lower())
            if hit:
                logger.info(f"[StreamProfiles] Twitch '{username}' → '{hit}' (config map)")
                return hit
        elif platform == "youtube":
            hit = self._youtube_map.get(username) or self._youtube_map.get(username.lower())
            if hit:
                logger.info(f"[StreamProfiles] YouTube '{username}' → '{hit}' (config map)")
                return hit

        # 2. Alias lookup
        hit = self._alias_lookup.get(username.lower())
        if hit:
            logger.info(f"[StreamProfiles] '{username}' → '{hit}' (alias lookup)")
            return hit

        # 3. No match — keep original
        return username

    # ── Message recording ─────────────────────────────────────────────────────

    def record_message(self, username: str, text: str, platform: str = "unknown") -> dict:
        """
        Record a chat message and return the resolved identity dict.
        Call this for every stream chat message before passing to process_text().
        """
        identity = self.get_identity(username, platform)
        viewer   = self.roster.record_message(username, text, platform)

        # Persist to stream profiles once threshold is hit
        if viewer.message_count == self._min_msgs:
            self._init_stream_profile(username, platform, identity["resolved_name"])
        elif viewer.message_count > self._min_msgs:
            self._update_stream_profile(username, platform, text)

        return identity

    # ── Persistent stream profiles ────────────────────────────────────────────

    def _profile_key(self, username: str, platform: str) -> str:
        return f"{platform}:{username}"

    def _init_stream_profile(self, username: str, platform: str, resolved_name: str):
        """Create a new stream profile entry when threshold is first hit."""
        key = self._profile_key(username, platform)
        with self._lock:
            if key not in self._stream_profiles:
                self._stream_profiles[key] = {
                    "username":      username,
                    "platform":      platform,
                    "resolved_name": resolved_name,
                    "first_seen":    datetime.now().isoformat(),
                    "last_seen":     datetime.now().isoformat(),
                    "message_count": self._min_msgs,
                    "facts":         {},
                }
                logger.info(f"[StreamProfiles] New stream profile: {key} → '{resolved_name}'")
                self._save_stream_profiles()

    def _update_stream_profile(self, username: str, platform: str, text: str):
        """Update last_seen and message count for an existing stream profile."""
        key = self._profile_key(username, platform)
        with self._lock:
            if key in self._stream_profiles:
                self._stream_profiles[key]["last_seen"] = datetime.now().isoformat()
                self._stream_profiles[key]["message_count"] = \
                    self._stream_profiles[key].get("message_count", 0) + 1

    def update_stream_fact(self, username: str, platform: str,
                           fact_key: str, fact_value: str):
        """
        Store a fact about a stream viewer in their stream profile.
        Does NOT write to user_profiles — stays in stream_profiles.json.
        """
        key = self._profile_key(username, platform)
        with self._lock:
            if key not in self._stream_profiles:
                return  # haven't hit threshold yet, don't bother
            self._stream_profiles[key].setdefault("facts", {})[fact_key] = fact_value
            self._save_stream_profiles()
            logger.debug(f"[StreamProfiles] {key}.{fact_key} = {fact_value!r}")

    def get_stream_profile(self, username: str, platform: str) -> dict:
        """Return stored stream profile for a viewer, or empty dict."""
        key = self._profile_key(username, platform)
        with self._lock:
            return dict(self._stream_profiles.get(key, {}))

    def get_viewer_context_block(self, max_viewers: int = 4) -> str:
        """
        Build a compact viewer context block for LLM prompt injection.
        FIX: max_viewers default 6→4 to save ~40 tokens per turn.
        Only includes viewers who hit the profiling threshold or are known users.
        """
        active = self.roster.get_active(8.0)
        if not active:
            return ""

        active.sort(key=lambda v: v.message_count, reverse=True)
        seen = []
        for v in active:
            if len(seen) >= max_viewers:
                break
            identity = self.get_identity(v.username, v.platform)
            if not identity["should_profile"]:
                continue
            label = identity["resolved_name"]
            if identity["is_known_user"] and label != v.username:
                label += f"({v.username})"
            seen.append(f"{label}:{v.message_count}msgs")

        if not seen:
            return ""
        # Compact single-line format — saves ~40 tokens vs multi-line bullet list
        return "Viewers in chat: " + ", ".join(seen)

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load_stream_profiles(self) -> dict:
        if self._profile_file.exists():
            try:
                return json.loads(self._profile_file.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"[StreamProfiles] Failed to load {self._profile_file}: {e}")
        return {}

    def _save_stream_profiles(self):
        try:
            tmp = self._profile_file.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self._stream_profiles, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
            tmp.replace(self._profile_file)
        except Exception as e:
            logger.error(f"[StreamProfiles] Save failed: {e}")

    def save(self):
        """Explicit save — call on shutdown."""
        with self._lock:
            self._save_stream_profiles()

    # ── Debug helpers ─────────────────────────────────────────────────────────

    def debug_summary(self) -> str:
        """Quick summary string for logging."""
        active = self.roster.active_count(5.0)
        total  = self.roster.total_unique()
        saved  = len(self._stream_profiles)
        return (
            f"[StreamProfiles] Session: {total} unique viewers, "
            f"{active} active last 5min, {saved} saved profiles"
        )