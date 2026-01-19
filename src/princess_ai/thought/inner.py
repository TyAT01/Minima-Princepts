"""Inner deliberation step."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Intent:
    goal: str
    tone: str
    tool: str | None = None


class InnerThought:
    def plan(self, prompt: str) -> Intent:
        last_message = self._extract_last_message(prompt)
        if self._should_store_memory(last_message):
            return Intent(goal=f"store memory: {last_message}", tone="friendly", tool="store_memory")
        return Intent(goal="respond helpfully", tone="friendly", tool=None)

    def _extract_last_message(self, prompt: str) -> str:
        marker = "Conversation:"
        if marker not in prompt:
            return ""
        conversation = prompt.split(marker, 1)[1]
        conversation = conversation.split("Princess Response:", 1)[0]
        lines = [line.strip() for line in conversation.splitlines() if line.strip()]
        if not lines:
            return ""
        last_line = lines[-1]
        if ":" in last_line:
            _, message = last_line.split(":", 1)
            return message.strip()
        return last_line

    def _should_store_memory(self, message: str) -> bool:
        lowered = message.lower()
        return any(
            keyword in lowered
            for keyword in ("remember", "save this", "note this", "don't forget", "my name is", "call me")
        )
