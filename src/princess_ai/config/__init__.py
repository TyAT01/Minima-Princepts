"""Configuration loading and profile selection."""

from __future__ import annotations

import json
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

    def load(self) -> Dict[str, Profile]:
        data = json.loads(self._profiles_path.read_text(encoding="utf-8"))
        return {key: self._parse_profile(value) for key, value in data.items()}

    def _parse_profile(self, raw: Dict[str, Any]) -> Profile:
        llm = raw.get("llm", {})
        return Profile(
            name=raw.get("name", "default"),
            description=raw.get("description", ""),
            llm=LLMProfile(
                backend=llm.get("backend", "llama.cpp"),
                model=llm.get("model", ""),
                context_size=int(llm.get("context_size", 2048)),
                threads=int(llm.get("threads", 4)),
                batch_size=int(llm.get("batch_size", 32)),
                streaming=bool(llm.get("streaming", True)),
            ),
        )
