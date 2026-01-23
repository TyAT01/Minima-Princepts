from __future__ import annotations

import logging
from dataclasses import dataclass

from core.emotion import EmotionEngine
from core.filters import ContentFilter
from core.persona import Persona
from llm.llama_cpp_client import LlamaCppClient
from memory.store import ChromaMemoryStore

logger = logging.getLogger(__name__)


@dataclass
class OrchestratorResponse:
    text: str
    emotion: str


class Orchestrator:
    def __init__(
        self,
        persona: Persona,
        memory_store: ChromaMemoryStore,
        llm_client: LlamaCppClient,
        filter_engine: ContentFilter | None = None,
        emotion_engine: EmotionEngine | None = None,
    ) -> None:
        self._persona = persona
        self._memory = memory_store
        self._llm = llm_client
        self._filter = filter_engine or ContentFilter()
        self._emotion = emotion_engine or EmotionEngine()

    @property
    def memory_store(self) -> ChromaMemoryStore:
        return self._memory

    def respond(self, text: str, source: str = "local") -> OrchestratorResponse:
        filter_result = self._filter.apply(text)
        if filter_result.flagged:
            logger.warning("Filtered message from %s: %s", source, filter_result.reason)
            return OrchestratorResponse(text="Message blocked by safety filter.", emotion="alert")

        emotion_state = self._emotion.detect(text)

        # Search for relevant memories
        relevant_memories = self._memory.search(text, n_results=3)
        memory_str = "\n".join(
            [f"- User: {mem['user_text']}, Bot: {mem['bot_text']}" for mem in relevant_memories]
        )

        prompt = (
            f"{self._persona.description}\n\n"
            "Here are some relevant memories from the past:\n"
            f"{memory_str}\n\n"
            "Current conversation context:\n"
            f"Emotion tone: {emotion_state.tone}\n"
            f"User: {text}\n"
            "Assistant:"
        )

        bot_response_text = self._llm.complete(prompt)
        self._memory.store_memory(source=source, user_text=text, bot_text=bot_response_text)

        return OrchestratorResponse(
            text=self._persona.apply(bot_response_text),
            emotion=emotion_state.tone,
        )
