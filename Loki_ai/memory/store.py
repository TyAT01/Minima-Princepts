from __future__ import annotations
import logging
import numpy as np
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

    def store_insight(self, insight: str, user_id: str, source: str = "reflection"):
        """Stores a lesson learned or a significant fact for long-term recall."""
        now = datetime.now(timezone.utc)
        insight_id = f"insight_{now.timestamp()}"

        metadata = {
            "user_id": user_id,
            "insight": insight,
            "source": source,
            "timestamp": now.isoformat(),
            "type": "insight"
        }

        self._collection.add(
            ids=[insight_id],
            documents=[insight],
            metadatas=[metadata]
        )
        logger.info(f"Stored insight for {user_id}: {insight_id}")

    def store_episodic_memory(self, event_description: str, user_id: str, importance: int = 5):
        """Stores a notable event or personal experience."""
        now = datetime.now(timezone.utc)
        event_id = f"event_{now.timestamp()}"

        self._collection.add(
            ids=[event_id],
            documents=[event_description],
            metadatas=[{
                "user_id": user_id,
                "description": event_description,
                "importance": importance,
                "timestamp": now.isoformat(),
                "type": "episodic"
            }]
        )
        logger.info(f"Stored episodic memory for {user_id}: {event_id}")

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

    def search_relevant_memories(
        self,
        query: str,
        n_results: int = 8,
        filter_type: Optional[str] = None,
        user_id: Optional[str] = None,
        use_mmr: bool = True,
        mmr_lambda: float = 0.5,
        hypothetical_answer: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Searches memory with optional MMR for diversity and HyDE support.
        """
        if n_results <= 0:
            return []

        search_query = hypothetical_answer if hypothetical_answer else query

        where = {}
        if filter_type:
            where["type"] = filter_type
        if user_id:
            if filter_type:
                where = {"$and": [{"type": filter_type}, {"user_id": user_id}]}
            else:
                where = {"user_id": user_id}

        # If using MMR, fetch more candidates than requested
        fetch_k = n_results * 3 if use_mmr else n_results

        results = self._collection.query(
            query_texts=[search_query],
            n_results=fetch_k,
            where=where if where else None,
            include=["documents", "metadatas", "embeddings", "distances"]
        )

        if not results or not results.get("metadatas") or not results["metadatas"][0]:
            return []

        candidates = []
        for i in range(len(results["ids"][0])):
            candidates.append({
                "id": results["ids"][0][i],
                "content": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "embedding": results["embeddings"][0][i],
                "distance": results["distances"][0][i]
            })

        if use_mmr and len(candidates) > n_results:
            selected_indices = self._perform_mmr(
                [c["embedding"] for c in candidates],
                [1.0 - c["distance"] for c in candidates],
                n_results,
                mmr_lambda
            )
            return [candidates[i] for i in selected_indices]

        return candidates[:n_results]

    def _perform_mmr(self, embeddings: List[List[float]], similarities: List[float], k: int, lambda_param: float) -> List[int]:
        """Simple MMR implementation for diversity."""
        if not embeddings or k <= 0: return []

        n = len(embeddings)
        k = min(k, n)

        # Convert to numpy for faster math
        emb_array = np.array(embeddings)

        selected = [0] # Start with the most relevant
        remaining = list(range(1, n))

        while len(selected) < k:
            best_mmr = -1e9
            best_idx = -1

            # Precompute similarity between remaining and selected
            for i in remaining:
                # Max similarity to any already selected document
                # We need cosine similarity here
                current_emb = emb_array[i]
                selected_embs = emb_array[selected]

                # Manual cosine similarity if not normalized
                # Assuming SentenceTransformer returns normalized embeddings
                dot_products = np.dot(selected_embs, current_emb)
                norms_selected = np.linalg.norm(selected_embs, axis=1)
                norm_current = np.linalg.norm(current_emb)

                # Avoid division by zero
                sim_to_selected = dot_products / (norms_selected * norm_current + 1e-9)
                max_sim_to_selected = np.max(sim_to_selected)

                mmr_score = lambda_param * similarities[i] - (1 - lambda_param) * max_sim_to_selected

                if mmr_score > best_mmr:
                    best_mmr = mmr_score
                    best_idx = i

            if best_idx == -1: break
            selected.append(best_idx)
            remaining.remove(best_idx)

        return selected

    def get_full_context(self, query: str, user_id: Optional[str] = None, max_chars: int = 3000, hypothetical_answer: Optional[str] = None) -> str:
        """
        Commercial-grade context assembly using a tiered priority budget system.
        Ensures the most critical memories are included first within the token limit.
        """
        # 1. Fetch broad range of candidates with MMR
        optimized_query = f"relevant past memories for: {query}"
        memories = self.search_relevant_memories(
            optimized_query,
            n_results=15,
            user_id=user_id,
            use_mmr=True,
            hypothetical_answer=hypothetical_answer
        )

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

        # Sort summaries by timestamp (newest first)
        categories["summary"].sort(key=lambda x: x["metadata"].get("timestamp", ""), reverse=True)

        # Take up to 2 global and 3 local for a balanced perspective
        balanced_summaries = global_sums[:2] + local_sums[:3]
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
