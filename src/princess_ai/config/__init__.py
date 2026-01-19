"""Configuration loading and profile selection."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


@dataclass(slots=True)
class LLMProfile:
    backend: str
    model: str
    context_size: int
    threads: int
    batch_size: int
    streaming: bool


@dataclass(slots=True)
class Profile:
    name: str
    description: str
    llm: LLMProfile


class ProfileLoader:
    def __init__(self, profiles_path: Path) -> None:
        self._profiles_path = profiles_path
        self._logger = logging.getLogger(__name__)

    def load(self) -> Dict[str, Profile]:
        try:
            data = json.loads(self._profiles_path.read_text(encoding="utf-8"))
            return {key: self._parse_profile(value) for key, value in data.items()}
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            self._logger.exception("Failed to load profiles: %s", exc)
            default_profile = self._parse_profile({})
            return {"default": default_profile}

    def _parse_profile(self, raw: Dict[str, Any]) -> Profile:
        try:
            llm = raw.get("llm", {}) if isinstance(raw, dict) else {}
            return Profile(
                name=str(raw.get("name", "default")) if isinstance(raw, dict) else "default",
                description=str(raw.get("description", "")) if isinstance(raw, dict) else "",
                llm=LLMProfile(
                    backend=str(llm.get("backend", "llama.cpp")),
                    model=str(llm.get("model", "")),
                    context_size=int(llm.get("context_size", 2048)),
                    threads=int(llm.get("threads", 4)),
                    batch_size=int(llm.get("batch_size", 32)),
                    streaming=bool(llm.get("streaming", True)),
                ),
            )
        except Exception as exc:  # noqa: BLE001 - keep profile parsing resilient
            self._logger.exception("Failed to parse profile: %s", exc)
            return Profile(
                name="default",
                description="",
                llm=LLMProfile(
                    backend="llama.cpp",
                    model="",
                    context_size=2048,
                    threads=4,
                    batch_size=32,
                    streaming=True,
                ),
            )
