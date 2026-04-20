━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  SHIRO — BOOK READER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FOLDER STRUCTURE
─────────────────────────────────────────────────────────────
  shiro-ai/
  └── book_reader/
      ├── read_book.py       ← the digestion engine
      ├── README.txt         ← this file
      ├── __init__.py        ← makes this a Python package
      ├── books/             ← DROP YOUR .txt BOOKS HERE
      │   └── example.txt
      └── book_memories/     ← auto-created; stores JSON digests


HOW TO ADD A BOOK
─────────────────────────────────────────────────────────────
1. Create a plain .txt file for your book.
2. Add a short header at the very top (see FORMAT below).
3. Drop the file into:  book_reader/books/
4. Open Shiro's web GUI → 📚 Books tab
5. Click  ↻ Refresh List  to see it in the dropdown.
6. Select it and click  📖 Digest & Load into Memory.

Shiro's memory updates live — she will reference the book
naturally in conversation when topics match.


BOOK FILE FORMAT
─────────────────────────────────────────────────────────────
The header goes at the very top of the file.
End the header with a separator line (---).
The rest of the file is the full book text.

  Title: The Hobbit
  Author: J.R.R. Tolkien
  Genre: Fantasy
  Fiction: true
  Year: 1937
  Description: A hobbit's unexpected journey into adventure.
  ---
  In a hole in the ground there lived a hobbit. Not a nasty,
  dirty, wet hole...

HEADER FIELDS (all optional except Title is strongly recommended)
─────────────────────────────────────────────────────────────
  Title        → How Shiro refers to the book
  Author       → Author name (surname used as a trigger word)
  Genre        → Fantasy / Sci-Fi / Non-Fiction / History / etc.
  Fiction      → true or false  (auto-detected from content if omitted)
  Year         → Publication year (informational only)
  Description  → One-sentence blurb (used if body is too short to summarize)

FICTION vs NON-FICTION
─────────────────────────────────────────────────────────────
  Fiction: true   → Shiro treats the book as a story.
                    She'll use it for analogies, metaphors,
                    and emotional parallels. She will NOT
                    present story events as real facts.

  Fiction: false  → Shiro treats the book as real knowledge.
                    She'll cite it as factual information and
                    reference it the way she would research.

  If you omit the Fiction field, Shiro auto-detects based on
  writing style and genre clues — but explicit is always better.

SUPPORTED GENRES (for best auto-detection)
─────────────────────────────────────────────────────────────
  Fiction:      Fantasy, Sci-Fi, Novel, Romance, Horror,
                Thriller, Mystery, Adventure, Story
  Non-Fiction:  Non-Fiction, History, Biography, Memoir,
                Science, Reference, Self-Help, Technical

TIPS
─────────────────────────────────────────────────────────────
  • Any plain UTF-8 .txt works — Project Gutenberg files are
    perfect (just add the header above the text).
  • Very short files (< 100 chars of body) will be rejected.
  • Already digested books are listed in the GUI's
    "✅ Already Digested" panel — no need to re-digest.
  • Digests are saved as JSON in book_memories/ — you can
    inspect them to see what Shiro extracted.
  • Re-digesting a book overwrites the old memory.

EXAMPLE FILE NAME CONVENTIONS
─────────────────────────────────────────────────────────────
  the_hobbit.txt
  dune.txt
  a_brief_history_of_time.txt
  sapiens.txt

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
