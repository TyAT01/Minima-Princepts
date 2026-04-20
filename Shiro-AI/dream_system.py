"""
dream_system.py — Shiro Dream System v1.1
==========================================
Hardware target: RTX 3070 8 GB | i9-9900K | 64 GB RAM

Optimizations vs v1.0:
  - ACTIVE_INTERVAL_S raised 300→600: dream cycle was competing with LLM inference
    during active conversation, adding latency spikes seen in the log.
  - CONSOLIDATION_INTERVAL_S raised 600→900: over-consolidation was creating
    redundant [CONSOLIDATED] memory entries, polluting memory retrieval.
  - KG decay cycle check now also gates on minimum edge count to avoid
    decaying a nearly-empty graph after first boot.
  - _write_dream_reflection: reflection importance lowered 0.35→0.25 so dream
    entries don't outcompete real episodic memories for retrieval slots.
  - Added primary_user param to DreamSystem for identity-aware curiosity wording.
  - _generate_curiosity: question templates now include Shiro's voice/character.
  - Shutdown: kg.flush(force=True) now guarded against None kg reference.
"""

from __future__ import annotations

import logging
import math
import random
import re
import time
import threading
import uuid
from collections import defaultdict
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from memory_system import MemorySystem, MemoryRecord
    from knowledge_graph import KnowledgeGraph

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# TF-IDF helpers (zero-dependency, for consolidation clustering)
# ──────────────────────────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z]{3,}", text.lower())


def _tfidf_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    tf: dict[str, float] = defaultdict(float)
    for t in tokens:
        tf[t] += 1.0
    n = len(tokens) or 1
    return {t: (c / n) * idf.get(t, 1.0) for t, c in tf.items()}


def _cosine(v1: dict[str, float], v2: dict[str, float]) -> float:
    shared = set(v1) & set(v2)
    if not shared:
        return 0.0
    dot = sum(v1[k] * v2[k] for k in shared)
    mag1 = math.sqrt(sum(x * x for x in v1.values())) or 1e-9
    mag2 = math.sqrt(sum(x * x for x in v2.values())) or 1e-9
    return dot / (mag1 * mag2)


def _build_idf(corpus: list[list[str]]) -> dict[str, float]:
    df: dict[str, int] = defaultdict(int)
    N = len(corpus) or 1
    for doc in corpus:
        for t in set(doc):
            df[t] += 1
    return {t: math.log(N / (df[t] + 1)) + 1.0 for t in df}


# ──────────────────────────────────────────────────────────────────────────────
# Importance Scorer
# ──────────────────────────────────────────────────────────────────────────────

class ImportanceScorer:
    """
    Re-scores a MemoryRecord in the context of the current session.
    Weights tuned for 8B model where memory retrieval slots are limited.
    """

    HALF_LIFE_DAYS = 7.0

    def score(self, rec: "MemoryRecord") -> float:
        now = time.time()
        age_days = (now - rec.timestamp) / 86_400
        recency  = math.exp(-0.693 * age_days / self.HALF_LIFE_DAYS)
        ret_freq = min(1.0, math.log1p(rec.retrieval_count) / math.log1p(20))
        ew       = rec.emotional_weight

        score = (
            rec.importance * 0.40
            + recency      * 0.25
            + ret_freq     * 0.20
            + ew           * 0.15
        )
        return round(min(1.0, score), 4)


# ──────────────────────────────────────────────────────────────────────────────
# DreamSystem
# ──────────────────────────────────────────────────────────────────────────────

