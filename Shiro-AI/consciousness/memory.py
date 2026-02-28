"""
memory.py — ConversationMemory + ContextWindow

ConversationMemory:
  - Stores per-user conversation summaries (short-term + long-term)
  - Compresses old message windows into rolling summaries (pure Python, no API)
  - Shiro can recall what she remembers about someone across sessions
  - Token-aware: estimates summary length to keep LLM context manageable

ContextWindow:
  - Smart builder for the LLM message array
  - Selects relevant memories, recent messages, and inner state
  - Respects a configurable token budget (safe for local LLMs)
  - Deduplicates and prioritizes by recency + relevance
"""

import time
import re
from dataclasses import dataclass, field
from typing import Optional
from collections import deque


# ─────────────────────────────────────────────────────────────
#  Rough token counter (no tiktoken needed)
# ─────────────────────────────────────────────────────────────

def _estimate_tokens(text: str) -> int:
    """Approximate token count: ~1 token per 3.8 chars (conservative)."""
    return max(1, int(len(text) / 3.8))


# ─────────────────────────────────────────────────────────────
#  Message record
# ─────────────────────────────────────────────────────────────

@dataclass
class Message:
    role: str          # "user" | "assistant" | "system"
    content: str
    user_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    emotions: dict = field(default_factory=dict)
    topics: list = field(default_factory=list)

    def to_llm(self) -> dict:
        return {"role": self.role, "content": self.content}

    def age_minutes(self) -> float:
        return (time.time() - self.timestamp) / 60.0


# ─────────────────────────────────────────────────────────────
#  MemorySummary — what Shiro "remembers" about a user
# ─────────────────────────────────────────────────────────────

@dataclass
class MemorySummary:
    user_id: str
    content: str           # The summary text
    session_count: int = 0
    message_count: int = 0
    topics_covered: list = field(default_factory=list)
    last_updated: float = field(default_factory=time.time)
    key_facts: list = field(default_factory=list)   # ["prefers dark themes", "has a cat named Max"]

    def age_hours(self) -> float:
        return (time.time() - self.last_updated) / 3600.0

    def to_prompt_str(self) -> str:
        parts = [f"Memory of {self.user_id} ({self.session_count} sessions):"]
        parts.append(self.content)
        if self.key_facts:
            parts.append("Key facts: " + "; ".join(self.key_facts))
        if self.topics_covered:
            parts.append("Topics: " + ", ".join(self.topics_covered[:5]))
        return "\n".join(parts)


# ─────────────────────────────────────────────────────────────
#  ConversationMemory
# ─────────────────────────────────────────────────────────────

