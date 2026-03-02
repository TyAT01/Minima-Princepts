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
from utils.text_utils import calculate_text_similarity

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
        self._last_seen_cache: dict = {}  # PERF: in-memory cache for get_last_interaction_time
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
        # Short-term buffer (in-memory)
        self.short_term_buffer.append({"role": "user", "content": user_text})
        self.short_term_buffer.append({"role": "assistant", "content": bot_text})
        self._last_seen_cache.pop(user_id, None)
        if len(self.short_term_buffer) > self.max_short_term * 2:
            self.short_term_buffer = self.short_term_buffer[-(self.max_short_term * 2):]
        # Long-term ChromaDB write
        self.add_interaction_to_longterm(user_text, bot_text, user_id)

    def add_interaction_to_longterm(self, user_text: str, bot_text: str, user_id: str = "Stranger"):
        """P4: Writes only to ChromaDB (no buffer). Called from background thread."""
        timestamp_human = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        timestamp_unix = time.time()
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
        self._last_seen_cache.pop(user_id, None)

    async def add_interaction_async(self, user_text: str, bot_text: str, user_id: str = "Stranger"):
        """Async version of add_interaction."""
        # ChromaDB is sync, but we wrap it in a thread for async safety
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.add_interaction, user_text, bot_text, user_id)

    def search_relevant_memories(self, query: str, n_results: int = 5, filter_type: Optional[str] = None, user_id: Optional[str] = None, exclude_list: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Searches for relevant memories using vector similarity + MMR diversification."""
        # Skip cache if we have an exclude list as it's dynamic
        cache_key = f"{query}_{n_results}_{filter_type}_{user_id}"
        if not exclude_list and cache_key in self._search_cache:
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

            # P5+B3 FIX: Pass pre-computed query embedding from ChromaDB result
            # to avoid re-embedding the same query string in _mmr.
            # Note: ChromaDB returns query embeddings in results['embeddings'][0]
            # but only when include=[..., "embeddings"] — already requested above.
            _qemb = None
            if results.get("embeddings") and len(results["embeddings"]) > 1:
                # Index 0 = candidate embeddings, index >0 = query embedding (ChromaDB >=0.4)
                _qemb = np.array(results["embeddings"][-1]) if results["embeddings"][-1] else None
            selected_indices = self._mmr(query, embs, n_results, query_emb=_qemb)

            for idx in selected_indices:
                content = docs[idx]

                # Anti-repetition: filter out memories too similar to exclude_list
                if exclude_list:
                    is_duplicate = False
                    for excluded in exclude_list:
                        if calculate_text_similarity(content, excluded) >= 0.5:
                            is_duplicate = True
                            break
                    if is_duplicate:
                        continue

                formatted_results.append({
                    "content": content,
                    "metadata": metas[idx],
                    "distance": dist[idx]
                })

        if not exclude_list:
            self._search_cache[cache_key] = formatted_results
        return formatted_results

    async def search_relevant_memories_async(self, query: str, n_results: int = 5, filter_type: Optional[str] = None, user_id: Optional[str] = None, exclude_list: Optional[List[str]] = None):
        """Async version of search_relevant_memories."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.search_relevant_memories, query, n_results, filter_type, user_id, exclude_list)

    def _mmr(self, query: str, candidate_embs: List[List[float]], n_results: int,
             lambda_param: float = 0.5, query_emb: Optional[np.ndarray] = None) -> List[int]:
        """Maximal Marginal Relevance selection.
        P5 FIX: Vectorized with numpy — avoids O(n²) Python loop.
        B3 FIX: Accepts pre-computed query_emb to avoid double embedding.
        Caller should pass the embedding ChromaDB already computed, not re-embed.
        """
        if candidate_embs is None or len(candidate_embs) == 0:
            return []
        # B3 FIX: Use passed-in embedding if available; only embed as fallback
        if query_emb is None:
            query_emb = np.array(self.get_embedding(query))
        C = np.array(candidate_embs, dtype=np.float32)     # shape (n, d)
        q = query_emb.astype(np.float32)

        # Normalise all vectors once
        C_norms = np.linalg.norm(C, axis=1, keepdims=True).clip(min=1e-10)
        C_unit  = C / C_norms
        q_unit  = q / np.linalg.norm(q).clip(min=1e-10)

        # Similarity of every candidate to the query (shape: n)
        sim_to_query = C_unit @ q_unit

        n = min(n_results, len(candidate_embs))
        selected: List[int] = []
        # max_sim_selected[i] = max cosine sim of candidate i to any selected doc
        max_sim_sel = np.full(len(candidate_embs), -np.inf)

        for _ in range(n):
            scores = lambda_param * sim_to_query - (1.0 - lambda_param) * np.maximum(max_sim_sel, 0)
            # Mask already-selected
            if selected:
                scores[selected] = -np.inf
            best = int(np.argmax(scores))
            selected.append(best)
            # Update max similarity to selected set (vectorized)
            new_sims = C_unit @ C_unit[best]
            np.maximum(max_sim_sel, new_sims, out=max_sim_sel)

        return selected

    def get_full_context(self, query: str, user_id: str = "Stranger", n_memories: int = 5, hypothetical_answer: str = "", exclude_list: Optional[List[str]] = None) -> str:
        """Assembles a rich context block from various memory tiers."""
        # Use HyDE if provided
        search_query = hypothetical_answer if hypothetical_answer else query

        # PERF FIX P3: Run all 4 ChromaDB searches concurrently.
        # Previously sequential (~4x single query time). Now runs in parallel
        # so total cost = slowest single search instead of sum of all four.
        from concurrent.futures import ThreadPoolExecutor, as_completed
        search_tasks = {
            "interactions": (search_query, n_memories, "interaction", user_id, exclude_list),
            "summaries":    (search_query, 2,         "summary",     user_id, None),
            "insights":     (search_query, 3,         "insight",     user_id, None),
            "entities":     (search_query, 3,         "entity",      user_id, None),
        }
        results_map = {}
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {
                ex.submit(self.search_relevant_memories, *args): key
                for key, args in search_tasks.items()
            }
            for fut in as_completed(futures):
                results_map[futures[fut]] = fut.result()
        interactions = results_map.get("interactions", [])
        summaries    = results_map.get("summaries", [])
        insights     = results_map.get("insights", [])
        entities     = results_map.get("entities", [])

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
        # PERF+BUG FIX: Old impl used query_texts=[""] — a vector search on an empty
        # string is meaningless (ChromaDB returns arbitrary results) and wastes ~20ms.
        # Now uses .get() with metadata filter (no embedding computation needed)
        # plus an in-memory cache so repeated calls within a session cost nothing.
        if user_id in self._last_seen_cache:
            return self._last_seen_cache[user_id]
        try:
            results = self.collection.get(
                where={"$and": [{"user_id": user_id}, {"type": "interaction"}]},
                include=["metadatas"],
                limit=100,
            )
            metas = results.get("metadatas", [])
            if metas:
                # Find the most recent by timestamp_unix
                best_ts = None
                for meta in metas:
                    ts = meta.get("timestamp_unix")
                    if ts:
                        if isinstance(ts, str):
                            ts = float(ts)
                        if best_ts is None or ts > best_ts:
                            best_ts = ts
                if best_ts:
                    dt = datetime.fromtimestamp(best_ts, tz=timezone.utc)
                    self._last_seen_cache[user_id] = dt
                    return dt
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
                        # FIX: Strip any "Shiro:" speaker prefix from stored bot text.
                        # Old leaky responses may have been stored with "Shiro: " prefixed,
                        # which would teach the LLM to use that prefix in future replies.
                        import re as _re
                        bot_part = _re.sub(r"(?i)^\s*shiro:\s*", "", bot_part).strip()
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
