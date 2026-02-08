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

    def __init__(self, db_path: Path | str = "./nym_memory", collection_name: str = "nym_ai_memories", max_short_term: int = 15):
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
        self._last_seen: Dict[str, datetime] = {}
        self._load_last_seen_times()

    def _load_last_seen_times(self):
        """Initializes the last interaction times for all users from optimized system metadata."""
        try:
            # Try to fetch from system_metadata first (efficient)
            meta_records = self._collection.get(where={"type": "system_metadata"}, include=["metadatas"])
            if meta_records and meta_records["metadatas"]:
                for m in meta_records["metadatas"]:
                    if not m: continue
                    uid = m.get("user_id")
                    ts_str = m.get("timestamp")
                    if uid and ts_str:
                        self._last_seen[uid] = datetime.fromisoformat(ts_str)
                logger.info(f"Loaded last seen times for {len(self._last_seen)} users from metadata.")
                return

            # Fallback for older databases: Fetch all interaction metadatas (slow but correct once)
            logger.info("No system metadata found. Rebuilding last seen cache from interactions...")
            all_meta = self._collection.get(where={"type": "interaction"}, include=["metadatas"])
            if all_meta and all_meta["metadatas"]:
                for m in all_meta["metadatas"]:
                    if not m: continue
                    uid = m.get("user_id", "default_user")
                    ts_str = m.get("timestamp")
                    if ts_str:
                        ts = datetime.fromisoformat(ts_str)
                        if uid not in self._last_seen or ts > self._last_seen[uid]:
                            self._last_seen[uid] = ts
        except Exception as e:
            logger.debug(f"Failed to load last seen times: {e}")

    def count(self) -> int:
        """Returns the total number of items in the long-term collection."""
        return self._collection.count()

    def add_interaction(self, user_text: str, bot_text: str, user_id: str = "default_user"):
        """Adds a new interaction to both short-term and long-term memory."""
        now = datetime.now(timezone.utc)
        self._last_seen[user_id] = now # Update cache
        timestamp_str = now.isoformat()

        # 1. Add to Vector DB (Long-term)
        memory_id = f"mem_{now.timestamp()}"
        document = f"User ({user_id}): {user_text}\nNym: {bot_text}"

        self._collection.add(
            ids=[memory_id],
            documents=[document],
            metadatas=[{
                "user_id": user_id,
                "user_text": user_text,
                "bot_text": bot_text,
                "timestamp": timestamp_str,
                "type": "interaction"
            }]
        )

        # 2. Update System Metadata (for efficient startup next time)
        self._collection.upsert(
            ids=[f"last_seen_{user_id}"],
            documents=[f"Last interaction with {user_id}"],
            metadatas=[{
                "user_id": user_id,
                "timestamp": timestamp_str,
                "type": "system_metadata"
            }]
        )

        # 2. Add to Short-term Buffer
        self.short_term_buffer.append({"role": "user", "content": user_text})
        self.short_term_buffer.append({"role": "assistant", "content": bot_text})

        # Keep buffer within limits (pairs of user/assistant)
        while len(self.short_term_buffer) > self.max_short_term * 2:
            self.short_term_buffer.pop(0)
            self.short_term_buffer.pop(0)

    def store_insight(self, insight: str, source: str = "reflection", user_id: Optional[str] = None):
        """Stores a lesson learned or a significant fact for long-term recall."""
        now = datetime.now(timezone.utc)
        insight_id = f"insight_{now.timestamp()}"

        metadata = {
            "insight": insight,
            "source": source,
            "timestamp": now.isoformat(),
            "type": "insight"
        }
        if user_id:
            metadata["user_id"] = user_id

        self._collection.add(
            ids=[insight_id],
            documents=[insight],
            metadatas=[metadata]
        )
        logger.info(f"Stored insight: {insight_id}")

    def store_episodic_memory(self, event_description: str, importance: int = 5):
        """Stores a notable event or personal experience."""
        now = datetime.now(timezone.utc)
        event_id = f"event_{now.timestamp()}"

        self._collection.add(
            ids=[event_id],
            documents=[event_description],
            metadatas=[{
                "description": event_description,
                "importance": importance,
                "timestamp": now.isoformat(),
                "type": "episodic"
            }]
        )
        logger.info(f"Stored episodic memory: {event_id}")

    def update_user_profile(self, user_id: str, fact: str):
        """Adds a specific fact about a person to their profile."""
        now = datetime.now(timezone.utc)
        fact_id = f"profile_{user_id}_{now.timestamp()}"

        self._collection.add(
            ids=[fact_id],
            documents=[f"Fact about {user_id}: {fact}"],
            metadatas=[{
                "user_id": user_id,
                "fact": fact,
                "timestamp": now.isoformat(),
                "type": "profile_fact"
            }]
        )
        logger.info(f"Updated profile for {user_id}")

    def search_relevant_memories(self, query: str, n_results: int = 8, filter_type: Optional[str] = None, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Searches memory for relevant past interactions, insights, or profile facts."""
        where = {}
        if filter_type:
            where["type"] = filter_type
        if user_id:
            # Note: ChromaDB 'where' with multiple conditions usually needs '$and'
            if filter_type:
                where = {"$and": [{"type": filter_type}, {"user_id": user_id}]}
            else:
                where = {"user_id": user_id}

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

    def get_full_context(self, query: str, user_id: Optional[str] = None, objectives: Optional[List[str]] = None) -> str:
        """Combines relevant long-term memories, insights, and user profile facts into a context string."""
        # Increase results for broader context
        memories = self.search_relevant_memories(query, n_results=12, user_id=user_id)

        interactions = [m["content"] for m in memories if m["metadata"].get("type") == "interaction"]
        insights = [m["content"] for m in memories if m["metadata"].get("type") == "insight"]
        profile_facts = [m["content"] for m in memories if m["metadata"].get("type") == "profile_fact"]
        episodic = [m["content"] for m in memories if m["metadata"].get("type") == "episodic"]

        context_parts = []
        if objectives:
            context_parts.append("### [ACTIVE SESSION OBJECTIVES & RECENT REHEARSALS]\n" + "\n".join([f"- {o}" for o in objectives]))
        if user_id:
            context_parts.append(f"### [USER PROFILE: {user_id}]\n" + (f"Recognized {user_id}. Relevant facts: " + ", ".join(profile_facts) if profile_facts else f"New user or no specific facts stored for {user_id}."))
        elif profile_facts:
            context_parts.append("### [PEOPLE & PROFILES]\n" + "\n".join([f"- {f}" for f in profile_facts]))

        if episodic:
            context_parts.append("### [NOTABLE EVENTS & EXPERIENCES]\n" + "\n".join([f"- {e}" for e in episodic]))

        if insights:
            context_parts.append("### [CORE INSIGHTS & LESSONS]\n" + "\n".join([f"- {i}" for i in insights]))

        if interactions:
            context_parts.append("### [PAST RELEVANT INTERACTIONS]\n" + "\n---\n".join(interactions[:5]))

        return "\n\n".join(context_parts) if context_parts else "No specific past context found."

    def get_history(self) -> List[Dict[str, str]]:
        """Returns the current short-term conversation history."""
        return self.short_term_buffer

    def clear_short_term(self):
        """Clears the short-term buffer."""
        self.short_term_buffer = []

    def get_last_interaction_time(self, user_id: str = "default_user") -> Optional[datetime]:
        """Retrieves the timestamp of the last interaction for a specific user (using cache)."""
        return self._last_seen.get(user_id)
