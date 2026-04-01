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

    def __init__(self, db_path: Path | str = "./loki_memory", collection_name: str = "loki_ai_memories", max_short_term: int = 15):
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
        self._last_user: Optional[str] = None
        self.session_objectives: List[str] = []
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
        # Multi-user isolation: clear buffer if user switches
        if self._last_user and self._last_user != user_id:
            logger.info(f"User switch detected ({self._last_user} -> {user_id}). Clearing short-term buffer.")
            self.clear_short_term()

        self._last_user = user_id
        now = datetime.now(timezone.utc)
        self._last_seen[user_id] = now # Update cache
        timestamp_str = now.isoformat()
        timestamp_human = now.astimezone().strftime('%Y-%m-%d %I:%M %p')

        # 1. Add to Vector DB (Long-term)
        memory_id = f"mem_{now.timestamp()}"
        document = f"[{timestamp_human}] User ({user_id}): {user_text}\nLoki: {bot_text}"

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
        self.short_term_buffer.append({"role": "user", "content": f"[{timestamp_human}] {user_text}"})
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

    def store_summary(self, user_id: str, summary: str, is_global: bool = False):
        """Stores a concise summary of a conversation segment."""
        now = datetime.now(timezone.utc)
        summary_id = f"summary_{'global_' if is_global else ''}{now.timestamp()}"

        self._collection.add(
            ids=[summary_id],
            documents=[summary],
            metadatas=[{
                "user_id": user_id,
                "summary": summary,
                "timestamp": now.isoformat(),
                "type": "summary",
                "is_global": is_global
            }]
        )
        logger.info(f"Stored summary for {user_id}: {summary_id}")

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

    def get_full_context(self, query: str, user_id: Optional[str] = None, max_chars: int = 3200) -> str:
        """
        Commercial-grade context assembly using a tiered priority budget system.
        Ensures the most critical memories are included first within the token limit.
        """
        # 1. Fetch broad range of candidates
        optimized_query = f"relevant past memories for: {query}"
        raw_results = self._collection.query(
            query_texts=[optimized_query],
            n_results=25,
            where={"user_id": user_id} if user_id else None
        )

        memories = []
        if raw_results and raw_results.get("metadatas") and raw_results["metadatas"][0]:
            for i, metadata in enumerate(raw_results["metadatas"][0]):
                if metadata:
                    memories.append({
                        "content": raw_results["documents"][0][i],
                        "metadata": metadata,
                        "relevance": 1.0 - (raw_results["distances"][0][i] if "distances" in raw_results else 0.5)
                    })

        # 2. Categorize and Rank by Importance/Relevance
        categories = {
            "profile": [],
            "episodic_high": [],
            "episodic_normal": [],
            "summary": [],
            "insight": [],
            "interaction": []
        }

        for m in memories:
            m_type = m["metadata"].get("type")
            importance = int(m["metadata"].get("importance", 5))

            if m_type == "profile_fact":
                categories["profile"].append(m)
            elif m_type == "episodic":
                if importance >= 7:
                    categories["episodic_high"].append(m)
                else:
                    categories["episodic_normal"].append(m)
            elif m_type == "summary":
                categories["summary"].append(m)
            elif m_type == "insight":
                categories["insight"].append(m)
            elif m_type == "interaction":
                categories["interaction"].append(m)

        # 3. Assemble with Budget (Priority Order)
        context_blocks = []
        current_chars = 0

        # Helper to add blocks if budget permits
        def add_to_context(title: str, items: List[str], prefix: str = "- ", joiner: str = "\n"):
            nonlocal current_chars
            if not items: return

            header = f"### [{title}]\n"
            block_content = joiner.join([f"{prefix}{item}" for item in items])
            full_block = header + block_content + "\n\n"

            if current_chars + len(full_block) <= max_chars:
                context_blocks.append(full_block)
                current_chars += len(full_block)
            elif current_chars < max_chars:
                # Partial add if possible (for interactions or lists)
                remaining = max_chars - current_chars - len(header) - 10
                if remaining > 100:
                    truncated_content = block_content[:remaining] + "... [TRUNCATED]"
                    context_blocks.append(header + truncated_content + "\n\n")
                    current_chars = max_chars

        # Priority 1: Session Objectives (Always try to include)
        if self.session_objectives:
            add_to_context("CURRENT SESSION OBJECTIVES", self.session_objectives)

        # Priority 2: User Profile
        profile_texts = list(set([m["content"] for m in categories["profile"]]))
        if user_id:
            profile_title = f"USER PROFILE: {user_id}"
            if not profile_texts:
                profile_texts = [f"No specific facts stored for {user_id} yet."]
            add_to_context(profile_title, profile_texts)
        elif profile_texts:
            add_to_context("RELEVANT PEOPLE & PROFILES", profile_texts[:5])

        # Priority 3: High Importance Episodic (e.g., Session Start)
        add_to_context("CRITICAL PAST EVENTS", [m["content"] for m in categories["episodic_high"]])

        # Priority 4: Summaries (The 'believable' long-term narrative)
        # Prioritize Global Summaries for high-level continuity, then recent segment summaries
        global_sums = [m["content"] for m in categories["summary"] if m["metadata"].get("is_global")]
        local_sums = [m["content"] for m in categories["summary"] if not m["metadata"].get("is_global")]

        # Sort local summaries by timestamp (newest first)
        categories["summary"].sort(key=lambda x: x["metadata"].get("timestamp", ""), reverse=True)

        # Take up to 2 global and 3 local for a balanced perspective
        balanced_summaries = global_sums[:2] + [m["content"] for m in categories["summary"] if not m["metadata"].get("is_global")][:3]
        add_to_context("CONVERSATION SUMMARIES", balanced_summaries)

        # Priority 5: Insights & Lessons
        add_to_context("CORE INSIGHTS", [m["content"] for m in categories["insight"][:8]])

        # Priority 6: Normal Episodic
        add_to_context("NOTABLE EXPERIENCES", [m["content"] for m in categories["episodic_normal"][:5]])

        # Priority 7: Relevant Interactions (Raw history)
        interaction_texts = [m["content"] for m in categories["interaction"]]
        add_to_context("RECENT RELEVANT INTERACTIONS", interaction_texts[:5], prefix="", joiner="\n---\n")

        return "".join(context_blocks).strip() if context_blocks else "No specific past context found."

    def get_history(self) -> List[Dict[str, str]]:
        """Returns the current short-term conversation history."""
        return self.short_term_buffer

    def clear_short_term(self):
        """Clears the short-term buffer."""
        self.short_term_buffer = []

    def get_last_interaction_time(self, user_id: str = "default_user") -> Optional[datetime]:
        """Retrieves the timestamp of the last interaction for a specific user (using cache)."""
        return self._last_seen.get(user_id)
