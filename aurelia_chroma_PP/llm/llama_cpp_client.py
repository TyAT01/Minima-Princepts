from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger(__name__)


@dataclass
class LlamaCompletion:
    text: str
    raw: dict[str, Any]


class LlamaCppClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def complete(self, prompt: str, max_tokens: int = 200) -> str:
        payload = {
            "prompt": prompt,
            "n_predict": max_tokens,
            "temperature": 0.7,
            "stop": ["User:", "Assistant:"],
        }
        try:
            response = requests.post(f"{self._base_url}/completion", json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
            return str(data.get("content", "")).strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Llama.cpp request failed: %s", exc)
            return "I'm having trouble reaching the local model right now."