class DreamSystem:
    """
    Autonomous background cognitive loop.

    Public interface:
        start()    — begin background thread
        pause()    — pause before active turn
        resume()   — resume after active turn
        shutdown() — clean stop + flush
        status()   — dict with stats
    """

    # Cycle intervals (seconds)
    IDLE_INTERVAL_S          = 90    # between dream cycles when idle
    ACTIVE_INTERVAL_S        = 600   # was 300 — raised to reduce inference contention
    CONSOLIDATION_INTERVAL_S = 900   # was 600 — over-consolidation polluted memory
    KG_DECAY_INTERVAL_S      = 7 * 86_400   # weekly
    KG_MIN_EDGES_FOR_DECAY   = 50    # don't decay a near-empty graph

    # Clustering threshold
    CONSOLIDATION_SIM = 0.62   # slightly tighter than 0.60 to avoid false merges

    def __init__(
        self,
        memory: "MemorySystem",
        kg: Optional["KnowledgeGraph"] = None,
        cycle_interval: int | None = None,
        inner_mind=None,
        primary_user: str = "Tyler",   # for identity-aware curiosity wording
    ):
        self._memory         = memory
        self._kg             = kg
        self._inner_mind     = inner_mind
        self._primary_user   = primary_user
        self._cycle_interval = cycle_interval or self.IDLE_INTERVAL_S
        self._paused         = threading.Event()
        self._paused.set()    # starts un-paused
        self._stop           = threading.Event()
        self._thread: threading.Thread | None = None
        self._scorer         = ImportanceScorer()

        self._last_consolidation = 0.0
        self._last_kg_decay      = 0.0
        self._cycles_run         = 0
        self._memories_consolidated = 0
        self._curiosities_generated = 0
        self._lock = threading.Lock()

        self._last_cycle_ts  = 0.0
        self._last_dream_log = ""

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="shiro_dream"
        )
        self._thread.start()
        logger.info("[DreamSystem] Background dream thread started")

    def pause(self):
        """Call before processing a user message."""
        self._paused.clear()

    def resume(self):
        """Call after response is sent."""
        self._paused.set()
        self._cycle_interval = self.IDLE_INTERVAL_S

    def set_active(self):
        """Mark that a conversation is ongoing — slow down cycle."""
        self._cycle_interval = self.ACTIVE_INTERVAL_S

    def shutdown(self):
        self._stop.set()
        self._paused.set()   # unblock if waiting
        if self._thread:
            self._thread.join(timeout=10)
        # Guard against None KG (vision-disabled boot etc.)
        if self._kg is not None:
            try:
                self._kg.flush(force=True)
            except Exception as _fe:
                logger.warning(f"[DreamSystem] KG flush on shutdown failed: {_fe}")
        logger.info("[DreamSystem] Shutdown complete")

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _loop(self):
        while not self._stop.is_set():
            self._paused.wait()
            if self._stop.is_set():
                break

            elapsed = 0
            while elapsed < self._cycle_interval:
                if self._stop.is_set():
                    return
                if not self._paused.is_set():
                    self._paused.wait()
                    elapsed = 0
                time.sleep(1.0)
                elapsed += 1

            if self._stop.is_set():
                break

            try:
                self._run_cycle()
            except Exception as e:
                logger.error(f"[DreamSystem] Cycle error: {e}", exc_info=True)

    def _run_cycle(self):
        start = time.time()
        self._cycles_run += 1
        logger.debug(f"[DreamSystem] Cycle #{self._cycles_run} starting")

        n_promoted    = self._rescore_and_promote()

        n_consolidated = 0
        if (start - self._last_consolidation) >= self.CONSOLIDATION_INTERVAL_S:
            n_consolidated = self._consolidate()
            self._last_consolidation = start

        n_curious = self._generate_curiosity()

        if self._kg is not None:
            self._kg.maybe_flush()
            edge_count = self._kg._edge_count() if hasattr(self._kg, "_edge_count") else 999
            if (
                (start - self._last_kg_decay) >= self.KG_DECAY_INTERVAL_S
                and edge_count >= self.KG_MIN_EDGES_FOR_DECAY
            ):
                self._kg.apply_weight_decay()
                self._last_kg_decay = start

        self._write_dream_reflection(n_promoted, n_consolidated, n_curious)

        elapsed = time.time() - start
        self._last_cycle_ts = start
        self._memories_consolidated += n_consolidated
        self._curiosities_generated  += n_curious

        logger.info(
            f"[DreamSystem] Cycle #{self._cycles_run} done in {elapsed:.2f}s — "
            f"promoted={n_promoted} consolidated={n_consolidated} curious={n_curious}"
        )

    # ── Step 1: Re-score + promote ────────────────────────────────────────────

    def _rescore_and_promote(self) -> int:
        episodic = getattr(self._memory, "episodic", None)
        if episodic is None:
            return 0
        try:
            records = (
                episodic.get_by_type("episode",   limit=30)
                + episodic.get_by_type("fact",     limit=20)
                + episodic.get_by_type("emotional", limit=15)
            )
            promote_ids = []
            for rec in records:
                if rec.is_core:
                    continue
                score = self._scorer.score(rec)
                if score >= 0.78:
                    promote_ids.append(rec.id)
            if promote_ids:
                episodic.promote_to_core(promote_ids)
                logger.debug(f"[DreamSystem] Promoted {len(promote_ids)} memories to core")
            return len(promote_ids)
        except Exception as e:
            logger.warning(f"[DreamSystem] Rescore error: {e}")
            return 0

    # ── Step 2: Memory consolidation ─────────────────────────────────────────

    def _consolidate(self) -> int:
        episodic = getattr(self._memory, "episodic", None)
        if episodic is None:
            return 0
        try:
            records = episodic.get_by_type("episode", limit=50)
            candidates = [
                r for r in records
                if not r.is_core and r.importance < 0.90
            ]
            if len(candidates) < 3:
                return 0

            tokenized = [_tokenize(r.content) for r in candidates]
            idf = _build_idf(tokenized)
            vectors = [_tfidf_vector(tok, idf) for tok in tokenized]

            used = [False] * len(candidates)
            clusters: list[list[int]] = []
            for i in range(len(candidates)):
                if used[i]:
                    continue
                cluster = [i]
                used[i] = True
                for j in range(i + 1, len(candidates)):
                    if used[j]:
                        continue
                    sim = _cosine(vectors[i], vectors[j])
                    if sim >= self.CONSOLIDATION_SIM:
                        cluster.append(j)
                        used[j] = True
                if len(cluster) >= 2:
                    clusters.append(cluster)

            if not clusters:
                return 0

            n_written = 0
            for cluster in clusters[:5]:
                members  = [candidates[i] for i in cluster]
                combined = " | ".join(r.content[:100] for r in members[:4])
                avg_imp  = sum(r.importance for r in members) / len(members)
                avg_ew   = sum(r.emotional_weight for r in members) / len(members)
                all_tags = list({t for r in members for t in r.tags})[:10]
                new_imp  = min(1.0, avg_imp + 0.10)

                try:
                    from memory_system import MemoryRecord
                    rec = MemoryRecord(
                        id=uuid.uuid4().hex,
                        content=f"[CONSOLIDATED] {combined}",
                        tags=all_tags + ["consolidated"],
                        importance=new_imp,
                        timestamp=time.time(),
                        turn_id="dream",
                        memory_type="consolidated",
                        emotional_weight=avg_ew,
                        is_core=new_imp >= 0.80,
                    )
                    episodic.insert(rec)
                    n_written += 1
                except Exception as _ins_e:
                    logger.debug(f"[DreamSystem] Consolidation insert error: {_ins_e}")

            return n_written
        except Exception as e:
            logger.warning(f"[DreamSystem] Consolidation error: {e}")
            return 0

    # ── Step 3: Curiosity generation ─────────────────────────────────────────

    def _generate_curiosity(self) -> int:
        if not self._kg:
            return 0
        episodic = getattr(self._memory, "episodic", None)
        if episodic is None:
            return 0
        try:
            recent_edges = self._kg.temporal_recall(since_seconds=3600.0, limit=10)
            if not recent_edges:
                return 0

            seed_edge = random.choice(recent_edges)
            seed = seed_edge["source"]
            visited = {e["source"] for e in recent_edges} | {e["target"] for e in recent_edges}
            candidates = self._kg.curiosity_candidates(seed, visited=visited, radius=2, top_n=3)
            if not candidates:
                return 0

            n = 0
            for cand in candidates:
                topic = cand["name"]
                question = self._make_curiosity_question(topic, seed, cand["relation"])
                if not question:
                    continue
                try:
                    from memory_system import MemoryRecord
                    rec = MemoryRecord(
                        id=uuid.uuid4().hex,
                        content=f"[CURIOSITY] {question}",
                        tags=["curiosity", topic, seed],
                        importance=0.40,   # was 0.45 — slightly less intrusive
                        timestamp=time.time(),
                        turn_id="dream",
                        memory_type="curiosity",
                        emotional_weight=0.15,
                    )
                    episodic.insert(rec)
                    n += 1
                except Exception:
                    pass
            return n
        except Exception as e:
            logger.warning(f"[DreamSystem] Curiosity error: {e}")
            return 0

    def _make_curiosity_question(self, topic: str, seed: str, relation: str) -> str:
        """Generate a curiosity question in Shiro's voice."""
        templates = [
            f"Hm. {topic} keeps surfacing near {seed} in my thoughts. What's the actual connection?",
            f"Haven't turned over {topic} much. Does it loop back to {seed} somehow?",
            f"What do I actually know about {topic}? Worth looking at.",
            f"{topic} and {seed} seem related. I want to know why.",
            f"I'm curious about {topic} — especially how it ties to {seed}.",
        ]
        return random.choice(templates)

    def _write_dream_reflection(self, promoted: int, consolidated: int, curious: int):
        """Write a short dream reflection entry."""
        if promoted == 0 and consolidated == 0 and curious == 0:
            return
        episodic = getattr(self._memory, "episodic", None)
        if episodic is None:
            return
        try:
            parts = []
            if promoted:     parts.append(f"{promoted} memories became core")
            if consolidated: parts.append(f"consolidated {consolidated} cluster(s)")
            if curious:      parts.append(f"generated {curious} curiosity thread(s)")

            reflection = f"[DREAM] Cycle #{self._cycles_run}: " + "; ".join(parts) + "."
            self._last_dream_log = reflection

            from memory_system import MemoryRecord
            rec = MemoryRecord(
                id=uuid.uuid4().hex,
                content=reflection,
                tags=["dream", "reflection"],
                importance=0.25,   # was 0.35 — lowered so dream entries don't crowd real memories
                timestamp=time.time(),
                turn_id="dream",
                memory_type="dream_reflection",
                emotional_weight=0.1,
            )
            episodic.insert(rec)
        except Exception as e:
            logger.debug(f"[DreamSystem] Dream reflection write error: {e}")

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self) -> dict:
        alive = self._thread is not None and self._thread.is_alive()
        return {
            "running":               alive,
            "paused":                not self._paused.is_set(),
            "cycles_run":            self._cycles_run,
            "memories_consolidated": self._memories_consolidated,
            "curiosities_generated": self._curiosities_generated,
            "last_cycle_ts":         self._last_cycle_ts,
            "last_dream_log":        self._last_dream_log,
            "cycle_interval_s":      self._cycle_interval,
        }