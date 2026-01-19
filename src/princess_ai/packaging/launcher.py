"""Launcher script to validate environment and dependencies."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from princess_ai.packaging.env import EnvironmentLocator


@dataclass(slots=True)
class LaunchReport:
    ok: bool
    message: str


class EnvironmentValidator:
    def __init__(self) -> None:
        self._logger = logging.getLogger(__name__)

    def validate(self) -> LaunchReport:
        try:
            paths = EnvironmentLocator().resolve()
            for path in (paths.base_path, paths.models_path, paths.logs_path, paths.cache_path):
                path.mkdir(parents=True, exist_ok=True)
            model_path = os.getenv("PRINCESS_LLAMA_CPP_MODEL") or os.getenv("PRINCESS_OLLAMA_MODEL")
            if model_path and not Path(model_path).exists():
                return LaunchReport(False, f"Model path not found: {model_path}")
            return LaunchReport(True, "Environment ready")
        except Exception as exc:  # noqa: BLE001 - keep launcher resilient
            self._logger.exception("Failed to validate environment: %s", exc)
            return LaunchReport(False, "Environment validation failed")
