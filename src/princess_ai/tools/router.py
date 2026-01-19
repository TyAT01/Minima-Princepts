"""Tool routing layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ToolCall:
    name: str
    payload: dict


class ToolRouter:
    def select_tool(self, intent: str) -> ToolCall | None:
        return None
