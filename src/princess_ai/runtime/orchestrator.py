"""Main runtime loop orchestrator."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Iterable

from princess_ai.context.builder import ContextBuilder, ContextInputs, Persona
from princess_ai.emotion.engine import EmotionEngine
from princess_ai.input_adapters.base import InputAdapter
from princess_ai.learning.controller import LearningController
from princess_ai.llm.engine import GenerationConfig, LLMEngine
from princess_ai.memory.retrieval import MemoryRetriever
from princess_ai.memory.state import ConversationState
from princess_ai.memory.store import MemoryStore
from princess_ai.personality.layer import PersonalityLayer
from princess_ai.safety.filter import SafetyFilter
from princess_ai.schemas.events import OutputMessage
from princess_ai.thought.inner import InnerThought
from princess_ai.tools.router import ToolRouter


@dataclass(slots=True)
class RuntimeDependencies:
    adapter: InputAdapter
    llm: LLMEngine
    memory_store: MemoryStore
    memory_retriever: MemoryRetriever
    context_builder: ContextBuilder
    safety_filter: SafetyFilter
    personality_layer: PersonalityLayer
    emotion_engine: EmotionEngine
    inner_thought: InnerThought
    tool_router: ToolRouter
    learning_controller: LearningController


class RuntimeOrchestrator:
    def __init__(self, deps: RuntimeDependencies) -> None:
        self._deps = deps
        self._conversation = ConversationState()
        self._persona = Persona()
        self._running = False

    async def run(self) -> None:
        self._running = True
        while self._running:
            events = list(self._deps.adapter.poll())
            if not events:
                await asyncio.sleep(0.1)
                continue
            for event in events:
                event.text = self._deps.safety_filter.filter_input(event.text)
            self._conversation.add_events(events)
            await self._respond()

    async def _respond(self) -> None:
        recent_events = self._conversation.recent()
        last_event = recent_events[-1]
        memories = list(self._deps.memory_store.list_memories())
        retrieved = self._deps.memory_retriever.retrieve(last_event.text, memories)
        context = self._deps.context_builder.build(
            ContextInputs(
                persona=self._persona,
                conversation=recent_events,
                memories=[item.text for item in retrieved],
                goals=["Keep the chat engaged", "Stay in character"],
                safety_rules=["No explicit content", "Avoid ban-worthy topics"],
            )
        )
        intent = self._deps.inner_thought.plan(context)
        tool_call = self._deps.tool_router.select_tool(intent.goal)
        if tool_call:
            pass
        response = self._deps.llm.generate(context, GenerationConfig())
        response = self._deps.personality_layer.apply(response)
        response = self._deps.emotion_engine.express(response)
        output = self._deps.safety_filter.filter_output(
            OutputMessage(text=response, intent=intent.goal)
        )
        print(output.text)

    def stop(self) -> None:
        self._running = False
