"""
read_book.py — Shiro's Book Digestion Module  v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Digests .txt books into rich memory dicts that wire directly into
Shiro's memory system. Triggered from the web GUI (📚 Books tab).

Book file format (.txt, place in books/ subfolder):
─────────────────────────────────────────────────────
Title: The Hobbit
Author: J.R.R. Tolkien
Genre: Fantasy
Fiction: true
Year: 1937
Description: A hobbit's unexpected journey.
---
Full text of the book starts here...
─────────────────────────────────────────────────────

Usage (from engine or GUI callback):
    reader = BookReader()
    memory = reader.digest_book("the_hobbit.txt")
    path   = reader.save_to_memories(memory)
    snippet = reader.get_shiro_prompt_snippet(memory)

All standard-library — no extra dependencies needed.
"""

from __future__ import annotations

import json
import re
import hashlib
import logging
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("shiro.book_reader")


# ─────────────────────────────────────────────────────────────────
#  HEADER DETECTION SEPARATORS
# ─────────────────────────────────────────────────────────────────

_SEPARATOR = re.compile(r'^\s*[-=*_]{3,}\s*$')


class BookReader:
    """
    Digests plain-text books into Shiro-compatible memory objects.

    Directory layout (auto-created if missing):
        books/          ← place your .txt files here
        book_memories/  ← JSON digests written here
    """

    # Default dirs are relative to this file's location (book_reader/books, book_reader/book_memories)
    _HERE = Path(__file__).resolve().parent

    def __init__(self,
                 books_dir:    str = "",
                 memories_dir: str = ""):
        self.books_dir    = Path(books_dir)    if books_dir    else self._HERE / "books"
        self.memories_dir = Path(memories_dir) if memories_dir else self._HERE / "book_memories"
        self.books_dir.mkdir(parents=True, exist_ok=True)
        self.memories_dir.mkdir(parents=True, exist_ok=True)

    # ── Public API ────────────────────────────────────────────────

    def list_books(self) -> list[str]:
        """Return all .txt filenames in the books folder."""
        return sorted(f.name for f in self.books_dir.glob("*.txt"))

    def list_digested(self) -> list[str]:
        """Return all already-digested book titles (from JSON memory files)."""
        results = []
        for p in self.memories_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                results.append(data.get("title", p.stem))
            except Exception:
                results.append(p.stem)
        return results

    def digest_book(self, filename: str) -> dict:
        """
        Main entry point. Parse a .txt file and return a rich memory dict.
        Raises FileNotFoundError if the file doesn't exist.
        """
        filepath = self.books_dir / filename
        if not filepath.exists():
            raise FileNotFoundError(
                f"Book not found: {filepath.resolve()}\n"
                f"Place your .txt file in: {self.books_dir.resolve()}"
            )

        raw = filepath.read_text(encoding="utf-8", errors="replace")
        metadata, body = self._parse_header(raw)
        body = self._clean_text(body)

        if len(body) < 100:
            raise ValueError(f"Book body is too short ({len(body)} chars). Check file format.")

        title  = metadata.get("title")  or self._title_from_filename(filename)
        author = metadata.get("author") or "Unknown"
        genre  = metadata.get("genre")  or "General"
        year   = metadata.get("year")   or metadata.get("publication_year") or metadata.get("published")
        desc   = metadata.get("description") or ""

        is_fiction = self._detect_fiction(metadata, body)
        summaries  = self._build_summaries(body)
        entities   = self._extract_entities(body, is_fiction)
        triggers   = self._build_triggers(metadata, summaries, entities)

        # Stable ID for deduplication
        book_id = hashlib.md5(f"{title}{author}".lower().encode()).hexdigest()[:10]

        memory = {
            "book_id":        book_id,
            "title":          title,
            "author":         author,
            "genre":          genre,
            "year":           year,
            "description":    desc or summaries["elevator_pitch"][:350],
            "is_fiction":     is_fiction,
            "source_file":    filename,
            "length_chars":   len(body),
            "digested_at":    datetime.now().isoformat(),
            "memory_type":    "book_digest",
            "version":        "1.0",

            # Summary layers
            "elevator_pitch":   summaries["elevator_pitch"],
            "detailed_summary": summaries["detailed_summary"],
            "key_themes":       summaries["key_themes"],
            "chapter_breakdown": summaries["chapter_breakdown"],

            # Entities and recall wiring
            "key_entities":    entities,
            "memory_triggers": triggers,

            # How Shiro should use this in chat
            "recall_guidance": (
                "Treat as a fictional story — use for analogies, metaphors, emotional parallels."
                if is_fiction else
                "Treat as real factual knowledge. Be precise and cite it as non-fiction."
            ),
            "how_to_use_in_chat": (
                f"When conversation overlaps with memory_triggers or key_entities, "
                f"reference '{title}' naturally — as if you personally read it. "
                f"Distinguish story from facts using is_fiction={is_fiction}."
            ),
        }

        logger.info(
            f"[BookReader] Digested '{title}' by {author} "
            f"({'fiction' if is_fiction else 'non-fiction'}, {len(body):,} chars, "
            f"{len(entities)} entities, {len(triggers)} triggers)"
        )
        return memory

    def save_to_memories(self, digested: dict) -> Path:
        """Persist the digest as JSON in the book_memories folder."""
        safe = re.sub(r'[^a-zA-Z0-9_]', '_', digested["title"]).lower().strip("_")
        safe = re.sub(r'_+', '_', safe)[:60]
        out = self.memories_dir / f"{safe}.json"
        out.write_text(
            json.dumps(digested, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        logger.info(f"[BookReader] Memory saved → {out}")
        return out

    def load_memory(self, filename: str) -> Optional[dict]:
        """Load a previously-saved book memory by JSON filename."""
        path = self.memories_dir / filename
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"[BookReader] Failed to load {filename}: {e}")
            return None

    def get_shiro_prompt_snippet(self, digested: dict) -> str:
        """
        Returns a compact text block ready to inject into Shiro's system prompt
        or memory context so she 'knows' she read this book.
        """
        fic_label = "fiction story (for analogies and metaphors)" if digested["is_fiction"] \
                    else "non-fiction book of real facts and knowledge"
        themes    = ", ".join(digested["key_themes"][:7]) or "various topics"
        entities  = ", ".join(e["entity"] for e in digested["key_entities"][:6]) or "various subjects"

        # FIX: removed [BOOK MEMORY] bracket token — replaced with plain header
        # to avoid the 8B model echoing it as output format.
        return (
            f"Book I have read — {fic_label}: \"{digested['title']}\" by {digested['author']}.\n"
            f"Genre: {digested.get('genre', 'General')}\n"
            f"Overview: {digested['elevator_pitch'][:300]}\n"
            f"Key themes: {themes}\n"
            f"Notable elements: {entities}\n"
            f"When conversation topics match, reference this book naturally "
            f"as though I personally read it. {'Story, not fact.' if digested['is_fiction'] else 'Real factual knowledge.'}"
        )

    def inject_into_shiro_memory(self, digested: dict, memory_store) -> bool:
        """
        Wire a digested book directly into Shiro's MemoryStore so it
        surfaces in semantic search during conversations.

        Uses store_book_memory() if available (type='book_memory'), falling
        back to store_insight() for older MemoryStore versions.

        memory_store: a MemoryStore instance (passed from engine).
        Returns True on success.
        """
        try:
            title  = digested["title"]
            author = digested.get("author", "Unknown")

            # Use store_book_memory if available (preferred — proper type tag)
            _store_fn = getattr(memory_store, "store_book_memory", None)
            if _store_fn is None:
                # Fallback for older MemoryStore without store_book_memory
                def _store_fn(content, book_title, source):
                    memory_store.store_insight(content, user_id="system", source=source)

            # Core entry
            _store_fn(
                f"I have read '{title}' by {author}. {digested['elevator_pitch'][:500]}",
                book_title=title, source="book_memory",
            )
            # Key themes
            if digested.get("key_themes"):
                _store_fn(
                    f"Key themes from '{title}': {', '.join(digested['key_themes'][:10])}.",
                    book_title=title, source="book_memory",
                )
            # Detailed summary in ~400-char chunks
            detail = digested.get("detailed_summary", "")
            if detail and len(detail) > 100:
                words = detail.split()
                chunk, chunks = [], []
                for w in words:
                    chunk.append(w)
                    if len(" ".join(chunk)) >= 380:
                        chunks.append(" ".join(chunk))
                        chunk = []
                if chunk:
                    chunks.append(" ".join(chunk))
                for chunk_text in chunks[:6]:
                    _store_fn(
                        f"From '{title}': {chunk_text}",
                        book_title=title, source="book_memory",
                    )
            # Top entities with context
            for ent in digested.get("key_entities", [])[:10]:
                snippet = ent.get("context_snippet", "")
                if snippet and len(snippet) > 20:
                    _store_fn(
                        f"In '{title}', {ent['entity']}: {snippet[:250]}",
                        book_title=title, source="book_memory",
                    )
            # Chapter summaries
            for chap in digested.get("chapter_breakdown", [])[:8]:
                chap_title   = chap.get("chapter", "")
                chap_summary = chap.get("summary", "")
                if chap_title and chap_summary:
                    _store_fn(
                        f"In '{title}', {chap_title}: {chap_summary[:300]}",
                        book_title=title, source="book_memory",
                    )

            count = (
                1
                + (1 if digested.get("key_themes") else 0)
                + min(len(digested.get("detailed_summary","").split()) // 70, 6)
                + min(len(digested.get("key_entities",[])), 10)
                + min(len(digested.get("chapter_breakdown",[])), 8)
            )
            logger.info(f"[BookReader] '{title}' injected into MemoryStore (~{count} entries)")
            return True
        except Exception as e:
            logger.error(f"[BookReader] inject_into_shiro_memory failed: {e}")
            return False

    # ── Internal helpers ──────────────────────────────────────────

    def _title_from_filename(self, filename: str) -> str:
        return Path(filename).stem.replace("_", " ").replace("-", " ").title()

    def _parse_header(self, content: str) -> tuple[dict, str]:
        """
        Parse optional key: value header from the top of the file.
        Header ends at the first separator line (---, ===, ***) or a blank line
        followed by non-header content.
        """
        lines    = content.splitlines()
        metadata: dict = {}
        i = 0

        for i, line in enumerate(lines):
            stripped = line.strip()
            # Separator line — header ends here
            if _SEPARATOR.match(stripped):
                i += 1
                break
            # Empty line with nothing useful left → stop
            if not stripped:
                # Peek ahead: if next non-empty line looks like a key:value, continue
                rest = "\n".join(lines[i+1:]).lstrip()
                if re.match(r'^[A-Za-z][\w ]*\s*:', rest):
                    continue
                break
            if ":" in stripped:
                key, _, value = stripped.partition(":")
                key   = key.strip().lower().replace(" ", "_")
                value = value.strip()
                if key and value:
                    metadata[key] = value
            # Lines without colon might be continuation — skip
        else:
            i = len(lines)

        body = "\n".join(lines[i:]).strip()
        return metadata, body

    def _clean_text(self, text: str) -> str:
        # Collapse excessive whitespace while preserving paragraph breaks
        text = re.sub(r'\r\n', '\n', text)
        text = re.sub(r'\r', '\n', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'[ \t]+', ' ', text)
        return text.strip()

    def _detect_fiction(self, metadata: dict, body: str) -> bool:
        """Determine fiction vs non-fiction from metadata first, then content signals."""
        # Explicit metadata flags
        for key in ("fiction", "is_fiction", "type", "category"):
            val = str(metadata.get(key, "")).lower()
            if val in ("true", "yes", "1", "fiction", "novel", "story",
                       "fantasy", "sci-fi", "scifi", "romance", "thriller",
                       "horror", "mystery", "adventure"):
                return True
            if val in ("false", "no", "0", "non-fiction", "nonfiction",
                       "biography", "memoir", "science", "history", "self-help",
                       "reference", "technical"):
                return False
        # Genre heuristic
        genre = metadata.get("genre", "").lower()
        if any(g in genre for g in ("fantasy", "sci-fi", "fiction", "novel",
                                     "romance", "horror", "thriller", "mystery")):
            return True
        if any(g in genre for g in ("non-fiction", "nonfiction", "history",
                                     "biography", "memoir", "science", "reference")):
            return False
        # Content signals from first 1500 chars
        sample = body[:1500].lower()
        fiction_hits = sum(1 for s in (
            "chapter ", '"', "she said", "he said", "they said", "whispered",
            "replied", "exclaimed", "once upon", "the wizard", "the hero",
            "had never", "would never", "could feel", "thought to himself",
        ) if s in sample)
        nonfiction_hits = sum(1 for s in (
            "according to", "research shows", "study found", "per cent", "percent",
            "in conclusion", "data indicates", "statistics", "in this chapter",
            "the author", "as we have seen", "evidence suggests",
        ) if s in sample)
        return fiction_hits >= nonfiction_hits

    def _sentences(self, text: str) -> list[str]:
        """Split into sentences, filtering very short fragments."""
        parts = re.split(r'(?<!\w\.\w.)(?<![A-Z][a-z]\.)(?<=\.|\?|!)\s+', text)
        return [s.strip() for s in parts if len(s.strip()) > 20]

    def _extractive_summary(self, text: str, n: int = 8) -> str:
        """Score sentences by word frequency and select top N in original order."""
        if not text or len(text) < 200:
            return text[:600]
        sents = self._sentences(text)
        if not sents:
            return text[:600]
        if len(sents) <= n:
            return " ".join(sents)

        stop = {
            "the","and","a","to","of","in","is","it","that","was","for","on",
            "with","as","be","at","by","this","from","but","not","or","are",
            "have","they","one","you","all","she","he","we","her","his","their",
            "there","about","which","when","would","could","into","been","has",
            "had","will","more","also","an","if","so","up","out","its","do",
        }
        words  = re.sub(r'[^\w\s]', '', text.lower()).split()
        freq   = Counter(w for w in words if w not in stop and len(w) > 3)
        scored = {}
        for s in sents:
            score = sum(freq.get(w, 0) for w in re.sub(r'[^\w\s]', '', s.lower()).split())
            if s in text[:800] or s in text[-800:]:
                score += 20   # boost intro/conclusion
            scored[s] = score

        top = sorted(scored, key=scored.__getitem__, reverse=True)[:n]
        # Restore original document order
        return " ".join(s for s in sents if s in top)

    def _build_summaries(self, body: str) -> dict:
        """Build layered summaries: elevator pitch, detailed, key themes, chapters."""
        elevator  = self._extractive_summary(body, 2)
        detailed  = self._extractive_summary(body, 15)

        # Key themes — top content words
        words = re.sub(r'[^\w\s]', '', body.lower()).split()
        stop  = {
            "the","and","a","to","of","in","is","it","that","was","for","on","with",
            "as","be","at","by","this","from","but","not","or","are","have","they",
            "one","you","all","she","he","we","her","his","their","there","about",
            "which","when","would","could","said","had","has","been","will","were",
            "into","him","her","them","its","our","my","your","do","did","an","if",
        }
        freq   = Counter(w for w in words if w not in stop and len(w) > 3)
        themes = [w for w, _ in freq.most_common(15)]

        # Chapter detection
        chapters = []
        chapter_re = re.compile(
            r'(?i)^\s*(chapter\s+(?:\d+|[ivxlcdm]+|one|two|three|four|five|six|'
            r'seven|eight|nine|ten|eleven|twelve)[\s.:—-]|'
            r'\d+\.\s+[A-Z]|\bPART\s+[IVX\d]+)',
            re.MULTILINE
        )
        matches = list(chapter_re.finditer(body))
        if len(matches) >= 2:
            for idx, m in enumerate(matches[:20]):  # cap at 20 chapters
                start = m.start()
                end   = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
                chunk = body[start:end].strip()
                if len(chunk) < 80:
                    continue
                title_line = chunk.splitlines()[0][:100].strip()
                summary    = self._extractive_summary(chunk, 3)
                chapters.append({"chapter": title_line, "summary": summary})

        return {
            "elevator_pitch":   elevator,
            "detailed_summary": detailed,
            "key_themes":       themes,
            "chapter_breakdown": chapters,
        }

    def _extract_entities(self, body: str, is_fiction: bool) -> list[dict]:
        """
        Lightweight named-entity extraction: find capitalized noun phrases
        that appear frequently (≥ 3 times) and grab a context snippet for each.
        """
        # Find capitalized words/phrases (simple heuristic NER)
        candidates = re.findall(r'\b[A-Z][a-zA-Z\'-]{2,}(?:\s+[A-Z][a-zA-Z\'-]{2,}){0,2}\b', body)
        freq = Counter(candidates)

        # Filter: must appear ≥ 3 times, length > 3 chars, not common sentence-starters
        noise = {"The", "And", "But", "For", "Yet", "Nor", "So", "He", "She",
                 "They", "We", "You", "It", "His", "Her", "Their", "Its",
                 "This", "That", "These", "Those", "A", "An"}
        top = [
            (ent, count) for ent, count in freq.most_common(50)
            if count >= 3 and len(ent) > 3 and ent not in noise
        ][:25]

        sents = self._sentences(body)
        result = []
        for ent, _ in top:
            # Find first sentence containing entity as context
            ctx = next(
                (s for s in sents if ent in s and len(s) < 250),
                "Mentioned throughout the text."
            )
            result.append({
                "entity":          ent,
                "type":            "character" if is_fiction else "concept_or_person",
                "context_snippet": ctx.strip(),
            })
        return result

    def _build_triggers(self, metadata: dict, summaries: dict, entities: list) -> list[str]:
        """Build recall trigger keywords for Shiro's memory surface."""
        triggers = set()

        # Title words
        title = metadata.get("title", "")
        triggers.update(w.lower() for w in re.findall(r'\w+', title) if len(w) > 3)

        # Author surname
        author = metadata.get("author", "")
        if author and author != "Unknown":
            parts = author.split()
            if parts:
                triggers.add(parts[-1].lower())   # surname

        # Key themes (top 10)
        triggers.update(summaries.get("key_themes", [])[:10])

        # Top entities (lowercased)
        for e in entities[:12]:
            triggers.add(e["entity"].lower())

        # Genre context words
        genre = metadata.get("genre", "").lower()
        if any(g in genre for g in ("fantasy", "fiction", "novel", "adventure")):
            triggers.update({"adventure", "quest", "magic", "hero", "journey"})
        elif any(g in genre for g in ("science", "history")):
            triggers.update({"history", "discovery", "research", "facts"})
        elif "biography" in genre or "memoir" in genre:
            triggers.update({"life", "biography", "memoir", "story"})

        # Deduplicate, sort, cap
        return sorted(triggers)[:20]


# ─────────────────────────────────────────────────────────────────
#  STANDALONE TEST
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")

    reader = BookReader()
    books  = reader.list_books()

    if not books:
        print(f"\n📚 No books found.")
        print(f"   Add .txt files to: {reader.books_dir.resolve()}")
        print("\n   File format:")
        print("   Title: My Book")
        print("   Author: Someone")
        print("   Fiction: true")
        print("   ---")
        print("   Full text here...\n")
        sys.exit(0)

    target = sys.argv[1] if len(sys.argv) > 1 else None
    to_digest = [target] if target else books

    for fname in to_digest:
        print(f"\n📖 Digesting: {fname}")
        try:
            mem  = reader.digest_book(fname)
            path = reader.save_to_memories(mem)
            print(f"   ✅ '{mem['title']}' by {mem['author']}")
            print(f"   Type: {'Fiction' if mem['is_fiction'] else 'Non-Fiction'}")
            print(f"   Themes: {', '.join(mem['key_themes'][:6])}")
            print(f"   Entities: {len(mem['key_entities'])} found")
            print(f"   Triggers: {mem['memory_triggers'][:8]}...")
            print(f"   Saved → {path}")
            print(f"\n   Prompt snippet preview:")
            print("   " + reader.get_shiro_prompt_snippet(mem)[:300])
        except Exception as e:
            print(f"   ❌ Error: {e}")