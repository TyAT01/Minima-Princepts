from __future__ import annotations
import chromadb
import uuid
import time
import logging
import re
import asyncio
from chromadb.config import Settings
from chromadb.utils import embedding_functions
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional
from cachetools import TTLCache
import numpy as np

logger = logging.getLogger(__name__)

class MemoryStore:
    """Manages long-term memory using ChromaDB."""

    def __init__(self, db_path: Path | str = "./shiro_memory", collection_name: str = "shiro_ai_memories", max_short_term: int = 15):
        self.db_path = Path(db_path)
        self.client = chromadb.PersistentClient(path=str(self.db_path))
        self.max_short_term = max_short_term

        # Use default Sentence Transformer for embeddings
        self.emb_fn = embedding_functions.DefaultEmbeddingFunction()

        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=self.emb_fn,
            metadata={"hnsw:space": "cosine"}
        )

        self.short_term_buffer: List[Dict[str, str]] = []
        self.session_objectives: List[str] = []

        # Dual Layer Cache
        self._search_cache = TTLCache(maxsize=100, ttl=300) # 5 min TTL
        self._embedding_cache = TTLCache(maxsize=200, ttl=600)

        logger.info(f"MemoryStore initialized at {self.db_path} (Collection: {collection_name})")

    def get_embedding(self, text: str) -> List[float]:
        """Gets or caches embeddings for text."""
        if text in self._embedding_cache:
             return self._embedding_cache[text]
        emb = self.emb_fn([text])[0]
        self._embedding_cache[text] = emb
        return emb

    def add_interaction(self, user_text: str, bot_text: str, user_id: str = "Stranger"):
        """Adds a conversation turn to both short-term and long-term memory."""
        timestamp_human = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        timestamp_unix = time.time()

        # 1. Long-Term (ChromaDB)
        interaction_id = str(uuid.uuid4())
        document = f"[{timestamp_human}] User ({user_id}): {user_text}\nShiro: {bot_text}"

        self.collection.add(
            documents=[document],
            metadatas=[{
                "type": "interaction",
                "user_id": user_id,
                "timestamp": timestamp_human,
                "timestamp_unix": timestamp_unix,
                "role": "conversation"
            }],
            ids=[interaction_id]
        )

        # 2. Short-Term (In-memory buffer)
        self.short_term_buffer.append({"role": "user", "content": user_text})
        self.short_term_buffer.append({"role": "assistant", "content": bot_text})

        # Keep buffer manageable
        if len(self.short_term_buffer) > self.max_short_term * 2:
            self.short_term_buffer = self.short_term_buffer[-(self.max_short_term * 2):]

    async def add_interaction_async(self, user_text: str, bot_text: str, user_id: str = "Stranger"):
        """Async version of add_interaction."""
        # ChromaDB is sync, but we wrap it in a thread for async safety
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.add_interaction, user_text, bot_text, user_id)

    def search_relevant_memories(self, query: str, n_results: int = 5, filter_type: Optional[str] = None, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Searches for relevant memories using vector similarity + MMR diversification."""
        cache_key = f"{query}_{n_results}_{filter_type}_{user_id}"
        if cache_key in self._search_cache:
             return self._search_cache[cache_key]

        filters = []
        if filter_type:
            filters.append({"type": filter_type})
        if user_id:
            filters.append({"user_id": user_id})

        where_clause = None
        if len(filters) > 1:
            where_clause = {"$and": filters}
        elif len(filters) == 1:
            where_clause = filters[0]

        # Use MMR for diverse results (Hybrid Search)
        results = self.collection.query(
            query_texts=[query],
            n_results=n_results * 2, # Fetch more to diversify
            where=where_clause if where_clause else None,
            include=["documents", "metadatas", "distances", "embeddings"]
        )

        formatted_results = []
        if results and results['documents']:
            docs = results['documents'][0]
            metas = results['metadatas'][0]
            dist = results['distances'][0]
            embs = results['embeddings'][0]

            # Perform MMR Selection
            selected_indices = self._mmr(query, embs, n_results)

            for idx in selected_indices:
                formatted_results.append({
                    "content": docs[idx],
                    "metadata": metas[idx],
                    "distance": dist[idx]
                })

        self._search_cache[cache_key] = formatted_results
        return formatted_results

    async def search_relevant_memories_async(self, query: str, n_results: int = 5, filter_type: Optional[str] = None, user_id: Optional[str] = None):
        """Async version of search_relevant_memories."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.search_relevant_memories, query, n_results, filter_type, user_id)

    def _mmr(self, query: str, candidate_embs: List[List[float]], n_results: int, lambda_param: float = 0.5) -> List[int]:
        """Maximal Marginal Relevance selection."""
        if candidate_embs is None or len(candidate_embs) == 0: return []
        query_emb = np.array(self.get_embedding(query))
        candidates = [np.array(e) for e in candidate_embs]

        selected = []
        remaining = list(range(len(candidates)))

        while len(selected) < min(n_results, len(candidates)):
            best_score = -float('inf')
            best_idx = -1

            for i in remaining:
                # Similarity to query
                sim_to_query = np.dot(query_emb, candidates[i]) / (np.linalg.norm(query_emb) * np.linalg.norm(candidates[i]))

                # Max similarity to selected
                max_sim_to_selected = 0
                for s_idx in selected:
                    sim = np.dot(candidates[i], candidates[s_idx]) / (np.linalg.norm(candidates[i]) * np.linalg.norm(candidates[s_idx]))
                    max_sim_to_selected = max(max_sim_to_selected, sim)

                score = lambda_param * sim_to_query - (1 - lambda_param) * max_sim_to_selected
                if score > best_score:
                    best_score = score
                    best_idx = i

            selected.append(best_idx)
            remaining.remove(best_idx)

        return selected

    def get_full_context(self, query: str, user_id: str = "Stranger", n_memories: int = 5, hypothetical_answer: str = "") -> str:
        """Assembles a rich context block from various memory tiers."""
        # Use HyDE if provided
        search_query = hypothetical_answer if hypothetical_answer else query

        # 1. Search Interactions
        interactions = self.search_relevant_memories(search_query, n_results=n_memories, filter_type="interaction", user_id=user_id)

        # 2. Search Summaries
        summaries = self.search_relevant_memories(search_query, n_results=2, filter_type="summary", user_id=user_id)

        # 3. Search Episodic / Insights
        insights = self.search_relevant_memories(search_query, n_results=3, filter_type="insight", user_id=user_id)

        # 4. Search Related Entities (Context Hops)
        # (This would use the Graph layer if implemented, for now just entities)
        entities = self.search_relevant_memories(search_query, n_results=3, filter_type="entity", user_id=user_id)

        context_parts = []

        if summaries:
            context_parts.append("### RECENT SUMMARIES")
            for s in summaries:
                context_parts.append(f"- {s['content']}")

        if insights:
            context_parts.append("### KEY INSIGHTS & LESSONS")
            for i in insights:
                context_parts.append(f"- {i['content']}")

        if entities:
             context_parts.append("### RELATED ENTITIES")
             for e in entities:
                  context_parts.append(f"- {e['content']}")

        if self.session_objectives:
            context_parts.append("### CURRENT OBJECTIVES")
            for obj in self.session_objectives:
                context_parts.append(f"- {obj}")

        if interactions:
            context_parts.append("### RELATED PAST CONVERSATIONS")
            for inter in interactions:
                context_parts.append(inter['content'])

        return "\n\n".join(context_parts)

    async def get_full_context_async(self, query: str, user_id: str = "Stranger", n_memories: int = 5):
        """Async version of get_full_context."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.get_full_context, query, user_id, n_memories)

    def store_summary(self, user_id: str, summary_text: str, is_global: bool = False):
        """Stores a conversation summary."""
        summary_id = str(uuid.uuid4())
        self.collection.add(
            documents=[summary_text],
            metadatas=[{
                "type": "summary",
                "user_id": user_id,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "timestamp_unix": time.time(),
                "is_global": is_global
            }],
            ids=[summary_id]
        )

    def store_insight(self, insight_text: str, user_id: str, source: str = "reflection"):
        """Stores an abstract insight about the user or world."""
        insight_id = str(uuid.uuid4())
        self.collection.add(
            documents=[insight_text],
            metadatas=[{
                "type": "insight",
                "user_id": user_id,
                "source": source,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "timestamp_unix": time.time()
            }],
            ids=[insight_id]
        )

    async def store_insight_async(self, insight_text: str, user_id: str, source: str = "reflection"):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.store_insight, insight_text, user_id, source)

    def store_episodic_memory(self, event_text: str, user_id: str, importance: int = 5):
        """Stores a specific notable event."""
        event_id = str(uuid.uuid4())
        self.collection.add(
            documents=[event_text],
            metadatas=[{
                "type": "event",
                "user_id": user_id,
                "importance": importance,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "timestamp_unix": time.time()
            }],
            ids=[event_id]
        )

    def add_entity_relation(self, source: str, target: str, relation: str):
        """Records a relationship between two entities."""
        rel_id = str(uuid.uuid4())
        doc = f"Entity '{source}' is connected to '{target}' via: {relation}"
        self.collection.add(
            documents=[doc],
            metadatas=[{
                "type": "entity",
                "source": source,
                "target": target,
                "relation": relation,
                "timestamp_unix": time.time()
            }],
            ids=[rel_id]
        )

    def update_user_profile(self, user_id: str, fact: str):
         """Stores a persistent fact about the user."""
         fact_id = str(uuid.uuid4())
         self.collection.add(
             documents=[fact],
             metadatas=[{
                 "type": "profile_fact",
                 "user_id": user_id,
                 "timestamp_unix": time.time()
             }],
             ids=[fact_id]
         )

    def get_last_interaction_time(self, user_id: str) -> Optional[datetime]:
        """Retrieves the timestamp of the last interaction with this user."""
        try:
            results = self.collection.query(
                query_texts=[""],
                n_results=1,
                where={"$and": [{"user_id": user_id}, {"type": "interaction"}]},
                include=["metadatas"]
            )

            if results and results['metadatas'] and results['metadatas'][0]:
                meta = results['metadatas'][0][0]
                ts = meta.get("timestamp_unix")
                if ts:
                    if isinstance(ts, (float, int)):
                         return datetime.fromtimestamp(ts, tz=timezone.utc)
                    else: # Legacy support for ISO strings
                         return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
            return None
        except Exception as e:
            logger.warning(f"Failed to get last interaction time: {e}")
            return None

    def get_history(self) -> List[Dict[str, str]]:
        """Returns the current short-term conversation buffer."""
        return self.short_term_buffer

    def load_recent_history(self, user_id: str = "Stranger", limit: int = 15):
        """Loads recent interactions from ChromaDB into the short-term buffer."""
        try:
            results = self.collection.query(
                query_texts=[""],
                n_results=limit,
                where={"$and": [{"user_id": user_id}, {"type": "interaction"}]},
                include=["documents", "metadatas"]
            )

            if results and results['documents'] and results['documents'][0]:
                # ChromaDB returns most relevant/recent if query is empty?
                # Actually, without a query it might be random-ish or by insertion.
                # Let's sort by timestamp_unix if available in metadata.
                docs = results['documents'][0]
                metas = results['metadatas'][0]

                combined = list(zip(docs, metas))
                # Sort by timestamp_unix ascending to rebuild conversation flow
                combined.sort(key=lambda x: x[1].get('timestamp_unix', 0))

                self.short_term_buffer = []
                for doc, meta in combined:
                    # Extract user and assistant parts from the stored document
                    # Format: "[timestamp] User (id): user_text\nShiro: bot_text"
                    lines = doc.split("\n")
                    user_part = ""
                    bot_part = ""
                    for line in lines:
                        if "User (" in line and "): " in line:
                            user_part = line.split("): ", 1)[1]
                        elif "Shiro: " in line:
                            bot_part = line.split("Shiro: ", 1)[1]

                    if user_part:
                        self.short_term_buffer.append({"role": "user", "content": user_part})
                    if bot_part:
                        self.short_term_buffer.append({"role": "assistant", "content": bot_part})

                # Truncate to max_short_term
                if len(self.short_term_buffer) > self.max_short_term * 2:
                    self.short_term_buffer = self.short_term_buffer[-(self.max_short_term * 2):]

                logger.info(f"Loaded {len(self.short_term_buffer)//2} recent turns into short-term buffer for {user_id}.")
        except Exception as e:
            logger.error(f"Failed to load recent history: {e}")

    def prune_old_memories(self, days: int = 30):
        """Removes low-priority memories older than N days."""
        cutoff_unix = time.time() - (days * 86400)
        try:
            # Delete low importance events
            self.collection.delete(
                where={
                    "$and": [
                        {"timestamp_unix": {"$lt": cutoff_unix}},
                        {"type": "event"},
                        {"importance": {"$lt": 4}}
                    ]
                }
            )
            logger.info(f"Pruned memories older than {days} days.")
        except Exception as e:
            logger.error(f"Pruning failed: {e}")
