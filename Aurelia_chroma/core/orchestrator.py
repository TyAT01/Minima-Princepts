from __future__ import annotations

import logging
from dataclasses import dataclass

from core.emotion import EmotionEngine
from core.filters import ContentFilter
from core.persona import Persona
from llm.llama_cpp_client import LlamaCppClient
from memory.digestor import MemoryDigestor
from memory.llm_scorer import LlmScorer
from memory.store import MemoryStore

logger = logging.getLogger(__name__)


@dataclass
class OrchestratorResponse:
    text: str
    emotion: str
    memory_id: str | None = None


class Orchestrator:
    def __init__(
        self,
        persona: Persona,
        memory_store: MemoryStore,
        llm_client: LlamaCppClient,
        filter_engine: ContentFilter | None = None,
        emotion_engine: EmotionEngine | None = None,
    ) -> None:
        self._persona = persona
        self._memory = memory_store
        self._llm = llm_client
        self._filter = filter_engine or ContentFilter()
        self._emotion = emotion_engine or EmotionEngine()
        self._digestor = MemoryDigestor()
        self._scorer = LlmScorer(llm_client)

    @property
    def memory_store(self) -> MemoryStore:
        return self._memory

    def respond(self, text: str, source: str = "local") -> OrchestratorResponse:
        filter_result = self._filter.apply(text)
        if filter_result.flagged:
            logger.warning("Filtered message from %s: %s", source, filter_result.reason)
            return OrchestratorResponse(text="Message blocked by safety filter.", emotion="alert")

        emotion_state = self._emotion.detect(text)
        memory_id = self._memory.store_entry(source=source, user_text=text)
        summary = self._digestor.digest(text)
        relevance = self._scorer.score(text, summary)

        prompt = (
            f"Persona: {self._persona.description}\n"
            f"Emotion tone: {emotion_state.tone}\n"
            f"Memory summary: {summary} (score {relevance:.2f})\n"
            f"User: {text}\n"
            "Assistant:"
        )
        response = self._llm.complete(prompt)
        return OrchestratorResponse(text=self._persona.apply(response), emotion=emotion_state.tone, memory_id=memory_id)