class ConversationMemory:
    """
    Shiro's layered conversation memory.

    Short-term: rolling deque of recent messages (in-session)
    Long-term: compressed summaries per user (persisted across sessions)

    The compressor runs locally — it uses extractive summarization
    (no API, no ML) to distill a window of messages into a compact summary.
    """

    def __init__(
        self,
        short_term_size: int = 40,         # messages kept in RAM
        compress_after: int = 25,          # compress when window exceeds this
        max_summary_chars: int = 600,      # max chars per user summary
    ):
        self.short_term_size = short_term_size
        self.compress_after = compress_after
        self.max_summary_chars = max_summary_chars

        # Per-user short-term buffers
        self._buffers: dict[str, deque[Message]] = {}

        # Per-user long-term summaries
        self._summaries: dict[str, MemorySummary] = {}

        # Global message log (all users, for context window building)
        self._global_log: deque[Message] = deque(maxlen=200)

    # ── Adding messages ──────────────────────────────────────────

    def add_user_message(
        self,
        user_id: str,
        content: str,
        emotions: Optional[dict] = None,
        topics: Optional[list] = None,
    ) -> Message:
        msg = Message(
            role="user",
            content=content,
            user_id=user_id,
            emotions=emotions or {},
            topics=topics or [],
        )
        self._push(user_id, msg)
        self._global_log.append(msg)
        return msg

    def add_assistant_message(self, content: str, user_id: Optional[str] = None) -> Message:
        msg = Message(role="assistant", content=content, user_id=user_id)
        if user_id:
            self._push(user_id, msg)
        self._global_log.append(msg)
        return msg

    def _push(self, user_id: str, msg: Message):
        buf = self._buffers.setdefault(user_id, deque(maxlen=self.short_term_size))
        buf.append(msg)
        # Trigger compression when buffer gets large
        if len(buf) >= self.compress_after:
            self._compress(user_id)

    # ── Retrieval ────────────────────────────────────────────────

    def recent_messages(self, user_id: str, n: int = 12) -> list[Message]:
        buf = self._buffers.get(user_id, deque())
        return list(buf)[-n:]

    def get_summary(self, user_id: str) -> Optional[MemorySummary]:
        return self._summaries.get(user_id)

    def recall(self, user_id: str) -> str:
        """
        Human-readable string of what Shiro remembers about this user.
        Used in system prompts.
        """
        summary = self._summaries.get(user_id)
        if not summary:
            return ""
        return summary.to_prompt_str()

    def recall_topics(self, user_id: str) -> list[str]:
        s = self._summaries.get(user_id)
        return s.topics_covered if s else []

    # ── Compression (extractive, pure Python) ────────────────────

    def _compress(self, user_id: str):
        """
        Compress the oldest half of the buffer into the user's summary.
        Uses extractive summarization: scores sentences by keyword density,
        picks top-N to represent the session. No API, no ML.
        """
        buf = self._buffers.get(user_id)
        if not buf or len(buf) < 10:
            return

        # Take the oldest 60% of messages
        msgs = list(buf)
        cut = int(len(msgs) * 0.6)
        to_compress = msgs[:cut]
        keep = msgs[cut:]

        # Rebuild buffer with only recent messages
        self._buffers[user_id] = deque(keep, maxlen=self.short_term_size)

        # Extract user messages only for summarization
        user_texts = [m.content for m in to_compress if m.role == "user"]
        if not user_texts:
            return

        # --- Extractive summarization ---
        summary_text = self._extractive_summarize(user_texts, max_chars=self.max_summary_chars)

        # Collect topics from compressed messages
        all_topics: list[str] = []
        for m in to_compress:
            all_topics.extend(m.topics)
        topic_counts: dict[str, int] = {}
        for t in all_topics:
            topic_counts[t] = topic_counts.get(t, 0) + 1
        top_topics = sorted(topic_counts, key=topic_counts.get, reverse=True)[:5]

        # Extract key facts (quoted phrases, names, strong opinions)
        key_facts = self._extract_key_facts(user_texts)

        # Update or create summary
        existing = self._summaries.get(user_id)
        if existing:
            # Merge with existing summary
            merged = self._merge_summaries(existing.content, summary_text)
            existing.content = merged[:self.max_summary_chars]
            existing.message_count += len(to_compress)
            existing.last_updated = time.time()
            for t in top_topics:
                if t not in existing.topics_covered:
                    existing.topics_covered.append(t)
            for f in key_facts:
                if f not in existing.key_facts:
                    existing.key_facts.append(f)
            existing.key_facts = existing.key_facts[-15:]   # cap
        else:
            self._summaries[user_id] = MemorySummary(
                user_id=user_id,
                content=summary_text,
                session_count=1,
                message_count=len(to_compress),
                topics_covered=top_topics,
                key_facts=key_facts,
            )

    def _extractive_summarize(self, texts: list[str], max_chars: int = 600) -> str:
        """
        Extract the most representative sentences from a list of user messages.
        Scoring: sentence length (substantive) + keyword density + question presence.
        """
        # Split into sentences
        all_sents: list[str] = []
        for t in texts:
            sents = re.split(r"(?<=[.!?])\s+", t.strip())
            all_sents.extend(s.strip() for s in sents if len(s.strip()) > 15)

        if not all_sents:
            return " ".join(texts)[:max_chars]

        # Build keyword frequency
        words: list[str] = []
        for s in all_sents:
            words.extend(w.lower() for w in re.findall(r"\b\w{4,}\b", s))
        freq: dict[str, int] = {}
        for w in words:
            freq[w] = freq.get(w, 0) + 1

        # Score sentences
        def score(s: str) -> float:
            wds = re.findall(r"\b\w{4,}\b", s.lower())
            if not wds:
                return 0.0
            keyword_score = sum(freq.get(w, 0) for w in wds) / len(wds)
            length_bonus = min(1.0, len(s) / 80)
            question_bonus = 0.3 if "?" in s else 0.0
            opinion_bonus = 0.4 if re.search(
                r"\b(think|feel|believe|love|hate|prefer|want|need|always|never)\b", s, re.I
            ) else 0.0
            return keyword_score * length_bonus + question_bonus + opinion_bonus

        scored = sorted(all_sents, key=score, reverse=True)

        # Pick top sentences, preserve order, stay within char budget
        selected: list[str] = []
        total = 0
        seen = set()
        for s in scored:
            if s in seen:
                continue
            seen.add(s)
            if total + len(s) > max_chars:
                break
            selected.append(s)
            total += len(s) + 2

        # Re-sort in original order
        order_map = {s: i for i, s in enumerate(all_sents)}
        selected.sort(key=lambda s: order_map.get(s, 9999))

        return ". ".join(selected) if selected else texts[-1][:max_chars]

    def _extract_key_facts(self, texts: list[str]) -> list[str]:
        """
        Pull short, memorable facts from user messages.
        Looks for: name mentions, preferences, strong opinions, facts about their life.
        """
        facts: list[str] = []
        patterns = [
            re.compile(r"\bmy (name is|name's) (\w+)\b", re.I),
            re.compile(r"\bi (have|own|got) (a |an )?(.{3,30})\b", re.I),
            re.compile(r"\bi (love|hate|enjoy|prefer|like|dislike) (.{3,30})", re.I),
            re.compile(r"\bi('m| am) (a |an )?(.{3,25})\b", re.I),
            re.compile(r"\bi (work|study|go) (.{3,30})", re.I),
        ]
        for text in texts:
            for p in patterns:
                m = p.search(text)
                if m:
                    fact = m.group(0).strip().rstrip(".,!?")
                    if len(fact) < 60 and fact not in facts:
                        facts.append(fact.lower())
        return facts[:10]

    def _merge_summaries(self, old: str, new: str) -> str:
        """Merge two summary strings, deduplicating overlapping content."""
        old_sents = set(re.split(r"[.!?]+\s*", old))
        new_sents = re.split(r"[.!?]+\s*", new)
        additions = [s for s in new_sents if s.strip() and s.strip() not in old_sents]
        if not additions:
            return old
        merged = old.rstrip(". ") + ". " + ". ".join(additions)
        return merged

    def new_session(self, user_id: str):
        """Call when a user starts a new session. Increments session count."""
        s = self._summaries.get(user_id)
        if s:
            s.session_count += 1

    # ── Persistence ──────────────────────────────────────────────

    def export(self) -> dict:
        return {
            "summaries": {
                uid: {
                    "content":       s.content,
                    "session_count": s.session_count,
                    "message_count": s.message_count,
                    "topics":        s.topics_covered,
                    "key_facts":     s.key_facts,
                    "last_updated":  s.last_updated,
                }
                for uid, s in self._summaries.items()
            }
        }

    def import_data(self, data: dict):
        for uid, d in data.get("summaries", {}).items():
            self._summaries[uid] = MemorySummary(
                user_id=uid,
                content=d["content"],
                session_count=d.get("session_count", 1),
                message_count=d.get("message_count", 0),
                topics_covered=d.get("topics", []),
                key_facts=d.get("key_facts", []),
                last_updated=d.get("last_updated", time.time()),
            )


