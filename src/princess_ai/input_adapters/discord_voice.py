"""Discord voice adapter stub."""

from __future__ import annotations

import logging
from typing import Iterable

from princess_ai.input_adapters.base import InputAdapter
from princess_ai.schemas.events import Event


class DiscordVoiceAdapter(InputAdapter):
    """Placeholder for Discord voice integration."""

    def __init__(self) -> None:
        self._logger = logging.getLogger(__name__)
        self._logger.info("DiscordVoiceAdapter initialized (stub).")

    def poll(self) -> Iterable[Event]:
        return []
