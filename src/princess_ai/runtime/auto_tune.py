"""Adaptive runtime tuning based on telemetry metrics."""

from __future__ import annotations

import asyncio
import logging

from princess_ai.hardware.profiler import AdaptiveResourceManager
from princess_ai.runtime.session import SessionManager
from princess_ai.runtime.telemetry import TelemetryHub


class RuntimeAutoTuner:
    def __init__(
        self,
        resource_manager: AdaptiveResourceManager,
        session_manager: SessionManager,
        telemetry: TelemetryHub,
        interval: float = 5.0,
    ) -> None:
        self._resource_manager = resource_manager
        self._session_manager = session_manager
        self._telemetry = telemetry
        self._interval = interval
        self._logger = logging.getLogger(__name__)
        self._running = False

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                snapshot = self._telemetry.snapshot()
                profile = self._resource_manager.tune_for_latency(
                    snapshot.qos.end_to_end_latency_ms
                )
                self._session_manager.set_profile(profile)
            except Exception as exc:  # noqa: BLE001 - keep auto-tune resilient
                self._logger.exception("Auto-tune failed: %s", exc)
            await asyncio.sleep(self._interval)

    def stop(self) -> None:
        self._running = False
