"""Session and runtime state management."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Tuple


@dataclass(slots=True)
class SessionState:
    mode: str = "local"
    modules_enabled: Dict[str, bool] = field(default_factory=lambda: {"text": True})
    active_profile: str = "default"
    active_engine: str = "llama_cpp_server"
    muted: bool = False
    persona_mode: str = "default"


@dataclass(slots=True)
class ChannelSession:
    channel_key: str
    last_user_id: str | None = None
    last_message: str | None = None
    persona_override: str | None = None


class SessionManager:
    def __init__(self, initial: SessionState | None = None) -> None:
        self._state = initial or SessionState()
        self._logger = logging.getLogger(__name__)
        self._channels: Dict[str, ChannelSession] = {}

    def snapshot(self) -> SessionState:
        return self._state

    def set_mode(self, mode: str) -> None:
        try:
            self._state.mode = mode
        except Exception as exc:  # noqa: BLE001 - keep session updates resilient
            self._logger.exception("Failed to set mode: %s", exc)

    def set_module(self, name: str, enabled: bool) -> None:
        try:
            self._state.modules_enabled[name] = enabled
        except Exception as exc:  # noqa: BLE001 - keep session updates resilient
            self._logger.exception("Failed to set module: %s", exc)

    def set_profile(self, profile: str) -> None:
        try:
            self._state.active_profile = profile
        except Exception as exc:  # noqa: BLE001 - keep session updates resilient
            self._logger.exception("Failed to set profile: %s", exc)

    def set_engine(self, engine: str) -> None:
        try:
            self._state.active_engine = engine
        except Exception as exc:  # noqa: BLE001 - keep session updates resilient
            self._logger.exception("Failed to set engine: %s", exc)

    def set_muted(self, muted: bool) -> None:
        try:
            self._state.muted = muted
        except Exception as exc:  # noqa: BLE001 - keep session updates resilient
            self._logger.exception("Failed to set muted: %s", exc)

    def set_persona_mode(self, mode: str) -> None:
        try:
            self._state.persona_mode = mode
        except Exception as exc:  # noqa: BLE001 - keep session updates resilient
            self._logger.exception("Failed to set persona mode: %s", exc)

    def upsert_channel(self, channel_key: str, user_id: str, message: str) -> ChannelSession:
        try:
            session = self._channels.get(channel_key)
            if not session:
                session = ChannelSession(channel_key=channel_key)
                self._channels[channel_key] = session
            session.last_user_id = user_id
            session.last_message = message
            return session
        except Exception as exc:  # noqa: BLE001 - keep session updates resilient
            self._logger.exception("Failed to update channel session: %s", exc)
            return ChannelSession(channel_key=channel_key)

    def channel_snapshot(self) -> Tuple[ChannelSession, ...]:
        return tuple(self._channels.values())
