"""
knowledge_graph.py — Shiro Knowledge Graph Layer v1.1
======================================================
Hardware target: RTX 3070 8 GB | i9-9900K | 64 GB RAM

Optimizations vs v1.0:
  - FLUSH_INTERVAL: 300s→180s — more frequent flushes reduce WAL file bloat
    and ensure graph state survives unexpected shutdowns.
  - MAX_NODES: 4000→2000 — the 8B model can't meaningfully use more than
    ~300 nodes per retrieval anyway; a smaller graph is faster to traverse
    and serialize.
  - MAX_EDGES_PER_NODE: 80→50 — cap fan-out tighter to prevent O(n²) ego-
    graph expansion on highly-connected topic nodes.
  - WEIGHT_DECAY_PER_WEEK: 0.05→0.08 — faster decay keeps the graph fresh
    and prevents old topics from dominating curiosity queries.
  - _prune_nodes(): now prunes by (visit_count, last_seen) composite score
    instead of just visit_count — avoids dropping recently-seen rare nodes.
  - add_edge(): added visit_count guard so orphaned nodes created by an edge
    addition aren't immediately candidates for pruning.
  - ego_graph(): radius default 2→1 for prompt injection (saves tokens);
    callers that need radius-2 pass it explicitly.
  - temporal_recall(): since_seconds default 3600→1800 — focuses on the
    most recent half-hour, which is more relevant for live conversation.
  - flush() / maybe_flush(): executemany replaces per-row execute —
    significantly faster for bulk node/edge writes at session end.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

VALID_RELATIONS = frozenset({
    "relates_to",
    "caused_by",
    "similar_to",
    "co_occurs_with",
    "contrasts_with",
    "is_a",
    "leads_to",
    "part_of",
    "associated_with",
})

VALID_ENTITY_TYPES = frozenset({
    "person", "topic", "concept", "place", "event",
    "emotion", "goal", "preference", "unknown",
})

MAX_NODES            = 2000   # was 4000 — 8B model retrieval window is limited
MAX_EDGES_PER_NODE   = 50    # was 80 — tighter cap prevents O(n²) traversal
WEIGHT_DECAY_PER_WEEK = 0.08  # was 0.05 — faster decay keeps graph fresh


# ──────────────────────────────────────────────────────────────────────────────
# GraphNode / GraphEdge
# ──────────────────────────────────────────────────────────────────────────────

class GraphNode:
    """Lightweight node. Stored as JSON in SQLite."""
    __slots__ = ("name", "entity_type", "visit_count", "first_seen", "last_seen", "tags")

    def __init__(
        self,
        name: str,
        entity_type: str = "unknown",
        visit_count: int = 1,
        first_seen: float | None = None,
        last_seen: float | None = None,
        tags: list[str] | None = None,
    ):
        now = time.time()
        self.name        = name
        self.entity_type = entity_type if entity_type in VALID_ENTITY_TYPES else "unknown"
        self.visit_count = visit_count
        self.first_seen  = first_seen or now
        self.last_seen   = last_seen or now
        self.tags        = tags or []

    def to_dict(self) -> dict:
        return {
            "name":        self.name,
            "entity_type": self.entity_type,
            "visit_count": self.visit_count,
            "first_seen":  self.first_seen,
            "last_seen":   self.last_seen,
            "tags":        self.tags,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GraphNode":
        return cls(
            name=d["name"],
            entity_type=d.get("entity_type", "unknown"),
            visit_count=d.get("visit_count", 1),
            first_seen=d.get("first_seen"),
            last_seen=d.get("last_seen"),
            tags=d.get("tags", []),
        )


class GraphEdge:
    """Directed edge with typed relation and strength."""
    __slots__ = ("source", "target", "relation", "weight", "timestamp", "co_count")

    def __init__(
        self,
        source: str,
        target: str,
        relation: str = "relates_to",
        weight: float = 0.5,
        timestamp: float | None = None,
        co_count: int = 1,
    ):
        self.source    = source
        self.target    = target
        self.relation  = relation if relation in VALID_RELATIONS else "relates_to"
        self.weight    = max(0.0, min(1.0, weight))
        self.timestamp = timestamp or time.time()
        self.co_count  = co_count

    def to_dict(self) -> dict:
        return {
            "source":    self.source,
            "target":    self.target,
            "relation":  self.relation,
            "weight":    self.weight,
            "timestamp": self.timestamp,
            "co_count":  self.co_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GraphEdge":
        return cls(
            source=d["source"],
            target=d["target"],
            relation=d.get("relation", "relates_to"),
            weight=d.get("weight", 0.5),
            timestamp=d.get("timestamp"),
            co_count=d.get("co_count", 1),
        )


# ──────────────────────────────────────────────────────────────────────────────
# KnowledgeGraph
# ──────────────────────────────────────────────────────────────────────────────

class KnowledgeGraph:
    """
    NetworkX-style knowledge graph backed by SQLite.

    In-memory: adjacency dict  { src: { tgt: GraphEdge } }
                node dict       { name: GraphNode }
    On disk:   SQLite (WAL mode, shared with memory system dir)

    Thread safety: _lock guards all mutations; reads are lock-free.
    """

    FLUSH_INTERVAL = 180   # was 300 — more frequent to reduce WAL bloat

    def __init__(self, db_path: str = "./shiro_data/knowledge_graph.db"):
        self._db_path  = db_path
        self._nodes:   Dict[str, GraphNode] = {}
        self._adj:     Dict[str, Dict[str, GraphEdge]] = defaultdict(dict)
        self._radj:    Dict[str, Set[str]] = defaultdict(set)
        self._lock     = threading.Lock()
        self._dirty    = False
        self._conn:    sqlite3.Connection | None = None
        self._last_flush = time.time()

    # ── Init ──────────────────────────────────────────────────────────────────

    def init(self):
        """Create schema and load existing graph from SQLite."""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA cache_size=-32768")
        self._conn.execute("PRAGMA mmap_size=134217728")
        try:
            self._conn.execute("DROP TABLE IF EXISTS kg_edges")
            self._conn.commit()
        except Exception:
            pass
        self._create_schema()
        self._load()
        logger.info(f"[KnowledgeGraph] Loaded {len(self._nodes)} nodes, {self._edge_count()} edges")

    def _create_schema(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS kg_nodes (
                name        TEXT PRIMARY KEY,
                data        TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS kg_edges2 (
                src      TEXT NOT NULL,
                tgt      TEXT NOT NULL,
                data     TEXT NOT NULL,
                PRIMARY KEY (src, tgt)
            );
            CREATE INDEX IF NOT EXISTS idx_kg2_src ON kg_edges2(src);
            CREATE INDEX IF NOT EXISTS idx_kg2_tgt ON kg_edges2(tgt);
        """)
        self._conn.commit()

    def _load(self):
        rows = self._conn.execute("SELECT name, data FROM kg_nodes").fetchall()
        for name, data_s in rows:
            try:
                self._nodes[name] = GraphNode.from_dict(json.loads(data_s))
            except Exception:
                pass

        rows = self._conn.execute("SELECT src, tgt, data FROM kg_edges2").fetchall()
        for src, tgt, data_s in rows:
            try:
                edge = GraphEdge.from_dict(json.loads(data_s))
                self._adj[src][tgt] = edge
                self._radj[tgt].add(src)
            except Exception:
                pass

    def _edge_count(self) -> int:
        return sum(len(v) for v in self._adj.values())

    # ── Entity management ─────────────────────────────────────────────────────

    def add_entity(
        self,
        name: str,
        entity_type: str = "unknown",
        tags: list[str] | None = None,
    ) -> GraphNode:
        """Add or update an entity node."""
        name = name.strip().lower()[:120]
        if not name:
            return None
        with self._lock:
            if name in self._nodes:
                node = self._nodes[name]
                node.visit_count += 1
                node.last_seen = time.time()
                if tags:
                    node.tags = list(set(node.tags + tags))[:20]
            else:
                node = GraphNode(name=name, entity_type=entity_type, tags=tags or [])
                self._nodes[name] = node
                if len(self._nodes) > MAX_NODES:
                    self._prune_nodes()
            self._dirty = True
        return node

    def add_edge(
        self,
        source: str,
        target: str,
        relation: str = "relates_to",
        weight: float = 0.5,
        strengthen: bool = True,
    ):
        """Add or strengthen a directed edge src→tgt."""
        source = source.strip().lower()[:120]
        target = target.strip().lower()[:120]
        if not source or not target or source == target:
            return

        # Ensure both nodes exist (with visit_count=1 so they aren't
        # immediately pruned as zero-visit nodes)
        if source not in self._nodes:
            self.add_entity(source)
        if target not in self._nodes:
            self.add_entity(target)

        with self._lock:
            # Cap fan-out
            if len(self._adj.get(source, {})) >= MAX_EDGES_PER_NODE:
                # Remove the weakest existing edge from this source
                weakest_tgt = min(
                    self._adj[source],
                    key=lambda t: self._adj[source][t].weight
                )
                removed = self._adj[source].pop(weakest_tgt, None)
                if removed:
                    self._radj[weakest_tgt].discard(source)

            if strengthen and target in self._adj.get(source, {}):
                edge = self._adj[source][target]
                edge.co_count  += 1
                edge.weight     = min(1.0, edge.weight + 0.05)
                edge.timestamp  = time.time()
            else:
                edge = GraphEdge(
                    source=source, target=target,
                    relation=relation, weight=weight
                )
                self._adj[source][target] = edge
                self._radj[target].add(source)

            self._dirty = True

    def _prune_nodes(self):
        """
        Remove least-valuable nodes when over MAX_NODES.
        Composite score = visit_count * 0.5 + recency_days_ago * -0.5
        (lower = worse = prune first).
        Nodes with any edges are protected.
        """
        now = time.time()
        connected = set(self._adj.keys()) | {t for tgts in self._adj.values() for t in tgts}

        def _node_score(name: str) -> float:
            n = self._nodes[name]
            age_days = (now - n.last_seen) / 86_400
            return n.visit_count * 0.5 - age_days * 0.5

        candidates = [
            n for n in self._nodes
            if n not in connected
        ]
        candidates.sort(key=_node_score)
        to_remove = candidates[: max(0, len(self._nodes) - MAX_NODES)]
        for name in to_remove:
            self._nodes.pop(name, None)

        if to_remove:
            logger.debug(f"[KnowledgeGraph] Pruned {len(to_remove)} nodes")

    # ── Retrieval ─────────────────────────────────────────────────────────────

    def ego_graph(
        self,
        seed: str,
        radius: int = 1,    # was default 2 — reduced to save prompt tokens
        min_weight: float = 0.1,
    ) -> List[dict]:
        """
        Return all nodes within `radius` hops of `seed`.
        radius=1 is recommended for prompt injection (typically 5-15 nodes).
        radius=2 is available for broader retrieval (can return 50-100+ nodes).
        """
        seed = seed.strip().lower()
        if seed not in self._nodes:
            return []

        visited: Set[str] = {seed}
        frontier = {seed}
        result: List[dict] = []

        for depth in range(radius):
            next_frontier: Set[str] = set()
            for node in frontier:
                for tgt, edge in self._adj.get(node, {}).items():
                    if edge.weight >= min_weight and tgt not in visited:
                        visited.add(tgt)
                        next_frontier.add(tgt)
                        n = self._nodes.get(tgt)
                        result.append({
                            "name":        tgt,
                            "entity_type": n.entity_type if n else "unknown",
                            "relation":    edge.relation,
                            "weight":      round(edge.weight, 3),
                            "depth":       depth + 1,
                        })
                # Also walk reverse edges
                for src in self._radj.get(node, set()):
                    if src not in visited:
                        edge = self._adj.get(src, {}).get(node)
                        if edge and edge.weight >= min_weight:
                            visited.add(src)
                            next_frontier.add(src)
                            n = self._nodes.get(src)
                            result.append({
                                "name":        src,
                                "entity_type": n.entity_type if n else "unknown",
                                "relation":    f"←{edge.relation}",
                                "weight":      round(edge.weight, 3),
                                "depth":       depth + 1,
                            })
            frontier = next_frontier
            if not frontier:
                break

        result.sort(key=lambda x: (-x["weight"], x["depth"]))
        return result

    def curiosity_candidates(
        self,
        seed: str,
        visited: Set[str] | None = None,
        radius: int = 2,
        top_n: int = 3,
    ) -> List[dict]:
        """
        Return nearby nodes that have NOT been recently visited.
        These are candidates for Shiro's curiosity questions.
        """
        seed = seed.strip().lower()
        visited = visited or set()
        all_nearby = self.ego_graph(seed, radius=radius, min_weight=0.05)
        candidates = [n for n in all_nearby if n["name"] not in visited]
        # Prefer lower visit_count nodes (less explored)
        candidates.sort(
            key=lambda x: (
                self._nodes[x["name"]].visit_count if x["name"] in self._nodes else 0,
                -x["weight"]
            )
        )
        return candidates[:top_n]

    def temporal_recall(
        self,
        since_seconds: float = 1800.0,   # was 3600 — focus on last 30 min
        limit: int = 20,
    ) -> List[dict]:
        """
        Return edges updated within the last `since_seconds`.
        """
        cutoff = time.time() - since_seconds
        results = []
        for src, tgts in self._adj.items():
            for tgt, edge in tgts.items():
                if edge.timestamp >= cutoff:
                    results.append({
                        "source":   src,
                        "target":   tgt,
                        "relation": edge.relation,
                        "weight":   round(edge.weight, 3),
                        "ts":       edge.timestamp,
                    })
        results.sort(key=lambda x: -x["ts"])
        return results[:limit]

    def apply_weight_decay(self):
        """Apply weekly weight decay to all edges. Remove near-zero edges."""
        decay = WEIGHT_DECAY_PER_WEEK
        removed = 0
        with self._lock:
            for src in list(self._adj.keys()):
                dead = []
                for tgt, edge in self._adj[src].items():
                    edge.weight = max(0.0, edge.weight - decay)
                    if edge.weight < 0.01:
                        dead.append(tgt)
                for tgt in dead:
                    self._adj[src].pop(tgt, None)
                    self._radj[tgt].discard(src)
                    removed += 1
                if not self._adj[src]:
                    del self._adj[src]
            self._dirty = True
        logger.info(f"[KG] Weight decay: {removed} edges weakened/removed")

    # ── Persistence ───────────────────────────────────────────────────────────

    def maybe_flush(self):
        """Auto-flush if dirty and interval has elapsed."""
        if self._dirty and (time.time() - self._last_flush) >= self.FLUSH_INTERVAL:
            self.flush()

    def flush(self, force: bool = False):
        """Write all dirty nodes and edges to SQLite using executemany."""
        if not self._dirty and not force:
            return
        if self._conn is None:
            return
        try:
            with self._lock:
                node_rows = [
                    (name, json.dumps(node.to_dict()))
                    for name, node in self._nodes.items()
                ]
                edge_rows = [
                    (src, tgt, json.dumps(edge.to_dict()))
                    for src, tgts in self._adj.items()
                    for tgt, edge in tgts.items()
                ]

            self._conn.executemany(
                "INSERT OR REPLACE INTO kg_nodes (name, data) VALUES (?, ?)",
                node_rows
            )
            self._conn.executemany(
                "INSERT OR REPLACE INTO kg_edges2 (src, tgt, data) VALUES (?, ?, ?)",
                edge_rows
            )
            self._conn.commit()
            self._dirty = False
            self._last_flush = time.time()
            logger.debug(f"[KnowledgeGraph] Flushed {len(node_rows)} nodes, {len(edge_rows)} edges")
        except Exception as e:
            logger.error(f"[KnowledgeGraph] Flush error: {e}")

    def close(self):
        self.flush(force=True)
        if self._conn:
            self._conn.close()
            self._conn = None