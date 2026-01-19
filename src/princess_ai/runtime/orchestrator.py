"""Main runtime loop orchestrator."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from princess_ai.context.builder import ContextBuilder, ContextInputs, Persona
from princess_ai.emotion.engine import EmotionEngine
from princess_ai.input_adapters.base import InputAdapter
from princess_ai.learning.controller import LearningController
from princess_ai.llm.engine import GenerationConfig, LLMEngine
from princess_ai.memory.retrieval import MemoryRetriever
from princess_ai.memory.state import ConversationState
from princess_ai.memory.store import MemoryRecord, MemoryStore
from princess_ai.personality.layer import PersonalityLayer
from princess_ai.safety.filter import SafetyFilter
from princess_ai.schemas.events import Event, OutputMessage
from princess_ai.thought.inner import InnerThought
from princess_ai.tools.router import ToolCall, ToolRouter


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
        self._last_activity = time.monotonic()
        self._idle_interval = 6.0
        self._logger = logging.getLogger(__name__)
        self._autonomous_prompts = [
            "Reflect on the recent conversation and share a helpful thought.",
            "Scan the conversation history and propose a next best action.",
            "Offer a proactive check-in or suggestion based on current goals.",
            "Summarize what you've learned recently and how it affects your plan.",
        ]
        self._autonomous_index = 0

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                events = list(self._deps.adapter.poll())
            except Exception as exc:  # noqa: BLE001 - keep runtime alive
                self._logger.exception("Adapter polling failed: %s", exc)
                await asyncio.sleep(0.2)
                continue
            if not events:
                if self._should_emit_autonomous_event():
                    events = [self._build_autonomous_event()]
                else:
                    await asyncio.sleep(0.1)
                    continue
            for event in events:
                try:
                    event.text = self._deps.safety_filter.filter_input(event.text)
                except Exception as exc:  # noqa: BLE001 - keep runtime alive
                    self._logger.exception("Safety filter failed for input: %s", exc)
            self._conversation.add_events(events)
            self._last_activity = time.monotonic()
            try:
                await self._respond()
            except Exception as exc:  # noqa: BLE001 - keep runtime alive
                self._logger.exception("Response generation failed: %s", exc)
                await asyncio.sleep(0.2)

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
        tool_call = self._deps.tool_router.select_tool(intent)
        tool_result = self._execute_tool(tool_call, last_event.text) if tool_call else None
        if tool_result:
            context = context.replace(
                "Princess Response:",
                f"Tool Result:\n{tool_result}\n\nPrincess Response:",
            )
        response = self._deps.llm.generate(context, GenerationConfig())
        response = self._deps.personality_layer.apply(response)
        response = self._deps.emotion_engine.express(response)
        output = self._deps.safety_filter.filter_output(
            OutputMessage(text=response, intent=intent.goal)
        )
        print(output.text)

    def _should_emit_autonomous_event(self) -> bool:
        return (time.monotonic() - self._last_activity) >= self._idle_interval

    def _build_autonomous_event(self) -> Event:
        prompt = self._autonomous_prompts[self._autonomous_index]
        self._autonomous_index = (self._autonomous_index + 1) % len(
            self._autonomous_prompts
        )
        return Event(
            source="autonomous",
            user_id="system",
            username="system",
            text=prompt,
            metadata={"autonomous": True},
        )

    def _execute_tool(self, tool_call: ToolCall, last_message: str) -> str | None:
        if tool_call.name == "store_memory":
            text = tool_call.payload.get("text", last_message).strip()
            if not text:
                return "No memory stored (empty message)."
            record = MemoryRecord(text=text, importance=1.0, timestamp=time.time())
            self._deps.memory_store.add_memory(record)
            return f"Saved memory: {text}"
        return None

    def stop(self) -> None:
        self._running = False
