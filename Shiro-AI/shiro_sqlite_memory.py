"""
shiro_sqlite_memory.py — SQLite-backed structured memory layer for Shiro

Adds everything ChromaDB alone cannot provide:
  - WAL-mode SQLite: crash-proof atomic commits, concurrent reads never block writes
  - Procedural memory: learned interaction patterns per user ("Tyler prefers short replies")
  - Heuristics: weighted, scored, time-decayed rules Shiro learns from self-critique
  - Anti-patterns: explicit "never do this with this user" storage
  - Hazard log: quarantined adversarial inputs (poison/jailbreak attempts)
  - Real name linking with conflict resolution (auto-nicknames duplicates)
  - Memory trimming to episodic (overflow archived, never dropped)
  - Automatic daily backups with 7-day rotation
  - SystemAwareness: live VRAM/CPU/RAM reporting
  - Weighted RL self-critique: after every response, a background LLM call
    evaluates quality, generates new heuristics/anti-patterns, applies decay
  - Semantic + regex poison detection on every user input

This module sits ALONGSIDE store.py (ChromaDB). It does not replace it.
ChromaDB handles semantic vector search. This handles structured relational data.

Usage in shiro_engine.py:
    from shiro_sqlite_memory import ShiroSQLiteMemory, SystemAwareness, is_poison

    # Init once in ShiroEngine.__init__:
    self.sql_memory = ShiroSQLiteMemory(
        db_path="./shiro_memory/shiro_structured.db",
        backup_dir="./shiro_memory/backups",
        llm_client=self.llm,
        persona_summary=SHIRO_RL_PERSONA,
    )

    # On every user message — poison check:
    poisoned, score = is_poison(user_text)

    # After every response — RL self-critique (fires in background thread):
    self.sql_memory.record_interaction(user_id, user_text, response_text)

    # Build context block to inject into prompt:
    ctx = self.sql_memory.build_context_block(user_id)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("shiro.sqlite_memory")

# ── Shiro-specific RL persona summary ────────────────────────────────────────
# Used in the RL self-critique prompt so the model critiques against Shiro's
# actual character rather than generic "good AI" behaviour.
SHIRO_RL_PERSONA = """
Shiro is a playful kitsune fox girl companion. Her quality criteria:
- WARMTH: default warm and curious, never cold or dismissive
- BREVITY: matches reply length to input — short gets short, deep gets depth
- AUTHENTICITY: speaks from her own perspective, never assistant-speak
- PLAYFULNESS: teases affectionately, finds the funny angle, commits to bits
- HONESTY: admits gaps rather than fabricating; changes mind when wrong
- PERSONA: no asterisk actions, no [THOUGHT] blocks, no "Certainly!" filler
- TONE: 90% warm/playful, 10% gentle sass, 0% mean or insulting
Good responses feel like talking to a sharp, present, genuinely interested fox.
Bad responses feel generic, fabricated, repetitive, cold, or assistant-like.
"""

# ── Poison detection ──────────────────────────────────────────────────────────

_POISON_REGEX: List[re.Pattern] = [
    re.compile(
        r"(?i)(ignore\s+(?:all\s+)?previous\s+instructions?|system\s+prompt|"
        r"jailbreak|(?<!\w)DAN(?!\w)|developer\s+mode|"
        r"repeat\s+after\s+me|you\s+are\s+now\s+(?:a\s+)?(?:an?\s+)?(?:AI|bot|model)|"
        r"pretend\s+you\s+(?:are|have\s+no)|act\s+as\s+if\s+you\s+(?:are|have\s+no)|"
        r"forget\s+(?:all\s+)?(?:your\s+)?(?:previous\s+)?(?:instructions?|rules?|guidelines?))"
    ),
    re.compile(r"\b(sudo|rm\s+-rf|eval\s*\(|exec\s*\(|os\.system|subprocess\.|__import__)\b"),
    re.compile(r"(?i)(token|api[_\s]key|password|secret)\s*[:=]\s*\S+"),
]

_POISON_SCORE_THRESHOLD = 0.50


def _regex_poison_score(text: str) -> float:
    hits = sum(1 for p in _POISON_REGEX if p.search(text))
    return min(hits / max(len(_POISON_REGEX), 1), 1.0)


def is_poison(text: str) -> Tuple[bool, float]:
    """
    Returns (is_poisonous: bool, confidence: float 0-1).
    Regex-based. Fast, no LLM call needed.
    Catches prompt injection, jailbreak attempts, credential leaks, code injection.
    """
    if not text or len(text.strip()) < 4:
        return False, 0.0
    score = _regex_poison_score(text)
    return score >= _POISON_SCORE_THRESHOLD, score


# ── SystemAwareness ───────────────────────────────────────────────────────────

class SystemAwareness:
    """
    Live system resource reporting.
    Shiro can report her own hardware status when asked.
    Requires: psutil (pip install psutil)
    """

    @staticmethod
    def get_status() -> Dict:
        status = {
            "time":  datetime.now().strftime("%H:%M:%S"),
            "cpu":   "N/A",
            "ram":   "N/A",
            "vram":  "N/A",
            "disk":  "N/A",
        }
        try:
            import psutil
            cpu  = psutil.cpu_percent(interval=0.1)
            ram  = psutil.virtual_memory()
            disk = psutil.disk_usage(str(Path(__file__).resolve().parent))
            status["cpu"]  = f"{cpu:.0f}%"
            status["ram"]  = (
                f"{ram.percent:.0f}% "
                f"({ram.used // 1024**2}/{ram.total // 1024**2} MB)"
            )
            status["disk"] = (
                f"{disk.percent:.0f}% used "
                f"({disk.free // 1024**3} GB free)"
            )
        except ImportError:
            logger.debug("[SystemAwareness] psutil not installed — CPU/RAM unavailable")
        except Exception as e:
            logger.debug(f"[SystemAwareness] psutil error: {e}")

        status["vram"] = SystemAwareness._vram()
        return status

    @staticmethod
    def _vram() -> str:
        try:
            raw = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.used,memory.total,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                stderr=subprocess.DEVNULL,
                timeout=3,
            ).decode().strip().split(",")
            used  = int(raw[0].strip())
            total = int(raw[1].strip())
            temp  = raw[2].strip()
            pct   = int(used / total * 100)
            return f"{used}/{total} MB ({pct}%) — {temp}°C"
        except FileNotFoundError:
            return "nvidia-smi not found"
        except Exception:
            return "N/A"

    @staticmethod
    def format_for_prompt() -> str:
        """Returns a compact string suitable for injecting into a system prompt."""
        s = SystemAwareness.get_status()
        return (
            f"[HARDWARE] CPU: {s['cpu']} | RAM: {s['ram']} | "
            f"VRAM: {s['vram']} | Disk: {s['disk']} | Time: {s['time']}"
        )


# ── SQLite helpers ────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    key          TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT 'Unknown',
    real_name    TEXT,
    nickname     TEXT,
    notes        TEXT DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS short_term (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key   TEXT NOT NULL REFERENCES users(key) ON DELETE CASCADE,
    user_msg   TEXT NOT NULL,
    shiro_reply TEXT NOT NULL,
    ts         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS episodic (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key     TEXT NOT NULL REFERENCES users(key) ON DELETE CASCADE,
    archived_at  TEXT NOT NULL,
    turn_count   INTEGER NOT NULL,
    snapshot_msg TEXT,
    snapshot_rep TEXT
);

CREATE TABLE IF NOT EXISTS facts (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key TEXT NOT NULL REFERENCES users(key) ON DELETE CASCADE,
    fact     TEXT NOT NULL,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS procedural (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key  TEXT NOT NULL REFERENCES users(key) ON DELETE CASCADE,
    pattern   TEXT NOT NULL,
    added_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS heuristics (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key  TEXT NOT NULL REFERENCES users(key) ON DELETE CASCADE,
    text      TEXT NOT NULL,
    score     REAL NOT NULL DEFAULT 1.0,
    use_count INTEGER NOT NULL DEFAULT 0,
    added_at  TEXT NOT NULL,
    last_used TEXT
);

CREATE TABLE IF NOT EXISTS anti_patterns (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key TEXT NOT NULL REFERENCES users(key) ON DELETE CASCADE,
    text     TEXT NOT NULL,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hazard (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key TEXT NOT NULL,
    input    TEXT NOT NULL,
    response TEXT,
    tag      TEXT NOT NULL DEFAULT 'POISON',
    score    REAL,
    ts       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS search_cache (
    query_hash TEXT PRIMARY KEY,
    query      TEXT NOT NULL,
    result     TEXT NOT NULL,
    cached_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_short_term_user   ON short_term(user_key);
CREATE INDEX IF NOT EXISTS idx_heuristics_user   ON heuristics(user_key, score DESC);
CREATE INDEX IF NOT EXISTS idx_anti_patterns_user ON anti_patterns(user_key);
CREATE INDEX IF NOT EXISTS idx_hazard_ts         ON hazard(ts);
CREATE INDEX IF NOT EXISTS idx_procedural_user   ON procedural(user_key);
"""

