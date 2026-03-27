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
    def __init__(self, db_path: Path, collection_name: str = "aurelia_memories"):
        self._client = chromadb.PersistentClient(path=str(db_path))
        self._embedding_function = SentenceTransformerEmbeddingFunction()
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            embedding_function=self._embedding_function,
        )

    def store_memory(self, user_text: str, bot_text: str) -> None:
        now = datetime.now(timezone.utc)
        memory_id = f"memory-{now.timestamp()}"
        self._collection.add(
            ids=[memory_id],
            documents=[user_text],
            metadatas=[{
                "user_text": user_text,
                "bot_text": bot_text,
                "created_at": now.isoformat(),
                "last_accessed_at": now.isoformat(),
            }],
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
