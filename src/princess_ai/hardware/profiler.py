"""Hardware profiling and resource management."""

from __future__ import annotations

import importlib.util
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
        if importlib.util.find_spec("psutil"):
            import psutil  # type: ignore

            self._psutil = psutil

    def detect(self) -> HardwareProfile:
        cpu_count = os.cpu_count() or 1
        total_ram_gb = self._get_total_ram_gb()
        gpu_name, vram_gb = self._get_gpu_info()
        return HardwareProfile(
            cpu_count=cpu_count,
            total_ram_gb=total_ram_gb,
            gpu_name=gpu_name,
            vram_gb=vram_gb,
        )

    def _get_total_ram_gb(self) -> float:
        if self._psutil:
            return round(self._psutil.virtual_memory().total / 1024**3, 2)
        return 0.0

    def _get_gpu_info(self) -> tuple[Optional[str], Optional[float]]:
        if platform.system().lower() == "windows":
            return None, None
        return None, None


class AdaptiveResourceManager:
    def __init__(self, profiler: HardwareProfiler) -> None:
        self._profiler = profiler

    def choose_profile(self) -> str:
        profile = self._profiler.detect()
        if profile.total_ram_gb and profile.total_ram_gb < 8:
            return "low_spec"
        return "default"
