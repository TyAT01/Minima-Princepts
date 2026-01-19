"""Local inference wrapper interfaces."""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass
from typing import Iterable, Optional, Protocol


@dataclass(slots=True)
class GenerationConfig:
    max_tokens: int = 512
    temperature: float = 0.7
    top_p: float = 0.9
    stop_sequences: Optional[list[str]] = None


class LLMEngine(Protocol):
    def generate(self, prompt: str, config: GenerationConfig) -> str:
        ...

    def stream(self, prompt: str, config: GenerationConfig) -> Iterable[str]:
        ...


class LLMServiceError(RuntimeError):
    """Raised when an LLM backend request fails."""


class HeuristicEngine:
    """Lightweight heuristic engine for offline fallback responses."""

    def generate(self, prompt: str, config: GenerationConfig) -> str:
        try:
            last_line = self._extract_last_line(prompt)
        except Exception:
            last_line = ""
        if last_line:
            return f"I hear you. Here's what I can share: {last_line}"
        return "I'm here and ready—what would you like to explore next?"

    def stream(self, prompt: str, config: GenerationConfig) -> Iterable[str]:
        reply = self.generate(prompt, config)
        for token in reply.split():
            yield token + " "

    @staticmethod
    def _extract_last_line(prompt: str) -> str:
        lines = [line.strip() for line in prompt.splitlines() if line.strip()]
        if not lines:
            return ""
        return lines[-1]


@dataclass(slots=True)
class LlamaCppServerConfig:
    base_url: str = "http://127.0.0.1:8080"
    model: str | None = None


class LlamaCppServerEngine:
    """Connector for llama.cpp server in OpenAI-compatible mode."""

    def __init__(self, config: LlamaCppServerConfig) -> None:
        self._config = config
        self._logger = logging.getLogger(__name__)

    def generate(self, prompt: str, config: GenerationConfig) -> str:
        payload = self._build_payload(prompt, config, stream=False)
        try:
            response = self._post_json(
                f"{self._config.base_url}/v1/chat/completions", payload
            )
            return response["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001 - keep runtime alive
            self._logger.exception("Llama.cpp generate failed: %s", exc)
            raise LLMServiceError("Failed to reach llama.cpp server.") from exc

    def stream(self, prompt: str, config: GenerationConfig) -> Iterable[str]:
        payload = self._build_payload(prompt, config, stream=True)
        request = urllib.request.Request(
            f"{self._config.base_url}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line or not line.startswith("data: "):
                        continue
                    data = line.removeprefix("data: ").strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    delta = chunk["choices"][0]["delta"].get("content")
                    if delta:
                        yield delta
        except Exception as exc:  # noqa: BLE001 - keep streaming resilient
            self._logger.exception("Llama.cpp stream failed: %s", exc)
            yield "I'm having trouble reaching the language model right now."

    def _build_payload(
        self, prompt: str, config: GenerationConfig, stream: bool
    ) -> dict:
        message = {"role": "user", "content": prompt}
        payload = {
            "messages": [message],
            "stream": stream,
            "temperature": config.temperature,
            "top_p": config.top_p,
            "max_tokens": config.max_tokens,
        }
        if self._config.model:
            payload["model"] = self._config.model
        if config.stop_sequences:
            payload["stop"] = config.stop_sequences
        return payload

    @staticmethod
    def _post_json(url: str, payload: dict) -> dict:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read().decode("utf-8")
        return json.loads(body)


@dataclass(slots=True)
class OllamaConfig:
    base_url: str = "http://127.0.0.1:11434"
    model: str = "llama3.1:8b-instruct-q4_K_M"


class OllamaEngine:
    """Connector for Ollama's /api/generate endpoint."""

    def __init__(self, config: OllamaConfig) -> None:
        self._config = config
        self._logger = logging.getLogger(__name__)

    def generate(self, prompt: str, config: GenerationConfig) -> str:
        payload = self._build_payload(prompt, config, stream=False)
        try:
            response = self._post_json(f"{self._config.base_url}/api/generate", payload)
            return response.get("response", "")
        except Exception as exc:  # noqa: BLE001 - keep runtime alive
            self._logger.exception("Ollama generate failed: %s", exc)
            raise LLMServiceError("Failed to reach Ollama server.") from exc

    def stream(self, prompt: str, config: GenerationConfig) -> Iterable[str]:
        payload = self._build_payload(prompt, config, stream=True)
        request = urllib.request.Request(
            f"{self._config.base_url}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        continue
                    chunk = json.loads(line)
                    token = chunk.get("response")
                    if token:
                        yield token
                    if chunk.get("done"):
                        break
        except Exception as exc:  # noqa: BLE001 - keep streaming resilient
            self._logger.exception("Ollama stream failed: %s", exc)
            yield "I'm having trouble reaching the language model right now."

    def _build_payload(
        self, prompt: str, config: GenerationConfig, stream: bool
    ) -> dict:
        return {
            "model": self._config.model,
            "prompt": prompt,
            "stream": stream,
            "options": {
                "temperature": config.temperature,
                "top_p": config.top_p,
                "num_predict": config.max_tokens,
            },
        }

    @staticmethod
    def _post_json(url: str, payload: dict) -> dict:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read().decode("utf-8")
        return json.loads(body)
