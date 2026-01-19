"""Prompt assembly logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from princess_ai.schemas.events import Event


@dataclass(slots=True)
class Persona:
    name: str = "princess"
    description: str = (
        "Princess is a streamer-style companion: warm, engaging, playful, and attentive."
    )
    safety: str = "Avoid unsafe or explicit content. Be friendly and respectful."


@dataclass(slots=True)
class ContextInputs:
    persona: Persona
    conversation: Iterable[Event]
    memories: Iterable[str]
    goals: Iterable[str]
    safety_rules: Iterable[str]


class ContextBuilder:
    def build(self, inputs: ContextInputs) -> str:
        persona_block = f"Persona: {inputs.persona.name}\n{inputs.persona.description}\n"
        safety_block = "\n".join([inputs.persona.safety, *inputs.safety_rules])
        memory_block = "\n".join(inputs.memories)
        goals_block = "\n".join(inputs.goals)
        conversation_block = "\n".join(
            f"{event.username}: {event.text}" for event in inputs.conversation
        )
        return (
            f"{persona_block}\n"
            f"Safety Rules:\n{safety_block}\n\n"
            f"Memories:\n{memory_block}\n\n"
            f"Goals:\n{goals_block}\n\n"
            f"Conversation:\n{conversation_block}\n\n"
            "Princess Response:"
        )
