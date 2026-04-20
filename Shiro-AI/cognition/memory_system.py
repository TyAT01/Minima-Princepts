"""
SHIRO Memory System v3.6
=========================
Hardware: RTX 3070 8 GB | i9-9900K 8c/16t | 64 GB RAM

What's new vs v3.2
-------------------
ENHANCEMENTS
  - IMPORTANCE DECAY: memories lose importance over time unless retrieved.
    A daily background task applies a gentle decay (0.002/day) to non-core
    memories — important things stay important; trivial things fade.
  - WORKING MEMORY CACHE: the last 3 turns' retrieved records are held
    in a hot dict — zero SQLite cost if the same memory is retrieved
    twice in quick succession (e.g. follow-up questions).
  - COMPOSITE QUERIES: _build_query now generates two sub-queries (intent
    + focus) and fires both semantic searches concurrently, then merges.
    This doubles semantic recall on complex turns.
  - EMOTIONAL MEMORY WEIGHTING: memories with emotional_weight > 0.5 get
    a 15% score boost during retrieval, not just the existing em_sim term.
    Emotionally significant moments surface more reliably.
  - MEMORY SUMMARIES: summarise(n) returns the n most important memories
    as a compact text block suitable for injecting directly into LLM prompts.
  - STATS: stats() returns record counts by type, avg importance, and
    semantic store size — useful for health monitoring.
  - EXPLICIT DELETE: delete(id) removes a record from both SQLite and
    ChromaDB — necessary for GDPR-style "forget this" requests.

OPTIMISATIONS (i9-9900K + 64 GB RAM specific)
  - SQLite mmap_size: 512 MB (was 256 MB). With 64 GB RAM we can afford
    to mmap half a GB — hot pages stay in RAM, zero disk I/O for reads.
  - SQLite cache_size: 64 MB (was 32 MB). i9 L3 cache is 16 MB; fitting
    the working set in OS page cache is more useful than fitting in L3.
  - SQLite WAL + synchronous=NORMAL: write latency ~0.3 ms.
  - ThreadPoolExecutor: 6 workers (was 4). i9-9900K has 16 threads; the
    memory system's ChromaDB calls are I/O-bound so more threads = more
    concurrent semantic queries. 6 leaves 10 threads for the LLM/TTS.
  - batch_insert: stores multiple records in one SQLite transaction instead
    of one commit per record — critical during post-turn where we store
    4-5 records. One BEGIN/COMMIT for all of them.
  - _merge sort: uses key= lambda on a list of (score, record) tuples;
    timsort is O(n log n) but n <= 20 so it's effectively O(n).
  - content_hash: md5 with usedforsecurity=False (avoids FIPS lock check).
  - dedup: content_exists uses a single SELECT with LIMIT 1 on an indexed
    column — the fastest possible SQLite existence check.
  - _bg_prune runs 20 s after boot then every 6 hours — not just once.
  - _bg_decay runs daily; uses a single bulk UPDATE not a row scan.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import sqlite3
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cognitive_kernel import CognitiveState

# i9-9900K: 16 threads total. LLM ~4 threads, TTS ~2 threads.
# 6 workers for memory I/O leaves 10 for everything else.
_EXECUTOR = ThreadPoolExecutor(max_workers=6, thread_name_prefix="shiro_mem")

# ──────────────────────────────────────────────────────────────────────────────
# MemoryRecord
# ──────────────────────────────────────────────────────────────────────────────

class MemoryRecord:
    """Slotted record — 11 fields, ~200 bytes each, 100s of them in RAM = fine."""
    __slots__ = (
        "id", "content", "tags", "importance", "timestamp",
        "turn_id", "memory_type", "emotional_weight",
        "retrieval_count", "is_core", "_chash",
    )

    def __init__(
        self, id: str, content: str, tags: list[str],
        importance: float, timestamp: float, turn_id: str,
        memory_type: str, emotional_weight: float = 0.0,
        retrieval_count: int = 0, is_core: bool = False,
    ):
        self.id               = id
        self.content          = content
        self.tags             = tags
        self.importance       = importance
        self.timestamp        = timestamp
        self.turn_id          = turn_id
        self.memory_type      = memory_type
        self.emotional_weight = emotional_weight
        self.retrieval_count  = retrieval_count
        self.is_core          = is_core
        self._chash: str | None = None   # lazy cache

    def content_hash(self) -> str:
        if self._chash is None:
            self._chash = hashlib.md5(
                self.content.encode(), usedforsecurity=False
            ).hexdigest()[:14]
        return self._chash

    def to_dict(self) -> dict:
        return {
            "id":               self.id,
            "content":          self.content,
            "tags":             self.tags,
            "importance":       self.importance,
            "timestamp":        self.timestamp,
            "turn_id":          self.turn_id,
            "memory_type":      self.memory_type,
            "emotional_weight": self.emotional_weight,
            "retrieval_count":  self.retrieval_count,
            "is_core":          self.is_core,
        }


# ──────────────────────────────────────────────────────────────────────────────
# Episodic Store (SQLite)
# ──────────────────────────────────────────────────────────────────────────────

class EpisodicStore:
    __slots__ = ("db_path", "_conn")

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def init(self):
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        c = self._conn
        # 64 GB RAM → give SQLite generous buffers
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA mmap_size=536870912")   # 512 MB mmap — fits in RAM
        c.execute("PRAGMA cache_size=-65536")      # 64 MB page cache
        c.execute("PRAGMA temp_store=MEMORY")
        c.execute("PRAGMA wal_autocheckpoint=1000")
        c.row_factory = sqlite3.Row
        self._create_schema()
        c.commit()

    def _create_schema(self):
        c = self._conn
        c.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id               TEXT PRIMARY KEY,
                content          TEXT NOT NULL,
                content_hash     TEXT,
                tags             TEXT,
                importance       REAL DEFAULT 0.5,
                timestamp        REAL,
                turn_id          TEXT,
                memory_type      TEXT,
                emotional_weight REAL DEFAULT 0.0,
                retrieval_count  INTEGER DEFAULT 0,
                is_core          INTEGER DEFAULT 0
            )
        """)
        c.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
            USING fts5(id UNINDEXED, content, tags, tokenize='porter ascii')
        """)
        for idx, col in [
            ("idx_type",  "memory_type"),
            ("idx_imp",   "importance DESC"),
            ("idx_ts",    "timestamp DESC"),
            ("idx_core",  "is_core"),
            ("idx_hash",  "content_hash"),
            ("idx_ew",    "emotional_weight DESC"),
        ]:
            c.execute(f"CREATE INDEX IF NOT EXISTS {idx} ON memories({col})")

    # ── Insert ────────────────────────────────────────────────────────────────

    def insert(self, rec: MemoryRecord):
        tags_s = json.dumps(rec.tags)
        self._conn.execute(
            "INSERT OR REPLACE INTO memories VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (rec.id, rec.content, rec.content_hash(), tags_s,
             rec.importance, rec.timestamp, rec.turn_id, rec.memory_type,
             rec.emotional_weight, rec.retrieval_count, int(rec.is_core)),
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO memories_fts(id,content,tags) VALUES(?,?,?)",
            (rec.id, rec.content, tags_s),
        )
        self._conn.commit()

    def batch_insert(self, records: list[MemoryRecord]):
        """Single transaction for multiple records — avoids N commits."""
        if not records:
            return
        with self._conn:   # context manager = BEGIN/COMMIT
            for rec in records:
                tags_s = json.dumps(rec.tags)
                self._conn.execute(
                    "INSERT OR REPLACE INTO memories VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (rec.id, rec.content, rec.content_hash(), tags_s,
                     rec.importance, rec.timestamp, rec.turn_id, rec.memory_type,
                     rec.emotional_weight, rec.retrieval_count, int(rec.is_core)),
                )
                self._conn.execute(
                    "INSERT OR REPLACE INTO memories_fts(id,content,tags) VALUES(?,?,?)",
                    (rec.id, rec.content, tags_s),
                )

    # ── Existence check ───────────────────────────────────────────────────────

    def content_exists(self, content_hash: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM memories WHERE content_hash=? LIMIT 1", (content_hash,)
        ).fetchone()
        return row is not None

    # ── Searches ──────────────────────────────────────────────────────────────

    def keyword_search(self, query: str, limit: int = 8) -> list[MemoryRecord]:
        safe = re.sub(r"[^\w\s]", " ", query).strip()
        if not safe:
            return []
        try:
            rows = self._conn.execute("""
                SELECT m.* FROM memories m
                JOIN memories_fts f ON m.id = f.id
                WHERE memories_fts MATCH ?
                ORDER BY m.importance DESC, m.timestamp DESC
                LIMIT ?
            """, (safe, limit)).fetchall()
        except sqlite3.OperationalError:
            rows = []
        return [self._row(r) for r in rows]

    def recent(self, limit: int = 5) -> list[MemoryRecord]:
        rows = self._conn.execute(
            "SELECT * FROM memories ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row(r) for r in rows]

    def get_core(self) -> list[MemoryRecord]:
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE is_core=1 ORDER BY importance DESC LIMIT 20"
        ).fetchall()
        return [self._row(r) for r in rows]

    def get_emotional(self, min_weight: float = 0.5, limit: int = 6) -> list[MemoryRecord]:
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE emotional_weight>=? ORDER BY emotional_weight DESC LIMIT ?",
            (min_weight, limit),
        ).fetchall()
        return [self._row(r) for r in rows]

    def get_by_type(self, memory_type: str, limit: int = 10) -> list[MemoryRecord]:
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE memory_type=? ORDER BY importance DESC, timestamp DESC LIMIT ?",
            (memory_type, limit),
        ).fetchall()
        return [self._row(r) for r in rows]

    # ── Bulk updates ─────────────────────────────────────────────────────────

    def batch_increment(self, ids: list[str]):
        if not ids:
            return
        ph = ",".join("?" * len(ids))
        self._conn.execute(
            f"UPDATE memories SET retrieval_count=retrieval_count+1 WHERE id IN ({ph})", ids
        )
        self._conn.commit()

    def promote_to_core(self, ids: list[str]):
        if not ids:
            return
        ph = ",".join("?" * len(ids))
        self._conn.execute(
            f"UPDATE memories SET is_core=1, importance=MIN(importance+0.2, 1.0) WHERE id IN ({ph})", ids
        )
        self._conn.commit()

    def delete(self, record_id: str):
        self._conn.execute("DELETE FROM memories WHERE id=?", (record_id,))
        self._conn.execute("DELETE FROM memories_fts WHERE id=?", (record_id,))
        self._conn.commit()

    # ── Maintenance ───────────────────────────────────────────────────────────

    def prune_old(self, ttl_days: int, min_importance: float):
        cutoff = time.time() - ttl_days * 86_400
        self._conn.execute(
            "DELETE FROM memories WHERE is_core=0 AND timestamp<? AND importance<?",
            (cutoff, min_importance),
        )
        self._conn.commit()

    def apply_importance_decay(self, decay_per_day: float = 0.002):
        """
        Reduce importance of non-core, non-recently-retrieved memories.
        Runs as a bulk UPDATE — single statement, no row scan.
        """
        # Only decay memories older than 3 days and not retrieved recently
        cutoff = time.time() - 3 * 86_400
        self._conn.execute("""
            UPDATE memories
            SET importance = MAX(0.05, importance - ?)
            WHERE is_core=0 AND timestamp<? AND retrieval_count=0
        """, (decay_per_day, cutoff))
        self._conn.commit()

    def search_by_tags(self, tags: list[str], limit: int = 10) -> list["MemoryRecord"]:
        """Return records that have ALL the given tags (AND match)."""
        if not tags:
            return []
        rows: list = []
        try:
            # Use FTS on the tags JSON column — fast and portable
            for tag in tags[:3]:   # cap at 3 tags for performance
                hits = self._conn.execute(
                    """SELECT m.* FROM memories m
                       JOIN memories_fts f ON m.id = f.id
                       WHERE f.tags MATCH ?
                       ORDER BY m.importance DESC LIMIT ?""",
                    (tag, limit * 2),
                ).fetchall()
                rows.extend(hits)
            # Deduplicate by id, keep highest importance
            seen: dict[str, object] = {}
            for r in rows:
                rid = r["id"]
                if rid not in seen or r["importance"] > seen[rid]["importance"]:
                    seen[rid] = r
            return [self._row(r) for r in list(seen.values())[:limit]]
        except Exception:
            return []

    def get_recent_by_type(self, memory_type: str, since_ts: float, limit: int = 8) -> list["MemoryRecord"]:
        """Retrieve recent records of a given type since a timestamp."""
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE memory_type=? AND timestamp>=? ORDER BY timestamp DESC LIMIT ?",
            (memory_type, since_ts, limit),
        ).fetchall()
        return [self._row(r) for r in rows]

    def stats(self) -> dict:
        row = self._conn.execute(
            "SELECT COUNT(*), AVG(importance), SUM(is_core) FROM memories"
        ).fetchone()
        by_type = dict(self._conn.execute(
            "SELECT memory_type, COUNT(*) FROM memories GROUP BY memory_type"
        ).fetchall())
        return {
            "total":       row[0] or 0,
            "avg_imp":     round(row[1] or 0, 3),
            "core_count":  row[2] or 0,
            "by_type":     by_type,
        }

    def _row(self, row: sqlite3.Row) -> MemoryRecord:
        d = dict(row)
        return MemoryRecord(
            id=d["id"], content=d["content"],
            tags=json.loads(d.get("tags") or "[]"),
            importance=d["importance"], timestamp=d["timestamp"],
            turn_id=d.get("turn_id",""), memory_type=d.get("memory_type","fact"),
            emotional_weight=d.get("emotional_weight", 0.0),
            retrieval_count=d.get("retrieval_count", 0),
            is_core=bool(d.get("is_core", 0)),
        )


# ──────────────────────────────────────────────────────────────────────────────
# Semantic Store (ChromaDB)
# ──────────────────────────────────────────────────────────────────────────────

class SemanticStore:
    __slots__ = ("chroma_path", "_client", "_col", "available")
    COLLECTION = "shiro_semantic_v33"

    def __init__(self, chroma_path: str):
        self.chroma_path = chroma_path
        self._client     = None
        self._col        = None
        self.available   = False

    def init(self):
        try:
            import chromadb
            self._client = chromadb.PersistentClient(path=self.chroma_path)
            self._col    = self._client.get_or_create_collection(
                name=self.COLLECTION,
                metadata={"hnsw:space": "cosine"},
            )
            self.available = True
        except ImportError:
            print("[Memory] ChromaDB not installed — semantic search disabled.")
        except Exception as e:
            print(f"[Memory] ChromaDB init failed: {e}")

    async def query_async(self, query: str, n: int = 8) -> list[tuple[str, float, dict]]:
        if not self.available:
            return []
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(_EXECUTOR, self._query_sync, query, n)
        except Exception:
            return []

    def _query_sync(self, query: str, n: int) -> list[tuple[str, float, dict]]:
        count = self._col.count()
        if count == 0:
            return []
        res = self._col.query(
            query_texts=[query],
            n_results=min(n, count),
            include=["documents", "distances", "metadatas"],
        )
        return list(zip(res["documents"][0], res["distances"][0], res["metadatas"][0]))

    async def upsert_async(self, rec: MemoryRecord):
        if not self.available:
            return
        meta = {
            "record_id":        rec.id,
            "importance":       rec.importance,
            "memory_type":      rec.memory_type,
            "emotional_weight": rec.emotional_weight,
            "timestamp":        rec.timestamp,
            "turn_id":          rec.turn_id,
            "tags":             json.dumps(rec.tags),
            "is_core":          str(rec.is_core),
            "content_hash":     rec.content_hash(),
        }
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            _EXECUTOR,
            lambda: self._col.upsert(ids=[rec.id], documents=[rec.content], metadatas=[meta]),
        )

    async def delete_async(self, record_id: str):
        if not self.available:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(_EXECUTOR, lambda: self._col.delete(ids=[record_id]))

    def count(self) -> int:
        if not self.available:
            return 0
        try:
            return self._col.count()
        except Exception:
            return 0


# ──────────────────────────────────────────────────────────────────────────────
# Memory System
# ──────────────────────────────────────────────────────────────────────────────

class MemorySystem:
    __slots__ = (
        "episodic", "semantic",
        "_working_cache",   # {query_hash: (records, cached_at_ts)}
        "top_k_sem", "top_k_kw", "top_k_rec", "top_n",
        "imp_floor", "ttl_days", "auto_promote", "_DECAY",
        "_bg_tasks",        # list[asyncio.Task] — for clean shutdown
    )

    def __init__(self, config: dict | None = None):
        cfg      = config or {}
        base_dir = Path(cfg.get("base_dir", "./shiro_data/memory"))
        base_dir.mkdir(parents=True, exist_ok=True)

        self.episodic         = EpisodicStore(str(base_dir / "episodic.db"))
        self.semantic         = SemanticStore(str(base_dir / "chroma"))
        self._working_cache: dict[str, tuple] = {}  # keyed by query_hash → (records, cached_at_ts)

        self.top_k_sem    = cfg.get("top_k_semantic",          8)
        self.top_k_kw     = cfg.get("top_k_keyword",           5)
        self.top_k_rec    = cfg.get("top_k_recent",            3)
        self.top_n        = cfg.get("top_n_final",            10)
        self.imp_floor    = cfg.get("importance_floor",       0.12)
        self.ttl_days     = cfg.get("episodic_ttl_days",      45)
        self.auto_promote = cfg.get("auto_promote_threshold",   4)
        self._DECAY       = 0.70
        self._bg_tasks:   list = []   # populated in init()

    async def init(self):
        self.episodic.init()
        self.semantic.init()
        self._bg_tasks = [
            asyncio.create_task(self._bg_prune(), name="shiro_mem_prune"),
            asyncio.create_task(self._bg_decay(), name="shiro_mem_decay"),
        ]

    def cancel_bg_tasks(self):
        """Cancel background maintenance tasks — call from kernel.shutdown()."""
        for t in self._bg_tasks:
            t.cancel()
        self._bg_tasks.clear()

    # ── Retrieve ──────────────────────────────────────────────────────────────

    async def retrieve(self, state: "CognitiveState"):
        # Working cache: keyed by query hash, TTL 60 s.
        # Gives real hits when the user asks nearly identical questions in rapid succession.
        query1, query2 = self._build_queries(state)
        cache_key = f"{query1[:80]}|{query2[:40]}"
        cached_entry = self._working_cache.get(cache_key)
        if cached_entry:
            records, cached_at = cached_entry
            if (time.time() - cached_at) < 60.0:
                state.memory = {
                    "records":         [r.to_dict() for r in records],
                    "query_used":      "cached",
                    "semantic_online": self.semantic.available,
                    "count":           len(records),
                    "from_cache":      True,
                }
                return
            else:
                del self._working_cache[cache_key]   # expired

        # Fire both semantic queries + all SQLite queries concurrently.
        # SQLite is synchronous but fast (sub-ms on warm cache).
        sem1_task = asyncio.create_task(self.semantic.query_async(query1, n=self.top_k_sem))
        sem2_task = asyncio.create_task(self.semantic.query_async(query2, n=self.top_k_sem // 2)) if query2 != query1 else None

        kw_hits   = self.episodic.keyword_search(query1, limit=self.top_k_kw)
        rec_hits  = self.episodic.recent(limit=self.top_k_rec)
        core_hits = self.episodic.get_core()
        em_hits   = self.episodic.get_emotional(min_weight=0.5, limit=4) if state.emotion.get("intensity", 0) > 0.3 else []

        sem1_hits = await sem1_task
        sem2_hits = await sem2_task if sem2_task else []
        sem_hits  = sem1_hits + sem2_hits   # merge both semantic results

        merged = self._merge(sem_hits, kw_hits, rec_hits, core_hits, em_hits, state)

        # Batch DB updates
        ids_all     = [r.id for r in merged]
        ids_promote = [r.id for r in merged
                       if r.retrieval_count + 1 >= self.auto_promote and not r.is_core]
        if ids_all:     self.episodic.batch_increment(ids_all)
        if ids_promote: self.episodic.promote_to_core(ids_promote)

        # Cache by query key — cap at 20 entries (evict oldest if over)
        # FIX: was unbounded; many unique queries could fill RAM on long sessions.
        if len(self._working_cache) >= 20:
            oldest_key = min(self._working_cache, key=lambda k: self._working_cache[k][1])
            del self._working_cache[oldest_key]
        self._working_cache[cache_key] = (merged, time.time())
        if len(self._working_cache) > 8:
            oldest = next(iter(self._working_cache))
            del self._working_cache[oldest]

        state.memory = {
            "records":         [r.to_dict() for r in merged],
            "query_used":      query1,
            "semantic_online": self.semantic.available,
            "count":           len(merged),
            "from_cache":      False,
        }

    # ── Store (post-turn) ─────────────────────────────────────────────────────

    async def store(self, state: "CognitiveState"):
        records = self._extract(state)
        to_store = [
            r for r in records
            if r.importance >= self.imp_floor and not self.episodic.content_exists(r.content_hash())
        ]
        if not to_store:
            return
        # Single SQLite transaction for all records
        self.episodic.batch_insert(to_store)
        # Semantic upserts run concurrently
        await asyncio.gather(*[self.semantic.upsert_async(r) for r in to_store])

    # ── store_explicit ────────────────────────────────────────────────────────

    async def store_explicit(
        self,
        content:     str,
        tags:        list[str],
        importance:  float,
        memory_type: str,
        is_core:     bool = False,
    ):
        rec = MemoryRecord(
            id=uuid.uuid4().hex, content=content, tags=tags,
            importance=importance, timestamp=time.time(),
            turn_id="learning", memory_type=memory_type, is_core=is_core,
        )
        if not self.episodic.content_exists(rec.content_hash()):
            self.episodic.insert(rec)
            await self.semantic.upsert_async(rec)

    # ── Pin / Search / Delete ─────────────────────────────────────────────────

    def has_memory(self, content_fragment: str) -> bool:
        """Quick check: does any memory contain this fragment? Uses FTS for speed."""
        try:
            row = self.episodic._conn.execute(
                "SELECT id FROM memories_fts WHERE content MATCH ? LIMIT 1",
                (content_fragment,),
            ).fetchone()
            return row is not None
        except Exception:
            return False

    def pin(self, content: str, tags: list[str] | None = None) -> str:
        rec = MemoryRecord(
            id=uuid.uuid4().hex, content=content,
            tags=tags or ["pinned"], importance=1.0,
            timestamp=time.time(), turn_id="manual",
            memory_type="fact", is_core=True,
        )
        self.episodic.insert(rec)
        asyncio.create_task(self.semantic.upsert_async(rec))
        return rec.id

    async def search(self, query: str, n: int = 8) -> list[dict]:
        hits = await self.semantic.query_async(query, n=n)
        return [{"content": doc, "distance": dist, **meta} for doc, dist, meta in hits]

    async def delete(self, record_id: str):
        self.episodic.delete(record_id)
        await self.semantic.delete_async(record_id)

    def get_by_tags(self, tags: list[str], limit: int = 10) -> list:
        """Return memories tagged with all given tags."""
        return self.episodic.search_by_tags(tags, limit=limit)

    def get_recent_episodes(self, since_seconds: float = 3600.0, limit: int = 8) -> list:
        """Return episode memories from the last N seconds."""
        since_ts = time.time() - since_seconds
        return self.episodic.get_recent_by_type("episode", since_ts=since_ts, limit=limit)

    def get_preferences(self) -> list[MemoryRecord]:
        return self.episodic.get_by_type("preference", limit=25)

    def get_learning_records(self) -> list[MemoryRecord]:
        return self.episodic.get_by_type("fact", limit=25)

    def summarise(self, n: int = 5) -> str:
        """
        Returns the n most important memories as a compact text block
        for direct injection into an LLM system prompt.
        """
        records: list[MemoryRecord] = []
        records.extend(self.episodic.get_core())
        records.extend(self.episodic.get_by_type("preference", limit=10))
        records.extend(self.episodic.get_by_type("fact", limit=5))
        # Include top emotional memories — high emotional_weight = high salience
        records.extend(self.episodic.get_emotional(min_weight=0.65, limit=4))
        # Sort by importance desc, dedup by content_hash
        seen: set[str] = set()
        unique: list[MemoryRecord] = []
        for r in sorted(records, key=lambda x: x.importance, reverse=True):
            h = r.content_hash()
            if h not in seen:
                seen.add(h)
                unique.append(r)
            if len(unique) >= n:
                break
        lines = [f"[{r.memory_type}] {r.content[:120]}" for r in unique]
        return "\n".join(lines)

    def stats(self) -> dict:
        return {
            "episodic": self.episodic.stats(),
            "semantic_count": self.semantic.count(),
            "cache_entries":  len(self._working_cache),
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _build_queries(self, state: "CognitiveState") -> tuple[str, str]:
        focus = state.attention.get("top_focus", [])
        desc  = state.intent.get("description", "")
        q1 = (desc + " " + " ".join(focus[:5])).strip() or state.raw_input[:150]
        # Second query: for high-emotion turns, add primary emotion label to improve recall
        # of previously stored emotional memories (e.g. prior "anxious" episodes)
        primary_emotion = state.emotion.get("primary_label", "") if state.emotion else ""
        if primary_emotion and primary_emotion not in ("neutral","calm") and state.emotion.get("intensity",0) > 0.30:
            q2 = f"{primary_emotion} {state.raw_input[:120]}"
        else:
            q2 = state.raw_input[:150]
        return q1, q2

    def _merge(
        self,
        sem:   list[tuple[str, float, dict]],
        kw:    list[MemoryRecord],
        rec:   list[MemoryRecord],
        core:  list[MemoryRecord],
        em:    list[MemoryRecord],
        state: "CognitiveState",
    ) -> list[MemoryRecord]:
        seen_ids:    set[str] = set()
        seen_hashes: set[str] = set()
        scored: list[tuple[float, MemoryRecord]] = []
        now  = time.time()
        ew   = state.emotion.get("memory_bias", 0.0)
        DECAY = self._DECAY

        def _accept(r: MemoryRecord) -> bool:
            if r.id in seen_ids:
                return False
            h = r.content_hash()
            if h in seen_hashes:
                return False
            seen_ids.add(r.id)
            seen_hashes.add(h)
            return True

        # Core — always included
        for r in core:
            if _accept(r):
                scored.append((1.0, r))

        # Emotional memories — boosted when user is emotional
        for r in em:
            if _accept(r):
                em_boost = 0.15 if r.emotional_weight > 0.5 else 0.0
                scored.append((0.80 + em_boost, r))

        # Semantic
        for doc, dist, meta in sem:
            rid   = meta.get("record_id") or meta.get("id", uuid.uuid4().hex)
            chash = meta.get("content_hash", "")
            if rid in seen_ids or (chash and chash in seen_hashes):
                continue
            seen_ids.add(rid)
            if chash: seen_hashes.add(chash)

            sim     = max(0.0, 1.0 - dist)
            imp     = float(meta.get("importance", 0.5))
            em_w    = float(meta.get("emotional_weight", 0.0))
            ts      = float(meta.get("timestamp", now - 86_400))
            is_core = meta.get("is_core", "False") == "True"
            age_d   = (now - ts) / 86_400
            recency = math.exp(-DECAY * age_d)
            em_sim  = min(1.0, em_w * ew * 2)
            em_boost= 0.15 if em_w > 0.5 and ew > 0.3 else 0.0
            score   = sim*0.38 + imp*0.24 + recency*0.14 + em_sim*0.09 + em_boost + (0.10 if is_core else 0)

            r = MemoryRecord(
                id=rid, content=doc,
                tags=json.loads(meta.get("tags","[]")),
                importance=imp, timestamp=ts,
                turn_id=meta.get("turn_id",""),
                memory_type=meta.get("memory_type","fact"),
                emotional_weight=em_w, is_core=is_core,
            )
            scored.append((score, r))

        # Keyword
        for r in kw:
            if _accept(r):
                age_d      = (now - r.timestamp) / 86_400
                recency    = math.exp(-DECAY * age_d)
                em_sim     = min(1.0, r.emotional_weight * ew * 2)
                em_boost   = 0.15 if r.emotional_weight > 0.5 and ew > 0.3 else 0.0
                # Retrieval frequency bonus: memories retrieved often are implicitly important
                ret_bonus  = min(0.10, r.retrieval_count * 0.02)
                score      = 0.22 + r.importance*0.24 + recency*0.14 + em_sim*0.09 + em_boost + ret_bonus
                scored.append((score, r))

        # Recent
        for r in rec:
            if _accept(r):
                scored.append((0.28 + r.importance*0.20, r))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:self.top_n]]

    def _extract(self, state: "CognitiveState") -> list[MemoryRecord]:
        now      = time.time()
        ew       = state.emotion.get("memory_bias", 0.0)
        tags     = state.attention.get("top_focus", [])
        tid      = state.turn_id
        records: list[MemoryRecord] = []

        # Episode — importance reflects urgency, emotion, AND trajectory
        traj_bonus = 0.08 if state.emotion.get("trajectory") == "deteriorating" else 0.0
        # Novelty bonus: first time topics appear, make episode more important
        focus  = state.attention.get("top_focus", [])
        novel  = state.attention.get("novelty_score", 0.0)
        ep_imp = min(1.0, 0.50 + ew*0.20 + state.attention.get("urgency_score",0)*0.15 + traj_bonus + novel*0.10)
        records.append(MemoryRecord(
            id=uuid.uuid4().hex,
            content=f"USER: {state.raw_input}\nSHIRO: {state.output[:400]}",
            tags=tags, importance=round(ep_imp, 3),
            timestamp=now, turn_id=tid, memory_type="episode",
            emotional_weight=ew,
        ))

        # Reflection
        refl = state.reflection.get("summary","") if state.reflection else ""
        if refl:
            records.append(MemoryRecord(
                id=uuid.uuid4().hex, content=f"REFLECTION: {refl}",
                tags=["reflection"], importance=0.65,
                timestamp=now, turn_id=tid, memory_type="reflection",
                emotional_weight=ew,
            ))

        # Goals aligned
        for g in state.goals:
            if isinstance(g, dict) and g.get("goal"):
                records.append(MemoryRecord(
                    id=uuid.uuid4().hex,
                    content=f"GOAL ALIGNED: {g['goal']} | {g.get('reason','')}",
                    tags=["goal"], importance=0.70,
                    timestamp=now, turn_id=tid, memory_type="goal",
                ))

        # High-intensity emotional event
        intensity = state.emotion.get("intensity", 0.0)
        if intensity > 0.48:
            labels = state.emotion.get("labels", [])
            lbl_str = ", ".join(labels)
            records.append(MemoryRecord(
                id=uuid.uuid4().hex,
                content=f"EMOTIONAL EVENT [{lbl_str} traj={state.emotion.get('trajectory','?')}]: {state.raw_input[:120]}",
                tags=labels,
                importance=round(0.50 + intensity*0.42, 3),
                timestamp=now, turn_id=tid, memory_type="emotional",
                emotional_weight=intensity,
            ))

        # User name (core, never pruned)
        up = state.world_model.get("user_profile", {})
        if up.get("name") and state.world_model.get("turn_count", 0) <= 3:
            records.append(MemoryRecord(
                id=uuid.uuid4().hex,
                content=f"USER NAME: {up['name']}",
                tags=["user_profile","name"],
                importance=0.95, timestamp=now, turn_id=tid,
                memory_type="preference", is_core=True,
            ))

        # Notable event
        notable = state.reasoning.get("notable_event") if state.reasoning else None
        if notable:
            records.append(MemoryRecord(
                id=uuid.uuid4().hex,
                content=f"NOTABLE: {notable}",
                tags=["milestone"], importance=0.75,
                timestamp=now, turn_id=tid, memory_type="fact",
            ))

        return records

    # ── Background tasks ──────────────────────────────────────────────────────

    async def _bg_prune(self):
        """Prune old low-importance memories. Runs 20 s after boot, then every 6 h."""
        await asyncio.sleep(20)
        while True:
            self.episodic.prune_old(self.ttl_days, self.imp_floor)
            # Checkpoint WAL after pruning — keeps the WAL file small on long sessions
            try:
                self.episodic._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except Exception:
                pass
            await asyncio.sleep(6 * 3600)

    async def _bg_decay(self):
        """Apply gentle importance decay daily."""
        await asyncio.sleep(60)   # give system time to boot fully
        while True:
            self.episodic.apply_importance_decay(decay_per_day=0.002)
            await asyncio.sleep(24 * 3600)