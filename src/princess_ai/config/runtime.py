"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass


@dataclass(slots=True)
class RuntimeConfig:
    engine: str = "llama_cpp_server"
    use_streaming: bool = True
    llama_cpp_url: str = "http://127.0.0.1:8080"
    llama_cpp_model: str | None = None
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.1:8b-instruct-q4_K_M"

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        logger = logging.getLogger(__name__)
        try:
            return cls(
                engine=os.getenv("PRINCESS_ENGINE", "llama_cpp_server"),
                use_streaming=os.getenv("PRINCESS_STREAMING", "true").lower() == "true",
                llama_cpp_url=os.getenv(
                    "PRINCESS_LLAMA_CPP_URL", "http://127.0.0.1:8080"
                ),
                llama_cpp_model=os.getenv("PRINCESS_LLAMA_CPP_MODEL"),
                ollama_url=os.getenv("PRINCESS_OLLAMA_URL", "http://127.0.0.1:11434"),
                ollama_model=os.getenv(
                    "PRINCESS_OLLAMA_MODEL", "llama3.1:8b-instruct-q4_K_M"
                ),
            )
        except Exception as exc:  # noqa: BLE001 - keep runtime config resilient
            logger.exception("Failed to load runtime config: %s", exc)
            return cls()
