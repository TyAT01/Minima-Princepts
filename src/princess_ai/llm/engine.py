"""Local inference wrapper interfaces."""

from __future__ import annotations

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


class DummyEngine:
    """Placeholder engine for wiring the orchestrator."""

    def generate(self, prompt: str, config: GenerationConfig) -> str:
        return f"[princess reply] {prompt[-200:]}"

    def stream(self, prompt: str, config: GenerationConfig) -> Iterable[str]:
        reply = self.generate(prompt, config)
        for token in reply.split():
            yield token + " "
