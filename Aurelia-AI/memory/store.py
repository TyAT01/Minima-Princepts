from __future__ import annotations
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Dict, Optional

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

logger = logging.getLogger(__name__)

class MemoryStore:
    """Intelligent memory system with short-term buffer, long-term vector storage, and insights."""

    def __init__(self, db_path: Path | str = "./aurelia_memory", collection_name: str = "aurelia_ai_memories", max_short_term: int = 15):
        self.db_path = Path(db_path)
        self.db_path.mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(path=str(self.db_path))
        self._embedding_function = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")

        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            embedding_function=self._embedding_function,
        )

        self.short_term_buffer: List[Dict[str, str]] = []
        self.max_short_term = max_short_term

    def add_interaction(self, user_text: str, bot_text: str):
        """Adds a new interaction to both short-term and long-term memory."""
        now = datetime.now(timezone.utc)
        timestamp_str = now.isoformat()

        # 1. Add to Vector DB (Long-term)
        memory_id = f"mem_{now.timestamp()}"
        document = f"User: {user_text}\nAurelia: {bot_text}"

        self._collection.add(
            ids=[memory_id],
            documents=[document],
            metadatas=[{
                "user_text": user_text,
                "bot_text": bot_text,
                "timestamp": timestamp_str,
                "type": "interaction"
            }]
        )

        # 2. Add to Short-term Buffer
        self.short_term_buffer.append({"role": "user", "content": user_text})
        self.short_term_buffer.append({"role": "assistant", "content": bot_text})

        # Keep buffer within limits (pairs of user/assistant)
        while len(self.short_term_buffer) > self.max_short_term * 2:
            self.short_term_buffer.pop(0)
            self.short_term_buffer.pop(0)

    def store_insight(self, insight: str, source: str = "reflection"):
        """Stores a lesson learned or a significant fact for long-term recall."""
        now = datetime.now(timezone.utc)
        insight_id = f"insight_{now.timestamp()}"

        self._collection.add(
            ids=[insight_id],
            documents=[insight],
            metadatas=[{
                "insight": insight,
                "source": source,
                "timestamp": now.isoformat(),
                "type": "insight"
            }]
        )
        logger.info(f"Stored insight: {insight_id}")

    def search_relevant_memories(self, query: str, n_results: int = 5, filter_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Searches memory for relevant past interactions or insights."""
        where = {}
        if filter_type:
            where["type"] = filter_type

        results = self._collection.query(
            query_texts=[query],
            n_results=n_results,
            where=where if where else None
        )

        memories = []
        if results and results.get("metadatas") and results["metadatas"][0]:
            for i, metadata in enumerate(results["metadatas"][0]):
                if metadata:
                    memories.append({
                        "content": results["documents"][0][i],
                        "metadata": metadata
                    })
        return memories

    def get_full_context(self, query: str) -> str:
        """Combines relevant long-term memories and insights into a context string."""
        # Query for both interactions and insights
        memories = self.search_relevant_memories(query, n_results=5)

        interactions = [m["content"] for m in memories if m["metadata"].get("type") == "interaction"]
        insights = [m["content"] for m in memories if m["metadata"].get("type") == "insight"]

        context_parts = []
        if insights:
            context_parts.append("### [CORE INSIGHTS & LESSONS]\n" + "\n".join([f"- {i}" for i in insights]))

        if interactions:
            context_parts.append("### [PAST RELEVANT INTERACTIONS]\n" + "\n---\n".join(interactions))

        return "\n\n".join(context_parts) if context_parts else "No specific past context found."

    def get_history(self) -> List[Dict[str, str]]:
        """Returns the current short-term conversation history."""
        return self.short_term_buffer

    def clear_short_term(self):
        """Clears the short-term buffer."""
        self.short_term_buffer = []
