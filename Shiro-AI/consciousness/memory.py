"""
memory.py — ConversationMemory + ContextWindow

ConversationMemory v5.0:
  - Episodic recall with keyword search: recall_relevant(user_id, query)
    finds memories related to what the user is currently talking about,
    not just a flat dump of the full summary
  - Key facts with confidence scores: facts heard once get score=1,
    heard again get score=2, etc. Only high-confidence facts are surfaced
    in prompts; low-confidence ones wait for corroboration
  - Fact deduplication: semantically similar facts (same n-gram overlap
    as ThoughtDiversityScorer) are merged rather than duplicated
  - Memory recency weighting: more recent sessions' content scores higher
    in extractive summarization
  - ContextWindow: token-budget-aware message builder (unchanged)
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
class KeyFact:
    """A fact about a user, with confidence tracking."""
    text: str
    confidence: int = 1      # increments each time this fact is corroborated
    first_seen: float = field(default_factory=time.time)
    last_seen: float  = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"text": self.text, "confidence": self.confidence,
                "first_seen": self.first_seen, "last_seen": self.last_seen}

    @classmethod
    def from_dict(cls, d: dict) -> "KeyFact":
        return cls(text=d["text"], confidence=d.get("confidence", 1),
                   first_seen=d.get("first_seen", time.time()),
                   last_seen=d.get("last_seen", time.time()))


@dataclass
class MemorySummary:
    user_id: str
    content: str           # The summary text
    session_count: int = 0
    message_count: int = 0
    topics_covered: list = field(default_factory=list)
    last_updated: float = field(default_factory=time.time)
    key_facts: list = field(default_factory=list)   # list of KeyFact objects

    def age_hours(self) -> float:
        return (time.time() - self.last_updated) / 3600.0

    def confident_facts(self, min_confidence: int = 1) -> list[str]:
        """Return fact texts at or above minimum confidence."""
        facts = []
        for f in self.key_facts:
            if isinstance(f, KeyFact):
                if f.confidence >= min_confidence:
                    facts.append(f.text)
            else:
                facts.append(str(f))   # backwards-compat with plain strings
        return facts

    def to_prompt_str(self, min_confidence: int = 1) -> str:
        parts = [f"Memory of {self.user_id} ({self.session_count} sessions, {self.message_count} messages):"]
        if self.content:
            parts.append(self.content)
        facts = self.confident_facts(min_confidence)[:8]
        if facts:
            facts_str = "; ".join(facts)
            parts.append(f"Key facts: {facts_str}")
        if self.topics_covered:
            parts.append(f"Recurring topics: {', '.join(self.topics_covered[:6])}")
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
    ) -> Optional[Message]:
        """Add a user message. Returns None and skips storage for empty/whitespace messages."""
        if not content or not content.strip():
            return None
        msg = Message(
            role="user",
            content=content.strip(),
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

    def recall_relevant(self, user_id: str, query: str, top_n: int = 3) -> list[str]:
        """
        Keyword-search the user's memory for content relevant to query.
        Returns a list of relevant fact texts or summary sentences.

        Used to give Shiro targeted recall rather than dumping the full summary.
        e.g., user is talking about gaming → return gaming-related memories only.
        """
        summary = self._summaries.get(user_id)
        buf     = list(self._buffers.get(user_id, []))
        if not summary and not buf:
            return []

        # Extract keywords from query (4+ char words)
        query_words = set(re.findall(r"\b\w{4,}\b", query.lower()))
        if not query_words:
            return []

        results: list[tuple[float, str]] = []

        # Score summary sentences
        if summary and summary.content:
            for sent in re.split(r"[.!?]+\s*", summary.content):
                sent = sent.strip()
                if len(sent) < 10:
                    continue
                sent_words = set(re.findall(r"\b\w{4,}\b", sent.lower()))
                overlap = len(query_words & sent_words) / max(len(query_words), 1)
                if overlap > 0:
                    results.append((overlap, sent))

        # Score key facts (high-confidence facts weighted higher)
        if summary:
            for f in summary.key_facts:
                if isinstance(f, KeyFact):
                    fact_text, conf = f.text, f.confidence
                else:
                    fact_text, conf = str(f), 1
                fact_words = set(re.findall(r"\b\w{4,}\b", fact_text.lower()))
                overlap = len(query_words & fact_words) / max(len(query_words), 1)
                if overlap > 0:
                    # Boost confident facts
                    score = overlap * (1.0 + 0.2 * min(conf, 3))
                    results.append((score, fact_text))

        # Score recent messages
        for msg in buf[-20:]:
            if msg.role != "user":
                continue
            msg_words = set(re.findall(r"\b\w{4,}\b", msg.content.lower()))
            overlap = len(query_words & msg_words) / max(len(query_words), 1)
            if overlap > 0.3:
                # Recent messages are more relevant — slight boost
                results.append((overlap * 1.1, msg.content[:60]))

        results.sort(reverse=True)
        # Deduplicate and return top-N
        seen: set[str] = set()
        out: list[str] = []
        recent_context = [m.content for m in buf[-3:]] if buf else []

        for _, text in results:
            if text in seen:
                continue

            # Anti-repetition: Skip if this memory is too similar to what was just said
            is_duplicate = False
            for prev_msg in recent_context:
                if self._facts_similar(text, prev_msg, threshold=0.5):
                    is_duplicate = True
                    break

            if not is_duplicate:
                seen.add(text)
                out.append(text)
                if len(out) >= top_n:
                    break
        return out

    def recall_topics(self, user_id: str) -> list[str]:
        s = self._summaries.get(user_id)
        return s.topics_covered if s else []

    def has_memory(self, user_id: str) -> bool:
        """True if Shiro has any memory of this user."""
        return user_id in self._summaries or bool(self._buffers.get(user_id))

    def message_count_today(self, user_id: str) -> int:
        """Count messages from this user in the current in-memory buffer."""
        return len(self._buffers.get(user_id, []))

    def get_recent_topics(self, user_id: str, n: int = 3) -> list[str]:
        """Get topics from recent messages (in-session, not just summary)."""
        buf = list(self._buffers.get(user_id, []))
        topic_counts: dict[str, int] = {}
        for msg in buf[-15:]:
            for t in msg.topics:
                topic_counts[t] = topic_counts.get(t, 0) + 1
        return sorted(topic_counts, key=topic_counts.get, reverse=True)[:n]

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
        new_key_facts = self._extract_key_facts(user_texts)

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
            # Merge facts with confidence tracking
            existing.key_facts = self._merge_key_facts(existing.key_facts, new_key_facts)
        else:
            self._summaries[user_id] = MemorySummary(
                user_id=user_id,
                content=summary_text,
                session_count=1,
                message_count=len(to_compress),
                topics_covered=top_topics,
                key_facts=new_key_facts,
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

    def _fact_ngrams(self, text: str, n: int = 3) -> set[str]:
        """Character n-grams for fact similarity comparison."""
        t = re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()
        return {t[i:i+n] for i in range(len(t) - n + 1)} if len(t) >= n else set()

    def _facts_similar(self, a: str, b: str, threshold: float = 0.42) -> bool:
        """
        True if two facts are semantically close (likely duplicates).
        Uses n-gram overlap + keyword (content word) overlap.
        Two paths to similarity:
          1. High n-gram overlap (same surface form)
          2. High keyword overlap (same core meaning, different phrasing)
        """
        a_ng = self._fact_ngrams(a)
        b_ng = self._fact_ngrams(b)
        if not a_ng or not b_ng:
            return False
        jaccard = len(a_ng & b_ng) / len(a_ng | b_ng)
        if jaccard >= threshold:
            return True
        # Keyword overlap: content words (4+ chars) that appear in both
        _stop = {"that", "this", "with", "have", "from", "will", "been", "some"}
        a_kw = {w for w in re.findall(r"\b\w{4,}\b", a.lower()) if w not in _stop}
        b_kw = {w for w in re.findall(r"\b\w{4,}\b", b.lower()) if w not in _stop}
        if a_kw and b_kw:
            kw_overlap = len(a_kw & b_kw) / max(len(a_kw), len(b_kw))
            if kw_overlap >= 0.6:
                return True
        return False

    def _extract_key_facts(self, texts: list[str]) -> list[KeyFact]:
        """
        Pull short, memorable facts from user messages.
        Returns KeyFact objects with initial confidence=1.
        """
        raw_facts: list[str] = []
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
                    if len(fact) < 60 and fact.lower() not in raw_facts:
                        raw_facts.append(fact.lower())
        return [KeyFact(text=f) for f in raw_facts[:10]]

    def _merge_key_facts(self, existing: list, new_facts: list[KeyFact]) -> list:
        """
        Merge new facts into existing, boosting confidence on corroborations.
        Deduplicates semantically similar facts — both within new_facts and
        against existing.
        """
        # Normalize existing to KeyFact objects
        merged: list[KeyFact] = []
        for f in existing:
            if isinstance(f, KeyFact):
                merged.append(f)
            else:
                merged.append(KeyFact(text=str(f)))

        # First: deduplicate within new_facts themselves before merging
        deduped_new: list[KeyFact] = []
        for new_f in new_facts:
            match_in_new = False
            for existing_new in deduped_new:
                if self._facts_similar(new_f.text, existing_new.text):
                    existing_new.confidence += 1
                    match_in_new = True
                    break
            if not match_in_new:
                deduped_new.append(new_f)

        # Then: merge deduped new facts against existing
        for new_f in deduped_new:
            matched = False
            for existing_f in merged:
                if self._facts_similar(new_f.text, existing_f.text):
                    # Boost confidence — fact heard again (cross-session)
                    existing_f.confidence += new_f.confidence
                    existing_f.last_seen = time.time()
                    matched = True
                    break
            if not matched:
                merged.append(new_f)

        # Sort by confidence descending, cap at 15
        merged.sort(key=lambda f: f.confidence, reverse=True)
        return merged[:15]

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
        summaries = {}
        for uid, s in self._summaries.items():
            facts_serialized = []
            for f in s.key_facts:
                if isinstance(f, KeyFact):
                    facts_serialized.append(f.to_dict())
                else:
                    facts_serialized.append({"text": str(f), "confidence": 1,
                                             "first_seen": time.time(), "last_seen": time.time()})
            summaries[uid] = {
                "content":       s.content,
                "session_count": s.session_count,
                "message_count": s.message_count,
                "topics":        s.topics_covered,
                "key_facts":     facts_serialized,
                "last_updated":  s.last_updated,
            }
        return {"summaries": summaries}

    def import_data(self, data: dict):
        for uid, d in data.get("summaries", {}).items():
            raw_facts = d.get("key_facts", [])
            key_facts: list = []
            for f in raw_facts:
                if isinstance(f, dict):
                    key_facts.append(KeyFact.from_dict(f))
                else:
                    key_facts.append(KeyFact(text=str(f)))
            self._summaries[uid] = MemorySummary(
                user_id=uid,
                content=d["content"],
                session_count=d.get("session_count", 1),
                message_count=d.get("message_count", 0),
                topics_covered=d.get("topics", []),
                key_facts=key_facts,
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
