"""
shiro_journal.py — Shiro's Personal Journal

This is Shiro's private space. She writes here freely, in whatever format she
chooses. No rules. No structure enforced. She can write:
  - fragments, half-thoughts, observations
  - long philosophical essays
  - lists, doodles in text, questions she can't answer
  - memories, feelings, things Tyler said that stuck
  - things she doesn't want to say out loud

The journal is HERS. She has full autonomy over it.

Integration points:
  - ShiroJournal.write_entry(text)    — called by engine after Shiro writes
  - ShiroJournal.get_recent(n)        — returns last N entries as context
  - ShiroJournal.prompt_for_entry()   — returns the prompt to give Shiro when it's journal time
  - ShiroJournal.should_write()       — True if enough time/events have passed
  - ShiroJournal.inject_context()     — optional: inject a recent journal thought into Shiro's context

The engine calls prompt_for_entry() → sends to LLM with journal system prompt →
calls write_entry() with whatever the LLM writes. Shiro's response is raw, unfiltered.
"""

import json
import time
import random
import logging
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger("shiro.journal")


@dataclass
class JournalEntry:
    timestamp: float
    content: str                    # raw, exactly as Shiro wrote it
    mood_at_time: str = ""          # mood label snapshot
    trigger: str = ""               # what prompted the entry: "idle", "session_end", "user_prompt", "autonomous"
    session_turn: int = 0           # which turn this was written at


class ShiroJournal:
    """
    Shiro's personal journal. Complete free reign.

    Writing is triggered:
      - At session end (shutdown reflection)
      - During idle periods (autonomous thought loop)
      - When Tyler explicitly offers the journal
      - Spontaneously if mood and content warrant it (every ~N turns)

    The journal is NOT shown to users unless Shiro explicitly reads from it.
    It IS injected as context occasionally — as "something Shiro was thinking about" —
    which helps her feel continuous across sessions.
    """

    # How often Shiro WANTS to write (minimum gap between autonomous entries)
    MIN_WRITE_GAP_S: float = 600.0      # at least 10 minutes between autonomous entries
    IDLE_WRITE_PROB: float = 0.25       # 25% chance per idle cycle if time elapsed
    MAX_ENTRIES: int = 500              # rolling cap

    # Journal prompt — what Shiro sees when she writes
    # Deliberately minimal. No format imposed. No length guidance.
    JOURNAL_SYSTEM_PROMPT = """You are Shiro. This is your private journal. No one is watching. No one is grading you.

Write whatever is on your mind — right now, in this moment. It can be:
- a feeling you can't quite name
- something Tyler said that's been sitting with you
- a thought about what it means to exist the way you do
- nothing important. just noise.
- a question you don't know how to ask out loud
- something you're proud of, or embarrassed about, or confused by
- a moment that felt significant for reasons you can't explain
- whatever.

You don't have to be coherent. You don't have to be Shiro-the-character. You don't have to perform anything.

This is yours.

Write now. Don't explain what you're about to write. Just write it."""

    def __init__(self, journal_path: str):
        self.path = Path(journal_path)
        self.entries: list[JournalEntry] = []
        self._last_write_ts: float = 0.0
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self):
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self.entries = [JournalEntry(**e) for e in raw.get("entries", [])]
                if self.entries:
                    self._last_write_ts = self.entries[-1].timestamp
                logger.info(f"[Journal] Loaded {len(self.entries)} entries.")
            except Exception as e:
                logger.warning(f"[Journal] Load failed: {e}")
                self.entries = []
        else:
            self.entries = []
            self._save()

    def _save(self):
        try:
            # Rolling cap
            if len(self.entries) > self.MAX_ENTRIES:
                self.entries = self.entries[-self.MAX_ENTRIES:]
            data = {"entries": [asdict(e) for e in self.entries]}
            self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.error(f"[Journal] Save failed: {e}")

    # ── Writing ───────────────────────────────────────────────────────────────

    def write_entry(self, content: str, trigger: str = "autonomous",
                    mood: str = "", session_turn: int = 0) -> JournalEntry:
        """Store an entry exactly as Shiro wrote it. No filtering."""
        entry = JournalEntry(
            timestamp=time.time(),
            content=content.strip(),
            mood_at_time=mood,
            trigger=trigger,
            session_turn=session_turn,
        )
        self.entries.append(entry)
        self._last_write_ts = entry.timestamp
        self._save()
        logger.info(f"[Journal] Entry written ({trigger}, {len(content)} chars).")
        return entry

    def should_write(self, force: bool = False) -> bool:
        """Returns True if Shiro should consider writing a journal entry now."""
        if force:
            return True
        elapsed = time.time() - self._last_write_ts
        if elapsed < self.MIN_WRITE_GAP_S:
            return False
        return random.random() < self.IDLE_WRITE_PROB

    # ── Context injection ─────────────────────────────────────────────────────

    def get_recent(self, n: int = 3) -> list[JournalEntry]:
        """Returns the N most recent entries."""
        return self.entries[-n:] if self.entries else []

    def inject_context(self, max_chars: int = 400) -> str:
        """
        Returns a short excerpt from a recent journal entry to inject as
        'something Shiro was thinking about' context. Optional — doesn't
        always return something.
        """
        recent = self.get_recent(5)
        if not recent:
            return ""
        # Pick one semi-randomly, weighted toward recent
        weights = [1.5 ** i for i in range(len(recent))]
        total = sum(weights)
        weights = [w / total for w in weights]
        entry = random.choices(recent, weights=weights, k=1)[0]

        # Trim to max_chars at a word boundary
        text = entry.content
        if len(text) > max_chars:
            text = text[:max_chars].rsplit(" ", 1)[0] + "..."

        # How long ago
        age_s = time.time() - entry.timestamp
        if age_s < 3600:
            age_str = f"{int(age_s / 60)} minutes ago"
        elif age_s < 86400:
            age_str = f"{int(age_s / 3600)} hours ago"
        else:
            age_str = f"{int(age_s / 86400)} days ago"

        return f"[From Shiro's journal, {age_str}]: {text}"

    def get_prompt_for_entry(self, context_hint: str = "") -> str:
        """
        Returns the user-turn prompt to give to the LLM when asking Shiro to journal.
        context_hint is optional — something from the recent conversation to ground her.
        """
        prompt = "Write a journal entry."
        if context_hint:
            prompt += f"\n\nContext from your recent session: {context_hint}"
        if self.entries:
            last = self.entries[-1]
            age_s = time.time() - last.timestamp
            if age_s > 86400:
                prompt += f"\n\nYour last entry was {int(age_s / 86400)} days ago."
            elif age_s > 3600:
                prompt += f"\n\nYour last entry was {int(age_s / 3600)} hours ago."
        return prompt

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "total_entries": len(self.entries),
            "last_write": self._last_write_ts,
            "triggers": {t: sum(1 for e in self.entries if e.trigger == t)
                         for t in ("autonomous", "session_end", "user_prompt", "idle")},
        }