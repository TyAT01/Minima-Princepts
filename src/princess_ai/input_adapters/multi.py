"""Combine multiple input adapters into a single pollable interface."""

from __future__ import annotations

import logging
from typing import Iterable, List

from princess_ai.input_adapters.base import InputAdapter
from princess_ai.runtime.event_bus import EventBus, EventBusMetrics
from princess_ai.schemas.events import Event


class MultiInputAdapter(InputAdapter):
    def __init__(self, adapters: Iterable[InputAdapter], event_bus: EventBus | None = None) -> None:
        self._adapters = list(adapters)
        self._logger = logging.getLogger(__name__)
        self._event_bus = event_bus or EventBus()

    def poll(self) -> Iterable[Event]:
        events: List[Event] = []
        for adapter in self._adapters:
            try:
                events.extend(adapter.poll())
            except Exception as exc:  # noqa: BLE001 - keep adapter resilient
                self._logger.exception("Adapter poll failed: %s", exc)
        if events:
            self._event_bus.publish(events)
        return self._event_bus.drain()

    def metrics(self) -> EventBusMetrics:
        return self._event_bus.metrics()