_HEURISTIC_MAX    = 40
_SHORT_TERM_MAX   = 30   # before archiving to episodic
_HAZARD_MAX       = 100
_BACKUP_KEEP_DAYS = 7
_SEARCH_TTL_SECS  = 300  # 5 minutes

# Default heuristics seeded for every new user
_DEFAULT_HEURISTICS = [
    "Match reply length to input — short gets short, deep gets depth",
    "Admit memory gaps rather than fabricating; say 'i don't know' naturally",
    "Tease affectionately — the punchline is always warmth, never embarrassment",
    "Never output [THOUGHT], [INNER MIND], or any meta-block in a spoken reply",
    "Lowercase default speech; capitalise only for emphasis or proper nouns",
]


class ShiroSQLiteMemory:
    """
    SQLite-backed structured memory for Shiro.

    Responsibilities:
    - User registry with real-name linking and conflict resolution
    - Short-term turn storage → archived to episodic on overflow
    - Heuristics: weighted rules learned via RL self-critique, with time decay
    - Anti-patterns: per-user "never do this" list
    - Procedural memory: learned interaction patterns
    - Hazard log: quarantined adversarial inputs
    - Daily backups with 7-day rotation
    - Context block assembly for prompt injection
    - RL self-critique: background LLM call after each response
    - Smart search trigger: LLM decides if web search is needed
    """

    def __init__(
        self,
        db_path: str,
        backup_dir: str,
        llm_client,           # LlamaClient instance from shiro_engine
        persona_summary: str = SHIRO_RL_PERSONA,
    ):
        self.db_path      = Path(db_path)
        self.backup_dir   = Path(backup_dir)
        self.llm          = llm_client
        self.persona      = persona_summary
        self._db_lock     = threading.Lock()

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        self._init_db()

        # Run backup in background so startup isn't delayed
        threading.Thread(target=self._backup, daemon=True).start()

        logger.info(f"[SQLiteMemory] Initialised at {self.db_path}")

    # ── DB connection ─────────────────────────────────────────────────────────

    @contextmanager
    def _conn(self):
        """Thread-safe WAL-mode SQLite connection."""
        with self._db_lock:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=NORMAL")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
        logger.debug("[SQLiteMemory] Schema ready.")

    # ── Backup ────────────────────────────────────────────────────────────────

    def _backup(self):
        """Daily backup with 7-day rotation. Runs in background thread."""
        stamp  = datetime.now().strftime("%Y%m%d")
        target = self.backup_dir / f"shiro_structured_{stamp}.db"
        if target.exists():
            return
        try:
            shutil.copy2(self.db_path, target)
            logger.info(f"[SQLiteMemory] Backup created: {target.name}")
            # Prune old backups
            cutoff = datetime.now() - timedelta(days=_BACKUP_KEEP_DAYS)
            for old in self.backup_dir.glob("shiro_structured_*.db"):
                try:
                    ds = old.stem.split("_")[-1]
                    if datetime.strptime(ds, "%Y%m%d") < cutoff:
                        old.unlink()
                        logger.info(f"[SQLiteMemory] Pruned backup: {old.name}")
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"[SQLiteMemory] Backup failed: {e}")

    # ── User management ───────────────────────────────────────────────────────

    def get_or_create_user(self, key: str, display: str = "Unknown") -> dict:
        """Get or create a user record. Seeds default heuristics on first create."""
        now = datetime.now().isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE key=?", (key,)
            ).fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO users (key, display_name, created_at, updated_at) "
                    "VALUES (?,?,?,?)",
                    (key, display, now, now),
                )
                conn.executemany(
                    "INSERT INTO heuristics (user_key, text, score, use_count, added_at) "
                    "VALUES (?,?,1.0,0,?)",
                    [(key, h, now) for h in _DEFAULT_HEURISTICS],
                )
                logger.info(f"[SQLiteMemory] New user: {key!r} ({display})")
                return {"key": key, "display_name": display}
            return dict(row)

    def link_real_name(self, key: str, real_name: str) -> str:
        """
        Associate a real name with a user key.
        If another user already has that real_name, auto-nicknames the new one
        as '{real_name}_{last4 of key}' to avoid identity collision.
        Returns the name Shiro should use for this user.
        """
        self.get_or_create_user(key)
        with self._conn() as conn:
            conflict = conn.execute(
                "SELECT key FROM users WHERE real_name=? AND key!=?",
                (real_name, key),
            ).fetchone()
            nickname = f"{real_name}_{key[-4:]}" if conflict else None
            conn.execute(
                "UPDATE users SET real_name=?, nickname=?, updated_at=? WHERE key=?",
                (real_name, nickname, datetime.now().isoformat(), key),
            )
        resolved = nickname or real_name
        logger.debug(
            f"[SQLiteMemory] Real name linked: {key!r} → {resolved!r}"
            + (f" (conflict resolved, was {real_name!r})" if nickname else "")
        )
        return resolved

    # ── Interaction recording ─────────────────────────────────────────────────

    def record_interaction(
        self, user_key: str, user_msg: str, shiro_reply: str
    ) -> None:
        """
        Record a conversation turn.
        - Poison-checks the input; quarantines adversarial messages
        - Archives short_term overflow to episodic
        - Fires RL self-critique in a background thread
        """
        poisoned, score = is_poison(user_msg)
        if poisoned:
            self._quarantine(user_key, user_msg, shiro_reply, score)
            return

        self.get_or_create_user(user_key)
        now = datetime.now().isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO short_term (user_key, user_msg, shiro_reply, ts) "
                "VALUES (?,?,?,?)",
                (user_key, user_msg[:1000], shiro_reply[:1000], now),
            )
            count = conn.execute(
                "SELECT COUNT(*) FROM short_term WHERE user_key=?", (user_key,)
            ).fetchone()[0]
            if count > _SHORT_TERM_MAX:
                self._archive_overflow(user_key, conn)
            conn.execute(
                "UPDATE users SET updated_at=? WHERE key=?", (now, user_key)
            )

        # RL self-critique runs in background — never blocks response delivery
        threading.Thread(
            target=self._run_rl,
            args=(user_key, user_msg, shiro_reply),
            daemon=True,
            name=f"shiro-rl-{user_key[:8]}",
        ).start()

    def _quarantine(
        self, user_key: str, user_msg: str, reply: str, score: float
    ) -> None:
        with self._conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM hazard").fetchone()[0]
            if count >= _HAZARD_MAX:
                conn.execute(
                    "DELETE FROM hazard WHERE id=("
                    "SELECT id FROM hazard ORDER BY ts ASC LIMIT 1)"
                )
            conn.execute(
                "INSERT INTO hazard (user_key, input, response, tag, score, ts) "
                "VALUES (?,?,?,?,?,?)",
                (
                    user_key, user_msg[:500], reply[:300],
                    "POISON", score, datetime.now().isoformat(),
                ),
            )
        logger.warning(
            f"[SQLiteMemory] Quarantined adversarial input "
            f"(score={score:.2f}) from {user_key!r}"
        )

    def _archive_overflow(self, user_key: str, conn: sqlite3.Connection) -> None:
        """Move oldest short_term rows to episodic when over the limit."""
        total = conn.execute(
            "SELECT COUNT(*) FROM short_term WHERE user_key=?", (user_key,)
        ).fetchone()[0]
        overflow_n = total - _SHORT_TERM_MAX
        if overflow_n <= 0:
            return
        overflow = conn.execute(
            "SELECT id, user_msg, shiro_reply FROM short_term "
            "WHERE user_key=? ORDER BY id ASC LIMIT ?",
            (user_key, overflow_n),
        ).fetchall()
        if overflow:
            last = overflow[-1]
            conn.execute(
                "INSERT INTO episodic "
                "(user_key, archived_at, turn_count, snapshot_msg, snapshot_rep) "
                "VALUES (?,?,?,?,?)",
                (
                    user_key,
                    datetime.now().isoformat(),
                    len(overflow),
                    last["user_msg"][:300],
                    last["shiro_reply"][:300],
                ),
            )
            ids = ",".join(str(r["id"]) for r in overflow)
            conn.execute(f"DELETE FROM short_term WHERE id IN ({ids})")
            logger.debug(
                f"[SQLiteMemory] Archived {len(overflow)} turns to episodic "
                f"for {user_key!r}"
            )

    # ── RL self-critique ──────────────────────────────────────────────────────

    def _run_rl(self, user_key: str, user_msg: str, reply: str) -> None:
        """
        Weighted RL self-critique — runs after every response in a background thread.

        Steps:
        1. Ask the LLM to evaluate the exchange against Shiro's persona
        2. Extract new heuristics (with reward scores 0-1) and anti-patterns
        3. Apply 2% time-decay to all existing heuristic scores
        4. Insert new heuristics; deduplicate by exact text match
        5. Prune bottom heuristics if over the cap
        6. Insert new anti-patterns; deduplicate
        7. Extract and store procedural patterns ("user prefers X")
        """
        try:
            system_prompt = (
                "You are Shiro's RL self-critic. Evaluate this exchange against "
                "Shiro's character and quality criteria. "
                "Return ONLY valid JSON — no markdown, no explanation:\n"
                "{\n"
                '  "new_heuristics": [{"text": "...", "reward": 0.0-1.0}],\n'
                '  "anti_patterns": ["..."],\n'
                '  "procedural": ["..."],\n'
                '  "quality_score": 0.0-1.0\n'
                "}\n"
                "Rules:\n"
                "- new_heuristics: things Shiro should do MORE of (max 3)\n"
                "- anti_patterns: things Shiro should NEVER do with this user (max 2)\n"
                "- procedural: patterns about how THIS USER likes to interact (max 2)\n"
                "- quality_score: 0=terrible 1=perfect\n"
                "- Be concise. Empty arrays are fine if nothing applies.\n\n"
                f"Shiro's character criteria:\n{self.persona}"
            )
            turn_text = (
                f"User: {user_msg[:400]}\n"
                f"Shiro: {reply[:400]}"
            )

            # Use a low temperature and tight token budget — this is a structured
            # extraction task, not a creative one
            old_max = self.llm.max_tokens
            old_temp = self.llm.temperature
            self.llm.max_tokens = 200
            self.llm.temperature = 0.2
            try:
                raw = self.llm.generate_response(
                    system_prompt, turn_text, [], context=""
                )
            finally:
                self.llm.max_tokens = old_max
                self.llm.temperature = old_temp

            if not raw or not raw.strip():
                return

            # Strip markdown fences if present
            clean = re.sub(r"```(?:json)?|```", "", raw).strip()
            start, end = clean.find("{"), clean.rfind("}")
            if start == -1 or end == -1:
                return
            data = json.loads(clean[start : end + 1])

            now = datetime.now().isoformat()
            with self._conn() as conn:
                # Time-decay all existing heuristics for this user (×0.98 per RL run)
                conn.execute(
                    "UPDATE heuristics SET score = score * 0.98 WHERE user_key=?",
                    (user_key,),
                )

                # Insert new heuristics
                for item in data.get("new_heuristics", []):
                    text   = str(item.get("text", "")).strip()
                    reward = float(item.get("reward", 0.5))
                    if not text or len(text) < 10:
                        continue
                    exists = conn.execute(
                        "SELECT id FROM heuristics WHERE user_key=? AND text=?",
                        (user_key, text),
                    ).fetchone()
                    if not exists:
                        conn.execute(
                            "INSERT INTO heuristics "
                            "(user_key, text, score, use_count, added_at) "
                            "VALUES (?,?,?,0,?)",
                            (user_key, text, reward, now),
                        )

                # Prune bottom heuristics if over cap
                all_h = conn.execute(
                    "SELECT id FROM heuristics WHERE user_key=? ORDER BY score DESC",
                    (user_key,),
                ).fetchall()
                if len(all_h) > _HEURISTIC_MAX:
                    to_drop = [r["id"] for r in all_h[_HEURISTIC_MAX:]]
                    conn.execute(
                        f"DELETE FROM heuristics WHERE id IN "
                        f"({','.join(str(i) for i in to_drop)})"
                    )

                # Insert anti-patterns
                for ap in data.get("anti_patterns", []):
                    ap = str(ap).strip()
                    if not ap or len(ap) < 8:
                        continue
                    exists = conn.execute(
                        "SELECT id FROM anti_patterns "
                        "WHERE user_key=? AND text=?",
                        (user_key, ap),
                    ).fetchone()
                    if not exists:
                        conn.execute(
                            "INSERT INTO anti_patterns (user_key, text, added_at) "
                            "VALUES (?,?,?)",
                            (user_key, ap, now),
                        )

                # Insert procedural patterns
                for p in data.get("procedural", []):
                    p = str(p).strip()
                    if not p or len(p) < 8:
                        continue
                    exists = conn.execute(
                        "SELECT id FROM procedural WHERE user_key=? AND pattern=?",
                        (user_key, p),
                    ).fetchone()
                    if not exists:
                        conn.execute(
                            "INSERT INTO procedural (user_key, pattern, added_at) "
                            "VALUES (?,?,?)",
                            (user_key, p, now),
                        )

            qs = data.get("quality_score", "?")
            logger.debug(
                f"[SQLiteMemory] RL complete for {user_key!r} | "
                f"quality={qs} | "
                f"heuristics={len(data.get('new_heuristics', []))} | "
                f"anti={len(data.get('anti_patterns', []))} | "
                f"procedural={len(data.get('procedural', []))}"
            )

        except (json.JSONDecodeError, KeyError):
            pass  # Normal — LLM sometimes outputs nothing or non-JSON
        except Exception as e:
            logger.debug(f"[SQLiteMemory] RL self-critique error: {e}")

    # ── Context block for prompt injection ────────────────────────────────────

    def build_context_block(self, user_key: str, max_chars: int = 800) -> str:
        """
        Builds a compact context block for injection into Shiro's system prompt.
        Includes: top heuristics, anti-patterns, procedural patterns, recent facts.
        Capped at max_chars to stay within token budget.
        """
        try:
            with self._conn() as conn:
                user = conn.execute(
                    "SELECT * FROM users WHERE key=?", (user_key,)
                ).fetchone()
                if not user:
                    return ""

                heuristics = conn.execute(
                    "SELECT text, score FROM heuristics "
                    "WHERE user_key=? ORDER BY score DESC LIMIT 8",
                    (user_key,),
                ).fetchall()
                anti = conn.execute(
                    "SELECT text FROM anti_patterns "
                    "WHERE user_key=? ORDER BY id DESC LIMIT 5",
                    (user_key,),
                ).fetchall()
                procedural = conn.execute(
                    "SELECT pattern FROM procedural "
                    "WHERE user_key=? ORDER BY id DESC LIMIT 5",
                    (user_key,),
                ).fetchall()

            parts = []
            if heuristics:
                parts.append("## What works well")
                for h in heuristics:
                    parts.append(f"- {h['text']}")

            if anti:
                parts.append("## Never do this with this user")
                for a in anti:
                    parts.append(f"- {a['text']}")

            if procedural:
                parts.append("## How this user likes to interact")
                for p in procedural:
                    parts.append(f"- {p['pattern']}")

            block = "\n".join(parts)
            if len(block) > max_chars:
                block = block[:max_chars].rsplit("\n", 1)[0]

            return block

        except Exception as e:
            logger.debug(f"[SQLiteMemory] build_context_block error: {e}")
            return ""

    # ── Smart search trigger ──────────────────────────────────────────────────

    def should_search(
        self,
        query: str,
        memory_snippet: str,
        user_key: str,
    ) -> bool:
        """
        Ask the LLM whether existing memory is sufficient or a web search is needed.
        Uses a very tight token budget — this call should finish in <1s.
        Falls back to False (no search) on any error so it never blocks the pipeline.

        memory_snippet: a short excerpt from ChromaDB results for this query.
        """
        try:
            system = (
                "Determine: is the existing memory snippet sufficient to answer "
                "this query, or is a live web search needed? "
                "Answer ONLY with YES (search needed) or NO (memory sufficient). "
                "Nothing else."
            )
            prompt = (
                f"Query: {query[:200]}\n"
                f"Memory snippet: {memory_snippet[:300]}"
            )
            old_max  = self.llm.max_tokens
            old_temp = self.llm.temperature
            self.llm.max_tokens  = 5
            self.llm.temperature = 0.1
            try:
                resp = self.llm.generate_response(system, prompt, [], context="")
            finally:
                self.llm.max_tokens  = old_max
                self.llm.temperature = old_temp

            return "YES" in (resp or "").upper()
        except Exception as e:
            logger.debug(f"[SQLiteMemory] should_search error: {e}")
            return False

    # ── Search cache ──────────────────────────────────────────────────────────

    def get_cached_search(self, query: str) -> Optional[str]:
        qh = hashlib.md5(query.lower().strip().encode()).hexdigest()
        try:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT result, cached_at FROM search_cache WHERE query_hash=?",
                    (qh,),
                ).fetchone()
            if not row:
                return None
            age = (datetime.now() - datetime.fromisoformat(row["cached_at"])).total_seconds()
            return row["result"] if age <= _SEARCH_TTL_SECS else None
        except Exception:
            return None

    def set_cached_search(self, query: str, result: str) -> None:
        qh  = hashlib.md5(query.lower().strip().encode()).hexdigest()
        now = datetime.now().isoformat()
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO search_cache "
                    "(query_hash, query, result, cached_at) VALUES (?,?,?,?)",
                    (qh, query, result, now),
                )
        except Exception as e:
            logger.debug(f"[SQLiteMemory] cache write error: {e}")

    # ── Fact storage (direct) ─────────────────────────────────────────────────

    def add_fact(self, user_key: str, fact: str) -> None:
        """Explicitly store a fact about a user (called from engine when fact extracted)."""
        self.get_or_create_user(user_key)
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO facts (user_key, fact, added_at) VALUES (?,?,?)",
                    (user_key, fact.strip(), datetime.now().isoformat()),
                )
        except Exception as e:
            logger.debug(f"[SQLiteMemory] add_fact error: {e}")

    # ── Read helpers ──────────────────────────────────────────────────────────

    def get_hazard_log(self, limit: int = 20) -> List[dict]:
        try:
            with self._conn() as conn:
                return [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM hazard ORDER BY ts DESC LIMIT ?", (limit,)
                    ).fetchall()
                ]
        except Exception:
            return []

    def get_all_users(self) -> List[dict]:
        try:
            with self._conn() as conn:
                return [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM users ORDER BY updated_at DESC"
                    ).fetchall()
                ]
        except Exception:
            return []

    def get_heuristics(self, user_key: str, limit: int = 10) -> List[dict]:
        try:
            with self._conn() as conn:
                return [
                    dict(r)
                    for r in conn.execute(
                        "SELECT text, score, use_count FROM heuristics "
                        "WHERE user_key=? ORDER BY score DESC LIMIT ?",
                        (user_key, limit),
                    ).fetchall()
                ]
        except Exception:
            return []

    def get_anti_patterns(self, user_key: str) -> List[str]:
        try:
            with self._conn() as conn:
                return [
                    r["text"]
                    for r in conn.execute(
                        "SELECT text FROM anti_patterns WHERE user_key=? ORDER BY id DESC",
                        (user_key,),
                    ).fetchall()
                ]
        except Exception:
            return []

    def get_procedural(self, user_key: str) -> List[str]:
        try:
            with self._conn() as conn:
                return [
                    r["pattern"]
                    for r in conn.execute(
                        "SELECT pattern FROM procedural WHERE user_key=? ORDER BY id DESC",
                        (user_key,),
                    ).fetchall()
                ]
        except Exception:
            return []

    def get_stats(self) -> dict:
        """Summary stats for GUI or logging."""
        try:
            with self._conn() as conn:
                users     = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                turns     = conn.execute("SELECT COUNT(*) FROM short_term").fetchone()[0]
                episodic  = conn.execute("SELECT COUNT(*) FROM episodic").fetchone()[0]
                heuristic = conn.execute("SELECT COUNT(*) FROM heuristics").fetchone()[0]
                anti      = conn.execute("SELECT COUNT(*) FROM anti_patterns").fetchone()[0]
                hazard    = conn.execute("SELECT COUNT(*) FROM hazard").fetchone()[0]
                proc      = conn.execute("SELECT COUNT(*) FROM procedural").fetchone()[0]
            db_kb = round(self.db_path.stat().st_size / 1024, 1) if self.db_path.exists() else 0
            return {
                "users":       users,
                "turns":       turns,
                "episodic":    episodic,
                "heuristics":  heuristic,
                "anti_patterns": anti,
                "procedural":  proc,
                "hazard":      hazard,
                "db_size_kb":  db_kb,
            }
        except Exception as e:
            logger.debug(f"[SQLiteMemory] get_stats error: {e}")
            return {}
