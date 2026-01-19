"""Main runtime loop orchestrator."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from dataclasses import dataclass, field

from princess_ai.context.builder import ContextBuilder, ContextInputs, Persona
from princess_ai.emotion.engine import EmotionEngine
from princess_ai.input_adapters.base import InputAdapter
from princess_ai.learning.controller import LearningController
from princess_ai.logging.telemetry import InMemoryLogStore, LogEntry
from princess_ai.llm.engine import GenerationConfig, LLMEngine
from princess_ai.memory.policy import MemoryPolicy
from princess_ai.memory.retrieval import MemoryRetriever
from princess_ai.memory.state import ConversationState
from princess_ai.memory.store import MemoryStore
from princess_ai.personality.layer import PersonalityLayer
from princess_ai.runtime.control import ControlHub
from princess_ai.runtime.event_router import EventRouter
from princess_ai.runtime.idle import IdleActivityPlanner
from princess_ai.runtime.session import SessionManager
from princess_ai.runtime.telemetry import TelemetryHub
from princess_ai.safety.filter import SafetyFilter
from princess_ai.schemas.events import Event, OutputMessage
from princess_ai.thought.inner import InnerThought
from princess_ai.tools.router import ToolCall, ToolRouter
from princess_ai.output.voice import VoiceOutputManager, VoiceOutputPacket


@dataclass(slots=True)
class RuntimeDependencies:
    adapter: InputAdapter
    llm: LLMEngine
    memory_store: MemoryStore
    memory_retriever: MemoryRetriever
    memory_policy: MemoryPolicy
    context_builder: ContextBuilder
    safety_filter: SafetyFilter
    personality_layer: PersonalityLayer
    emotion_engine: EmotionEngine
    inner_thought: InnerThought
    tool_router: ToolRouter
    learning_controller: LearningController
    event_router: EventRouter
    session_manager: SessionManager
    control_hub: ControlHub
    log_store: InMemoryLogStore
    telemetry: TelemetryHub
    voice_output: VoiceOutputManager | None = None
    use_streaming: bool = False
    persona: Persona = field(default_factory=Persona)


class RuntimeOrchestrator:
    def __init__(self, deps: RuntimeDependencies) -> None:
        self._deps = deps
        self._conversation = ConversationState()
        self._persona = deps.persona
        self._running = False
        self._last_activity = time.monotonic()
        self._idle_interval = 6.0
        self._logger = logging.getLogger(__name__)
        self._idle_planner = IdleActivityPlanner()
        self._last_idle_activity: str | None = None

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                events = list(self._deps.adapter.poll())
            except Exception as exc:  # noqa: BLE001 - keep runtime alive
                self._logger.exception("Adapter polling failed: %s", exc)
                await asyncio.sleep(0.2)
                continue
            manual = self._deps.control_hub.drain_manual()
            for message in manual:
                events.append(
                    Event(
                        source="manual",
                        user_id="operator",
                        username="operator",
                        text=message,
                        metadata={"priority": "high"},
                    )
                )
            if not events:
                if self._should_emit_autonomous_event():
                    events = [self._build_autonomous_event()]
                else:
                    await asyncio.sleep(0.1)
                    continue
            events = self._deps.event_router.select(events, mode=self._deps.session_manager.snapshot().mode)
            for event in events:
                try:
                    original_text = event.text
                    event.text = self._deps.safety_filter.filter_input(event.text)
                    if original_text != event.text:
                        self._deps.log_store.add(
                            LogEntry(
                                name="safety_rewrite",
                                payload={"source": event.source, "before": original_text, "after": event.text},
                            )
                        )
                except Exception as exc:  # noqa: BLE001 - keep runtime alive
                    self._logger.exception("Safety filter failed for input: %s", exc)
                self._deps.session_manager.upsert_channel(
                    f"{event.source}:{event.metadata.get('channel', event.user_id)}",
                    event.user_id,
                    event.text,
                )
                self._deps.log_store.add(LogEntry(name="input", payload={"source": event.source, "text": event.text}))
            self._conversation.add_events(events)
            self._last_activity = time.monotonic()
            try:
                await self._respond()
            except Exception as exc:  # noqa: BLE001 - keep runtime alive
                self._logger.exception("Response generation failed: %s", exc)
                await asyncio.sleep(0.2)

    async def _respond(self) -> None:
        recent_events = self._conversation.recent()
        if not recent_events:
            self._logger.warning("No recent events available to respond to.")
            return
        last_event = recent_events[-1]
        scope_key = f"{last_event.source}:{last_event.metadata.get('channel', last_event.user_id)}"
        memories = list(self._deps.memory_store.list_memories(scope=scope_key))
        retrieved = self._deps.memory_retriever.retrieve(last_event.text, memories)
        if retrieved:
            self._deps.log_store.add(
                LogEntry(name="memory_retrieved", payload={"items": [item.text for item in retrieved]})
            )
        session = self._deps.session_manager.snapshot()
        stream_mode = self._deps.control_hub.snapshot().stream_mode
        safety_rules = ["No explicit content", "Avoid ban-worthy topics"]
        goals = ["Keep the chat engaged", "Stay in character"]
        if stream_mode or session.persona_mode == "stream":
            safety_rules.append("Stream-safe mode: avoid controversial or sensitive topics.")
            goals.append("Engage with stream chat concisely and warmly.")
        system_notes = self._build_system_notes()
        context = self._deps.context_builder.build(
            ContextInputs(
                persona=self._persona,
                conversation=recent_events,
                memories=[item.text for item in retrieved],
                goals=goals,
                safety_rules=safety_rules,
                system_notes=system_notes,
            )
        )
        intent = self._deps.inner_thought.plan(context)
        tool_call = self._deps.tool_router.select_tool(intent)
        if tool_call:
            self._deps.log_store.add(
                LogEntry(name="tool_call", payload={"name": tool_call.name, "payload": tool_call.payload})
            )
        tool_result = self._execute_tool(tool_call, last_event.text, scope_key) if tool_call else None
        if tool_result:
            context = context.replace(
                self._persona.response_marker,
                f"Tool Result:\n{tool_result}\n\n{self._persona.response_marker}",
            )
        self._deps.log_store.add(LogEntry(name="decision", payload={"intent": intent.goal}))
        response = self._generate_response(context)
        response = self._deps.personality_layer.apply(response)
        response = self._deps.emotion_engine.express(response)
        emotion_state = self._deps.emotion_engine.state
        self._deps.telemetry.update_emotion(
            mood=emotion_state.mood,
            valence=emotion_state.valence,
            arousal=emotion_state.arousal,
        )
        output = self._deps.safety_filter.filter_output(OutputMessage(text=response, intent=intent.goal))
        if output.text != response:
            self._deps.log_store.add(
                LogEntry(name="safety_rewrite", payload={"source": "output", "before": response, "after": output.text})
            )
        if not self._deps.session_manager.snapshot().muted:
            print(output.text)
        self._deps.log_store.add(LogEntry(name="output", payload={"text": output.text}))
        if self._deps.voice_output:
            self._deps.voice_output.speak(
                VoiceOutputPacket(
                    text=output.text,
                    source=last_event.source,
                    channel=last_event.metadata.get("channel"),
                )
            )

    def _should_emit_autonomous_event(self) -> bool:
        return (time.monotonic() - self._last_activity) >= self._idle_interval

    def _build_autonomous_event(self) -> Event:
        activity, prompt = self._idle_planner.choose(self._last_idle_activity)
        self._last_idle_activity = activity
        return Event(
            source="autonomous",
            user_id="system",
            username="system",
            text=prompt,
            metadata={"autonomous": True, "idle_activity": activity},
        )

    def _execute_tool(self, tool_call: ToolCall, last_message: str, scope_key: str) -> str | None:
        try:
            if tool_call.name == "store_memory":
                text = tool_call.payload.get("text", last_message).strip()
                if not text:
                    return "No memory stored (empty message)."
                importance = self._deps.memory_policy.reinforce(text, 1.0)
                self._deps.memory_policy.store_memory(text, importance, scope=scope_key)
                self._deps.log_store.add(
                    LogEntry(name="memory_saved", payload={"text": text, "importance": importance})
                )
                return f"Saved memory: {text}"
            if tool_call.name == "get_time":
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                return f"Current time: {now}"
            if tool_call.name == "summarize_recent":
                return self._summarize_recent()
        except Exception as exc:  # noqa: BLE001 - keep tool execution resilient
            self._logger.exception("Tool execution failed: %s", exc)
            return "Tool execution failed."
        return None

    def _summarize_recent(self) -> str:
        try:
            events = self._conversation.recent()[-5:]
            if not events:
                return "No recent conversation to summarize."
            summary_lines = [f"- {event.username}: {event.text}" for event in events]
            return "Recent conversation summary:\n" + "\n".join(summary_lines)
        except Exception as exc:  # noqa: BLE001 - keep summary resilient
            self._logger.exception("Failed to summarize recent events: %s", exc)
            return "Unable to summarize recent conversation."

    def _generate_response(self, context: str) -> str:
        config = GenerationConfig()
        try:
            if not self._deps.use_streaming:
                start = time.monotonic()
                response = self._deps.llm.generate(context, config)
                elapsed = (time.monotonic() - start) * 1000
                self._deps.telemetry.update_qos(end_to_end_latency_ms=elapsed)
                return response
            self._deps.telemetry.reset_llm_tokens()
            tokens = []
            start = time.monotonic()
            first_token_at = None
            for token in self._deps.llm.stream(context, config):
                tokens.append(token)
                self._deps.telemetry.add_llm_token(token)
                if first_token_at is None:
                    first_token_at = time.monotonic()
                    self._deps.telemetry.update_qos(
                        time_to_first_token_ms=(first_token_at - start) * 1000
                    )
            self._deps.telemetry.update_qos(end_to_end_latency_ms=(time.monotonic() - start) * 1000)
            return "".join(tokens)
        except Exception as exc:  # noqa: BLE001 - keep runtime alive
            self._logger.exception("LLM response generation failed: %s", exc)
            return "I'm having trouble reaching my language model right now, but I'm still here."

    def stop(self) -> None:
        self._running = False

    def _build_system_notes(self) -> list[str]:
        notes: list[str] = []
        try:
            errors = [
                entry
                for entry in self._deps.log_store.snapshot()
                if entry.name == "error"
            ]
            if errors:
                notes.append("Recent runtime errors:")
                for entry in errors[-5:]:
                    payload = entry.payload
                    message = payload.get("message", "Unknown error")
                    logger_name = payload.get("logger", "runtime")
                    notes.append(f"- {logger_name}: {message}")
        except Exception as exc:  # noqa: BLE001 - keep notes resilient
            self._logger.exception("Failed to build system notes: %s", exc)
        return notes
