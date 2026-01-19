"""Memory retrieval with lightweight scoring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from princess_ai.memory.store import MemoryRecord


@dataclass(slots=True)
class RetrievedMemory:
    text: str
    score: float


class MemoryRetriever:
    def retrieve(self, query: str, memories: Iterable[MemoryRecord], limit: int = 5) -> list[RetrievedMemory]:
        scored: list[RetrievedMemory] = []
        try:
            query_terms = {term.lower() for term in query.split() if term}
            for memory in memories:
                memory_terms = {term.lower() for term in memory.text.split() if term}
                overlap = query_terms.intersection(memory_terms)
                score = len(overlap) + memory.importance
                scored.append(RetrievedMemory(text=memory.text, score=score))
            scored.sort(key=lambda item: item.score, reverse=True)
            return scored[:limit]
        except Exception:
            return scored[:limit]
