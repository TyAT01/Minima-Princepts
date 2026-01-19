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
        return Intent(goal="respond helpfully", tone="friendly", tool=None)
