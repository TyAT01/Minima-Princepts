"""Memory retrieval with lightweight scoring and optional vector search."""

from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass
from typing import Iterable, List

from princess_ai.memory.store import MemoryRecord


@dataclass(slots=True)
class RetrievedMemory:
    text: str
    score: float


class MemoryRetriever:
    def __init__(self) -> None:
        self._logger = logging.getLogger(__name__)
        self._vector_backend = self._init_vector_backend()

    def retrieve(self, query: str, memories: Iterable[MemoryRecord], limit: int = 5) -> list[RetrievedMemory]:
        logger = logging.getLogger(__name__)
        scored: list[RetrievedMemory] = []
        try:
            memories_list = list(memories)
            if not memories_list:
                return []
            if self._vector_backend:
                vector_hits = self._vector_backend.search(query, memories_list, limit=limit)
                if vector_hits:
                    return vector_hits
            query_terms = {term.lower() for term in query.split() if term}
            for memory in memories_list:
                memory_terms = {term.lower() for term in memory.text.split() if term}
                overlap = query_terms.intersection(memory_terms)
                score = len(overlap) + memory.importance
                scored.append(RetrievedMemory(text=memory.text, score=score))
            scored.sort(key=lambda item: item.score, reverse=True)
            return scored[:limit]
        except Exception as exc:  # noqa: BLE001 - keep retrieval resilient
            logger.exception("Failed to score memories: %s", exc)
            return scored[:limit]

    def _init_vector_backend(self) -> "FaissMemoryIndex | None":
        try:
            if importlib.util.find_spec("faiss"):
                return FaissMemoryIndex()
        except Exception as exc:  # noqa: BLE001 - keep backend resilient
            self._logger.exception("Failed to init vector backend: %s", exc)
        return None


class FaissMemoryIndex:
    """FAISS-backed semantic retrieval using hashed bag-of-words embeddings."""

    def __init__(self, dims: int = 256) -> None:
        self._dims = dims
        import faiss  # type: ignore

        self._faiss = faiss

    def search(
        self, query: str, memories: List[MemoryRecord], limit: int = 5
    ) -> list[RetrievedMemory]:
        try:
            vectors = [self._embed(record.text) for record in memories]
            query_vec = self._embed(query)
            index = self._faiss.IndexFlatIP(self._dims)
            index.add(self._to_float32(vectors))
            scores, indices = index.search(self._to_float32([query_vec]), limit)
            results: list[RetrievedMemory] = []
            for score, idx in zip(scores[0], indices[0]):
                if idx < 0 or idx >= len(memories):
                    continue
                results.append(RetrievedMemory(text=memories[idx].text, score=float(score)))
            return results
        except Exception:
            return []

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self._dims
        for token in text.lower().split():
            bucket = hash(token) % self._dims
            vec[bucket] += 1.0
        return vec

    def _to_float32(self, vectors: List[List[float]]):
        import numpy as np  # type: ignore

        return np.array(vectors, dtype="float32")
