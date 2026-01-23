from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

logger = logging.getLogger(__name__)


@dataclass
class Memory:
    id: str
    source: str
    user_text: str
    bot_text: str
    created_at: datetime
    last_accessed_at: datetime
    embedding: Optional[list[float]] = None

    def to_chroma(self) -> dict[str, Any]:
        """Return a dictionary representation for ChromaDB metadata."""
        return {
            "source": self.source,
            "user_text": self.user_text,
            "bot_text": self.bot_text,
            "created_at": self.created_at.isoformat(),
            "last_accessed_at": self.last_accessed_at.isoformat(),
        }


class ChromaMemoryStore:
    def __init__(self, db_path: Path, collection_name: str = "aurelia_memories"):
        self._client = chromadb.PersistentClient(path=str(db_path))
        self._embedding_function = SentenceTransformerEmbeddingFunction()
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            embedding_function=self._embedding_function,
        )

    def store_memory(self, source: str, user_text: str, bot_text: str) -> None:
        now = datetime.utcnow()
        memory_id = f"{source}-{now.timestamp()}"
        memory = Memory(
            id=memory_id,
            source=source,
            user_text=user_text,
            bot_text=bot_text,
            created_at=now,
            last_accessed_at=now,
        )
        self._collection.add(
            ids=[memory_id],
            documents=[user_text],  # The user text is used for similarity search
            metadatas=[memory.to_chroma()],
        )
        logger.info("Stored memory: %s", memory_id)

    def search(self, query: str, n_results: int = 5) -> list[dict[str, Any]]:
        results = self._collection.query(
            query_texts=[query],
            n_results=n_results,
        )
        memories = []
        if results and "metadatas" in results and results["metadatas"]:
            for metadata in results["metadatas"][0]:
                if metadata:
                    memories.append(metadata)
        return memories

    def list_memories(self) -> list[dict[str, Any]]:
        """Return all memories from the collection."""
        results = self._collection.get()
        return results.get("metadatas", [])
