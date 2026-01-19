"""Hardware profiling and resource management."""

from __future__ import annotations

import importlib.util
import logging
import os
import platform
from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class HardwareProfile:
    cpu_count: int
    total_ram_gb: float
    gpu_name: Optional[str]
    vram_gb: Optional[float]


class HardwareProfiler:
    def __init__(self) -> None:
        self._psutil = None
        self._logger = logging.getLogger(__name__)
        if importlib.util.find_spec("psutil"):
            import psutil  # type: ignore

            self._psutil = psutil

    def detect(self) -> HardwareProfile:
        try:
            cpu_count = os.cpu_count() or 1
            total_ram_gb = self._get_total_ram_gb()
            gpu_name, vram_gb = self._get_gpu_info()
            return HardwareProfile(
                cpu_count=cpu_count,
                total_ram_gb=total_ram_gb,
                gpu_name=gpu_name,
                vram_gb=vram_gb,
            )
        except Exception as exc:  # noqa: BLE001 - keep runtime resilient
            self._logger.exception("Failed to detect hardware profile: %s", exc)
            return HardwareProfile(cpu_count=1, total_ram_gb=0.0, gpu_name=None, vram_gb=None)

    def _get_total_ram_gb(self) -> float:
        try:
            if self._psutil:
                return round(self._psutil.virtual_memory().total / 1024**3, 2)
            return 0.0
        except Exception as exc:  # noqa: BLE001 - keep runtime resilient
            self._logger.exception("Failed to read total RAM: %s", exc)
            return 0.0

    def _get_gpu_info(self) -> tuple[Optional[str], Optional[float]]:
        try:
            if platform.system().lower() == "windows":
                return None, None
            return None, None
        except Exception as exc:  # noqa: BLE001 - keep runtime resilient
            self._logger.exception("Failed to detect GPU info: %s", exc)
            return None, None


class AdaptiveResourceManager:
    def __init__(self, profiler: HardwareProfiler) -> None:
        self._profiler = profiler
        self._logger = logging.getLogger(__name__)
        self._active_profile = "default"

    def choose_profile(self) -> str:
        try:
            profile = self._profiler.detect()
            if profile.total_ram_gb and profile.total_ram_gb < 8:
                return "low_spec"
            return "default"
        except Exception as exc:  # noqa: BLE001 - keep runtime resilient
            self._logger.exception("Failed to choose profile: %s", exc)
            return "default"

    def auto_tune(self) -> str:
        try:
            if self._profiler._psutil:
                cpu = self._profiler._psutil.cpu_percent(interval=0.1)
                if cpu > 85:
                    self._active_profile = "low_spec"
                else:
                    self._active_profile = "default"
            return self._active_profile
        except Exception as exc:  # noqa: BLE001 - keep auto tuning resilient
            self._logger.exception("Failed to auto-tune profile: %s", exc)
            return self._active_profile
