from __future__ import annotations
import chromadb
import uuid
import time
import logging
import re
import asyncio
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
        # Per-user short-term buffers — keyed by user_id.
        # self.short_term_buffer is the active buffer for the current user.
        # Switching users swaps which per-user buffer is active.
        self._user_buffers: dict = {}    # user_id -> List[Dict]
        self._active_user_id: str = ""   # which user owns the current short_term_buffer

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

        # PERF FIX P3: Run all searches concurrently.
        from concurrent.futures import ThreadPoolExecutor, as_completed
        search_tasks = {
            "interactions":  (search_query, n_memories, "interaction",   user_id, exclude_list),
            "summaries":     (search_query, 2,          "summary",       user_id, None),
            "insights":      (search_query, 3,          "insight",       user_id, None),
            "entities":      (search_query, 3,          "entity",        user_id, None),
            # Book memories stored under user_id="system" — globally available
            "book_memories": (search_query, 4,          "book_memory",   None,    None),
            # User profile facts — always searched for this user specifically
            "profile_facts": (search_query, 6,          "profile_fact",  user_id, None),
        }
        results_map = {}
        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = {
                ex.submit(self.search_relevant_memories, *args): key
                for key, args in search_tasks.items()
            }
            for fut in as_completed(futures):
                results_map[futures[fut]] = fut.result()

        interactions  = results_map.get("interactions",  [])
        summaries     = results_map.get("summaries",     [])
        insights      = results_map.get("insights",      [])
        entities      = results_map.get("entities",      [])
        book_memories = results_map.get("book_memories", [])
        profile_facts = results_map.get("profile_facts", [])

        # Filter book memories to only include genuinely relevant ones (distance < 0.82)
        # Books use extractive summaries — their embeddings score lower than direct conversation.
        # 0.75 was too tight and was filtering out valid book memories on book-topic queries.
        book_memories = [b for b in book_memories if b.get("distance", 1.0) < 0.82]
        # Filter profile facts to closest matches only (distance < 0.80)
        profile_facts = [p for p in profile_facts if p.get("distance", 1.0) < 0.80]

        context_parts = []

        # Profile facts first — most personal, highest relevance signal
        if profile_facts:
            context_parts.append(f"### WHAT I KNOW ABOUT {user_id.upper()}")
            for p in profile_facts:
                context_parts.append(f"- {p['content']}")

        # Book memories second — curated, high-quality knowledge
        if book_memories:
            context_parts.append("### BOOKS I HAVE READ")
            for b in book_memories:
                context_parts.append(f"- {b['content']}")

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

    def get_memory_gap_hint(self, query: str, user_id: str = "Stranger") -> str:
        """
        Returns a hint string describing how much relevant memory exists for a query.
        Used by the engine to tell Shiro whether she's drawing from real memory or
        operating blind — so she can respond naturally about gaps rather than fabricating.

        Checks: user interactions + profile facts + book memories.
        Returns one of:
          "rich"    — strong memories found (distance < 0.4)
          "partial" — some memories but weak match (0.4–0.65)
          "sparse"  — very little relevant memory found
          "none"    — nothing found at all
        """
        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            tasks = {
                "interactions":  (query, 3, "interaction",  user_id, None),
                "profile_facts": (query, 3, "profile_fact", user_id, None),
                "book_memories": (query, 3, "book_memory",  None,    None),
            }
            all_results = []
            with ThreadPoolExecutor(max_workers=3) as ex:
                futs = {ex.submit(self.search_relevant_memories, *args): k
                        for k, args in tasks.items()}
                for fut in as_completed(futs):
                    try:
                        all_results.extend(fut.result())
                    except Exception:
                        pass

            if not all_results:
                return "none"
            distances = [r.get("distance", 1.0) for r in all_results]
            best = min(distances)
            if best < 0.40:
                return "rich"
            elif best < 0.65:
                return "partial"
            else:
                return "sparse"
        except Exception:
            return "none"

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

    def store_book_memory(self, content: str, book_title: str, source: str = "book_memory"):
        """
        Stores a book-derived memory with type='book_memory'.
        Stored under user_id='system' so it's globally accessible across all users.
        Uses type='book_memory' so get_full_context can tier it separately from
        conversation memories — always available, never pruned by user filters.
        """
        mem_id = str(uuid.uuid4())
        self.collection.add(
            documents=[content],
            metadatas=[{
                "type":            "book_memory",
                "user_id":         "system",
                "source":          source,
                "book_title":      book_title,
                "timestamp":       datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "timestamp_unix":  time.time(),
            }],
            ids=[mem_id]
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

    def store_user_facts_batch(self, user_id: str, facts: dict):
        """
        Store multiple user facts at once as profile_fact entries.
        Called in real-time from the engine whenever inner_mind extracts
        new facts from a user message — much faster than waiting for reflection.

        facts: dict of {fact_type: value}, e.g. {"likes": "board games", "age": "26"}
        Deduplicates against recent profile_facts for this user so we don't
        spam ChromaDB with identical entries every turn.
        """
        if not facts:
            return
        # Fetch existing recent facts for this user to deduplicate
        try:
            existing = self.collection.get(
                where={"$and": [{"user_id": user_id}, {"type": "profile_fact"}]},
                include=["documents"],
                limit=100,
            )
            existing_docs = set(existing.get("documents") or [])
        except Exception:
            existing_docs = set()

        to_add_docs, to_add_metas, to_add_ids = [], [], []
        now = time.time()
        ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for fact_type, value in facts.items():
            if not value or not str(value).strip():
                continue
            doc = f"{fact_type}: {value}"
            if doc in existing_docs:
                continue   # already stored — skip
            to_add_docs.append(doc)
            to_add_metas.append({
                "type":            "profile_fact",
                "user_id":         user_id,
                "fact_type":       fact_type,
                "timestamp":       ts,
                "timestamp_unix":  now,
            })
            to_add_ids.append(str(uuid.uuid4()))

        if to_add_docs:
            try:
                self.collection.add(
                    documents=to_add_docs,
                    metadatas=to_add_metas,
                    ids=to_add_ids,
                )
                logger.debug(f"[MemoryStore] Stored {len(to_add_docs)} user facts for {user_id}")
            except Exception as e:
                logger.warning(f"[MemoryStore] store_user_facts_batch error: {e}")

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
        """
        Loads the most recent interactions from ChromaDB into the short-term buffer
        for the given user_id, and makes that buffer the active one.

        On user switch: saves the current buffer for the previous user, then loads
        or creates a fresh buffer for the new user. This prevents memory bleed
        between users in multi-user chat sessions.
        """
        import re as _re

        # Save the current buffer back to the per-user store before switching
        if self._active_user_id and self._active_user_id != user_id:
            self._user_buffers[self._active_user_id] = list(self.short_term_buffer)

        # If we already have an in-memory buffer for this user from earlier in the
        # session, restore it immediately (avoids a ChromaDB round-trip on switch-back)
        if user_id in self._user_buffers and self._active_user_id != user_id:
            self.short_term_buffer = self._user_buffers[user_id]
            self._active_user_id = user_id
            logger.debug(f"[MemoryStore] Restored in-session buffer for {user_id} ({len(self.short_term_buffer)//2} turns)")
            return

        self._active_user_id = user_id

        try:
            fetch_limit = max(limit * 4, 60)
            results = self.collection.get(
                where={"$and": [{"user_id": user_id}, {"type": "interaction"}]},
                include=["documents", "metadatas"],
                limit=fetch_limit,
            )

            docs  = results.get("documents", [])
            metas = results.get("metadatas", [])

            if not docs:
                # Always clear the buffer when switching users — even if this user
                # has no history. Leaving a previous user's turns in the buffer
                # causes memory cross-contamination between users in multi-user chat.
                self.short_term_buffer = []
                self._user_buffers[user_id] = []
                logger.info(f"No stored history found for {user_id}.")
                return

            combined = list(zip(docs, metas))
            combined.sort(key=lambda x: x[1].get("timestamp_unix", 0), reverse=True)
            combined = combined[:limit]
            combined.reverse()

            self.short_term_buffer = []
            for doc, meta in combined:
                lines = doc.split("\n")
                user_part = ""
                bot_part  = ""
                for line in lines:
                    if "User (" in line and "): " in line:
                        user_part = line.split("): ", 1)[1].strip()
                    elif "Shiro: " in line:
                        bot_part = line.split("Shiro: ", 1)[1].strip()
                        bot_part = _re.sub(r"(?i)^\s*shiro:\s*", "", bot_part).strip()

                if user_part:
                    self.short_term_buffer.append({"role": "user",      "content": user_part})
                if bot_part:
                    self.short_term_buffer.append({"role": "assistant", "content": bot_part})

            if len(self.short_term_buffer) > self.max_short_term * 2:
                self.short_term_buffer = self.short_term_buffer[-(self.max_short_term * 2):]

            self._user_buffers[user_id] = list(self.short_term_buffer)
            logger.info(f"Loaded {len(self.short_term_buffer)//2} recent turns into short-term buffer for {user_id}.")
        except Exception as e:
            logger.error(f"Failed to load recent history for {user_id}: {e}")

    def get_memory_count(self) -> int:
        """Returns total number of records in the ChromaDB collection."""
        try:
            return self.collection.count()
        except Exception:
            return 0

    def get_episodic_memories(self, limit: int = 20, user_id: str = None) -> list:
        """
        Returns recent episodic memories (type=event or type=insight) for the
        memory management panel. Sorted newest-first.
        """
        try:
            where_filter = {"type": {"$in": ["event", "insight"]}}
            if user_id:
                where_filter = {"$and": [{"user_id": user_id}, {"type": {"$in": ["event", "insight"]}}]}
            results = self.collection.get(
                where=where_filter,
                include=["documents", "metadatas"],
                limit=limit * 2,  # fetch extra to sort
            )
            docs  = results.get("documents", [])
            metas = results.get("metadatas", [])
            combined = list(zip(docs, metas))
            combined.sort(key=lambda x: x[1].get("timestamp_unix", 0), reverse=True)
            combined = combined[:limit]
            return [
                {
                    "content":   doc,
                    "type":      meta.get("type", ""),
                    "timestamp": meta.get("timestamp", ""),
                    "user_id":   meta.get("user_id", ""),
                }
                for doc, meta in combined
            ]
        except Exception as e:
            logger.warning(f"get_episodic_memories error: {e}")
            return []

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

    def purge_memories_containing(self, phrase: str, user_id: str = None) -> int:
        """
        Delete all ChromaDB memories whose content contains the given phrase.
        Used to clean up false/fabricated memories before they propagate.
        Returns the number of entries deleted.
        """
        try:
            where = {"user_id": user_id} if user_id else None
            results = self.collection.get(
                where=where,
                include=["documents", "metadatas"],
                limit=500,
            )
            ids_to_delete = []
            phrase_lower = phrase.lower()
            docs  = results.get("documents", [])
            ids   = results.get("ids", [])
            for doc, doc_id in zip(docs, ids):
                if phrase_lower in doc.lower():
                    ids_to_delete.append(doc_id)
            if ids_to_delete:
                self.collection.delete(ids=ids_to_delete)
                logger.info(f"[MemoryStore] Purged {len(ids_to_delete)} memories containing {phrase!r}")
            return len(ids_to_delete)
        except Exception as e:
            logger.error(f"[MemoryStore] purge_memories_containing error: {e}")
            return 0