# ─────────────────────────────────────────────────────────────
#  ContextWindow
# ─────────────────────────────────────────────────────────────

class ContextWindow:
    """
    Smart LLM message array builder.

    Assembles the optimal set of messages to send to a local LLM,
    respecting a token budget. Local models (7B–13B) typically have
    4K–8K context windows, so we need to be deliberate about what we include.

    Priority order (highest → lowest):
      1. System prompt (always included)
      2. Memory summary for the current user
      3. Recent conversation messages
      4. Older messages (trimmed first if over budget)
    """

    def __init__(self, token_budget: int = 2048):
        """
        token_budget: approximate max tokens for context (excluding generation).
        Defaults to 2048 which is safe for most local 7B models.
        Set higher (4096) for larger models or if you have more VRAM.
        """
        self.token_budget = token_budget

    def build(
        self,
        system_prompt: str,
        recent_messages: list[Message],
        memory_summary: Optional[str] = None,
        max_history: int = 12,
    ) -> list[dict]:
        """
        Build the messages list for your LLM.

        Returns: [{"role": ..., "content": ...}, ...]
        """
        messages: list[dict] = []
        used_tokens = 0

        # 1. System prompt — always included
        sys_tokens = _estimate_tokens(system_prompt)
        messages.append({"role": "system", "content": system_prompt})
        used_tokens += sys_tokens

        # 2. Memory summary — inject as a system note if we have budget
        if memory_summary and memory_summary.strip():
            mem_block = f"[What you remember about this person]\n{memory_summary}"
            mem_tokens = _estimate_tokens(mem_block)
            if used_tokens + mem_tokens < self.token_budget * 0.5:
                messages.append({"role": "system", "content": mem_block})
                used_tokens += mem_tokens

        # 3. Fill remaining budget with recent messages (newest first, then reverse)
        budget_left = self.token_budget - used_tokens - 150  # reserve 150 for generation overhead
        history = recent_messages[-max_history:]

        selected: list[dict] = []
        for msg in reversed(history):
            t = _estimate_tokens(msg.content)
            if budget_left - t < 50:
                break
            selected.append(msg.to_llm())
            budget_left -= t

        selected.reverse()
        messages.extend(selected)

        return messages

    def estimate_usage(self, messages: list[dict]) -> int:
        return sum(_estimate_tokens(m.get("content", "")) for m in messages)
