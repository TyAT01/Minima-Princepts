"""Prompt assembly logic."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

from princess_ai.schemas.events import Event


@dataclass(slots=True)
class Persona:
    name: str = "Aurelia Vale"
    description: str = (
        "Aurelia Vale is a streamer-style companion: warm, engaging, playful, and attentive."
    )
    safety: str = "Avoid unsafe or explicit content. Be friendly and respectful."
    response_marker: str = "Aurelia Response:"
    llm_constraints: str = ""


@dataclass(slots=True)
class ContextInputs:
    persona: Persona
    conversation: Iterable[Event]
    memories: Iterable[str]
    goals: Iterable[str]
    safety_rules: Iterable[str]
    system_notes: Iterable[str]


class ContextBuilder:
    def build(self, inputs: ContextInputs) -> str:
        logger = logging.getLogger(__name__)
        try:
            if not inputs.conversation:
                logger.warning("Empty conversation supplied to context builder.")
            persona_block = (
                f"Persona: {inputs.persona.name}\n{inputs.persona.description}\n"
            )
            if inputs.persona.llm_constraints:
                persona_block += f"Model Constraints: {inputs.persona.llm_constraints}\n"
            safety_block = "\n".join([inputs.persona.safety, *inputs.safety_rules])
            memory_block = "\n".join(text for text in inputs.memories if text)
            goals_block = "\n".join(goal for goal in inputs.goals if goal)
            notes_block = "\n".join(note for note in inputs.system_notes if note)
            conversation_block = "\n".join(
                f"{event.username}: {event.text}" for event in inputs.conversation
            )
            response_marker = inputs.persona.response_marker
            return (
                f"{persona_block}\n"
                f"Safety Rules:\n{safety_block}\n\n"
                f"Memories:\n{memory_block}\n\n"
                f"System Notes:\n{notes_block}\n\n"
                f"Goals:\n{goals_block}\n\n"
                f"Conversation:\n{conversation_block}\n\n"
                f"{response_marker}"
            )
        except Exception as exc:  # noqa: BLE001 - fallback safe prompt
            logger.exception("Failed to build prompt context: %s", exc)
            return "Persona: Aurelia Vale\nSafety Rules:\nBe safe and respectful.\n\nAurelia Response:"
