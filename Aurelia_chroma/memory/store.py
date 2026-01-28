from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

logger = logging.getLogger(__name__)

@dataclass
class Memory:
    id: str
    user_text: str
    bot_text: str
    created_at: datetime
    last_accessed_at: datetime

class ChromaMemoryStore:
    def __init__(self, db_path: Path | None = None, collection_name: str = "aurelia_memories", max_short_term: int = 10):
        if db_path:
            self._client = chromadb.PersistentClient(path=str(db_path))
        else:
            self._client = chromadb.Client()
        self._embedding_function = SentenceTransformerEmbeddingFunction()
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            embedding_function=self._embedding_function,
        )
        self._short_term_buffer: list[dict[str, str]] = []
        self._max_short_term = max_short_term

    def store_memory(self, user_text: str, bot_text: str) -> None:
        now = datetime.now(timezone.utc)
        memory_id = f"memory-{now.timestamp()}"
        self._collection.add(
            ids=[memory_id],
            documents=[user_text],
            metadatas=[{
                "type": "interaction",
                "user_text": user_text,
                "bot_text": bot_text,
                "created_at": now.isoformat(),
                "last_accessed_at": now.isoformat(),
            }],
        )
        # Update short-term buffer
        self._short_term_buffer.append({"user": user_text, "bot": bot_text})
        if len(self._short_term_buffer) > self._max_short_term:
            self._short_term_buffer.pop(0)

        logger.info("Stored memory: %s", memory_id)

    def store_insight(self, insight: str, source: str) -> None:
        """Stores a lesson learned or a reflection."""
        now = datetime.now(timezone.utc)
        insight_id = f"insight-{now.timestamp()}"
        self._collection.add(
            ids=[insight_id],
            documents=[insight],
            metadatas=[{
                "type": "insight",
                "insight": insight,
                "source": source,
                "created_at": now.isoformat(),
            }],
        )
        logger.info("Stored insight: %s", insight_id)

    def get_short_term_context(self) -> str:
        """Returns the recent interactions as a formatted string."""
        if not self._short_term_buffer:
            return "No recent interactions."

        context_lines = []
        for i, interaction in enumerate(self._short_term_buffer):
            context_lines.append(f"Recent {i+1} - User: {interaction['user']}")
            context_lines.append(f"Recent {i+1} - Aurelia: {interaction['bot']}")
        return "\n".join(context_lines)

    def search(self, query: str, n_results: int = 5, filter_type: str = None) -> list[dict[str, Any]]:
        # For insights, we use a strict filter.
        # For interactions, we allow documents without a type for backward compatibility.
        where = {"type": "insight"} if filter_type == "insight" else None

        results = self._collection.query(
            query_texts=[query],
            n_results=n_results,
            where=where
        )
        memories = []
        if results and "metadatas" in results and results["metadatas"]:
            for metadata in results["metadatas"][0]:
                if metadata:
                    # If we are looking for interactions, skip anything explicitly marked as an insight
                    if filter_type == "interaction" and metadata.get("type") == "insight":
                        continue
                    memories.append(metadata)
        return memories
