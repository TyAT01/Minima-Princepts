"""Environment helpers for local installation paths."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class EnvironmentPaths:
    base_path: Path
    models_path: Path
    logs_path: Path
    cache_path: Path


class EnvironmentLocator:
    def __init__(self, base_env_var: str = "PRINCESS_AI_BASE") -> None:
        self._base_env_var = base_env_var
        self._logger = logging.getLogger(__name__)

    def resolve(self) -> EnvironmentPaths:
        try:
            base = Path(os.getenv(self._base_env_var, Path.home() / "princess_ai"))
            return EnvironmentPaths(
                base_path=base,
                models_path=base / "models",
                logs_path=base / "logs",
                cache_path=base / "cache",
            )
        except Exception as exc:  # noqa: BLE001 - keep environment resolution resilient
            self._logger.exception("Failed to resolve environment paths: %s", exc)
            fallback = Path.home() / "princess_ai"
            return EnvironmentPaths(
                base_path=fallback,
                models_path=fallback / "models",
                logs_path=fallback / "logs",
                cache_path=fallback / "cache",
            )
