"""Combine multiple input adapters into a single pollable interface."""

from __future__ import annotations

import logging
from typing import Iterable, List

from princess_ai.input_adapters.base import InputAdapter
from princess_ai.schemas.events import Event


class MultiInputAdapter(InputAdapter):
    def __init__(self, adapters: Iterable[InputAdapter]) -> None:
        self._adapters = list(adapters)
        self._logger = logging.getLogger(__name__)

    def poll(self) -> Iterable[Event]:
        events: List[Event] = []
        for adapter in self._adapters:
            try:
                events.extend(adapter.poll())
            except Exception as exc:  # noqa: BLE001 - keep adapter resilient
                self._logger.exception("Adapter poll failed: %s", exc)
        return events
