"""Tool routing layer."""

from __future__ import annotations

from dataclasses import dataclass

from princess_ai.thought.inner import Intent


@dataclass(slots=True)
class ToolCall:
    name: str
    payload: dict


class ToolRouter:
    _MEMORY_KEYWORDS = (
        "remember",
        "save this",
        "note this",
        "don't forget",
        "my name is",
        "call me",
    )

    def select_tool(self, intent: Intent) -> ToolCall | None:
        try:
            explicit_tool = (intent.tool or "").strip().lower()
            if explicit_tool:
                payload = {"intent": intent.goal}
                if explicit_tool == "store_memory":
                    payload["text"] = self._strip_prefix(intent.goal, "store memory:")
                return ToolCall(name=explicit_tool, payload=payload)
            lowered_goal = intent.goal.lower()
            if any(keyword in lowered_goal for keyword in self._MEMORY_KEYWORDS):
                return ToolCall(
                    name="store_memory",
                    payload={
                        "text": self._strip_prefix(intent.goal, "store memory:"),
                        "intent": intent.goal,
                    },
                )
            return None
        except Exception:
            return None

    @staticmethod
    def _strip_prefix(value: str, prefix: str) -> str:
        lowered = value.lower()
        if lowered.startswith(prefix):
            return value[len(prefix) :].strip()
        return value
