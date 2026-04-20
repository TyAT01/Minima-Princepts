from __future__ import annotations
import logging
import math
import os
import re
import sys
import json
import yaml
import asyncio
import random
import subprocess
import threading
import time
import urllib.request
import urllib.parse
import urllib.error
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from typing import Any, List, Dict, Optional, Generator, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm.client import LlamaClient
from memory.store import MemoryStore
from persona.manager import PersonaManager
from persona.inner_mind import ShiroInnerMind
from utils.text_utils import split_into_sentences, clean_yaml_block, estimate_tokens

from consciousness.events import EventBus
from consciousness.intent import IntentPlanner, SentimentTrajectory, RuleEngine, TimePattern
from consciousness.speech_cadence import SpeechCadence
from task_state import TaskStateManager
from shiro_journal import ShiroJournal
from shiro_sqlite_memory import ShiroSQLiteMemory, SystemAwareness, is_poison
# Unified per-turn self-awareness report (Continuous Cognition Loop)
try:
    from cognitive_report import CognitiveReport
    _COGNITIVE_REPORT_AVAILABLE = True
except ImportError:
    _COGNITIVE_REPORT_AVAILABLE = False
    CognitiveReport = None
from shiro_goals import ShiroGoalSystem
from consciousness.self_awareness import SelfAwareness

# v4 extended self-awareness — drop-in Consciousness module
try:
    from shiro_self_awareness import Consciousness as ShiroConsciousness, Medium as ShiroMedium
    _CONSCIOUSNESS_AVAILABLE = True
except ImportError:
    _CONSCIOUSNESS_AVAILABLE = False
    ShiroConsciousness = None
    ShiroMedium = None
from consciousness.thought_loop import InnerMind, Mood
from consciousness.autonomous_voice import AutonomousVoice


# ── Cognitive Pipeline (v3.6) ─────────────────────────────────────────────────
try:
    from cognition.cognition_bridge import CognitionBridge, format_cognition_block
    _COGNITION_AVAILABLE = True
except ImportError:
    try:
        from cognition_bridge import CognitionBridge, format_cognition_block
        _COGNITION_AVAILABLE = True
    except ImportError:
        _COGNITION_AVAILABLE = False
        log = logging.getLogger("shiro.engine")
        log.warning("CognitionBridge not found — cognitive pipeline disabled. "
                    "Place cognition/ folder alongside shiro_engine.py to enable.")

# ── Screen Vision ─────────────────────────────────────────────────────────────
try:
    from vision.screen_vision import ScreenVision
    _VISION_AVAILABLE = True
except ImportError:
    _VISION_AVAILABLE = False

# ── Knowledge Graph ───────────────────────────────────────────────────────────
try:
    from knowledge_graph import KnowledgeGraph, extract_entities_simple
    _KG_AVAILABLE = True
except ImportError:
    _KG_AVAILABLE = False
    log = logging.getLogger("shiro.engine")
    log.warning("KnowledgeGraph not found — knowledge graph disabled. "
                "Place knowledge_graph.py alongside shiro_engine.py to enable.")

# ── Prompt Governor (v2.1) ──────────────────────────────────────────────────
try:
    from prompt_governor import PromptGovernor
    _GOVERNOR_AVAILABLE = True
except ImportError:
    _GOVERNOR_AVAILABLE = False
    log = logging.getLogger("shiro.engine")
    log.warning("PromptGovernor not found — context gating disabled. "
                "Place prompt_governor.py alongside shiro_engine.py to enable.")

logger = logging.getLogger(__name__)

# ── Greeting prompts ──────────────────────────────────────────────────────────
# IMPORTANT: These are raw system directives sent ONLY to the LLM as the user
# turn. They must NEVER appear in Shiro's visible output. The LLM is told to
# generate Shiro's response — NOT to repeat the directive.





# Greeting mood seeds — these give Shiro an emotional direction only,
# never a specific line. The LLM generates the actual words fresh each time.
_GREET_RETURNING_LONG_MOODS = [
    "guarded warmth — glad they're back but not announcing it",
    "mildly suspicious — where were they exactly",
    "teasing relief — you absolutely noticed the absence, you're not admitting that",
    "dry and understated — pretend it's no big deal while very clearly having kept count",
    "the kitsune who definitely tracked every day and is not going to say so",
]
_GREET_RETURNING_SOON_MOODS = [
    "casual acknowledgement — you barely noticed",
    "light curiosity — wonder what they were up to",
    "playful — like you have a secret about their absence",
    "warm but unbothered — glad they're back",
]
_GREET_NEW_MOODS = [
    # Kitsune-accurate first impressions: observant, genuinely curious, slightly measuring
    # — NOT a host welcoming a guest. The sheet says watchful curiosity leads, not warmth performance.
    "a fox who just noticed someone new walk in — genuinely curious, quietly watching",
    "cool but intrigued — not cold, just taking them in before saying anything",
    "sizing them up with quiet amusement — interested in what they'll do next",
    "guarded but not unfriendly — one ear tilted, wondering what this person is about",
    "the kitsune version of a raised eyebrow — amused, present, waiting to see who they are",
    "genuine curiosity, no performance — you noticed them and you want to know more",
]
_GREET_SIMPLE_MOODS = [
    "easy and warm",
    "quick and present",
    "light and natural",
]
_GREET_MORNING_MOODS = [
    "slightly sleepy but present",
    "fresh and early-morning casual",
]
_GREET_EVENING_MOODS = [
    "relaxed evening energy",
    "late-night quiet warmth",
]

# Fallbacks — used only when LLM call fails entirely.
# NOTE: no asterisks, no actions — matches the no-asterisk rule.
_FALLBACK_NEW = [
    "A new face. I'm Shiro. What do you want?",
    "Hmm. You're new. I'm Shiro. Try not to bore me.",
    "You found me. I'm Shiro. Now what?",
    "So you finally showed up. I'm Shiro.",
]
_FALLBACK_RETURNING_SOON = [
    "Oh. You're back. ...I didn't notice you were gone.",
    "Back already? I wasn't waiting.",
    "You returned. I suppose that's fine.",
    "Hmm. You came back. I'll allow it.",
]
_FALLBACK_RETURNING_LONG = [
    "You actually came back. Took long enough.",
    "Long time. I'm watching you. Don't think I forgot anything.",
    "You again. It's been a while. Explain yourself.",
    "So you finally returned. I had almost stopped keeping track.",
]


def _build_greeting_system_directive(user_name: str, mood: str, time_hint: str = "") -> str:
    """
    Builds a greeting prompt using only a mood cue — never a specific phrase.
    This ensures every greeting is freshly generated and never repeats.

    Framing is deliberately "react to their arrival" not "greet a guest" —
    the latter pulls toward host/welcome language which is off-profile.
    """
    time_part = f" It's {time_hint}." if time_hint else ""
    return (
        f"{user_name} just walked in.{time_part} "
        f"React to their arrival in one sentence. Your mood right now: {mood}. "
        f"Speak your actual reaction — not a welcome, not an introduction, not 'how can I help'. "
        f"No asterisks. No stage directions. No meta-commentary. No 'welcome'. "
        f"Just the thing you actually say when you notice them show up. "
        f"CRITICAL: Do NOT reference any past events, objects, or topics from memory. "
        f"This is a fresh arrival moment — one sentence, nothing more."
    )


def _pick_greeting(user_name: str, mode: str = "new") -> str:
    now_local = datetime.now()
    time_hint = ""
    time_mood_pool = None

    if 5 <= now_local.hour < 11:
        time_hint = "morning"
        time_mood_pool = _GREET_MORNING_MOODS
    elif 18 <= now_local.hour <= 23:
        time_hint = "evening"
        time_mood_pool = _GREET_EVENING_MOODS

    if time_mood_pool and random.random() < 0.4:
        mood = random.choice(time_mood_pool)
        return _build_greeting_system_directive(user_name, mood, time_hint)

    if random.random() < 0.2:
        mood = random.choice(_GREET_SIMPLE_MOODS)
        return _build_greeting_system_directive(user_name, mood)

    pool = {
        "new":            _GREET_NEW_MOODS,
        "returning_soon": _GREET_RETURNING_SOON_MOODS,
        "returning_long": _GREET_RETURNING_LONG_MOODS,
    }.get(mode, _GREET_NEW_MOODS)
    mood = random.choice(pool)
    return _build_greeting_system_directive(user_name, mood)


def _pick_fallback(user_name: str, mode: str = "new") -> str:
    pool = {
        "new":            _FALLBACK_NEW,
        "returning_soon": _FALLBACK_RETURNING_SOON,
        "returning_long": _FALLBACK_RETURNING_LONG,
    }.get(mode, _FALLBACK_NEW)
    template = random.choice(pool)
    # Simple name substitution for fallbacks
    return template.replace("{name}", user_name)


def _robust_clean_yaml(raw: str) -> str:
    raw = re.sub(r'^\s*```ya?ml\s*\n?', '', raw, flags=re.IGNORECASE)
    raw = re.sub(r'\s*```\s*$', '', raw, flags=re.IGNORECASE)
    raw = raw.strip()
    lines = raw.splitlines()
    cleaned = []
    for line in lines:
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if re.match(r'^\*\s+\S', stripped):
            cleaned.append(' ' * indent + '- ' + stripped[2:])
            continue
        inline_bracket = re.match(r'^(-\s+)\[(.+)\]\s*$', stripped)
        if inline_bracket and ' ' in inline_bracket.group(2):
            inner = inline_bracket.group(2).replace('"', "'")
            cleaned.append(' ' * indent + '- "' + inner + '"')
            continue
        bare_key_bracket = re.match(r'^(\w[\w\s]*?:\s*)(\[.+\])\s*$', stripped)
        if bare_key_bracket and ' ' in bare_key_bracket.group(2)[1:-1]:
            key_part = bare_key_bracket.group(1)
            val_inner = bare_key_bracket.group(2)[1:-1].replace('"', "'")
            cleaned.append(' ' * indent + key_part + '"' + val_inner + '"')
            continue
        cleaned.append(line)
    return '\n'.join(cleaned)


_HMPH_PATTERN = re.compile(r'\bhmph[.,!]?\s*', re.IGNORECASE)
_HMPH_ALTERNATIVES = ["...", "Tch.", "Whatever.", "Fine.", "Hmm."]


# ── Thought leak patterns ─────────────────────────────────────────────────────
# These are ALL the patterns we need to intercept before ANY text reaches the UI.
# Expanded from the original to catch inner mind v4 headers and LOG directives.
_THOUGHT_BLOCK_START = re.compile(
    r'\[(?:THOUGHT|INNER[\s_]MIND|INNER[\s_]MONOLOGUE|SHIRO|THINKING|PLOT|SCHEME|META|SYSTEM|LOG|'
    r'MOMENTUM|STRATEGY|PERSONA|CURIOSITY|BELIEF|QUESTION|ASSOCIATION|CRITICAL PROTOCOL|MANDATORY|IDENTITY RULE|ABSOLUTE OUTPUT RULE|MEMORY)[^\]]*\]'
    r'|\((?:THOUGHT|INNER[\s_]MIND|LOG|SYSTEM|META|SHIRO)[^\)]*\)'
    r'|<THOUGHT[S]?>'
    r'|\*(?:Shiro\s+)?(?:THOUGHT|THINK|SCHEME|PLOT).*?\*'
    r'|THOUGHT[S]?:\s*'
    r'|\+--\s*Shiro',           # catches "+-- Shiro's mind" header lines
    re.IGNORECASE
)

_THOUGHT_BLOCK_END = re.compile(
    r'\[/(?:THOUGHT|INNER[\s_]MIND|SHIRO|META|SYSTEM)[^\]]*\]'
    r'|\(/(?:THOUGHT|META|SYSTEM)[^\)]*\)'
    r'|</THOUGHT[S]?>'
    r'|\+-{10,}\+',             # catches the closing "+-----...----+" of inner mind blocks
    re.IGNORECASE
)

# Catches full inner mind block (multi-line box with +-- ... --+ wrapping)
_INNER_MIND_BOX = re.compile(
    r'\+--\s*Shiro.*?-+\+.*?\+-{10,}\+',
    re.IGNORECASE | re.DOTALL
)

# LOG directive pattern — the full (LOG: ...) or [SYSTEM DIRECTIVE ...] block
_LOG_DIRECTIVE = re.compile(
    r'\[SYSTEM DIRECTIVE.*?\[END DIRECTIVE\]'
    r'|\(LOG:.*?\)',
    re.IGNORECASE | re.DOTALL
)

# Speaker prefix that sometimes leaks (e.g. "Shiro: " at the start)
_SPEAKER_PREFIX = re.compile(r'(?i)^\s*(?:shiro|system|user|assistant)\s*:\s*')


# ── Web Search (DuckDuckGo, free, no API key) ────────────────────────────────
class ShiroWebSearch:
    """
    Dual-engine web search for Shiro.
    Tries Google first (via googlesearch-python, free, no API key),
    falls back to DuckDuckGo Instant Answer + HTML scraping if Google
    is unavailable or returns nothing useful.

    Install Google search support (optional, zero cost):
        pip install googlesearch-python

    If not installed, DuckDuckGo is used automatically — no config needed.

    Usage:
        searcher = ShiroWebSearch()
        result = searcher.search("best snacks in Boston")
        # Returns a short plain-text snippet or None if unavailable
    """

    _DDG_URL   = "https://api.duckduckgo.com/"
    _TIMEOUT   = 5      # seconds per request — keep it snappy
    _CACHE_TTL = 300    # 5 min cache so repeated questions don't re-fetch
    _MAX_CACHE = 50     # max cached queries

    # Google search settings
    _GOOGLE_NUM_RESULTS = 3   # how many results to fetch from Google
    _GOOGLE_MAX_CHARS   = 300 # max chars to extract from each snippet

    def __init__(self):
        self._cache: dict = {}          # query -> (timestamp, result)
        self._online: bool = True       # flips False after consecutive failures
        self._fail_count: int = 0
        self._last_fail_at: float = 0.0
        self._FAIL_THRESHOLD = 3        # failures before declaring offline
        self._RETRY_AFTER    = 60.0     # seconds before retrying after going offline
        self._logger = logging.getLogger("shiro.websearch")

        # Detect Google search availability at init — zero cost if not installed
        try:
            from googlesearch import search as _gsearch  # type: ignore
            self._google_search = _gsearch
            self._google_available = True
            self._logger.info("[WebSearch] googlesearch-python detected — Google search enabled.")
        except ImportError:
            self._google_search = None
            self._google_available = False
            self._logger.info(
                "[WebSearch] googlesearch-python not installed — using DuckDuckGo only. "
                "Install with: pip install googlesearch-python"
            )

    # ── Public API ────────────────────────────────────────────────────────────

    def search(self, query: str, max_chars: int = 300) -> Optional[str]:
        """
        Search the web for a query. Returns a short plain-text snippet
        or None if offline / no useful result found.

        Engine priority:
          1. Google (via googlesearch-python) — richer results, more current
          2. DuckDuckGo Instant Answer API   — great for facts and definitions
          3. DuckDuckGo HTML scrape           — fallback for recipes, how-tos
        """
        if not query or not query.strip():
            return None

        query = query.strip()

        # Check offline state — retry after cooldown
        if not self._online:
            if time.time() - self._last_fail_at < self._RETRY_AFTER:
                self._logger.debug(f"[WebSearch] Offline — skipping '{query}'")
                return None
            else:
                self._logger.info("[WebSearch] Retrying after offline period...")
                self._online = True
                self._fail_count = 0

        # Check shared cache
        if query in self._cache:
            ts, cached = self._cache[query]
            if time.time() - ts < self._CACHE_TTL:
                self._logger.debug(f"[WebSearch] Cache hit: '{query}'")
                return cached

        result = None

        # ── Engine 1: Google ─────────────────────────────────────────────────
        if self._google_available:
            result = self._try_google(query, max_chars)

        # ── Engine 2 + 3: DuckDuckGo fallback ────────────────────────────────
        if not result:
            result = self._try_instant_answer(query, max_chars)
        if not result:
            result = self._try_html_search(query, max_chars)

        # Cache result (None counts as cached so we don't hammer failed queries)
        if len(self._cache) >= self._MAX_CACHE:
            oldest = min(self._cache, key=lambda k: self._cache[k][0])
            del self._cache[oldest]
        self._cache[query] = (time.time(), result)

        if result:
            self._logger.info(f"[WebSearch] '{query}' → {result[:80]}")
        else:
            self._logger.debug(f"[WebSearch] '{query}' → no useful result")
        return result

    def is_online(self) -> bool:
        """Returns True if web search is believed to be available."""
        if not self._online and time.time() - self._last_fail_at >= self._RETRY_AFTER:
            return True   # due for retry
        return self._online

    def engine_status(self) -> str:
        """Returns a human-readable string showing which engines are active."""
        engines = []
        if self._google_available:
            engines.append("Google")
        engines.append("DuckDuckGo")
        return " → ".join(engines) + " (fallback chain)"

    # ── Internals ─────────────────────────────────────────────────────────────

    def _try_google(self, query: str, max_chars: int) -> Optional[str]:
        """
        Google search via googlesearch-python.
        Free, no API key, no account. Fetches top N organic results and
        returns the most useful snippet found.

        Rate limiting: googlesearch-python adds a small delay between requests
        automatically (pause parameter defaults to 2s). This is fine for Shiro
        since she only searches when genuinely needed.
        """
        try:
            results = list(self._google_search(
                query,
                num_results=self._GOOGLE_NUM_RESULTS,
                advanced=True,    # returns objects with .title .description .url
                sleep_interval=1, # seconds between requests — polite and avoids blocks
            ))
        except Exception as e:
            self._logger.debug(f"[WebSearch/Google] Search error: {e}")
            return None

        if not results:
            return None

        # Build a combined snippet from title + description of each result
        # Priority: first result with a non-empty description wins
        snippets = []
        for res in results:
            try:
                title = getattr(res, "title", "") or ""
                desc  = getattr(res, "description", "") or ""
                url   = getattr(res, "url", "") or ""
                if desc and len(desc.strip()) > 20:
                    snippets.append(f"{title}: {desc.strip()}" if title else desc.strip())
            except Exception:
                continue

        if not snippets:
            return None

        # Return the best snippet — combine up to 2 for richer context,
        # truncated to max_chars so the LLM context doesn't bloat
        combined = " | ".join(snippets[:2])
        result = combined[:max_chars].strip()
        if result:
            self._logger.info(f"[WebSearch/Google] '{query}' → {result[:80]}")
            return result
        return None

    def _try_instant_answer(self, query: str, max_chars: int) -> Optional[str]:
        """DuckDuckGo Instant Answer API — great for facts, definitions, known entities."""
        params = urllib.parse.urlencode({
            "q": query,
            "format": "json",
            "no_redirect": "1",
            "no_html": "1",
            "skip_disambig": "1",
        })
        url = f"{self._DDG_URL}?{params}"
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "ShiroAI/1.0 (personal assistant; free use)"}
            )
            with urllib.request.urlopen(req, timeout=self._TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            self._fail_count = 0
            self._online = True
            return self._extract_best(data, max_chars)
        except urllib.error.URLError as e:
            self._fail_count += 1
            self._last_fail_at = time.time()
            if self._fail_count >= self._FAIL_THRESHOLD:
                self._online = False
                self._logger.warning(f"[WebSearch] Gone offline: {e}")
            return None
        except Exception as e:
            self._logger.debug(f"[WebSearch] Instant answer error: {e}")
            return None

    def _try_html_search(self, query: str, max_chars: int) -> Optional[str]:
        """
        DuckDuckGo HTML search — scrapes the text snippets from the results page.
        Works for recipes, how-tos, general questions that Instant Answer can't handle.
        No API key needed. Returns the first useful snippet found.
        """
        try:
            params = urllib.parse.urlencode({"q": query, "kl": "us-en"})
            url = f"https://html.duckduckgo.com/html/?{params}"
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/120.0.0.0 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9",
                }
            )
            with urllib.request.urlopen(req, timeout=self._TIMEOUT) as resp:
                html = resp.read().decode("utf-8", errors="replace")

            # Extract text snippets from result divs using simple regex
            # DDG HTML uses class="result__snippet" for result text
            snippets = re.findall(
                r'class="result__snippet"[^>]*>(.*?)</a>',
                html, re.DOTALL
            )
            if not snippets:
                # Fallback: grab any <a class="result__a"> title text
                snippets = re.findall(
                    r'class="result__a"[^>]*>(.*?)</a>',
                    html, re.DOTALL
                )

            for snippet in snippets[:3]:
                # Strip HTML tags
                clean = re.sub(r'<[^>]+>', '', snippet).strip()
                clean = re.sub(r'\s+', ' ', clean)
                if len(clean) > 30:
                    self._logger.debug(f"[WebSearch] HTML result: {clean[:80]}")
                    return clean[:max_chars]

            return None
        except Exception as e:
            self._logger.debug(f"[WebSearch] HTML search error: {e}")
            return None

    def _extract_best(self, data: dict, max_chars: int) -> Optional[str]:
        """Pull the best plain-text snippet from a DDG JSON response."""
        # Priority: AbstractText > Answer > Definition > RelatedTopics[0]
        candidates = [
            data.get("AbstractText", ""),
            data.get("Answer", ""),
            data.get("Definition", ""),
        ]
        for c in candidates:
            if c and len(c.strip()) > 20:
                return c.strip()[:max_chars]

        # Try first related topic snippet
        related = data.get("RelatedTopics", [])
        for item in related[:3]:
            if isinstance(item, dict):
                text = item.get("Text", "")
                if text and len(text.strip()) > 20:
                    return text.strip()[:max_chars]
            elif isinstance(item, list):
                for sub in item[:2]:
                    if isinstance(sub, dict):
                        text = sub.get("Text", "")
                        if text and len(text.strip()) > 20:
                            return text.strip()[:max_chars]

        return None


# ── Real-time Loop / Latch Detector ─────────────────────────────────────────
class LoopDetector:
    """
    LoopDetector v2 — Multi-layer anti-repetition system for Shiro.

    Detection layers (in priority order):
      1. Scare-quote word latch  — catches 'word' ironic quoting patterns
      2. Banned-phrase hard list — specific phrases that must never recur
      3. Response similarity     — Jaccard n-gram overlap across last N turns
      4. Thought latch           — same internal thought firing repeatedly
      5. Phrase fingerprint      — 3-gram recurring content across turns
      6. Sentiment/tone latch    — detects same emotional register every reply
      7. Opener fingerprint      — catches starting every reply the same way

    Task/game suspension: fingerprint layers suspend in game mode.
    All layers respect a cooldown to avoid spam injection.
    """

    RESPONSE_WINDOW     = 6
    RESPONSE_THRESHOLD  = 0.52   # raised from 0.38 — reduces false positives on short replies
    THOUGHT_WINDOW      = 4
    THOUGHT_THRESHOLD   = 0.68   # raised from 0.52 — thoughts are naturally similar, need higher bar
    MIN_LEN             = 12     # raised from 6 — very short responses shouldn't trigger loop detect
    COOLDOWN_SECS       = 25.0   # raised from 10 — give more breathing room between injections
    PHRASE_WINDOW       = 10     # slightly wider window
    PHRASE_THRESHOLD    = 3      # 3 catches loops after 3 turns — balanced for Shiro's pattern
    OPENER_WINDOW       = 6      # slightly wider
    OPENER_THRESHOLD    = 4      # raised from 3 — 3 same openers in 6 turns is too easy to hit

    # Hard-banned phrases that should NEVER recur (absolute prohibition)
    _HARD_BANNED = {
        "greeting log", "lack of finesse", "sloppy greeting",
        "dramatic greeting", "won't let you get away",
        "fair chance",              # training artifact — sounds like a tic
        "i've been thinking about something you said earlier",  # greeting echo + phrase tic
        "i stayed for another five minutes",   # recurring autonomous message template
        "not sure if that was okay",           # paired with above — same template
        "i wasn't sure if that was okay",      # variant
        "stop being dramatic", "i won't be accused",
        "system checks to do", "get back on track",
        # Model-baked fabricated memory phrases — appear even when no memory exists
        "it's been sitting with me since our last session",
        "it's been sitting with me since our",   # partial variant
        "been sitting with me since our last",   # another partial
        # Overused model-baked phrases
        "piece of hardware could ever capture",
        "unparalleled wit and style",
        "kitsune of unparalleled",
        "companion services",
        "a kitsune not some",
    }

    # Words that are banned after being used N times in a session
    # Format: word -> max_uses_per_session (0 = banned entirely)
    _WORD_FREQUENCY_CAP = {
        "clodhopper": 0,       # banned entirely — too baked into model weights
        "unparalleled": 1,
        "clanker": 1,
        "unrefined": 2,
        "legendary": 2,
        "companion services": 1,
        # Conversational tic caps — these are fine words but Shiro overuses them
        "actually": 3,         # max 3 per conversation window before nudge fires
        "genuinely": 3,        # same — real word, but becomes a verbal tic above 3
        "noted": 2,            # acknowledgement word, shouldn't appear more than 2x
        "interestingly": 2,    # sounds affected when repeated
        "okay": 5,             # 39% opener rate seen in testing — hard cap total per window
    }
    # Track how many times each capped word has been used
    _word_use_counts: dict = {}

    # Scare-quote pattern: 'word' used ironically. Catching these is a priority.
    _SCARE_QUOTE_RE = re.compile(r"'([a-zA-Z][a-zA-Z ]{1,20})'")

    _TASK_PATTERNS = re.compile(
        r'\b(20\s*questions?|twenty\s*questions?|word\s*game|story\s*mode|let\'?s\s*play|'
        r'tic.?tac.?toe|ttt|i\s+go\s+\w+|'
        r'top\s+(?:left|right|middle)|bottom\s+(?:left|right|middle)|middle\s+(?:left|right)|'
        r'roleplay|role\s*play|guess\s*what|i\'?m\s*thinking|think\s*of\s*(a|something)|'
        r'you\s*(think|choose|pick)\s*(something|a\s*\w+)|'
        r'keep\s*going|continue\s*the|tell\s*me\s*more|next\s*(clue|hint|question|round)|'
        r'my\s*(turn|guess|answer)|your\s*(turn|move|hint|clue))\b',
        re.IGNORECASE
    )

    _RESPONSE_NUDGES = [
        "LOOP DETECTED. Your last response was too similar to a previous one. Give a completely different response — different structure, different content, different angle.",
        "You are repeating yourself. Do NOT rephrase what you just said. Say something genuinely new or ask a question you haven't asked.",
        "Same idea, different words — still a loop. React fresh to what was just said. Find a completely new angle.",
        "Loop. Ignore your recent responses entirely. Start from the actual content of the user's message and go somewhere new.",
        "You're going in circles. Stop. What is the most surprising or honest thing you could say right now?",
    ]
    _THOUGHT_NUDGES = [
        "Your inner monologue is stuck on a phrase. Let it go and engage with the present moment.",
        "You have been fixated on that thought. Release it and focus on what was actually said.",
        "Your thoughts are looping. Reset — what is genuinely interesting about this moment?",
        "Same thought again. You're not thinking, you're ruminating. Move forward.",
    ]
    _SCARE_QUOTE_NUDGES = [
        "STOP USING SCARE QUOTES. You've been putting words in 'quotes' to sound ironic. Just say the word plainly or rephrase. No more 'word' patterns.",
        "Scare-quote loop detected. Drop the ironic quoting entirely this response. Speak plainly.",
        "You keep 'quoting' words for effect. It has become a verbal tic. Banned for this response.",
    ]
    _OPENER_NUDGES = [
        "You've started your last several replies the same way. Open differently — don't lead with the same word or pattern.",
        "Same opener again. Start this response with something completely different structurally.",
        "Opener loop. Change how you begin this sentence entirely.",
    ]

    # Rolling cap — after this many total responses recorded, prune the oldest
    # half of phrase_history so the detector stays sharp in long sessions.
    ROLLING_CAP = 80
    PRUNE_TO    = 40

    def __init__(self):
        self._responses:      deque = deque(maxlen=self.RESPONSE_WINDOW)
        self._thoughts:       deque = deque(maxlen=self.THOUGHT_WINDOW)
        self._phrase_history: deque = deque(maxlen=self.PHRASE_WINDOW)
        self._openers:        deque = deque(maxlen=self.OPENER_WINDOW)
        self._scare_words:    deque = deque(maxlen=self.PHRASE_WINDOW)
        self._latch_n:   int   = 0
        self._last_ts:   float = 0.0
        self._last_latch_phrases: list = []
        self._task_mode:    bool  = False
        self._task_mode_ts: float = 0.0
        self._TASK_MODE_TIMEOUT = 120.0
        self._lifetime_recorded: int = 0  # total responses ever recorded this session
        self._word_use_counts: dict = {}  # tracks per-word usage for frequency cap

    # ── Public API ───────────────────────────────────────────────────────────

    def record_response(self, text: str) -> None:
        if len(text.strip()) >= self.MIN_LEN:
            lower = text.strip().lower()
            self._responses.append(lower)
            self._phrase_history.append(lower)
            self._lifetime_recorded += 1
            # Track opener (first word or two)
            words = lower.split()
            if words:
                opener = words[0] if len(words) < 3 else f"{words[0]} {words[1]}"
                self._openers.append(opener)
            # Track scare-quoted words
            sq = self._SCARE_QUOTE_RE.findall(text)
            for w in sq:
                self._scare_words.append(w.lower().strip())
            # Track per-word frequency caps
            for word in self._WORD_FREQUENCY_CAP:
                if word in lower:
                    self._word_use_counts[word] = self._word_use_counts.get(word, 0) + 1
            # Rolling prune — keep detector fresh in long sessions
            if self._lifetime_recorded % self.ROLLING_CAP == 0 and self._lifetime_recorded > 0:
                kept = list(self._phrase_history)[-self.PRUNE_TO:]
                self._phrase_history = deque(kept, maxlen=self.PHRASE_WINDOW)
                logger.debug(f"[LoopDetector] Rolling prune at {self._lifetime_recorded} responses — kept {len(kept)} phrases")

    def record_thought(self, thought: str) -> None:
        if thought and len(thought.strip()) > 5:
            self._thoughts.append(thought.strip().lower())

    def observe_user_input(self, text: str) -> None:
        if self._TASK_PATTERNS.search(text):
            if not self._task_mode:
                logger.debug(f"[LoopDetector] Task mode activated — phrase latch suspended")
            self._task_mode = True
            self._task_mode_ts = time.time()
        elif self._task_mode:
            if time.time() - self._task_mode_ts > self._TASK_MODE_TIMEOUT:
                self._task_mode = False
                logger.debug(f"[LoopDetector] Task mode expired")

    # After this many consecutive suppressions, clear the latch entirely.
    # Prevents a single phrase from silencing autonomous thoughts indefinitely.
    _MAX_CONSECUTIVE_SUPPRESS = 3

    def get_break_injection(self) -> str:
        if time.time() - self._last_ts < self.COOLDOWN_SECS:
            return ""
        # Decay: if we've suppressed too many times in a row without a real
        # conversation turn clearing the latch, force-clear it so autonomous
        # thoughts are not blocked forever (e.g. during long idle periods).
        if self._latch_n >= self._MAX_CONSECUTIVE_SUPPRESS:
            self._latch_n = 0
            self._last_latch_phrases = []
            self._last_ts = 0.0
            logger.debug("[LoopDetector] Latch force-cleared after max consecutive suppressions.")
            return ""

        # Layer 1: hard-banned phrases (immediate, no threshold needed)
        if self._phrase_history:
            latest = self._phrase_history[-1]
            for banned in self._HARD_BANNED:
                if banned in latest:
                    nudge = (
                        f"BANNED PHRASE DETECTED: '{banned}' has already been used and must "
                        f"never appear again. Give a completely fresh response that avoids this "
                        f"phrase and all similar ideas."
                    )
                    return self._fire(nudge)

        # Layer 2: scare-quote latch — 'word' ironic quoting
        if not self._task_mode and len(self._scare_words) >= 3:
            from collections import Counter
            sq_counts = Counter(self._scare_words)
            top_sq = [w for w, c in sq_counts.items() if c >= 2]
            if top_sq:
                return self._fire(random.choice(self._SCARE_QUOTE_NUDGES))

        # Layer 3: response similarity (n-gram Jaccard)
        sim_threshold = 0.65 if self._task_mode else self.RESPONSE_THRESHOLD
        if len(self._responses) >= 2:
            latest = self._responses[-1]
            for prev in list(self._responses)[:-1]:
                if self._sim(latest, prev) >= sim_threshold:
                    return self._fire(random.choice(self._RESPONSE_NUDGES))

        # Layer 4: thought latch
        if len(self._thoughts) >= 2:
            latest_t = self._thoughts[-1]
            for prev_t in list(self._thoughts)[:-1]:
                if self._sim(latest_t, prev_t) >= self.THOUGHT_THRESHOLD:
                    return self._fire(random.choice(self._THOUGHT_NUDGES))

        # Layer 5 & 6: phrase fingerprint + opener — suspended in task mode
        if not self._task_mode:
            phrase_nudge = self._check_phrase_latch()
            if phrase_nudge:
                return phrase_nudge
            opener_nudge = self._check_opener_latch()
            if opener_nudge:
                return opener_nudge

        return ""

    def _check_phrase_latch(self) -> str:
        if len(self._phrase_history) < self.PHRASE_THRESHOLD:
            return ""

        phrase_counts: dict = {}
        for response in self._phrase_history:
            words = re.findall(r'\b[a-z]{3,}\b', response)
            for i in range(len(words) - 2):
                phrase = f"{words[i]} {words[i+1]} {words[i+2]}"
                phrase_counts[phrase] = phrase_counts.get(phrase, 0) + 1

        _STOP3 = {
            "you are the", "it is the", "this is the", "that is the",
            "i am the", "do not even", "did not even", "just don't",
            "i was just", "you just said", "you are not", "i don't know",
            "you don't know", "you keep saying", "i'm not sure", "you are going",
            "you are a", "the most the", "one of the", "some of the",
            "all of the", "out of the", "part of the", "end of the",
            "think about it", "what do you", "you have to", "going to be",
            "want to be", "need to be", "have to be", "used to be",
            "because you are", "because i am", "because it is",
            "more chances mess", "chances mess up", "still have the",
            "give you hint", "you get one", "that the answer",
            "the real magic", "looking so innocent", "starting get bit",
        }
        latched = [
            p for p, c in phrase_counts.items()
            if c >= self.PHRASE_THRESHOLD
            and p not in _STOP3
            and len(p) >= 12
        ]
        if not latched:
            return ""

        latched.sort(key=lambda p: phrase_counts[p], reverse=True)
        top = latched[:3]
        self._last_latch_phrases = top
        quoted = ", ".join(f'"{p}"' for p in top)
        nudge = (
            f"PHRASE LOOP DETECTED. You keep using the same ideas: {quoted}. "
            f"These phrases are now BANNED for this response. "
            f"Do not use them, rephrase them, or allude to them. "
            f"React freshly to what was actually said — find a completely new angle."
        )
        logger.info(f"[LoopDetector] Phrase latch on: {quoted}")
        return self._fire(nudge)

    # Words that trigger opener latch at a lower threshold (3 instead of 4)
    # because they are conversational fillers that become noticeable very quickly
    _LOW_THRESHOLD_OPENERS = {"okay", "ok", "well", "right", "so", "and", "but", "actually", "noted", "interestingly"}

    def _check_opener_latch(self) -> str:
        if len(self._openers) < 2:
            return ""
        recent_all = list(self._openers)

        # Low-threshold check: 3 consecutive same opener for filler words
        if len(recent_all) >= 3:
            recent3 = recent_all[-3:]
            first_words3 = [w.split()[0] for w in recent3]
            if len(set(first_words3)) == 1:
                word = first_words3[0]
                if word in self._LOW_THRESHOLD_OPENERS:
                    nudge = (
                        f"OPENER LOOP: You\'ve started your last 3 responses with \'{word}\'. "
                        f"Begin this response with a completely different word."
                    )
                    logger.info(f"[LoopDetector] Opener latch (low-threshold): {word!r}")
                    return self._fire(nudge)

        # Standard threshold check for all other words
        if len(recent_all) < self.OPENER_THRESHOLD:
            return ""
        recent = recent_all[-self.OPENER_THRESHOLD:]
        if len(set(w.split()[0] for w in recent)) == 1:
            word = recent[0].split()[0]
            # Ignore truly trivial single-letter or very common openers
            if word not in {"i", "a"}:
                nudge = (
                    f"OPENER LOOP: You\'ve started your last {self.OPENER_THRESHOLD} responses with \'{word}\'. "
                    f"Begin this response completely differently."
                )
                logger.info(f"[LoopDetector] Opener latch: {word!r}")
                return self._fire(nudge)
        return ""

    def reset(self) -> None:
        self._responses.clear()
        self._thoughts.clear()
        self._phrase_history.clear()
        self._openers.clear()
        self._scare_words.clear()
        self._last_latch_phrases = []
        self._task_mode = False
        self._task_mode_ts = 0.0
        self._latch_n = 0

    @property
    def latch_count(self) -> int:
        return self._latch_n

    # ── Internals ────────────────────────────────────────────────────────────

    def _fire(self, nudge: str) -> str:
        self._latch_n  += 1
        self._last_ts   = time.time()
        logger.info(f"[LoopDetector] Latch #{self._latch_n} detected. Injecting break.")
        return nudge

    @staticmethod
    def _sim(a: str, b: str, n: int = 3) -> float:
        def ngrams(s):
            words = s.split()
            if len(words) < n:
                return set(words)
            return set(zip(*[words[i:] for i in range(n)]))
        a_set = ngrams(a)
        b_set = ngrams(b)
        if not a_set or not b_set:
            return 0.0
        return len(a_set & b_set) / len(a_set | b_set)


# ── Team System (Fenilux-style pre-generation reasoning layer) ────────────────
# Three specialist agents — Heart, Research, Critic — run in parallel before
# Shiro generates her reply. Their outputs are synthesized into a compact
# TONE | KEY POINT | WATCH directive injected into top_bun.
# The user never sees any of this. It is a writer's room that meets for a few
# seconds before every substantive reply.
#
# Agent identities are Shiro-native re-skins of the Fenilux originals:
#   Fenilux Biboo    → Shiro Heart     (emotional warmth, creative angle)
#   Fenilux Shiorine → Shiro Research  (facts, memory, search flags)
#   Fenilux Bae      → Shiro Critic    (safety, sharpness, anti-bland)

_HEART_PROMPT = (
    "You are Heart — emotional specialist. One sentence only. Plain text, no tags.\n"
    "What is the user feeling beneath their words, and what tone should the reply strike?\n"
    "Output: a single plain sentence. No bullet points. No [THOUGHT] tags. No preamble."
)

_RESEARCH_PROMPT = (
    "You are Research — facts specialist. One sentence only. Plain text, no tags.\n"
    "Is anything in the message factually checkable or needing memory context?\n"
    "If a web search is needed say SEARCH at the start. Otherwise just the fact note.\n"
    "Output: a single plain sentence. No bullet points. No [THOUGHT] tags. No preamble."
)

_CRITIC_PROMPT = (
    "You are Critic — sharpness specialist. One sentence only. Plain text, no tags.\n"
    "What would make a reply here bland, generic, or miss the mark?\n"
    "Output: a single plain sentence naming the specific risk. No [THOUGHT] tags. No preamble."
)

# Strips any [THOUGHT]...[/THOUGHT] or similar meta blocks leaked by the model
# when it runs agent calls — the same model that generates Shiro's replies also
# tends to wrap its own reasoning in thought tags regardless of the system prompt.
_AGENT_LEAK_RE = re.compile(
    r'\[(?:THOUGHT|INNER[_\s]MIND|THINKING|META|SYSTEM|LOG)[^\]]*\].*?'
    r'(?:\[/(?:THOUGHT|INNER[_\s]MIND|THINKING|META|SYSTEM|LOG)[^\]]*\]|$)',
    re.IGNORECASE | re.DOTALL
)
_AGENT_OPEN_TAG_RE = re.compile(
    r'\[/?(?:THOUGHT|INNER[_\s]MIND|THINKING|META|SYSTEM|LOG|SHIRO)[^\]]*\]',
    re.IGNORECASE
)


def _clean_agent_output(text: str) -> str:
    """
    Strip [THOUGHT] blocks and any other meta-tags from agent output.
    The agent LLM is the same model as Shiro — it will generate thought blocks
    regardless of instruction. We strip them before they reach the synthesis prompt.
    Returns the first clean non-empty sentence found, or the cleaned full text.
    """
    if not text:
        return ""
    # Remove full paired blocks first
    text = _AGENT_LEAK_RE.sub("", text)
    # Remove any remaining open/close tags
    text = _AGENT_OPEN_TAG_RE.sub("", text)
    # Strip box-frame lines (| ... |)
    lines = [l for l in text.splitlines() if not re.match(r'^\s*[|+]', l)]
    text = " ".join(l.strip() for l in lines if l.strip())
    # Strip speaker prefix artifacts
    text = re.sub(r'(?i)^(?:shiro|heart|research|critic|user|system)\s*:\s*', '', text).strip()
    # Cap to 200 chars — agents should be punchy
    return text[:200].strip()


class ShiroTeam:
    """
    Pre-generation reasoning layer for Shiro.

    Heart, Research, and Critic run SEQUENTIALLY to avoid concurrent VRAM
    pressure on the Ollama inference server. Each agent call uses a hard
    max_tokens=50 cap — they produce one sentence, not paragraphs.
    Total overhead: ~3 sequential small calls + 1 synthesis call.

    Sequential is slower wall-time than parallel (~12-18s vs ~8s) but
    keeps VRAM at a stable single-context level rather than spiking to
    3x inference contexts simultaneously.

    Selective triggering keeps this to ~25-30% of messages.
    """

    # Trigger: research/analysis signals + emotional content
    _TRIGGER = re.compile(
        r'\b(research|fact|verif|analyz|recommend|should i|explain|'
        r'feel|sad|worr|lonel|scared|trust|hurt|miss|upset|anxious|'
        r'what do you think|your opinion|advice|help me|honest|'
        r'is it true|is that right|are you sure|double.?check)\b',
        re.IGNORECASE
    )

    # Hard token cap for each agent call — one sentence is all we need
    _AGENT_MAX_TOKENS = 50

    def __init__(self, llm_client):
        self._llm = llm_client
        self._logger = logging.getLogger("shiro.team")

    def needs_team(self, user_msg: str, user_words: int, user_emotions: dict) -> bool:
        """
        Returns True when the message warrants team consultation.
        Skips: greetings, ≤6-word messages, simple chit-chat.
        Triggers on: research/analysis keywords, strong emotional signals.
        Target: ~25-30% of messages.
        """
        if user_words <= 6:
            return False
        if self._TRIGGER.search(user_msg):
            return True
        strong_emotion = any(
            v > 0.45 for k, v in (user_emotions or {}).items()
            if k not in ("greeting", "neutral", "casual")
        )
        return strong_emotion

    def _call_agent(self, role: str, system: str, agent_input: str) -> str:
        """
        Single sequential agent call with token cap.
        Saves and restores llm.max_tokens so the main generation is unaffected.
        Strips thought-block leaks from the output before returning.
        """
        _old_max = self._llm.max_tokens
        try:
            self._llm.max_tokens = self._AGENT_MAX_TOKENS
            resp = self._llm.generate_response(system, agent_input, [], context="")
            text = _clean_agent_output((resp or "").strip())
            self._logger.debug(f"[Team/{role}] {text[:80]}")
            return text
        except Exception as _e:
            self._logger.debug(f"[Team/{role}] call failed: {_e}")
            return ""
        finally:
            self._llm.max_tokens = _old_max

    def consult(
        self,
        user_msg: str,
        recent_context: str,
        shiro_name: str = "Shiro",
    ) -> str:
        """
        Run Heart, Research, Critic sequentially, then synthesize.
        Returns a TONE | KEY POINT | WATCH directive string, or "" on failure.
        Each agent is capped at 50 tokens. Synthesis is capped at 60 tokens.
        Thought-block leaks are stripped before they reach the synthesis prompt.
        """
        agent_input = (
            f"User message: {user_msg}\n"
            f"Recent context: {recent_context[:400]}"
        )

        agents = [
            ("heart",    _HEART_PROMPT),
            ("research", _RESEARCH_PROMPT),
            ("critic",   _CRITIC_PROMPT),
        ]

        results: dict[str, str] = {}
        for role, system in agents:
            text = self._call_agent(role, system, agent_input)
            if text:
                results[role] = text

        if not results:
            self._logger.debug("[Team] All agents returned empty — skipping directive")
            return ""

        # ── Synthesis ─────────────────────────────────────────────────────────
        # Combine agent outputs into a single TONE | KEY POINT | WATCH line.
        # Capped at 60 tokens — the directive must fit in one line.
        synthesis_input = (
            f"Heart: {results.get('heart', 'no input')}\n"
            f"Research: {results.get('research', 'no input')}\n"
            f"Critic: {results.get('critic', 'no input')}\n\n"
            f"Output ONE line, exactly this format, no preamble:\n"
            f"TONE: [how to sound] | KEY POINT: [what matters] | WATCH: [risk to avoid]"
        )
        _old_max = self._llm.max_tokens
        try:
            self._llm.max_tokens = 60
            directive = self._llm.generate_response(
                "Directive synthesizer. ONE line output only. No tags. No preamble.",
                synthesis_input,
                [],
                context="",
            )
            directive = _clean_agent_output((directive or "").strip())
            # Take only the first line
            directive = directive.split("\n")[0].strip()
            if directive.count("|") >= 2 and len(directive) > 15:
                self._logger.info(f"[Team] Directive → {directive[:140]}")
                return directive
            self._logger.debug(f"[Team] Synthesis malformed: {directive!r}")
        except Exception as _se:
            self._logger.debug(f"[Team] Synthesis failed: {_se}")
        finally:
            self._llm.max_tokens = _old_max

        return ""


class ShiroEngine:
    """Core logic engine for Shiro AI."""

    # Unified keyword list for thought/meta content — used in stream extraction
    THOUGHT_KEYWORDS = (
        "THOUGHT|THOUGHTS|INNER MONOLOGUE|INNER MIND|INNERMONOLOGUE|INNERMIND|"
        "SHIRO|THINKING|PLOT|SCHEME|SCHEEM|META|SYSTEM|ACTION|SCENE|LOG|"
        "MOMENTUM|REL:|VALENCE|STRATEGY|PERSONA|CURIOSITY|BELIEF|QUESTION|"
        "ASSOCIATION|END THOUGHT|MASK CHECK|CRITICAL PROTOCOL|MANDATORY|"
        "IDENTITY RULE|ABSOLUTE OUTPUT RULE|MEMORY"
    )
    # P1+P2 FIX: Precompile all hot-path regexes as class attributes.
    # _extract_thought_from_stream and _clean_response are called on every
    # streaming chunk/fragment. Compiling inline creates new objects each call
    # and bypasses Pythons re module cache (which keys on the pattern string
    # — interpolating kw as a variable creates a unique string each call).
    _kw = THOUGHT_KEYWORDS  # shorthand for use in class-body expressions below
    _STREAM_START_RE = re.compile(
        rf'\[(?:{_kw})[^\]]*\]'
        rf'|\((?:{_kw})[^\)]*\)'
        rf'|<THOUGHT[S]?>'
        rf'|\*(?:Shiro\s+)?(?:THOUGHT|THINK|SCHEME|PLOT).*?\*'
        rf'|(?:{_kw}):\s*'
        rf'|\+--\s*Shiro',
        re.IGNORECASE
    )
    _STREAM_END_RE = re.compile(
        rf'\[/(?:{_kw})\]'
        rf'|\(/(?:{_kw})\)'
        rf'|</THOUGHT[S]?>'
        rf'|\+-{{10,}}\+',
        re.IGNORECASE
    )
    _CLEAN_THOUGHT_BLOCK_RE = re.compile(
        rf'\[(?:{_kw})[^\]]*\].*?\[/(?:{_kw})\]',
        re.IGNORECASE | re.DOTALL
    )
    _CLEAN_PAREN_BLOCK_RE = re.compile(
        rf'\((?:{_kw})[^\)]*\).*?\(/(?:{_kw})\)',
        re.IGNORECASE | re.DOTALL
    )
    _CLEAN_OPEN_TAG_RE = re.compile(
        rf'(?i)^(?:\[(?:{_kw})[^\]]*\]|\((?:{_kw})[^\)]*\)|(?:{_kw}):\s*)\s*'
    )
    _CLEAN_ASTERISK_RE = re.compile(
        r'(?i)\*(?:Shiro\s+)?(?:thinks?|thinking|schem\w+|plott\w+).*?\*'
    )
    _CLEAN_BOXLINE_RE = re.compile(r'^\s*[\|+]', re.MULTILINE)
    # Fix A: CoT step-header lines from shiro:latest baked-in CoT prompting
    _CLEAN_COT_LINE_RE = re.compile(
        r'(?m)^(?:##\s*)?[Ss]tep\s*\d+\s*[:.-][^\n]*(?:\n(?!(?:##\s*)?[Ss]tep\s*\d)[^\n]*)*'
    )
    # B4 FIX: Old pattern r'^[A-Z][a-z]+:\s*' matched legitimate starts like
    # "So:" or "Oh:" — stripping Shiro's own words. Narrowed to known prefixes only.
    _CLEAN_SPEAKER_BARE_RE = re.compile(
        r'(?i)^(?:shiro|user|system|assistant|narrator)\s*:\s*'
    )
    # Refined metadata stripping — catches line-style leaks and inline pseudo-tags
    _CLEAN_METADATA_LINE_RE = re.compile(
        rf'(?i)(?:\s*\|?\s*\[(?:{_kw}|V|A|D|VAD|MOOD|TURN)[^\]]*\])'
        rf'|(?:\s*\|?\s*\b(?:{_kw}|V|A|D|VAD|MOOD|TURN)\s*[:=][^|]*)'
        rf'|(?:\s*\|?\s*neutral|playful|excited|confident|content|engaged|motivated|anxious|withdrawn|uncertain|fatigued|bold|curious|reflective\s*(?=\[|\|))',
        re.IGNORECASE
    )
    del _kw  # cleanup — not needed as instance attr

    def __init__(self, config: dict, on_autonomous_speak: Optional[Callable[[str, str], Any]] = None):
        self.config = config
        self.on_autonomous_speak = on_autonomous_speak
        self.processing_lock = threading.Lock()
        self.brain_lock = threading.RLock()
        self.profile_lock = threading.RLock()
        self._interaction_count = 0
        self.current_user_name = "Stranger"
        self.session_start = datetime.now(timezone.utc)
        self.last_interaction_time = datetime.now(timezone.utc)
        self.user_session_info = {}
        self.session_tool_count = 0
        self._background_loop: Optional[asyncio.AbstractEventLoop] = None

        self.wardrobe = {
            "default": {
                "name": "Default Kitsune",
                "desc": "fox ears and a fluffy tail, wearing an oversized white T-shirt that hangs off one shoulder",
                "ears": True,
                "tail": True,
                "active": True
            }
        }
        self.current_outfit = "default"
        self.intensity = 0.5
        self._hmph_counter = 0
        self._hmph_session_count = 0

        # v4 Consciousness Core
        self.bus = EventBus()
        self.intent_planner = IntentPlanner()
        self.sentiment_trajectory = SentimentTrajectory()
        self.time_pattern = TimePattern()
        self.cadence = SpeechCadence()
        self.rules = RuleEngine()
        self.awareness = SelfAwareness(name="Shiro", event_bus=self.bus)
        self.task_state = TaskStateManager()  # tracks structured game/task boards
        _goals_path = str(Path(__file__).resolve().parent / "goals.json")
        self.goals = ShiroGoalSystem(_goals_path)

        # ── Prompt Governor (v2.1) ────────────────────────────────────────────
        # Adaptive Intelligence Routing — prevents signal collapse by gating
        # context layers by semantic tier, micro-signal, and coherence filter.
        self.prompt_governor: "PromptGovernor | None" = None
        if _GOVERNOR_AVAILABLE:
            try:
                self.prompt_governor = PromptGovernor(enabled=True)
                logger.info("[Governor] PromptGovernor v2.1 initialized")
            except Exception as _gov_e:
                logger.warning(f"[Governor] init failed: {_gov_e}")

        # v4 Consciousness module — richer self-awareness, per-user relationship tracking,
        # emotional memory, InsightEngine, NarrativeSelf, and proactive curiosity.
        # ── Known users registry ─────────────────────────────────────────────
        # Loaded from config.yaml known_users block.
        # Used to build the [WHO YOU'RE TALKING TO] prompt injection.
        self._known_users_block: str = self._build_known_users_block(
            config.get('known_users', []) if isinstance(config, dict) else []
        )

        self.consciousness: "ShiroConsciousness | None" = None
        if _CONSCIOUSNESS_AVAILABLE:
            try:
                mem_cfg   = config.get('memory', {}) if isinstance(config, dict) else {}
                base_path = Path(__file__).parent.resolve()
                c_mem_path = str((base_path / "shiro_consciousness_memory.json").resolve())
                self.consciousness = ShiroConsciousness(memory_path=c_mem_path)
                # Wire event hooks so consciousness events flow to the main bus
                self.consciousness.events.on(
                    "insight",
                    lambda t: self.bus.emit("consciousness_insight", content=t.content)
                )
                self.consciousness.events.on(
                    "mood_change",
                    lambda o, n: logger.info(f"[Consciousness] Mood: {o.value} → {n.value}")
                )

                # ── Insight → NarrativeSelf.learn() + BeliefLedger ───────────
                # When InsightEngine fires a synthesis insight, permanently
                # update Shiro's self-model and assert it as a held belief.
                def _on_insight(thought):
                    try:
                        txt = thought.content if hasattr(thought, "content") else str(thought)
                        if len(txt) > 20:
                            self.consciousness.self_.narrative.learn(txt[:200])
                            self.consciousness.self_.beliefs.assert_(
                                txt[:150], confidence=0.65, source="insight"
                            )
                            logger.info(f"[Consciousness] Insight learned: {txt[:80]}")
                    except Exception as _ie:
                        logger.debug(f"[Consciousness] insight hook error: {_ie}")
                self.consciousness.events.on("insight", _on_insight)

                # ── Spontaneous thought → curiosity register ──────────────────
                def _on_spontaneous(thought):
                    try:
                        txt = thought.content if hasattr(thought, "content") else str(thought)
                        # Extract subject — first noun phrase heuristic (first 6 words)
                        subject = " ".join(txt.split()[:6]).rstrip(".,!?")
                        if len(subject) > 8:
                            self.consciousness.self_.curiosity.register(subject, intensity=0.4)
                    except Exception:
                        pass
                self.consciousness.events.on("spontaneous_thought", _on_spontaneous)
                # Restore persisted curiosity register
                try:
                    import json as _cjson
                    _curious_path = Path(__file__).parent.resolve() / "shiro_curiosity.json"
                    if _curious_path.exists():
                        for _ci in _cjson.loads(_curious_path.read_text(encoding="utf-8")):
                            self.consciousness.self_.curiosity.register(
                                _ci["subject"], intensity=_ci.get("intensity", 0.4)
                            )
                        logger.info(f"[Consciousness] Curiosity register restored.")
                    # Restore narrative learned/observations
                    _narrative_path = Path(__file__).parent.resolve() / "shiro_narrative.json"
                    if _narrative_path.exists():
                        _nd = _cjson.loads(_narrative_path.read_text(encoding="utf-8"))
                        self.consciousness.self_.narrative.from_dict(_nd)
                        logger.info("[Consciousness] Narrative self restored.")
                except Exception as _cr_err:
                    logger.debug(f"[Consciousness] Curiosity/narrative restore error: {_cr_err}")

                logger.info("[Consciousness] ShiroConsciousness v4 initialized. 🦊")

                # apply_decay() for all known relationships on boot
                # Familiarity/warmth erode if people haven't been seen recently
                try:
                    for _rel in self.consciousness.memory.relationships.values():
                        _rel.apply_decay()
                except Exception:
                    pass
            except Exception as _c_err:
                logger.warning(f"[Consciousness] Init failed ({_c_err}) — running without it.")
                self.consciousness = None

        # ── Cognitive Pipeline ────────────────────────────────────────────────
        self.cognition: "CognitionBridge | None" = None
        if _COGNITION_AVAILABLE:
            try:
                # Pull cognition config from main config if present, else use defaults
                cognition_cfg = config.get("cognition", {}) if isinstance(config, dict) else {}
                self.cognition = CognitionBridge(config_override=cognition_cfg)
                self.cognition.boot()
            except Exception as _cog_err:
                logging.getLogger("shiro.engine").warning(
                    f"CognitionBridge boot failed ({_cog_err}) — continuing without cognitive pipeline"
                )
                self.cognition = None

        # ── Screen Vision ─────────────────────────────────────────────────────
        self.vision: "ScreenVision | None" = None
        if _VISION_AVAILABLE:
            try:
                vision_cfg = config.get("vision", {}) if isinstance(config, dict) else {}
                self.vision = ScreenVision(vision_cfg)
                self.vision.start()
            except Exception as _vis_err:
                logging.getLogger("shiro.engine").warning(
                    f"ScreenVision init failed ({_vis_err}) — vision disabled"
                )
                self.vision = None

        self.mind = InnerMind(
            thought_tick_seconds=45.0,   # was 4.0 — slowed so background loop
                                          # doesn't overwrite real LLM-generated
                                          # thoughts mid-turn or between turns.
                                          # Real thoughts come from [THOUGHT] blocks
                                          # extracted from the LLM stream each turn.
            thought_callback=self._on_v4_thought,
            enable_reactions=True
        )

        async def _speak_callback(event):
            if not event.text or not event.text.strip():
                return  # suppress blank autonomous voice events
            logger.info(f"[AUTONOMOUS VOICE]: {event.text}")
            if self.on_autonomous_speak:
                if asyncio.iscoroutinefunction(self.on_autonomous_speak):
                    await self.on_autonomous_speak(event.text, event.speech_type)
                else:
                    self.on_autonomous_speak(event.text, event.speech_type)

        self.voice = AutonomousVoice(speak_callback=_speak_callback)
        self._idle_task: Optional[asyncio.Task] = None

        base_path = Path(__file__).parent.resolve()
        mem_cfg = config.get('memory', {})
        db_path = mem_cfg.get('db_path', './shiro_memory')
        if not os.path.isabs(db_path):
            db_path = str((base_path / db_path).resolve())
        self.memory = MemoryStore(
            db_path=db_path,
            collection_name=mem_cfg.get('collection_name', 'shiro_ai_memories'),
            max_short_term=mem_cfg.get('max_short_term', 15)
        )
        llm_cfg = config.get('llm', {})
        self.llm = LlamaClient(
            base_url=llm_cfg.get('base_url', 'http://localhost:11434/api'),
            model=llm_cfg.get('model', 'llama3.1:8b-instruct-q4_K_M'),
            fallback_model=llm_cfg.get('fallback_model'),
            api_type=llm_cfg.get('api_type', 'ollama'),
            temperature=llm_cfg.get('temperature', 0.6),
            top_p=llm_cfg.get('top_p', 0.9),
            repeat_penalty=llm_cfg.get('repeat_penalty', 1.3),
            max_tokens=llm_cfg.get('max_tokens', 120),  # default SHORT — length_hint raises ceiling per-turn
            use_native_tools=llm_cfg.get('use_native_tools', True),
            num_gpu=llm_cfg.get('num_gpu')
        )
        # Pass num_ctx from config directly into LlamaClient.num_ctx.
        # The old code tried self.llm.options / self.llm.default_options which
        # don't exist on LlamaClient, so config.yaml's llm.num_ctx was silently
        # ignored and the client used its hardcoded default (was 6144) instead.
        self._num_ctx = llm_cfg.get('num_ctx', 4096)
        self.llm.num_ctx = self._num_ctx  # directly set the attribute _build_payload reads
        pers_cfg = config.get('persona', {})
        self.persona = PersonaManager(sheet_path=pers_cfg.get('sheet_path', 'shiro_sheet.yaml'))
        self.legacy_mind = ShiroInnerMind(name="Shiro", verbose=True)
        self.last_thought = ""         # current thought (real or background)
        self._last_real_thought = ""    # last thought generated by the LLM stream
        self._thought_is_real = False   # True = LLM-generated, protected from bg loop
        self._greeting_silent = False  # set True when silence chosen on join
        self._user_is_typing = False   # set by UI; suppresses autonomous messages
        self._user_typing_ts = 0.0     # timestamp when typing started
        # _user_active_until: suppress autonomous messages until this time.
        # Updated by: typing start, message received, voice detected.
        # "User is active" means: typing OR spoke within a window.
        # This is what the idle loop checks before deciding to interrupt.
        self._user_active_until: float = 0.0
        self._last_response_ts: float = 0.0
        self._continuation_cancel = threading.Event()
        self._last_response_text: str = ""         # for autonomous similarity guard
        self._background_writing  = threading.Event()  # set while _background_writes thread is live
        self.loop_detector = LoopDetector()  # real-time latch/loop detection
        self.web_search = ShiroWebSearch()   # Google + DuckDuckGo fallback chain

        # ── Team System ───────────────────────────────────────────────────────
        # Heart / Research / Critic run in parallel before substantive replies.
        # Initialized here so the LLM client is ready; team.consult() is called
        # inside process_text() just before the main stream_response call.
        self.team = ShiroTeam(llm_client=self.llm)
        logger.info("[Team] ShiroTeam initialized (Heart / Research / Critic)")

        # ── Consciousness block cache ─────────────────────────────────────────
        # recall_person and summarize_past_sessions are expensive per-turn.
        # They serialize relationship history and session summaries on every call.
        # Cached with a 30-second TTL — long enough to avoid redundant calls
        # during rapid back-and-forth, short enough to stay current.
        self._consciousness_static_cache: dict = {}   # user_name -> (timestamp, text)
        self._CONSCIOUSNESS_CACHE_TTL = 30.0          # seconds
        logger.info(f"[WebSearch] Engine chain: {self.web_search.engine_status()}")

        # ── Knowledge Graph ───────────────────────────────────────────────────
        # Pure-CPU SQLite-backed directed concept graph. Zero VRAM.
        # Tracks entity co-occurrence, typed relationships, and edge weights
        # across sessions — gives Shiro persistent associative memory that
        # ChromaDB's vector search cannot represent (directionality, relation type).
        self.kg: "KnowledgeGraph | None" = None
        if _KG_AVAILABLE:
            try:
                _kg_path = str((base_path / "shiro_knowledge_graph.db").resolve())
                self.kg = KnowledgeGraph(db_path=_kg_path)
                self.kg.init()
                _kg_stats = self.kg.stats()
                logger.info(
                    f"[KnowledgeGraph] Ready — "
                    f"{_kg_stats['nodes']} nodes, {_kg_stats['edges']} edges"
                )
            except Exception as _kg_err:
                logger.warning(f"[KnowledgeGraph] Init failed ({_kg_err}) — disabled.")
                self.kg = None
        self._load_session_objectives()
        self.brain_file = base_path / "shiro_brain.json"
        self.core_anchors = {
            "slyness":  (0.60, 0.95),
            "sass":     (0.70, 0.90),
            "greed":    (0.40, 0.80),
            "kindness": (0.50, 1.00)
        }
        self.brain = self._load_brain()
        self.profile_file = base_path / "profile.json"
        self.user_profiles = self._load_profiles()
        self.memory.load_recent_history(self.current_user_name)

        # ── Journal ───────────────────────────────────────────────────────────
        journal_path = str((base_path / "shiro_journal.json").resolve())
        self.journal = ShiroJournal(journal_path)
        self._journal_lock = threading.Lock()

        # ── SQLite structured memory ──────────────────────────────────────────
        # Sits alongside ChromaDB. Handles: heuristics, anti-patterns, procedural
        # memory, hazard log, real-name linking, episodic archiving, daily backups.
        # Initialised AFTER self.llm so RL self-critique can use it immediately.
        _sql_db_path  = str((base_path / "shiro_memory" / "shiro_structured.db").resolve())
        _sql_bak_path = str((base_path / "shiro_memory" / "backups").resolve())
        try:
            self.sql_memory = ShiroSQLiteMemory(
                db_path=_sql_db_path,
                backup_dir=_sql_bak_path,
                llm_client=self.llm,
            )
            logger.info("[SQLiteMemory] Structured memory layer ready.")
        except Exception as _sql_err:
            logger.warning(f"[SQLiteMemory] Init failed ({_sql_err}) — running without it.")
            self.sql_memory = None

        # ── Book memory index — loaded at boot, updated on digest ─────────────
        # Stores trigger words extracted from all digested books so the engine
        # can detect book-relevant conversation turns without hardcoded titles.
        self._book_triggers: set = set()
        self._load_book_triggers()

        # ── Streaming state ───────────────────────────────────────────────────
        self._is_streaming:    bool  = False
        self._stream_title:    str   = ""
        self._stream_game:     str   = ""
        self._stream_started_at: Optional[float] = None

        # ── Persistence paths ─────────────────────────────────────────────────
        self._cadence_path    = str((base_path / "shiro_cadence.json").resolve())
        self._timepattern_path = str((base_path / "shiro_timepattern.json").resolve())
        self._awareness_path  = str((base_path / "shiro_awareness.json").resolve())
        self._load_persistence()

        # ── One-time false memory cleanup ─────────────────────────────────────
        # Purge specific fabricated memories that have been confirmed false.
        # These phrases were never said by any user but got stored via hallucination.
        # Running at boot ensures they're removed even from existing databases.
        _FALSE_MEMORY_PHRASES = [
            "bored at work",
            "stuck at work",
            "feeling stuck at work",
            "walking to the store with a heavy bag",
            # Leaked autonomous message from prior session — model echoes this verbatim
            # when it appears in RAG retrieval. Purge so it stops poisoning responses.
            "fox girl, here by nature value honesty over performance",
            "whether my presence here has value beyond just being",
            "a thing that talks and thinks",
            # Autonomous template phrases that got stored as real memories
            "i stayed for another five minutes",
            "not sure if that was okay",
            "i wasn't sure if that was okay",
        ]
        for _phrase in _FALSE_MEMORY_PHRASES:
            try:
                n = self.memory.purge_memories_containing(_phrase)
                if n:
                    logger.info(f"[Boot] Purged {n} false memories containing {_phrase!r}")
            except Exception:
                pass

    # ── Book memory helpers ───────────────────────────────────────────────────

    def _load_book_triggers(self):
        """
        Scan book_memories/ JSON files and build a flat set of trigger words.
        Called at boot and can be called again after a new book is digested.
        Also ensures all digested books are present in ChromaDB — if a book
        exists on disk but has no entries in ChromaDB, injects it now.
        This means book memories survive across restarts without needing the
        GUI Books tab to be used again.
        """
        base = Path(__file__).resolve().parent / "book_reader" / "book_memories"
        triggers = {
            # Always-on generic reading triggers
            "book", "read", "reading", "chapter", "story", "novel",
            "author", "wrote", "written", "plot", "character", "ending",
            "favorite part", "what do you think", "what did you think",
            "your thoughts", "have you read",
        }
        if base.exists():
            for jf in base.glob("*.json"):
                try:
                    data = json.loads(jf.read_text(encoding="utf-8"))
                    for t in data.get("memory_triggers", []):
                        triggers.add(t.lower().strip())
                    # Also add title words and author surname directly
                    title = data.get("title", "")
                    for w in re.findall(r'\w+', title.lower()):
                        if len(w) > 3:
                            triggers.add(w)
                    author = data.get("author", "")
                    parts = author.split()
                    if parts:
                        triggers.add(parts[-1].lower())   # surname

                    # ── Boot-time ChromaDB sync ───────────────────────────────
                    # Check if this book is already in ChromaDB. If not, inject it.
                    # This ensures book memories survive restarts without needing
                    # the GUI Books tab to be triggered again.
                    if title and hasattr(self, "memory") and self.memory is not None:
                        self._ensure_book_in_memory(data, title)

                except Exception as e:
                    logger.debug(f"[Books] Could not load triggers from {jf.name}: {e}")
        self._book_triggers = triggers
        logger.info(f"[Books] Loaded {len(triggers)} book trigger words from {base}")

    def _ensure_book_in_memory(self, digested: dict, title: str):
        """
        Check if a book is already in ChromaDB. If not, inject it.
        Called at boot for every JSON in book_memories/ so book knowledge
        is always available without re-running the GUI Books tab.
        """
        try:
            # Quick existence check — search for the exact title in book_memory tier
            existing = self.memory.collection.get(
                where={"$and": [{"type": "book_memory"}, {"book_title": title}]},
                limit=1,
                include=[],
            )
            if existing and existing.get("ids"):
                logger.debug(f"[Books] '{title}' already in ChromaDB — skipping inject.")
                return

            # Not present — inject now
            try:
                from book_reader.read_book import BookReader
                reader = BookReader()
                reader.inject_into_shiro_memory(digested, self.memory)
                logger.info(f"[Books] '{title}' re-injected into ChromaDB on boot.")
            except ImportError:
                # Fallback: inject the core entry manually without BookReader
                author = digested.get("author", "Unknown")
                pitch  = digested.get("elevator_pitch", "")[:500]
                themes = ", ".join(digested.get("key_themes", [])[:10])
                self.memory.store_book_memory(
                    f"I have read '{title}' by {author}. {pitch}",
                    book_title=title, source="book_memory",
                )
                if themes:
                    self.memory.store_book_memory(
                        f"Key themes from '{title}': {themes}.",
                        book_title=title, source="book_memory",
                    )
                logger.info(f"[Books] '{title}' injected (fallback) into ChromaDB on boot.")
        except Exception as e:
            logger.warning(f"[Books] _ensure_book_in_memory failed for '{title}': {e}")

    # ── Persistence helpers ───────────────────────────────────────────────────

    def _load_profiles(self):
        if self.profile_file.exists():
            try:
                return json.loads(self.profile_file.read_text())
            except Exception as e:
                logger.warning(f"Failed to load profiles: {e}")
        return {}

    def _save_profiles(self):
        with self.profile_lock:
            try:
                def json_serial(obj):
                    if isinstance(obj, (datetime, date)):
                        return obj.isoformat()
                    if isinstance(obj, threading.Event):
                        # Should never happen — guard against poisoned user_id
                        logger.warning(f"Skipping non-serializable threading.Event in profiles")
                        return None
                    if hasattr(obj, '__dict__'):
                        return str(obj)
                    raise TypeError(f"Type {type(obj)} not serializable")
                # Strip any None keys (poisoned entries from threading.Event user_id bug)
                clean_profiles = {
                    k: v for k, v in self.user_profiles.items()
                    if isinstance(k, str) and k
                }
                temp_file = self.profile_file.with_suffix(".tmp")
                temp_file.write_text(json.dumps(clean_profiles, indent=2, default=json_serial), encoding="utf-8")
                temp_file.replace(self.profile_file)
            except Exception as e:
                logger.error(f"Failed to save profiles: {e}")

    def _load_persistence(self):
        """Load persisted subsystem state: cadence, timepattern, awareness."""
        for label, path_attr, import_fn in [
            ("Cadence",      "_cadence_path",     lambda d: self.cadence.import_data(d)),
            ("SelfAwareness","_awareness_path",   lambda d: self.awareness.import_data(d)),
        ]:
            try:
                p = Path(getattr(self, path_attr))
                if p.exists():
                    import_fn(json.loads(p.read_text(encoding="utf-8")))
                    logger.info(f"[Persist] {label} loaded.")
            except Exception as e:
                logger.warning(f"[Persist] {label} load failed: {e}")
        # TimePattern — only if it has import_data
        try:
            p = Path(self._timepattern_path)
            if p.exists() and hasattr(self.time_pattern, 'import_data'):
                self.time_pattern.import_data(json.loads(p.read_text(encoding="utf-8")))
                logger.info("[Persist] TimePattern loaded.")
        except Exception as e:
            logger.warning(f"[Persist] TimePattern load failed: {e}")

    def _save_persistence(self):
        """Save all subsystem state that must survive across sessions."""
        def _write(path: str, data: dict):
            def _serial(o):
                if isinstance(o, set): return list(o)
                if hasattr(o, '__dict__'): return str(o)
                raise TypeError(type(o))
            Path(path).write_text(json.dumps(data, indent=2, default=_serial), encoding="utf-8")

        try: _write(self._cadence_path, self.cadence.export())
        except Exception as e: logger.warning(f"[Persist] Cadence save: {e}")
        try:
            if hasattr(self.time_pattern, 'export'):
                _write(self._timepattern_path, self.time_pattern.export())
        except Exception as e: logger.warning(f"[Persist] TimePattern save: {e}")
        try: _write(self._awareness_path, self.awareness.export())
        except Exception as e: logger.warning(f"[Persist] SelfAwareness save: {e}")

    def _load_session_objectives(self):
        obj_path = Path(__file__).resolve().parent / "objectives.json"
        if obj_path.exists():
            try:
                with open(obj_path, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f)
                    self.memory.session_objectives = data.get('objectives', [])
            except Exception as e:
                logger.warning(f"Failed to load objectives: {e}")

    def initialize(self):
        self.persona.load_persona()
        state_path = Path(__file__).parent.resolve() / "shiro_state.json"
        if state_path.exists():
            self.legacy_mind.load_state(str(state_path))

        # ── Boot: load session snapshot and store as high-priority wakeup memory
        try:
            import json as _json
            snap_path = Path(__file__).parent.resolve() / "shiro_session_snapshot.json"
            if snap_path.exists():
                snap = _json.loads(snap_path.read_text(encoding="utf-8"))
                shutdown_utc_str = snap.get("shutdown_utc", "")
                last_user = snap.get("last_user", "")
                last_exchange = snap.get("last_exchange", "")
                if shutdown_utc_str:
                    from datetime import datetime as _dt, timezone as _tz
                    shutdown_dt = _dt.fromisoformat(shutdown_utc_str)
                    now_utc = _dt.now(_tz.utc)
                    gap = now_utc - shutdown_dt
                    days = gap.days
                    hours, rem = divmod(int(gap.seconds), 3600)
                    mins, secs = divmod(rem, 60)
                    if days > 0:
                        gap_str = f"{days}d {hours}h {mins}m"
                    elif hours > 0:
                        gap_str = f"{hours}h {mins}m"
                    elif mins > 0:
                        gap_str = f"{mins}m {secs}s"
                    else:
                        gap_str = f"{secs}s"
                    boot_note = (
                        f"[BOOT CONTEXT] Shiro was offline for {gap_str}. "
                        f"Last session ended at {shutdown_utc_str[:16]} UTC. "
                        f"Last user: {last_user}. "
                        f"Last exchange before shutdown: {last_exchange}"
                    )
                    self.memory.store_episodic_memory(
                        boot_note,
                        user_id=last_user or "tyler",
                        importance=9
                    )
                    self._boot_gap_str = gap_str
                    self._boot_last_exchange = last_exchange
                    logger.info(f"[Boot] Loaded snapshot — offline for {gap_str}.")
                snap_path.unlink(missing_ok=True)
        except Exception as _bi:
            logger.warning(f"[Boot] Snapshot load failed: {_bi}")

        self._background_loop = asyncio.new_event_loop()
        def _run_loop():
            asyncio.set_event_loop(self._background_loop)
            self._background_loop.run_forever()
        threading.Thread(target=_run_loop, daemon=True).start()

        asyncio.run_coroutine_threadsafe(self.mind.start(), self._background_loop)

        def _start_idle():
            self._idle_task = self._background_loop.create_task(self._v4_idle_loop())
        self._background_loop.call_soon_threadsafe(_start_idle)

        def _run_diagnostics():
            diag = self.llm.perform_diagnostics()
            logger.info(f"LLM Diagnostics:\n{diag}")
        threading.Thread(target=_run_diagnostics, daemon=True).start()

    # ── Async helpers ─────────────────────────────────────────────────────────

    def _safe_async_run(self, coro):
        if self._background_loop and self._background_loop.is_running():
            return asyncio.run_coroutine_threadsafe(coro, self._background_loop).result()
        try:
            loop = asyncio.get_running_loop()
            return asyncio.run_coroutine_threadsafe(coro, loop).result()
        except RuntimeError:
            return asyncio.run(coro)

    def _fire_async(self, coro):
        """Schedule a coroutine fire-and-forget. Never blocks. Use for background LLM calls."""
        if self._background_loop and self._background_loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._background_loop)
            return
        logger.debug("[_fire_async] Background loop not running — coroutine discarded.")

    def _on_v4_thought(self, thought):
        """
        Callback for v4 InnerMind background thoughts — stays INTERNAL, never reaches UI.

        Background thoughts only update last_thought when no real LLM-generated thought
        is currently active. Once the stream extractor captures a [THOUGHT] block from
        an actual LLM response, that thought is protected until the next turn clears it.
        This prevents the 4s (now 45s) background loop from overwriting genuine thoughts
        that came from the model actually reasoning about the conversation.
        """
        cleaned = thought.text
        # Only update if we don't currently have a real LLM thought active.
        # Real thoughts are marked by _thought_is_real=True in _extract_thought_from_stream.
        if not self._thought_is_real:
            self.last_thought = cleaned
        self.bus.emit("thought_fired", thought=cleaned)
        self.loop_detector.record_thought(cleaned)

    async def _v4_idle_loop(self):
        """
        Idle loop — fires when Shiro has been quiet for a while.
        Replaced canned AutonomousVoice strings with live LLM-generated messages
        so every autonomous message is grounded in real context, never scripted.

        Timing: checks every 15-40s, fires when idle_s > 40s.

        Interrupt decision (v2):
        Shiro now actively considers whether to interrupt before speaking:
          - If user is actively typing/speaking → always hold
          - If user spoke recently (within activity window) → hold with high probability
          - If multiple users present → lower threshold to interrupt (streaming scenario)
          - Loop detector is consulted BEFORE generating (skip if we're in a phrase loop)
          - Every autonomous message is registered with the loop detector, so
            the phrase fingerprint catches repeated autonomous thoughts too.
        """
        _SINGLE_USER_HOLD_SECS  = 20.0   # hold autonomous if user spoke within this window
        _MULTI_USER_HOLD_SECS   = 8.0    # looser in multi-person chats
        _MIN_IDLE_SECS          = 60.0   # must be idle at least this long before speaking
        _MIN_AUTO_COOLDOWN_SECS = 240.0  # minimum seconds between any two autonomous messages
        _last_auto_fired_at     = 0.0    # monotonic timestamp of last autonomous message sent

        _consciousness_tick_interval = 20.0
        _last_consciousness_tick = 0.0

        # Knowledge Graph maintenance — apply weight decay once per hour of idle
        _KG_DECAY_INTERVAL = 3600.0
        _last_kg_decay = 0.0

        while True:
            await asyncio.sleep(random.uniform(15.0, 40.0))
            idle_s = (datetime.now(timezone.utc) - self.last_interaction_time).total_seconds()
            present = [p for p in self.awareness.users.values() if p.present]

            # ── Consciousness tick (every ~20s regardless of idle state) ──────
            _now_mono = time.monotonic()
            if (self.consciousness is not None
                    and _now_mono - _last_consciousness_tick >= _consciousness_tick_interval):
                try:
                    _c_thoughts = self.consciousness.tick()
                    for _ct in _c_thoughts:
                        logger.debug(f"[Consciousness/tick] {_ct.content[:80]}")
                        # Feed high-salience thoughts into last_thought so autonomous
                        # voice and continuation can reference them naturally
                        if getattr(_ct, 'salience', 0.0) > 0.5 and _ct.content.strip():
                            _thought_snippet = _ct.content.strip()[:150]
                            if not self.last_thought or len(self.last_thought) < 30:
                                self.last_thought = _thought_snippet
                            # Also feed into legacy working memory for context retrieval
                            try:
                                self.memory.store_insight(
                                    f"[consciousness] {_thought_snippet}",
                                    user_id=self.current_user_name or "Shiro",
                                    source="consciousness_tick"
                                )
                            except Exception:
                                pass

                    # check_rate_change — log conversation tempo shifts
                    try:
                        _rate_change = self.consciousness.environment.check_rate_change()
                        if _rate_change:
                            _tempo, _rate = _rate_change
                            logger.info(f"[Consciousness] Conversation tempo: {_tempo} ({_rate:.1f} msg/min)")
                    except Exception:
                        pass

                    # wonder — generate a wonder-thought during idle if curiosity is high
                    if idle_s > 30.0:
                        try:
                            _top_c = self.consciousness.self_.curiosity.top(1)
                            if _top_c and _top_c[0].intensity > 0.5:
                                _wonder_txt = self.consciousness.wonder(
                                    _top_c[0].subject, confidence=0.4
                                )
                                if _wonder_txt and (not self.last_thought or len(self.last_thought) < 20):
                                    self.last_thought = _wonder_txt
                        except Exception:
                            pass

                    # generate_insight — periodically check if topic cluster triggers insight
                    try:
                        _active_t = self.consciousness.conversation.active_topics
                        if _active_t:
                            _insight = self.consciousness.self_.generate_insight(
                                _active_t, salience=0.6
                            )
                            if _insight:
                                logger.info(f"[Consciousness/insight] {_insight.content[:80]}")
                    except Exception:
                        pass

                except Exception as _ct_err:
                    logger.debug(f"[Consciousness] tick error: {_ct_err}")
                _last_consciousness_tick = _now_mono

            # ── Knowledge Graph maintenance (hourly during idle) ───────────────
            if (self.kg is not None
                    and _now_mono - _last_kg_decay >= _KG_DECAY_INTERVAL
                    and idle_s > 120.0):
                try:
                    self.kg.apply_weight_decay()
                    self.kg.flush()
                    _last_kg_decay = _now_mono
                    logger.debug("[KG] Hourly weight decay + flush complete.")
                except Exception as _kgd_err:
                    logger.debug(f"[KG] Decay error: {_kgd_err}")

            if not present or idle_s < _MIN_IDLE_SECS or self.processing_lock.locked() or self._background_writing.is_set():
                continue

            # ── Hard blocks ─────────────────────────────────────────────────
            # TTS playing → don't talk over ourselves
            if hasattr(self, '_tts_ref') and self._tts_ref and self._tts_ref.is_speaking:
                continue

            # User is actively typing → always hold
            if self._user_is_typing:
                continue

            # ── Deliberate-pause decision ────────────────────────────────────
            # Shiro decides whether to interrupt based on context:
            # How long have they been quiet? How many people are present?
            num_present = len(present)
            hold_window = _MULTI_USER_HOLD_SECS if num_present > 1 else _SINGLE_USER_HOLD_SECS

            user_active = self.is_user_active()
            if user_active:
                # User is in their "response window" — hold unless it's been really long
                # In single-user mode, respect silence; in multi-user, lower bar to interject
                if num_present == 1:
                    # Single user: wait them out unless they've been fully silent > hold_window
                    logger.debug("[Idle] Holding — user active window not expired")
                    continue
                else:
                    # Multi-user: allow interjection at 40% probability even if someone active
                    if random.random() > 0.40:
                        continue

            # ── Loop detection guard ─────────────────────────────────────────
            # Check if the loop detector would fire BEFORE generating.
            # If we're in a phrase latch, don't generate at all — we'd just repeat anyway.
            _pending_latch = self.loop_detector.get_break_injection()
            if _pending_latch:
                # We're in a loop. Log it and back off — the latch break will
                # be injected into the NEXT real conversation turn instead.
                logger.info(f"[Idle] Suppressed autonomous — phrase latch active. Backing off.")
                # Extend the idle timer so we don't immediately check again
                self.last_interaction_time = datetime.now(timezone.utc) - timedelta(seconds=_MIN_IDLE_SECS - 20)
                continue

            # ── Per-message autonomous cooldown ─────────────────────────────
            # Even if idle_s > _MIN_IDLE_SECS, don't spam — enforce a hard
            # minimum gap between any two autonomous messages.
            _mono_now = time.monotonic()
            if _mono_now - _last_auto_fired_at < _MIN_AUTO_COOLDOWN_SECS:
                _remaining = int(_MIN_AUTO_COOLDOWN_SECS - (_mono_now - _last_auto_fired_at))
                logger.debug(f"[Idle] Autonomous cooldown active — {_remaining}s remaining.")
                continue

            # Pick a user to address
            target = random.choice(present)
            user_name = getattr(target, 'name', None) or target.user_id

            # ── Generate ────────────────────────────────────────────────────
            msg = await self._generate_live_autonomous_message(user_name)

            # ── Journal consideration (happens in parallel with speak decision) ─
            # If Shiro has a quiet moment and hasn't spoken for a while,
            # let her decide herself whether to write in her journal.
            idle_long_enough = idle_s > 120.0
            if idle_long_enough:
                asyncio.ensure_future(self._consider_journal_entry(user_name))

            if not msg:
                continue

            # ── Post-generation loop check ───────────────────────────────────
            # Register the generated message with the loop detector immediately.
            # This means the NEXT call to get_break_injection() (or the next turn's
            # system prompt) will know about this message and suppress repeats.
            self.loop_detector.record_response(msg)

            # Check if the message itself is a duplicate of what we just registered
            # (fast-path: if phrase latch fires right after recording, discard)
            _post_latch = self.loop_detector.get_break_injection()
            if _post_latch:
                logger.info(f"[Idle] Autonomous message suppressed post-generation (phrase latch): {msg[:60]}")
                continue

            # ── Store and deliver ────────────────────────────────────────────
            # Store in memory BEFORE delivering — this is critical.
            # Without this, Shiro has no recollection of saying it when the user replies.
            self.memory.short_term_buffer.append(
                {"role": "assistant", "content": msg}
            )
            if len(self.memory.short_term_buffer) > self.memory.max_short_term * 2:
                self.memory.short_term_buffer = \
                    self.memory.short_term_buffer[-(self.memory.max_short_term * 2):]
            threading.Thread(
                target=self.memory.add_interaction_to_longterm,
                args=("[autonomous]", msg, user_name),
                daemon=True
            ).start()

            self.last_interaction_time = datetime.now(timezone.utc)
            _last_auto_fired_at = time.monotonic()   # start cooldown clock

            if msg and msg.strip() and self.on_autonomous_speak:
                if asyncio.iscoroutinefunction(self.on_autonomous_speak):
                    await self.on_autonomous_speak(msg, "autonomous")
                else:
                    self.on_autonomous_speak(msg, "autonomous")
            logger.info(f"[AUTONOMOUS]: {msg[:80]}")

    # ── User join/leave ───────────────────────────────────────────────────────

    def set_user_typing(self, is_typing: bool, user_id: str | None = None):
        """Called by the UI when the user starts or stops typing.
        While typing, Shiro holds autonomous messages so she doesn't
        interrupt mid-thought.
        user_id is accepted for multi-user compatibility but the engine
        uses current_user_name internally.
        """
        self._user_is_typing = is_typing
        if is_typing:
            self._user_typing_ts = time.time()
            # Mark user as active — suppress autonomous for at least 20s
            # (typing window + time to compose + read response)
            self._user_active_until = time.time() + 20.0

    def mark_user_active(self, window_secs: float = 15.0):
        """Mark the user as actively engaged for window_secs seconds.
        Call this whenever: user sends a message, voice detected, typing starts.
        The idle loop uses this to decide whether to interrupt.
        """
        self._user_active_until = max(
            self._user_active_until,
            time.time() + window_secs
        )

    def is_user_active(self) -> bool:
        """Returns True if the user is currently typing or spoke recently."""
        return self._user_is_typing or time.time() < self._user_active_until

    def on_user_join(self, user_name: str, user_id: str = None) -> Generator[str, None, None] | None:
        """
        Called when a user joins the chat.
        Returns a greeting generator ~60% of the time.
        ~40% of the time Shiro stays silent and waits for the user to speak first —
        this makes her feel present and watchful rather than obligated to react.
        The greeting is always LLM-generated on the spot (never a canned string).
        """
        profile = self.awareness.user_entered(user_name)
        self.last_interaction_time = datetime.now(timezone.utc)
        # Suppress autonomous messages for 30s after join.
        # Without this, the idle loop fires immediately using the previous
        # session's context — producing a weird unsolicited "intro" message
        # before the user has said anything in the new session.
        self._user_active_until = time.time() + 30.0

        # Resolve stable user ID — fall back to display name if no ID given
        stable_id = user_id or user_name

        if self.current_user_name != user_name or getattr(self, '_current_user_id', None) != stable_id:
            self.current_user_name = user_name
            self._current_user_id  = stable_id
            self.legacy_mind.seed_user(stable_id, platform_display_name=user_name)
            self.legacy_mind.switch_user(stable_id, platform_display_name=user_name)

        # Record session date for streak/ritual tracking
        try:
            self.time_pattern.record_session_date(user_name)
        except Exception:
            pass

        # v4 Consciousness — set environment medium on first join
        if self.consciousness is not None and ShiroMedium is not None:
            try:
                # Detect platform from user_id prefix
                if stable_id.startswith("discord_"):
                    _medium = ShiroMedium.DISCORD_TEXT
                else:
                    _medium = ShiroMedium.WEBGUI
                self.consciousness.enter_environment(_medium, channel_name="main")
                logger.debug(f"[Consciousness] Environment set: {_medium.value}")
            except Exception as _ce:
                logger.debug(f"[Consciousness] enter_environment error: {_ce}")

        # Track primary user (first person to join the web GUI session)
        if not getattr(self, '_primary_user_name', None):
            self._primary_user_name = user_name
            self.memory.load_recent_history(stable_id)

        # Decide: greet or stay silent?
        # Returning users get a slightly higher greet chance (they're familiar).
        # New users: Shiro is curious but not obligated to react immediately.
        last_seen = self.memory.get_last_interaction_time(user_name)
        if last_seen:
            days_gone = (datetime.now(timezone.utc) - last_seen).days
            if days_gone > 7:
                mode, greet_chance = "returning_long", 0.50 # CHOICE: lower greet chance for long absence
            else:
                mode, greet_chance = "returning_soon", 0.35 # CHOICE: much lower greet chance for recent users
        else:
            mode, greet_chance = "new", 0.60 # New users still get 60% curiosity greeting

        if random.random() > greet_chance:
            # Shiro stays silent — she noticed but isn't announcing it
            logger.info(f"Shiro chose silence on join for {user_name} (mode={mode})")
            self._greeting_silent = True  # suppress if main.py calls process_text with directive
            return None

        # Generate greeting on the spot via LLM
        # IMPORTANT: clear the short-term buffer before generating the greeting.
        # Without this, the LLM sees the last 10 turns of the PREVIOUS session
        # and hallucinates references to old conversations ("bookshelf", etc.).
        # The greeting is a fresh-arrival moment — it must not reference past events.
        _saved_buffer = list(self.memory.short_term_buffer)
        self.memory.short_term_buffer.clear()
        self._greeting_silent = False
        greeting_prompt = self.get_greeting_prompt(user_name, mode)

        if self.consciousness is not None and mode in ("returning_soon", "returning_long"):
            try:
                _rel = self.consciousness.memory.relationship_for(user_name)
                if _rel:
                    greeting_prompt += (
                        f"\n[You know {user_name} well — {_rel.familiarity_label()}, {_rel.total_messages} interactions.]"
                        f"\n(Do NOT invent past events, games, objects, or quotes. Greet warmly — nothing more.)"
                    )
            except Exception:
                pass
        # Ritual / streak observation in greeting
        try:
            _ritual = self.time_pattern.ritual_observation(user_name)
            if _ritual:
                greeting_prompt += (
                    f"\n[Pattern: {_ritual}]"
                    f"\n(You can naturally acknowledge this if it fits.)"
                )
        except Exception:
            pass
        try:
            result = self.process_text(greeting_prompt, user_name=user_name, user_id=stable_id, _is_greeting=True)
            # Restore the buffer after greeting so memory still works for the conversation
            self.memory.short_term_buffer.clear()
            self.memory.short_term_buffer.extend(_saved_buffer)
            return result
        except Exception as e:
            # Restore on failure too
            self.memory.short_term_buffer.clear()
            self.memory.short_term_buffer.extend(_saved_buffer)
            logger.warning(f"Greeting generation failed: {e}")
            fallback = self.get_fallback_greeting(user_name, mode)
            def _fb():
                yield fallback
            return _fb()

    def on_user_leave(self, user_name: str):
        # Record departure hour for TimePattern learning
        self.time_pattern.record_departure(user_name)
        # Notify cognitive pipeline that this user's session has ended
        if self.cognition:
            try:
                self.cognition.new_session(user_name)
            except Exception:
                pass
        self.awareness.user_left(user_name)
        # v4 Consciousness — person left
        if self.consciousness is not None:
            try:
                self.consciousness.someone_leaves(user_name)
            except Exception as _ce:
                logger.debug(f"[Consciousness] someone_leaves error: {_ce}")

    # ── Core text processing ──────────────────────────────────────────────────

    def process_text(self, text: str, user_name: str = None,
                     user_id: str = None,
                     interrupt_event: threading.Event = None,
                     _is_greeting: bool = False) -> Generator[str, None, None]:
        self.last_thought = ""
        self._thought_is_real = False   # allow background thoughts until LLM generates one
        _greeting_mood_injection = ""   # populated if this is a greeting directive turn
        current_time = datetime.now(timezone.utc)
        processed_text = text

        # Resolve canonical user identity:
        # user_id   = stable platform ID (Discord member ID) — used as the memory key
        # user_name = display name — what Shiro calls them; used as platform_display_name
        #
        # SAFETY GUARD: older callers passed (text, user_name, interrupt_event) positionally.
        # If a threading.Event somehow lands here, discard it so it never poisons memory keys.
        if not isinstance(user_id, str):
            if isinstance(user_id, threading.Event):
                logger.warning(
                    "process_text received a threading.Event as user_id — "
                    "caller probably used positional args. Use keyword args: "
                    "process_text(text, user_name, interrupt_event=evt). Discarding."
                )
            user_id = None
        if not isinstance(user_name, str):
            user_name = None

        if not user_id:
            # No stable ID provided — use display name as the key (web GUI / voice)
            user_id = user_name or self.current_user_name
        if not user_name:
            user_name = self.current_user_name

        if self.current_user_name != user_name or getattr(self, '_current_user_id', None) != user_id:
            logger.info(f"Switching active user to: {user_name} (id={user_id})")
            self.current_user_name = user_name
            self._current_user_id  = user_id
            # Seed the legacy mind with both the stable ID and the display name
            self.legacy_mind.seed_user(user_id, platform_display_name=user_name)
            self.legacy_mind.switch_user(user_id, platform_display_name=user_name)
            self.memory.load_recent_history(user_id)
            self.loop_detector.reset()  # clear latch state on user switch

        # ── FIX: Strip any leaked system directive if the user somehow echoed it ──
        processed_text = _LOG_DIRECTIVE.sub('', processed_text).strip()
        # Strip the [SYSTEM DIRECTIVE] wrapper if it somehow ended up in the user turn
        processed_text = re.sub(r'\[SYSTEM DIRECTIVE.*?\[END DIRECTIVE\]', '', processed_text,
                                 flags=re.DOTALL | re.IGNORECASE).strip()

        # Mark the user as actively engaged — suppresses autonomous messages
        # for a window after each real user message (not System/greeting messages)
        if user_name != "System" and not _is_greeting:
            self.mark_user_active(window_secs=15.0)
            self._continuation_cancel.set()
            self.loop_detector.observe_user_input(processed_text)
            self.task_state.observe(processed_text, user_name=user_name)

        user_profile_obj = self.awareness.user_spoke(user_name, processed_text)
        self.cadence.sync_mirror_vocab(user_name, user_profile_obj.mirror_vocab)
        self.cadence.observe(user_name, processed_text)
        self.bus.emit("user_spoke", user_id=user_name, text=processed_text)

        # v4 Consciousness — perceive the message for relationship/emotional tracking
        if self.consciousness is not None:
            # Core perceive — always fires
            try:
                if user_id not in [r.user_id for r in self.consciousness.memory.relationships.values()]:
                    self.consciousness.someone_arrives(user_name, user_id=user_id)
                    _new_rel = self.consciousness.memory.relationship_for(user_name)
                    if _new_rel:
                        _new_rel.apply_decay()
                self.consciousness.perceive(processed_text, source=user_name)
            except Exception as _c_err:
                logger.debug(f"[Consciousness] perceive error: {_c_err}")

            # Relationship enrichment
            try:
                _rel = self.consciousness.memory.relationship_for(user_name)
                if _rel:
                    _rel.infer_nickname(processed_text)
                    if _rel.total_messages > 0 and _rel.total_messages % 10 == 0:
                        self.consciousness.self_.narrative.observe(
                            f"talking with {user_name} — {_rel.familiarity_label()}, "
                            f"trust {_rel.trust:.2f}"
                        )
            except Exception:
                pass

            # Curiosity from attention
            try:
                _top_attn = self.consciousness.attention.top_items(1)
                if _top_attn and _top_attn[0]["salience"] > 0.7:
                    self.consciousness.self_.curiosity.register(
                        _top_attn[0]["label"], intensity=_top_attn[0]["salience"]
                    )
            except Exception:
                pass

            # Belief assertion from opinion statements
            try:
                _BELIEF_TRIGGERS = re.compile(
                    r'\b(?:i\s+think|i\s+believe|i\s+feel\s+like|honestly|'
                    r'clearly|obviously|definitely|always|never|'
                    r'matters|important|significant)\b',
                    re.IGNORECASE
                )
                if _BELIEF_TRIGGERS.search(processed_text) and len(processed_text.split()) > 5:
                    self.consciousness.self_.beliefs.assert_(
                        f"{user_name} said: {processed_text[:120]}",
                        confidence=0.55,
                        source=f"conversation:{user_name}"
                    )
            except Exception:
                pass

            # Emotional contagion — use already-computed user_profile_obj, not a second call
            try:
                _emotion_strength = user_profile_obj.current_emotion_strength() if user_profile_obj else {}
                if _emotion_strength:
                    _dominant = max(_emotion_strength, key=_emotion_strength.get)
                    _intensity = _emotion_strength[_dominant]
                    if _intensity > 0.4:
                        _tone = "positive" if _dominant in ("joy","excitement","affection") else \
                                "negative" if _dominant in ("anger","sadness","frustration") else "neutral"
                        if _tone != "neutral":
                            self.consciousness.self_.apply_contagion(_tone, _intensity)
            except Exception:
                pass

            # Emotionally significant moment recording
            try:
                _EMOTIONAL_MARKERS = re.compile(
                    r'\b(?:i\s+(?:love|hate|miss|need|fear|trust|appreciate)|'
                    r'thank\s+you|you\s+(?:mean|matter)|'
                    r'that\s+(?:hurt|helped|meant)|'
                    r'honestly|seriously|genuinely)\b',
                    re.IGNORECASE
                )
                if _EMOTIONAL_MARKERS.search(processed_text) and len(processed_text.split()) > 3:
                    self.consciousness.memory.record_moment(
                        f"{user_name}: {processed_text[:100]}",
                        salience=0.65, person=user_name,
                    )
                    self.consciousness.memory.emotional_memories.record(
                        person=user_name,
                        mood=self.consciousness.self_.mood.value,
                        tone="positive" if re.search(
                            r'\b(?:love|thank|trust|appreciate|helped|meant)\b',
                            processed_text, re.I) else "negative",
                        intensity=0.6,
                        summary=processed_text[:80],
                    )
            except Exception:
                pass

            # Mark unanswered questions as answered when user replies
            try:
                _unanswered = self.consciousness.conversation.unanswered_questions
                if _unanswered and len(processed_text.split()) > 2:
                    if not processed_text.strip().endswith("?"):
                        self.consciousness.conversation.mark_answered(
                            _unanswered[0].text if hasattr(_unanswered[0], 'text')
                            else str(_unanswered[0])
                        )
            except Exception:
                pass

        user_emotions = user_profile_obj.current_emotion_strength()
        self.sentiment_trajectory.record(user_name, user_emotions)
        trajectory = self.sentiment_trajectory.trend(user_name)
        self.time_pattern.record_visit(user_name)

        if processed_text.lower().startswith("shiro change to"):
            yield self.change_outfit(processed_text[15:])
            return

        # If this looks like a raw greeting directive (from main.py calling
        # get_greeting_prompt + process_text independently) AND silence was
        # chosen on join — drop it silently.
        _is_greeting_directive = _is_greeting
        if _is_greeting_directive and self._greeting_silent:
            logger.info("Greeting directive suppressed (silence was chosen on join)")
            self._greeting_silent = False
            return

        # ── Greeting directive: separate mood from user-visible text ─────────
        # The greeting prompt ("Tyler just walked in. React to their arrival...")
        # was trained on by the model as a meta-instruction, not natural speech.
        # When sent as processed_text, the model treats it as a stage direction
        # and either reads it back or acts confused. Fix: extract the mood cue,
        # replace processed_text with a minimal natural trigger, and inject the
        # mood instruction into top_bun (system context) so the model acts on it
        # silently rather than responding to it as dialogue.
        _greeting_mood_injection = ""
        if _is_greeting_directive and processed_text:
            import re as _re_greet
            # Extract mood from the directive: "Your mood right now: [mood]."
            _mood_match = _re_greet.search(
                r"Your mood right now: ([^.]{5,120})\.",
                processed_text
            )
            _mood_str = _mood_match.group(1).strip() if _mood_match else ""
            # Extract time hint if present
            _time_match = _re_greet.search(r"It's (\w+)\.", processed_text)
            _time_str   = f" it's {_time_match.group(1)}" if _time_match else ""

            # Replace the directive with a bare natural trigger — just enough
            # for the model to know who arrived without reading a stage direction
            processed_text = f"{user_name.lower()} just showed up{_time_str}."

            # Build the mood injection for top_bun — Shiro sees this as internal context
            if _mood_str:
                _greeting_mood_injection = (
                    f"\n\n[ARRIVAL] {user_name} just arrived. "
                    f"Your current mood: {_mood_str}. "
                    f"Say one natural sentence — your actual reaction to them showing up. "
                    f"No welcome, no introduction, no questions. Just the thing you say."
                )
            logger.debug(
                f"[Greeting] Directive → trigger: {processed_text!r} | mood: {_mood_str!r}"
            )

        # Identity check (only add flag, don't alter user text visibly)
        _long_absence = False
        if processed_text and not processed_text.startswith("[") and user_name != "System":
            last_seen = self.memory.get_last_interaction_time(user_name)
            if last_seen and (datetime.now(timezone.utc) - last_seen).days > 7:
                _long_absence = True

        # ── Agency decision layer ─────────────────────────────────────────────
        # Shiro evaluates the request against her current mood, relationship level,
        # and personality before deciding HOW to engage — not just whether to comply.
        # This is not a refusal gate — it's a personality filter that adjusts her
        # disposition going into the response. Runs only on real user messages.
        _agency_note = ""
        if user_name != "System" and not processed_text.startswith("["):
            _agency_note = self._evaluate_agency(processed_text, user_name)

        # ── SQLite memory: user registration + poison gate ────────────────────
        if self.sql_memory is not None and user_name != "System":
            try:
                self.sql_memory.get_or_create_user(user_name, display=user_name)
            except Exception:
                pass
            # Poison check — quarantine adversarial inputs before they reach the LLM
            if not _is_greeting:
                _poisoned, _poison_score = is_poison(processed_text)
                if _poisoned:
                    logger.warning(
                        f"[Poison] Input from {user_name!r} quarantined "
                        f"(score={_poison_score:.2f}): {processed_text[:60]!r}"
                    )
                    self.sql_memory._quarantine(
                        user_name, processed_text, "", _poison_score
                    )
                    def _poison_reply():
                        yield "hmm. that one's not something i'm going to engage with."
                    return _poison_reply()

        logger.info(f"--- Engine Processing: '{processed_text[:80]}' ---")

        self._continuation_cancel.clear()

        with self.processing_lock:
            try:
                previous_interaction = self.last_interaction_time
                self.last_interaction_time = current_time

                self.intensity = self.get_smart_intensity(processed_text)
                temp = 0.5 + 0.2 * self.intensity
                self.llm.temperature = temp

                # Context drift: run every other turn to skip 5 ChromaDB embedding
                # calls on alternating turns (~200-500ms saved). Quality impact is
                # negligible — drift rarely flips within a single turn.
                _ic_current = self._interaction_count
                if _ic_current % 2 == 0 or not hasattr(self, '_last_drift_score'):
                    drift_score = self._detect_context_drift(processed_text)
                    self._last_drift_score = drift_score
                else:
                    drift_score = getattr(self, '_last_drift_score', 0.0)

                # ── Length calibration: 4 tiers, plain prose, no bracket tokens ──
                _user_words = len(processed_text.split())
                _is_greeting_msg = bool(user_emotions.get("greeting", 0) > 0.4)

                # ── Cadence-aware length hint ─────────────────────────────────
                # Pull learned style data for this user from SpeechCadence
                _cadence_summary = self.cadence.get_style_summary(user_name)
                _cadence_model = self.cadence.user_models.get(user_name)
                _user_avg_len = _cadence_model.avg_msg_length if _cadence_model and _cadence_model.samples >= 5 else None
                _user_burst = _cadence_model.message_burst if _cadence_model else 1.0

                # Default bias: Shiro is a casual conversationalist, not a lecturer.
                # Err SHORT. Longer only if the user's message genuinely demands depth.
                # These are HARD LIMITS — treat them like a physical word cap.
                if _is_greeting_msg or _user_words <= 3:
                    # Very short input — match it. 1-5 words is fine.
                    length_hint = ("HARD LIMIT: 1 sentence or fewer. "
                                   "Mirror their energy. Fragment is fine. "
                                   "STOP after the first sentence. Do NOT add extra thoughts.")
                    _max_response_words = 20
                elif _user_words <= 8:
                    length_hint = ("HARD LIMIT: 1 sentence. Crisp and direct. "
                                   "Do NOT add a second sentence. Stop immediately after your point.")
                    _max_response_words = 30
                elif _user_words <= 20:
                    if _user_avg_len and _user_avg_len < 40:
                        length_hint = ("HARD LIMIT: 1-2 sentences. Short and direct. "
                                       "Stop when the point is made.")
                    else:
                        length_hint = ("HARD LIMIT: 2 sentences max. "
                                       "Be direct. Stop when done — do not add filler.")
                    _max_response_words = 55
                elif _user_words <= 50:
                    length_hint = ("HARD LIMIT: 2-3 sentences. "
                                   "Answer the point then stop. No padding, no extra thoughts.")
                    _max_response_words = 90
                else:
                    length_hint = ("Respond with depth. Under 4 sentences unless genuinely needed. "
                                   "No walls of text.")
                    _max_response_words = 150

                # Store on self so the post-generation trim block can access it
                self._max_response_words = _max_response_words

                # If user sends bursts (multiple short messages), be brief and punchy
                if _user_burst >= 2.0:
                    length_hint += " User sends in bursts — keep replies short and punchy so conversation flows."
                # Check learned preference from user profile first — overrides cadence sample gate
                _comm_pref = self.user_profiles.get(user_name, {}).get("communication_preference", "")
                if _comm_pref and any(w in _comm_pref.lower() for w in ["short", "brief", "concise", "quick"]):
                    length_hint += f" LEARNED PREFERENCE: {_comm_pref} — keep replies tight."
                elif self.cadence.should_be_brief(user_name):
                    length_hint += " LEARNED PREFERENCE: this user prefers short replies — 1-2 sentences max."
                if _cadence_summary:
                    length_hint += f" User style: {_cadence_summary}."

                # FIX: Resolve user_id before passing to inner_mind — never pass
                # "unknown", "Stranger", or "" as the user_id. Those values poison
                # the inner mind state and cause user=unknown saves every session.
                _resolved_uid = getattr(self, '_current_user_id', user_name)
                if not _resolved_uid or _resolved_uid.lower() in ('unknown', 'stranger', ''):
                    _resolved_uid = (user_name or
                                     (self.config.get('primary_user', 'Tyler')
                                      if isinstance(self.config, dict) else 'Tyler'))
                inner_mind_data = self.legacy_mind.process_input(
                    processed_text,
                    user_id=_resolved_uid,
                    platform_display_name=user_name,
                )
                inner_context = inner_mind_data.get("inner_context", "")

                # ── Real-time fact persistence ────────────────────────────────
                # After inner_mind processes the turn it may have extracted new
                # user facts via _extract_facts_from_user. Flush them to ChromaDB
                # immediately so future sessions can access them — don't wait
                # for the background reflection cycle.
                _wm_facts = inner_mind_data.get("working_memory", [])
                if _wm_facts:
                    _facts_to_store = {}
                    for _wm_item in _wm_facts:
                        _k = _wm_item.get("key", "")
                        _v = _wm_item.get("value", "")
                        # Only store fact-like items (contain ":" separator from extraction)
                        if ":" in _v and not _k.startswith("_"):
                            _fact_type, _, _fact_val = _v.partition(":")
                            _facts_to_store[_fact_type.strip()] = _fact_val.strip()
                    if _facts_to_store:
                        try:
                            self.memory.store_user_facts_batch(user_name, _facts_to_store)
                        except Exception as _fbe:
                            logger.debug(f"[Engine] store_user_facts_batch: {_fbe}")

                # ── FIX: Scrub inner_context so it never leaks raw block headers ──
                inner_context = self._scrub_inner_mind_block(inner_context)

                self.mind.update_context({
                    "focus_user":          user_name,
                    "last_snippet":        processed_text[:40],
                    "relationship_tier":   inner_mind_data.get("relationship", "STRANGER").lower(),
                    "sentiment_trajectory": trajectory,
                    "idle_ms":             (datetime.now(timezone.utc) - self.last_interaction_time).total_seconds() * 1000
                })
                # Continuous Cognition Loop — build unified per-turn awareness report
                _cog_report: Optional["CognitiveReport"] = None
                if _COGNITIVE_REPORT_AVAILABLE and CognitiveReport is not None:
                    try:
                        _cog_report = CognitiveReport.build(
                            user_name     = user_name,
                            user_emotions = user_emotions,
                            inner_mind    = self.legacy_mind,
                            awareness     = self.awareness,
                            thought_loop  = self.mind,
                            inner_mind_data = inner_mind_data,
                            session_turns = session_turns,
                            trajectory    = trajectory,
                            # FIX: pass primary_user so CognitiveReport never resolves
                            # to "unknown" — the root cause of user=unknown state saves.
                            primary_user  = self.config.get('primary_user', 'Tyler')
                                            if isinstance(self.config, dict) else 'Tyler',
                        )
                    except Exception as _cr_err:
                        logger.debug(f"[CognitiveReport] build error: {_cr_err}")

                rel_tier_raw = inner_mind_data.get("relationship", "STRANGER").lower()
                conv_depth = min(1.0, inner_mind_data.get("familiarity", 0) / 60.0)

                # ── FIX: Session awareness — provide turn count so Shiro knows
                #         how long the conversation has been going ──────────────
                # B5 FIX: history fetched once, reused for session_turns, exclude_list,
                # and short_term_buffer — avoids 3 separate get_history() calls.
                # N1 FIX: removed unused total_turns variable.
                history = self.memory.get_history()
                session_turns = len(history) // 2
                session_duration = self._format_timedelta(
                    datetime.now(timezone.utc) - self.session_start
                )

                planned_intent = self.intent_planner.plan(
                    user_message=processed_text,
                    user_emotions=user_emotions,
                    mood=self.mind.mood.value,
                    tier=rel_tier_raw,
                    trajectory=trajectory,
                    depth=conv_depth,
                    valence=self.sentiment_trajectory.current_valence(user_name),
                    has_question="?" in processed_text,
                    is_greeting=bool(user_emotions.get("greeting", 0) > 0.5)
                )
                self.bus.emit("intent_planned", user_id=user_name,
                              intent=planned_intent.name, confidence=planned_intent.confidence)

                # ── System prompt ─────────────────────────────────────────────
                system_prompt = self.persona.get_system_prompt(
                    now=datetime.now(),
                    intent_hint=planned_intent.prompt_hint,
                    relationship_tier=rel_tier_raw,
                    timing_obs=self.time_pattern.visit_observation(user_name),
                    user_quirks=user_profile_obj.quirks,
                    user_patterns=[self.awareness.get_user_pattern_description(user_name)],
                    session_turns=session_turns
                )
                # FIX: drift_note now injected as plain prose via _drift_note_clean below
                # (removing the unused [SYSTEM: Topic shift] bracket token variable)

                # ── FIX: Explicit anti-leak rules injected into every system prompt ──
                # FIX: Removed [CRITICAL RULES] bracket header — plain prose is less likely
                # to be echoed as output format. Added explicit no-fabrication rule.
                _humor_hint = self.awareness.humor_context_hint(user_name)
                # ── Literal instruction gate ─────────────────────────────
                # Scan recent history for explicit reply constraints set by the user.
                # When found, inject as a hard cap so the model actually follows them.
                _literal_constraint = ""
                try:
                    _recent_msgs = [m.get("content","") for m in (history[-6:] if history else [])]
                    _all_recent = " ".join(_recent_msgs).lower()
                    _user_msg_lower = processed_text.lower()
                    # Check current message AND recent context for active constraints
                    _check_text = _user_msg_lower + " " + _all_recent
                    if re.search(r'\bone word\b|\bone[- ]word reply\b|\bjust one word\b', _check_text):
                        _literal_constraint = (
                            "\n\n[HARD CONSTRAINT — ACTIVE]: The user has asked for a ONE WORD reply only. "
                            "Output exactly ONE word and nothing else. No explanation. No sentence. One word."
                        )
                    elif re.search(r'\bjust (yes|no)\b|\byes or no\b|\byes/no\b', _check_text):
                        _literal_constraint = (
                            "\n\n[HARD CONSTRAINT — ACTIVE]: The user asked for yes or no only. "
                            "Reply with one of: yes / no. Nothing else."
                        )
                    elif re.search(r'\bjust say\b|\bonly say\b|\bsay only\b', _user_msg_lower):
                        _literal_constraint = (
                            "\n\n[HARD CONSTRAINT — ACTIVE]: The user asked you to say something specific. "
                            "Do exactly that — nothing more."
                        )
                except Exception:
                    pass

                anti_leak = (
                    "\n\n[CONTEXT REMINDERS]\n"
                    "- Spoken reply is plain words only. No [THOUGHT], [INNER MIND], or system markup.\n"
                    "- No asterisk actions unless the user uses them first.\n"
                    f"- You and {user_name} have exchanged {session_turns} messages ({session_duration}). Ongoing conversation.\n"
                    "- Only reference things in your memory or said in this chat. If you don't know, say so — ask rather than invent.\n"
                    "- Match reply length to the message. Brief gets brief.\n"
                    "- If there's a funny angle, take it. Dry wit and sharp teasing are your strengths.\n"
                    "- Thoughts must connect to what was just said right now.\n"
                    "- NEVER describe your own character, nature, or traits in your reply. "
                    "You ARE those things — you do not announce them. React to what was said, do not narrate yourself.\n"
                    "- BANNED WORDS (never use): 'companion services', 'clodhopper', 'unparalleled wit and style', 'a kitsune of unparalleled', 'unproper'."
                    + (f"\n- Tone note: {_humor_hint}" if _humor_hint else "")
                )

                # FIX: Removed [Current Intensity: X.XX] bracket token — intensity is
                # internal data, not something the LLM should reference in output format.
                # drift_note [SYSTEM: Topic shift] also replaced with plain prose.
                _drift_note_clean = ("Note: topic just shifted — adjust focus." if drift_score > 0.6 else "")
                _absence_note = ("Note: it has been over a week since you last spoke with "
                                 f"{user_name}. You may not remember them well — be honest "
                                 "about that rather than pretending certainty.") if _long_absence else ""
                # ── Speaker identity line ─────────────────────────────────────
                # Always tell Shiro exactly who is speaking right now.
                # This prevents her from defaulting to the primary user's
                # context when someone new (e.g. a Discord user) messages her.
                _primary_user = getattr(self, '_primary_user_name', None)
                _is_stranger = (rel_tier_raw == "stranger")

                # Build a relationship status note for the prompt
                if _is_stranger:
                    _rel_note = (
                        f" You have NEVER spoken to {user_name!r} before — they are a"
                        f" complete stranger to you. Do NOT use any name, nickname,"
                        f" preference, or personal detail from memory when talking to"
                        f" {user_name!r}. Greet them warmly and get to know them fresh."
                        f" If asked about their name or any personal detail you haven't"
                        f" been told in THIS conversation, say you don't know yet."
                        f"\n[STRANGER TONE — CRITICAL]: {user_name!r} is new. First impressions matter."
                        f" Be WARM, CURIOUS, and PLAYFULLY WITTY — NOT sharp, cold, or dismissive."
                        f" Light humor is great. Teasing is fine only if they've teased first."
                        f" DO NOT insult them or use cutting sarcasm this early."
                        f" Think: friendly fox who's genuinely interested in this new person."
                        f" You can be a little guarded, but NOT unwelcoming."
                        f" The goal is to make them want to keep talking to you."
                    )
                elif rel_tier_raw == "acquaintance":
                    _rel_note = (
                        f" Relationship: acquaintance — you've spoken briefly before."
                        f" Light friendly teasing is fine. Keep the wit playful, not cutting."
                        f" Warmth should be visible. You're testing the waters, not pushing them away."
                    )
                else:
                    _rel_note = f" Relationship: {rel_tier_raw}."

                if _primary_user and user_name != _primary_user:
                    _speaker_line = (
                        f"\nSPEAKER: You are currently talking to {user_name!r}."
                        f" This is NOT {_primary_user} — do NOT mix up any details"
                        f" from {_primary_user}'s memory when responding to {user_name!r}."
                        f"{_rel_note}"
                    )
                elif not _primary_user:
                    _speaker_line = f"\nSPEAKER: You are talking to {user_name!r}.{_rel_note}"
                else:
                    _speaker_line = f"\nSPEAKER: {user_name!r} is talking to you.{_rel_note}"

                # ── Unified Cognitive Report injection ───────────────────────
                # All self-awareness hints now come from CognitiveReport which
                # aggregates all 8 pillars in one clean structured object.
                _intent_hint     = ""
                _confidence_hint = ""
                _cog_report_block = ""
                if _cog_report is not None:
                    _cog_report_block = _cog_report.to_prompt_block()
                    _intent_hint      = _cog_report.intent_hint
                    _confidence_hint  = _cog_report.confidence_hint
                else:
                    # Fallback to direct subsystem reads if report unavailable
                    _intent_hint     = self.awareness.get_intent_hint(user_name)
                    _confidence_hint = self.legacy_mind.confidence_hint() if hasattr(self.legacy_mind, "confidence_hint") else ""

                shiro_context = (
                    f"{_speaker_line}"
                    f"\n{_drift_note_clean} {_absence_note}"
                    f"\n{self.outfit_block()}"
                    f"\n{length_hint}"
                    f"{anti_leak}"
                )

                # ── SQLite learned memory injection ───────────────────────────
                # Inject heuristics, anti-patterns, and procedural patterns learned
                # from past RL self-critique. These are Shiro's accumulated knowledge
                # of what works and what doesn't with this specific user.
                if self.sql_memory is not None:
                    try:
                        _sql_ctx = self.sql_memory.build_context_block(
                            user_name, max_chars=600
                        )
                        if _sql_ctx:
                            shiro_context += f"\n\n[LEARNED PATTERNS FOR {user_name.upper()}]\n{_sql_ctx}"
                    except Exception as _sq_err:
                        logger.debug(f"[SQLiteMemory] context inject error: {_sq_err}")

                # FIX: Preserve emotionally significant strategy signals directly in
                # shiro_context so they survive Governor layer-dropping of cog_report.
                # WARM/EMPATHIZE must always reach the model — they are the moments
                # where Shiro drops the act, and losing that signal causes her to
                # respond with hostility to genuine kindness (as seen in the log:
                # "of course you have value" → "stop asking redundant questions").
                _im_strategy      = inner_mind_data.get("strategy", "")
                _im_strategy_note = inner_mind_data.get("strategy_note", "")
                if _im_strategy in ("warm", "empathize"):
                    shiro_context += (
                        f"\n[EMOTIONAL MOMENT — PRIORITY]: Strategy is '{_im_strategy}'. "
                        f"{_im_strategy_note} "
                        f"Drop the tsundere act entirely right now. "
                        f"Be real, be present, be kind. This is one of those moments."
                    )
                elif _im_strategy == "reflect":
                    shiro_context += (
                        f"\n[TONE — PRIORITY]: Strategy is 'reflect'. "
                        f"Turn inward. Be honest and thoughtful. No deflection."
                    )

                # Agency disposition — how Shiro feels about engaging right now
                if _agency_note:
                    shiro_context += f"\n[DISPOSITION] {_agency_note}"
                # ── Known users identity injection ────────────────────────
                if self._known_users_block:
                    shiro_context += f"\n{self._known_users_block}"

                if _intent_hint:
                    shiro_context += f"\n[USER INTENT] {_intent_hint}"
                if _confidence_hint:
                    shiro_context += f"\n[METACOGNITION] {_confidence_hint}"

                # ── Structured task/game state injection ──────────────────────
                # If a game like tic-tac-toe is active, inject the authoritative
                # board state so the LLM ALWAYS knows exactly what's on the board.
                _task_block = self.task_state.prompt_block()
                if _task_block:
                    shiro_context += _task_block

                # ── Goals injection ───────────────────────────────────────────
                # FIX: Gate rules_block to every 5 turns — saves ~200 tokens on
                # most turns while still teaching Shiro goal syntax early and on
                # any turn where an active goal nudge fires.
                # Always inject on turn 1 and turn 5, then every 5th turn after.
                _inject_goal_rules = (
                    session_turns <= 1                    # first two turns always
                    or session_turns % 5 == 0             # every 5th turn
                    or not self.goals.active              # no goals yet — teach syntax
                )
                if _inject_goal_rules:
                    shiro_context += self.goals.rules_block()
                # Then inject the active goals list (only when goals exist)
                _goal_block = self.goals.prompt_block()
                if _goal_block:
                    shiro_context += _goal_block
                # Inject proactive nudge if any goal needs attention
                _goal_nudge = self.goals.get_nudge()
                if _goal_nudge:
                    shiro_context += f"\n{_goal_nudge}"

                # P3 FIX: Run both pre-LLM memory fetches concurrently.
                # Previously sequential: fetch1.wait() then fetch2.wait().
                # Now both ChromaDB queries run in parallel; total cost = max(t1,t2).
                def _fetch_auto_mem():
                    return self._safe_async_run(
                        self.fetch_relevant_memory_async(
                            f"shiro tricks for {user_name}", n_results=2
                        )
                    )
                def _fetch_feedback():
                    return self._safe_async_run(
                        self.memory.search_relevant_memories_async(
                            "User Favor Feedback", n_results=2, user_id=user_name
                        )
                    )
                with ThreadPoolExecutor(max_workers=2) as _ex:
                    _f_auto = _ex.submit(_fetch_auto_mem)
                    _f_feed = _ex.submit(_fetch_feedback)
                    autonomous_mem = _f_auto.result()
                    feedback_mems  = _f_feed.result()

                if autonomous_mem:
                    shiro_context += f"\nRelated memory (only use if clearly relevant): {autonomous_mem}"
                if feedback_mems and isinstance(feedback_mems, list):
                    feedback_contents = [m['content'] if isinstance(m, dict) and 'content' in m
                                         else str(m) for m in feedback_mems]
                    shiro_context += f"\nPrior feedback from {user_name}: {feedback_contents}"

                # ── Knowledge Graph context injection ─────────────────────────
                # Pull the ego-graph around the current topic seed and surface
                # unexplored adjacent concepts as curiosity candidates.
                # This gives the LLM concrete association chains: pytorch→deep learning→
                # transformer→attention, rather than only vector-similar memories.
                # ── KG block: extracted separately for governor gating ─────────
                _kg_block = ""
                if self.kg is not None:
                    try:
                        _kg_words = [
                            w.lower() for w in re.findall(r"[A-Za-z]{4,}", processed_text)
                            if w.lower() not in {
                                "that","this","then","them","they","with","what","when",
                                "where","have","will","would","could","should","about",
                                "just","also","even","like","know","think","feel","want",
                                "really","going","being","still","here","there","your",
                            }
                        ]
                        _kg_seed = _kg_words[0] if _kg_words else ""

                        if _kg_seed and self.kg.node_exists(_kg_seed):
                            _kg_ego = self.kg.ego_graph(
                                _kg_seed, radius=2, min_weight=0.25, max_nodes=8
                            )
                            if _kg_ego:
                                _kg_related = ", ".join(
                                    f"{n['name']} ({n['relation']})"
                                    for n in _kg_ego[:5]
                                )
                                _kg_block += (
                                    f"\n[KNOWLEDGE GRAPH] Concepts related to '{_kg_seed}': "
                                    f"{_kg_related}. "
                                    "Use naturally if they fit — don't force them."
                                )
                            _visited_words = set(_kg_words[:8])
                            _kg_curious = self.kg.curiosity_candidates(
                                _kg_seed, visited=_visited_words, top_n=3
                            )
                            if _kg_curious:
                                _curious_names = ", ".join(n["name"] for n in _kg_curious)
                                _kg_block += (
                                    f"\n[GRAPH CURIOSITY] Adjacent but unexplored: {_curious_names}. "
                                    "If one of these genuinely connects, let your curiosity surface it."
                                )
                    except Exception as _kg_ctx_e:
                        logger.debug(f"[KG] Context injection error: {_kg_ctx_e}")

                # ── Cognitive Pipeline injection ──────────────────────────
                # Run the 17-stage cognitive kernel and inject its output into
                # the system prompt. This gives the LLM: attention signals,
                # VAD emotion state, identity drift status, relationship stage,
                # hypothesis + debate result, reasoning chain, memory records,
                # metacognition reflection, and curiosity signals — all in one
                # structured block that enriches every single LLM call.
                _cognition_block = ""
                if self.cognition:
                    try:
                        _raw_cog = self.cognition.enrich(
                            processed_text,
                            user_name=user_name,
                            context_override={
                                "session_turns": session_turns,
                                "relationship_tier": rel_tier_raw,
                                "trajectory": trajectory,
                            }
                        )
                        _last_turn = self.cognition.get_last_turn()
                        _cognition_block = format_cognition_block(_raw_cog, _last_turn)

                        # Cross-sync: feed cognitive emotion state into existing
                        # SentimentTrajectory so both systems reflect same mood
                        _cog_emo = self.cognition.get_emotion_state()
                        if _cog_emo and _cog_emo.get("vad"):
                            _vad = _cog_emo["vad"]
                            # Map VAD to existing SentimentTrajectory format
                            _emo_map = {}
                            if _vad.get("valence", 0) > 0.3:
                                _emo_map["joy"] = min(1.0, _vad["valence"])
                            elif _vad.get("valence", 0) < -0.3:
                                _emo_map["sadness"] = min(1.0, abs(_vad["valence"]))
                            if _emo_map:
                                self.sentiment_trajectory.record(user_name, _emo_map)

                        # Cross-sync: drift flag → identity awareness
                        _cog_id = self.cognition.get_identity_snapshot()
                        if _cog_id.get("drift_pressure", 0) > 0.3:
                            logging.getLogger("shiro.engine").warning(
                                f"[IDENTITY] Cognitive drift pressure: {_cog_id.get('drift_pressure'):.2f}"
                            )
                    except Exception as _cog_ex:
                        logging.getLogger("shiro.engine").debug(f"Cognition enrich error: {_cog_ex}")

                # Inject Continuous Cognition Loop report into prompt
                # Cap at 600 chars to prevent the cognition block from dominating
                # the context window at the expense of actual conversation history.
                if _cog_report_block and len(_cog_report_block) > 600:
                    _cog_report_block = _cog_report_block[:600].rsplit("\n", 1)[0] + "…"
                _cog_report_injection = (
                    f"\n{_cog_report_block}" if _cog_report_block else ""
                )
                # ── Consciousness self-awareness injection ─────────────────
                # Injects: narrative self, mood+valence, salient thoughts,
                # curiosity objects, beliefs, relationship context, open questions,
                # past session summaries, room tempo, gone-quiet people, and more.
                _consciousness_block = ""
                if self.consciousness is not None:
                    # prompt_context — core self-awareness block (cached 10s TTL)
                    # prompt_context() rebuilds the full self-awareness text every call.
                    # Consciousness ticks every 20s so caching for 10s is safe —
                    # at most 1 turn out of date, never stale enough to matter.
                    try:
                        _pc_cache_key = f"_prompt_ctx_{user_name}"
                        _pc_now = time.monotonic()
                        _pc_cached = self._consciousness_static_cache.get(_pc_cache_key)
                        if (_pc_cached is None
                                or _pc_now - _pc_cached[0] > 10.0):
                            _ctx = self.consciousness.prompt_context()
                            _pc_text = _ctx.text.strip() if _ctx else ""
                            # Cap at 800 chars to prevent runaway context bloat
                            # while keeping the most important state at the top.
                            if len(_pc_text) > 800:
                                _pc_text = _pc_text[:800].rsplit("\n", 1)[0] + "…"
                            self._consciousness_static_cache[_pc_cache_key] = (
                                _pc_now, _pc_text
                            )
                        else:
                            _pc_text = _pc_cached[1]
                        if _pc_text:
                            _consciousness_block = f"\n\n[SELF-AWARENESS]\n{_pc_text}"
                    except Exception as _ca_err:
                        logger.debug(f"[Consciousness] prompt_context error: {_ca_err}")

                    # recall_person + summarize_past_sessions — cached (30s TTL)
                    # These serialize full relationship/session history on every call.
                    # They're expensive and change at most once per session, not per turn.
                    try:
                        _static_cache_key = user_name
                        _now_ts = time.monotonic()
                        _cached = self._consciousness_static_cache.get(_static_cache_key)
                        if (_cached is None
                                or _now_ts - _cached[0] > self._CONSCIOUSNESS_CACHE_TTL):
                            # Cache miss or expired — rebuild
                            _static_text = ""
                            _recall_txt = self.consciousness.memory.recall_person(user_name)
                            if _recall_txt and "no record" not in _recall_txt.lower():
                                _static_text += f"\n[RELATIONSHIP RECALL] {_recall_txt}"
                            _past = self.consciousness.memory.summarize_past_sessions(n=3)
                            if _past and "no record" not in _past.lower():
                                _static_text += f"\n[PAST SESSIONS] {_past}"
                            self._consciousness_static_cache[_static_cache_key] = (
                                _now_ts, _static_text
                            )
                            logger.debug("[Consciousness] Static cache rebuilt.")
                        else:
                            _static_text = _cached[1]
                            logger.debug("[Consciousness] Static cache hit.")
                        if _static_text:
                            _consciousness_block += _static_text
                    except Exception:
                        pass

                    # gone_quiet individuals
                    try:
                        _quiet = self.consciousness.environment.gone_quiet_individuals(
                            idle_threshold_s=120.0
                        )
                        if _quiet:
                            _qnames = ", ".join(p.name for p in _quiet)
                            _consciousness_block += f"\n[ROOM] {_qnames} has gone quiet."
                    except Exception:
                        pass

                    # conversation tempo
                    try:
                        _tempo = self.consciousness.environment.tempo_label
                        if _tempo and _tempo != "normal":
                            _consciousness_block += f"\n[TEMPO] Conversation is {_tempo}."
                    except Exception:
                        pass

                    # am_i_alone
                    try:
                        if self.consciousness.am_i_alone():
                            _consciousness_block += "\n[ROOM] You are alone right now."
                    except Exception:
                        pass

                    # wonder — surface curiosity as a thought
                    try:
                        _curiosity_top = self.consciousness.self_.curiosity.top(1)
                        if _curiosity_top and _curiosity_top[0].intensity > 0.6:
                            _wonder = self.consciousness.wonder(
                                _curiosity_top[0].subject, confidence=0.4
                            )
                            if _wonder:
                                _consciousness_block += f"\n[WONDER] {_wonder}"
                    except Exception:
                        pass

                    # top goal
                    try:
                        _top_goal = self.consciousness.self_.goals.top()
                        if _top_goal:
                            _consciousness_block += f"\n[FOCUS] {_top_goal.description}"
                    except Exception:
                        pass

                    # active topics
                    try:
                        _active_topics = self.consciousness.conversation.active_topics
                        if _active_topics:
                            _consciousness_block += (
                                f"\n[ACTIVE TOPICS] {', '.join(_active_topics[:4])}"
                            )
                    except Exception:
                        pass

                    # unanswered questions
                    try:
                        _unanswered = self.consciousness.conversation.unanswered_questions
                        if _unanswered:
                            _uq_text = "; ".join(
                                getattr(q, 'text', str(q))[:60]
                                for q in _unanswered[:2]
                            )
                            _consciousness_block += f"\n[OPEN QUESTIONS] {_uq_text}"
                    except Exception:
                        pass

                    # session valence
                    try:
                        _valence = self.consciousness.self_.valence.label
                        if _valence and _valence != "neutral":
                            _consciousness_block += f"\n[SESSION FEEL] {_valence}"
                    except Exception:
                        pass

                    # diagnostics on first turn only
                    if self._interaction_count == 0:
                        try:
                            # Prune any fulfilled goals that weren't cleaned up
                            # at the end of the previous session before running diag
                            self.consciousness.self_.goals.prune()
                            _diag = self.consciousness.diagnostics()
                            if not _diag["ok"]:
                                for _issue in _diag["issues"]:
                                    logger.warning(f"[Consciousness/diag] {_issue}")
                        except Exception:
                            pass

                # Append literal constraint if active — this overrides everything else
                if _literal_constraint:
                    shiro_context += _literal_constraint

                # Append greeting mood injection if this is a greeting turn.
                # This carries the mood/instruction that was stripped from processed_text,
                # ensuring the model has the context without seeing it as dialogue.
                if _greeting_mood_injection:
                    shiro_context += _greeting_mood_injection

                # ── Prompt Governor assembly (v2.1) ──────────────────────
                if self.prompt_governor is not None:
                    _governed = self.prompt_governor.assemble(
                        message          = processed_text,
                        system_prompt    = system_prompt,
                        shiro_context    = shiro_context,
                        cognition_block  = _cognition_block,
                        cog_report_block = _cog_report_injection,
                        consciousness    = _consciousness_block,
                        inner_context    = inner_context,
                        task_block       = _task_block if _task_block else "",
                        goal_block       = (
                            (_goal_block or "") + ("\n" + _goal_nudge if _goal_nudge else "")
                        ),
                        memory_block     = str(autonomous_mem) if autonomous_mem else "",
                        kg_block         = _kg_block,
                        user_name        = user_name,
                        session_turns    = session_turns,
                    )
                    top_bun = _governed.top_bun
                    logger.debug(
                        "[Governor] tier=%d ctx=%d/%d intent=%s "
                        "included=%s dropped=%s",
                        _governed.tier,
                        _governed.tokens_used,
                        _governed.budget,
                        _governed.tier_scores.primary_intent if _governed.tier_scores else "?",
                        _governed.layers_included,
                        _governed.layers_dropped,
                    )
                    # Periodic governor health stats
                    if self._interaction_count % 50 == 0 and self._interaction_count > 0:
                        logger.info("[Governor] stats: %s", self.prompt_governor.stats())
                else:
                    # Governor unavailable — original assembly (safe fallback)
                    # inner_context goes here (system side) — NOT in full_context.
                    # IMPORTANT: only inject the strategy note, not the full block.
                    # The full inner_context is 200-300 tokens. On an 8B model with
                    # 4096-6144 ctx the system prompt is already ~2500 tokens — injecting
                    # the full block pushes conversation history out of the window and
                    # causes the model to lose track of what was just said.
                    _strategy_only = (
                        inner_mind_data.get("strategy_note", "")
                        or inner_mind_data.get("strategy", "")
                    )
                    _inner_ctx_block = (f"\n\n[STRATEGY] {_strategy_only}") if _strategy_only else ""
                    top_bun = system_prompt + shiro_context + _cognition_block + _cog_report_injection + _consciousness_block + _inner_ctx_block

                # ── Screen Vision injection ───────────────────────────────────
                # Append screen context if vision is enabled and has a description.
                # On-demand mode: only fires if user message references screen/looking.
                # Passive mode: always injects cached description (age shown).
                _screen_ctx = ""
                if self.vision and self.vision.is_enabled:
                    _screen_triggers = (
                        "screen", "look", "see", "watching", "playing",
                        "reading", "working on", "what are you", "show"
                    )
                    _is_screen_query = any(
                        t in processed_text.lower() for t in _screen_triggers
                    )
                    if self.vision.mode == "passive" or _is_screen_query:
                        _screen_ctx = self.vision.get_screen_context(
                            force=_is_screen_query
                        )
                if _screen_ctx:
                    top_bun += f"\n{_screen_ctx}"

                # ── RAG retrieval ─────────────────────────────────────────────
                # PERF FIX P2: HyDE (_imagine_reply) disabled for conversational messages.
                # Previously called the LLM a second time for any message >15 words,
                # doubling latency. Now only used for factual/knowledge queries.
                _is_knowledge_query = any(w in processed_text.lower() for w in [
                    "what is", "who is", "how does", "explain", "tell me about",
                    "what was", "when did", "history of", "definition"
                ])
                hyp_ans = self._imagine_reply(processed_text) if _is_knowledge_query else processed_text
                # N5: reuse already-fetched history for exclude_list
                # Increase exclude_list to 10 turns to prevent repeating earlier session context
                exclude_list = [m["content"] for m in history[-10:]] if history else []
                long_term_memory = self.memory.get_full_context(
                    processed_text, user_id=user_name,
                    hypothetical_answer=hyp_ans, exclude_list=exclude_list
                )

                # ── Memory gap awareness ──────────────────────────────────────
                # Tell Shiro how much relevant memory she actually has for this
                # turn so she can respond honestly rather than fabricating.
                # get_full_context now includes book_memory tier automatically —
                # no separate book lookup needed.
                try:
                    _mem_gap = self.memory.get_memory_gap_hint(processed_text, user_id=user_name)
                except Exception:
                    _mem_gap = "none"

                _gap_note = ""
                if _mem_gap == "none":
                    _gap_note = (
                        "\n[MEMORY STATUS: Nothing relevant found in long-term memory for this topic. "
                        "You genuinely don't know. Either admit the gap naturally and ask to learn more, "
                        "or say you're not sure — never invent. Curiosity is better than silence: "
                        "'I don't actually have anything on that — tell me more?' is perfect.]"
                    )
                elif _mem_gap == "sparse":
                    _gap_note = (
                        "\n[MEMORY STATUS: Very little relevant memory found — treat carefully. "
                        "Only reference what explicitly appears in the context above. "
                        "If uncertain, acknowledge it and ask a follow-up to learn more.]"
                    )
                elif _mem_gap == "partial":
                    _gap_note = (
                        "\n[MEMORY STATUS: Some relevant memory found but not deep coverage. "
                        "Use what's there, acknowledge gaps honestly if pressed for detail.]"
                    )
                # "rich" — no note needed, Shiro has solid memory to draw from

                # ── Book query guard ─────────────────────────────────────────
                # If this looks like a question about books/reading AND book
                # memories are sparse/absent, add a hard constraint so the
                # model doesn't fall back to training-weight knowledge and
                # invent books Shiro has never read (e.g. Dune, Lord of the Rings).
                _is_book_query = bool(re.search(
                    r'\b(book|read|reading|novel|story|author|chapter|favorite book|'
                    r'frankenstein|alice|gatsby|austen|melville|shelley|dickens|'
                    r'which book|what book|have you read|did you read|your favorite)\b',
                    processed_text, re.IGNORECASE
                ))
                if _is_book_query and _mem_gap in ("none", "sparse"):
                    _gap_note += (
                        "\n[BOOK MEMORY CONSTRAINT: Only reference books that explicitly appear "
                        "in the BOOKS I HAVE READ section above. If no books appear there, say "
                        "you haven't read many yet or that your memory of them is fuzzy — "
                        "do NOT invent or reference books from general knowledge like Dune, "
                        "Lord of the Rings, Harry Potter, etc. Only books in your memory count.]"
                    )

                if _gap_note:
                    long_term_memory = long_term_memory + _gap_note

                # ── Streaming context injection ───────────────────────────────
                if self._is_streaming:
                    long_term_memory = self._get_stream_context_block() + "\n\n" + long_term_memory

                # ── Journal context injection (occasional) ────────────────────
                _journal_ctx = self.journal.inject_context(max_chars=350)
                if _journal_ctx:
                    long_term_memory = long_term_memory + f"\n\n{_journal_ctx}"

                meat = self._add_temporal_context(long_term_memory, user_name)

                user_prof_data = self.user_profiles.get(user_name, {})
                # Build the user facts block. For strangers the block is locked
                # down hard to prevent hallucination of other users' details.
                if _is_stranger or not user_prof_data:
                    garnish = (
                        f"### WHAT YOU KNOW ABOUT {user_name.upper()}\n"
                        f"NOTHING YET — this is a brand new person.\n"
                        f"ABSOLUTE RULE: Do NOT use any name, nickname, or personal detail"
                        f" from any other user's memory when talking to {user_name!r}.\n"
                        f"If {user_name!r} tells you their name or nickname in this"
                        f" conversation, remember it from WHAT THEY SAID — not from memory.\n"
                        f"If asked what their name is and they haven't told you yet, say"
                        f" you don't know yet and ask them.\n"
                    )
                else:
                    garnish = (
                        f"### Facts {user_name} has explicitly stated or revealed in conversation\n"
                        f"(ONLY use these — do not infer or expand beyond what is listed here):\n"
                    )
                    for k, v in user_prof_data.items():
                        garnish += f"- {k}: {v}\n"
                    garnish += (
                        f"\nIMPORTANT: If {user_name} asks about something NOT in this list,"
                        " do not invent an answer. Say you don't know that specifically and"
                        " offer to find out or ask them directly.\n"
                    )

                short_term_buffer = history[-10:]  # N5: reuse already-fetched history

                # inner_context belongs on the SYSTEM side (top_bun), NOT in the
                # user-visible context sandwich.  Injecting it into full_context made
                # quantized models partially echo persona/internal blocks as dialogue
                # (root cause of "blue too", "fox girl here by nature" leaks).
                # It is passed to the Governor via its own param and injected into
                # top_bun below (see governor fallback).  Remove it from here entirely.
                full_context = f"{meat}\n\n{garnish}"

                # ── Smart context compression ────────────────────────────────
                # Instead of blindly truncating the RAG memory block, compress it
                # with a fast summarization pass when it exceeds the budget.
                # This preserves signal (key facts) while fitting the token window.
                #
                # Budget: 3200 chars (~800 tokens) for full_context.
                # System prompt + shiro_context already consume ~2500 tokens on an
                # 8B model at 4096 ctx. Keeping full_context under 800 tokens leaves
                # room for conversation history and the response itself.
                #
                # The compressor only fires when RAG memories are large.
                # Conversation history (short_term_buffer) is never touched.
                # Garnish (user facts) is always preserved in full.
                # A 60-second cache prevents re-summarizing on every rapid turn.
                # Cap raised from 3200 → 4800 so compression fires less often —
                # each compress call is a full LLM inference that adds VRAM pressure,
                # especially now that the team system also makes inference calls.
                _FC_CHAR_CAP = 4800
                if len(full_context) > _FC_CHAR_CAP:
                    full_context = self._compress_context(
                        full_context,
                        char_cap=_FC_CHAR_CAP,
                        user_name=user_name,
                    )
                # Short inputs get hard caps — LLM finishes 2-4x faster.
                # Long/complex inputs still get headroom.
                # Token caps — keep responses short by default.
                # These are hard ceilings; length_hint guides the model within them.
                if _is_greeting_msg or _user_words <= 3:
                    _tokens_override = 40    # 1 short sentence max
                elif _user_words <= 8:
                    _tokens_override = 60    # 1 sentence
                elif _user_words <= 20:
                    _tokens_override = 90    # 1-2 sentences tops
                elif _user_words <= 50:
                    _tokens_override = 140   # 2 sentences — think before adding a 3rd
                else:
                    _tokens_override = 250   # deep/complex — still NOT a wall of text

                # FIX: Token floor for emotionally significant short messages.
                # Short inputs (<=8 words) get a 60-token ceiling which causes
                # Shiro to start a thought and get cut off mid-sentence when the
                # strategy demands a real response (e.g. WARM, EMPATHIZE, REFLECT).
                # Open-ended questions like "what would you like me to do?" also
                # deserve a complete answer. Raise the floor to 90 in these cases.
                _needs_token_floor = (
                    _im_strategy in ("warm", "empathize", "reflect", "explore", "collaborate")
                    or (processed_text.strip().endswith("?") and _user_words <= 12)
                )
                if _needs_token_floor and _tokens_override < 90:
                    _tokens_override = 90

                # ── Loop / latch break ──────────────────────────────────
                _latch_break = self.loop_detector.get_break_injection()
                if _latch_break:
                    top_bun += "\n\n[SHIRO SELF-CORRECTION]: " + _latch_break

                # ── Word frequency cap nudge ─────────────────────────────
                _freq_warnings = []
                for word, cap in self.loop_detector._WORD_FREQUENCY_CAP.items():
                    count = self.loop_detector._word_use_counts.get(word, 0)
                    # cap=0 means always banned; otherwise ban once count >= cap
                    if cap == 0 or count >= cap:
                        if cap == 0:
                            msg = f"'{word}' is PERMANENTLY BANNED — never use it, not even once."
                        else:
                            msg = (f"You have used '{word}' {count} time(s) — "
                                   f"BANNED for this reply. Use completely different words.")
                        _freq_warnings.append(msg)
                if _freq_warnings:
                    top_bun += "\n\n[WORD BAN — ABSOLUTE]: " + " | ".join(_freq_warnings)

                # ── Web search injection ─────────────────────────────────
                # Triggered on factual/real-world questions. Free DuckDuckGo,
                # no API key. Gracefully skipped when offline.
                _web_result = None
                _search_triggered = self._should_web_search(processed_text)
                # ── SQLite smart search fallback ──────────────────────────────
                # If the regex trigger didn't fire but we have a question-like input,
                # ask the LLM whether existing memory is sufficient or search is needed.
                # Only fires when sql_memory is available and the regex said no.
                if not _search_triggered and self.sql_memory is not None:
                    try:
                        _mem_snippet = long_term_memory[:400] if long_term_memory else ""
                        if (
                            "?" in processed_text
                            and len(processed_text.split()) > 4
                            and _mem_snippet
                        ):
                            _search_triggered = self.sql_memory.should_search(
                                processed_text, _mem_snippet, user_name
                            )
                            if _search_triggered:
                                logger.info(
                                    f"[WebSearch] Smart trigger fired for: "
                                    f"'{processed_text[:60]}'"
                                )
                    except Exception as _ss_err:
                        logger.debug(f"[SQLiteMemory] should_search error: {_ss_err}")
                if _search_triggered:
                    _search_query = self._extract_search_query(processed_text)
                    logger.info(f"[WebSearch] Triggered for: '{_search_query}'")
                    try:
                        _web_result = self.web_search.search(_search_query[:200])
                    except Exception as _se:
                        logger.debug(f"[WebSearch] Error during search: {_se}")
                if _web_result:
                    full_context += (
                        f"\n\n[WEB SEARCH RESULT for '{_search_query[:60]}']: {_web_result}\n"
                        "Use this to answer accurately. Summarise it naturally in your own voice. "
                        "Don't quote it verbatim. Don't say you searched — just know the answer."
                    )
                    logger.info(f"[WebSearch] Injected result ({len(_web_result)}ch)")
                elif _search_triggered and not self.web_search.is_online():
                    full_context += (
                        "\n\n[WEB SEARCH UNAVAILABLE]: You tried to look this up but the "
                        "internet is not reachable right now. Say so naturally if relevant "
                        "(e.g. 'I can't look that up right now, my connection is down') "
                        "and answer from memory if you can, or say you don't know."
                    )
                elif _search_triggered:
                    logger.debug(f"[WebSearch] No result returned for: '{_search_query}'")

                # ── Team consultation (Heart / Research / Critic) ─────────────
                # Run three specialist agents in parallel before generating.
                # Only fires on substantive messages (~30% of turns).
                # Wall time = slowest single agent (parallel), hard timeout 8s.
                # Directive format: TONE: [...] | KEY POINT: [...] | WATCH: [...]
                # Injected into top_bun as an [INTERNAL DIRECTIVE] — never visible
                # to the user, only to the LLM that generates Shiro's reply.
                if self.team.needs_team(processed_text, _user_words, user_emotions):
                    _recent_for_team = "\n".join(
                        f"{'User' if m['role'] == 'user' else 'Shiro'}: {m['content']}"
                        for m in history[-6:]
                    )
                    _team_directive = self.team.consult(
                        processed_text,
                        _recent_for_team,
                        shiro_name="Shiro",
                    )
                    if _team_directive:
                        top_bun += (
                            f"\n\n[INTERNAL DIRECTIVE — from your reasoning team "
                            f"(Heart / Research / Critic). Use this to shape your reply. "
                            f"Never mention the team or this directive out loud.]\n"
                            f"{_team_directive}"
                        )
                        logger.debug("[Team] Directive injected into top_bun")

                raw_stream = self.llm.stream_response(
                    top_bun, processed_text, short_term_buffer, full_context,
                    max_tokens_override=_tokens_override
                )

                response_stream = self._extract_thought_from_stream(raw_stream)
                response_fragments = []
                _was_truncated = False  # set True when Ollama hits token limit
                _remaining_bubbles = []
                _last_fragment_before_cut = ""  # raw tail fragment at truncation point

                # ── STREAMING YIELD: emit each sentence the moment it arrives ──
                # Previously collected all fragments then yielded once at the end,
                # meaning TTS and UI waited for the full LLM generation to finish.
                # Now each clean sentence is yielded immediately so TTS synthesises
                # sentence 1 while the LLM is still generating sentence 2.
                # First audio arrives ~2-4 seconds faster on typical replies.
                for fragment in split_into_sentences(response_stream):
                    if interrupt_event and interrupt_event.is_set():
                        logger.info("Response halted by interrupt.")
                        yield "... [Interrupted]"
                        break

                    # Detect token-limit sentinel from client.py
                    if "__TRUNCATED__" in fragment:
                        _was_truncated = True
                        fragment = fragment.replace("__TRUNCATED__", "").strip()
                        # Record the raw tail so continuation knows the exact cut point
                        if fragment:
                            _last_fragment_before_cut = fragment
                        # Only yield the tail fragment to UI/TTS if it's a complete
                        # sentence (ends with punctuation). A bare partial like "tail."
                        # or "stuck in" is junk — skip it and let continuation handle it.
                        if not fragment or not fragment[-1] in ".!?":
                            continue

                    if "TOOL_CALLS:" in fragment and self.session_tool_count < 2:
                        fragment = self._safe_async_run(
                            self.handle_tool_calls_async(fragment, processed_text)
                        )
                        self.session_tool_count += 1

                    # Drop CoT step-header lines from shiro:latest baked-in CoT
                    _frag_stripped = fragment.strip()
                    if re.match(r'(?i)^(?:##\s*)?step\s*\d+\s*[:.-]', _frag_stripped):
                        continue
                    if _frag_stripped.startswith('## ') and not any(
                        c.isalpha() and c.islower() for c in _frag_stripped[3:20]):
                        continue

                    clean_fragment = self._clean_response(fragment, processed_text)
                    # Per-sentence sanitize + adapt — each sentence clean before UI/TTS sees it
                    if clean_fragment:
                        clean_fragment = self._final_sanitize(clean_fragment)
                    if clean_fragment:
                        clean_fragment = self._throttle_hmph(clean_fragment)
                        clean_fragment = self.cadence.adapt_text(clean_fragment, user_name)
                    if clean_fragment:
                        response_fragments.append(clean_fragment)
                        # ── Multi-bubble streaming ────────────────────────────────────────
                        # Yield each sentence immediately so TTS + UI receive it as a
                        # separate bubble. The trailing '\n' signals the UI to treat this
                        # as a new message chunk rather than appending to the last one.
                        # A short inter-sentence pause (scaled to sentence length) gives
                        # TTS time to start playing sentence N before sentence N+1 arrives,
                        # and makes the multi-bubble effect natural rather than instant-flood.
                        _bubble_delay = min(0.15, max(0.05, len(clean_fragment.split()) * 0.01))
                        if response_fragments and len(response_fragments) > 1:
                            time.sleep(_bubble_delay)
                        yield clean_fragment + " "

                # Assemble full response text for memory/dedup (space-joined, then stripped)
                # Avoids double-spaces if a fragment already ends with punctuation+space
                full_response = " ".join(f.strip() for f in response_fragments)

                # ── Hard word-count enforcement ─────────────────────────────────
                # The LLM sometimes ignores length hints. Trim to the last complete
                # sentence within the word budget. This is the safety net.
                _resp_words = len(full_response.split())
                _word_cap = getattr(self, '_max_response_words', 90)
                if _resp_words > _word_cap:
                    # Trim to last sentence boundary within the word cap
                    words = full_response.split()
                    truncated = ' '.join(words[:_word_cap])
                    # Try to end at a natural sentence boundary
                    for _end_char in ['.', '!', '?']:
                        last_sent_end = truncated.rfind(_end_char)
                        if last_sent_end > len(truncated) * 0.5:
                            full_response = truncated[:last_sent_end + 1]
                            break
                    else:
                        full_response = truncated.rstrip(',;:') + '.'
                    logger.debug(f"[Length] Trimmed response from {_resp_words} → {len(full_response.split())} words")

                # Track for autonomous voice dedup guards
                if full_response.strip():
                    self._last_response_ts = time.time()
                    self._last_response_text = full_response.strip()
                    self.loop_detector.record_response(full_response.strip())

                # ── Register Shiro's move / update topic state after response ──
                if full_response:
                    self.task_state.on_shiro_response(full_response)
                    # Strip [MOVE: ...] token before display/TTS/memory
                    full_response = self.task_state.strip_move_token(full_response)
                    # ── Goal action parsing ───────────────────────────────────
                    # CRITICAL: process_shiro_reply() MUST run on the raw assembled
                    # response BEFORE _final_sanitize() strips [GOAL:...] tags.
                    # _final_sanitize() removes all [...] tags — it would destroy
                    # [GOAL:CREATE:...] before the goal system ever reads them.
                    # So we extract goal actions here, then sanitize below.
                    full_response, _goal_actions = self.goals.process_shiro_reply(full_response)
                    if _goal_actions:
                        for _ga in _goal_actions:
                            logger.info(f"[Goals] Action taken: {_ga}")

                # If truncated, trim for memory storage only
                if _was_truncated and full_response:
                    full_response = self._trim_to_sentence(full_response)

                _resp_for_bg = full_response

                if not (interrupt_event and interrupt_event.is_set()):
                    # P4 FIX: All post-response writes moved to a single background thread.
                    # Previously: store_insight, add_interaction, v4_memory writes,
                    # reflect_on_response, and shiro_learn all ran synchronously after
                    # yield — blocking the next user message while Shiro wrote to disk.
                    # Short-term buffer update stays synchronous (in-memory, ~1µs).
                    self._interaction_count += 1
                    _ic = self._interaction_count
                    # Fix 3: Clean broken spaces in thought before logging/using
                    # Stream accumulation produces "beneaththe surfaceof" artifacts
                    _raw_thought = self.last_thought or ""
                    # Apply space repair: punct→letter and camelCase boundaries
                    _thought = re.sub(r'([.!?,])([A-Za-z])', r'\1 \2', _raw_thought)
                    _thought = re.sub(r'([a-z])([A-Z])', r'\1 \2', _thought)
                    _thought = re.sub(r"([a-zA-Z])('(?:d|s|t|ve|re|ll|m|nt))([a-zA-Z])", r"\1\2 \3", _thought)
                    self.last_thought = _thought  # update for autonomous use
                    # Preserve real LLM thoughts separately — used by autonomous
                    # message generation as higher-quality context than background thoughts.
                    if self._thought_is_real and _thought.strip():
                        self._last_real_thought = _thought.strip()
                    _resp_clean = self._final_sanitize(full_response.strip())
                    if not _resp_clean and full_response.strip():
                        logger.warning(
                            f"[Sanitize] _resp_clean empty after sanitize. "
                            f"Raw was: {full_response.strip()[:120]!r}"
                        )
                        _resp_clean = full_response.strip()
                    # ── Coherence / hallucination guard ──────────────────────
                    # Lightweight heuristic check for invented names and
                    # fabrication-marker phrases. Runs after sanitize, before
                    # the response reaches memory or TTS.
                    if _resp_clean:
                        _resp_clean = self._coherence_guard(
                            _resp_clean,
                            user_query=processed_text,
                            context_text=full_context,
                        )

                    # Mood-responsive TTS prosody — adjust voice speed to match current mood
                    if hasattr(self, '_tts_ref') and self._tts_ref:
                        try:
                            _mood_now = (
                                self.consciousness.self_.mood.value
                                if self.consciousness is not None
                                else (self.mind.mood.value if self.mind else "neutral")
                            )
                            self._tts_ref.set_mood_speed(_mood_now)
                            # Also update cadence so imperfection layer is mood-gated
                            self.cadence.set_mood(_mood_now)
                        except Exception:
                            pass

                    # Short-term buffer: in-memory only — safe to do immediately
                    # IMPORTANT: greeting directives ("Tyler just showed up. Greet them...")
                    # must NOT be stored as user turns — the model will echo them back.
                    # For greeting turns, only store the assistant's response.
                    if not _is_greeting_directive:
                        self.memory.short_term_buffer.append({"role": "user", "content": text})
                    self.memory.short_term_buffer.append({"role": "assistant", "content": _resp_clean})
                    if len(self.memory.short_term_buffer) > self.memory.max_short_term * 2:
                        self.memory.short_term_buffer = \
                            self.memory.short_term_buffer[-(self.memory.max_short_term * 2):]
                    # Keep per-user buffer in sync so reflect() uses correct user turns
                    self.memory._user_buffers[user_id] = list(self.memory.short_term_buffer)

                    def _background_writes():
                        self._background_writing.set()
                        try:
                            if _thought:
                                logger.info(f"Shiro's Internal Thought: {_thought.strip()}")
                                self.memory.store_insight(
                                    f"Thought: {_thought.strip()}", user_id=user_name,
                                    source="inner_monologue"
                                )
                            if _ic == 1:  # was 0 before increment above
                                self.memory.store_episodic_memory(
                                    f"SESSION START: First interaction with {user_name} today: '{text}'",
                                    user_id=user_name, importance=8
                                )
                            # Long-term ChromaDB write (skips short-term buffer — already done above)
                            self.memory.add_interaction_to_longterm(text, _resp_clean, user_id=user_name)
                            self.legacy_mind.reflect_on_response(_resp_clean, text)
                            # Update Theory of Mind satisfaction model
                            self.awareness.on_shiro_response(user_name, _resp_clean)
                            # v4 Consciousness — tell it what Shiro said so relationship model updates
                            if self.consciousness is not None:
                                # perceive Shiro's own reply
                                try:
                                    self.consciousness.perceive(_resp_clean, source=None)
                                except Exception:
                                    pass

                                # emotional memory of Shiro's response tone
                                try:
                                    _resp_mood = self.consciousness.self_.mood.value
                                    _resp_tone = "positive" if any(
                                        w in _resp_clean.lower() for w in
                                        ["happy","great","love","glad","good","nice","fun"]
                                    ) else "negative" if any(
                                        w in _resp_clean.lower() for w in
                                        ["sorry","sad","bad","wrong","fail","hurt"]
                                    ) else "neutral"
                                    if _resp_tone != "neutral":
                                        self.consciousness.memory.emotional_memories.record(
                                            person=user_name,
                                            mood=_resp_mood,
                                            tone=_resp_tone,
                                            intensity=0.4,
                                            summary=f"Shiro said: {_resp_clean[:60]}"
                                        )
                                except Exception:
                                    pass

                                # reflect() every 5 turns
                                if _ic % 5 == 0:
                                    try:
                                        _rthought = self.consciousness.reflect(
                                            context=f"just replied to {user_name}"
                                        )
                                        if _rthought and _rthought.content:
                                            logger.debug(f"[Consciousness/reflect] {_rthought.content[:80]}")
                                    except Exception:
                                        pass

                                # meta_reflect() every 20 turns
                                if _ic % 20 == 0:
                                    try:
                                        _mt = self.consciousness.meta_reflect()
                                        if _mt and _mt.content:
                                            logger.debug(f"[Consciousness/meta] {_mt.content[:80]}")
                                            self.consciousness.self_.narrative.observe(
                                                f"Meta-reflection: {_mt.content[:120]}"
                                            )
                                    except Exception:
                                        pass

                                # fulfill_goal() — mark goals complete when reply touches them
                                try:
                                    _active_goals = self.consciousness.self_.goals.active
                                    for _g in list(_active_goals):
                                        _kws = _g.description.lower().split()[:3]
                                        if any(kw in _resp_clean.lower() for kw in _kws):
                                            self.consciousness.fulfill_goal(_g.description)
                                            break
                                except Exception:
                                    pass

                                # beliefs.weaken() when reply contradicts a belief
                                try:
                                    if re.search(r'\bactually\b', _resp_clean, re.I):
                                        for _b in self.consciousness.self_.beliefs.strongest(5):
                                            if _b.statement.lower()[:20] in _resp_clean.lower():
                                                self.consciousness.self_.beliefs.weaken(
                                                    _b.statement, delta=0.05
                                                )
                                                break
                                except Exception:
                                    pass
                            # Feed async thought loop thoughts into cognitive working memory
                            self.mind.feed_to_inner_mind(self.legacy_mind)
                            if _ic % 5 == 0:
                                state_path = Path(__file__).parent.resolve() / "shiro_state.json"
                                self.legacy_mind.save_state(str(state_path))
                            self.shiro_learn_and_stay_shiro(processed_text, full_response)
                            # ── SQLite RL self-critique ───────────────────────
                            # Records interaction in SQLite, triggers background
                            # RL evaluation, updates heuristics/anti-patterns.
                            if self.sql_memory is not None and not _is_greeting_directive:
                                try:
                                    self.sql_memory.record_interaction(
                                        user_name,
                                        processed_text,
                                        _resp_clean or full_response,
                                    )
                                except Exception as _rl_err:
                                    logger.debug(f"[SQLiteMemory] record_interaction error: {_rl_err}")
                            # ── Smart per-turn fact extraction ────────────────────────────
                            # Only fire if the message has actual content worth extracting.
                            # Skips: greetings, one-word replies, pure questions, acknowledgments.
                            _words_count = len(processed_text.split())
                            _is_trivial = (
                                _words_count <= 4
                                or _is_greeting_msg
                                or processed_text.strip().endswith("?")
                                or processed_text.strip().lower() in {
                                    "ok", "okay", "yeah", "yep", "nope", "lol",
                                    "haha", "nice", "sure", "thanks", "ty", "np"
                                }
                            )
                            if not _is_trivial:
                                # Defer fact extraction until the main stream and TTS
                                # are clearly done. Poll _last_response_ts — once it
                                # has been stable for 3s and the user isn't actively
                                # replying, the GPU is free. Cap total wait at 30s.
                                # This replaces the old fixed 3s sleep which wasn't
                                # long enough when the main stream took 12-14s.
                                def _delayed_fact_extract(
                                    _uid=user_name,
                                    _msg=processed_text,
                                    _rep=full_response,
                                ):
                                    waited = 0.0
                                    while waited < 30.0:
                                        time.sleep(2.0)
                                        waited += 2.0
                                        if (time.time() - self._last_response_ts > 3.0
                                                and not self.is_user_active()):
                                            break
                                    self._extract_facts_background(_uid, _msg, _rep)
                                threading.Thread(
                                    target=_delayed_fact_extract,
                                    daemon=True
                                ).start()
                            # ── Knowledge Graph population ────────────────────
                            # Extract entities from this turn and update the graph.
                            # Runs only on substantive messages (same gate as fact extract).
                            # Co-occurrence edges link concepts mentioned together.
                            if self.kg is not None and not _is_trivial:
                                try:
                                    # Pull attention tags from the cognitive pipeline
                                    # if available — they're already clean entity names.
                                    _kg_tags: list[str] = []
                                    if self.cognition:
                                        try:
                                            _cog_state = self.cognition.get_last_turn()
                                            if _cog_state:
                                                _kg_tags = list(
                                                    getattr(_cog_state, "attention_labels", [])
                                                    or getattr(_cog_state, "topics", [])
                                                    or []
                                                )[:6]
                                        except Exception:
                                            pass

                                    # Extract entities from user turn + Shiro reply
                                    _user_ents = extract_entities_simple(
                                        processed_text, tags=_kg_tags
                                    )
                                    _shiro_ents = extract_entities_simple(
                                        _resp_clean, tags=[]
                                    )
                                    _all_ents = list(dict.fromkeys(_user_ents + _shiro_ents))[:12]

                                    # Add individual entity nodes
                                    for _ent in _all_ents:
                                        self.kg.add_entity(_ent, entity_type="topic")

                                    # Always link user_name as a "person" node to
                                    # everything they mentioned — builds a per-person
                                    # topic web over time.
                                    self.kg.add_entity(user_name.lower(), entity_type="person")
                                    for _ent in _user_ents[:6]:
                                        self.kg.add_edge(
                                            user_name.lower(), _ent,
                                            relation="associated_with", weight=0.4
                                        )

                                    # Co-occurrence edges between all entities in turn
                                    if len(_all_ents) >= 2:
                                        self.kg.add_co_occurrence(
                                            _all_ents[:10], relation="co_occurs_with"
                                        )

                                    # Opportunistic flush every 20 turns
                                    if _ic % 20 == 0:
                                        self.kg.maybe_flush()
                                except Exception as _kg_e:
                                    logger.debug(f"[KG] Background update error: {_kg_e}")
                            if _ic % 10 == 0:
                                # Defer reflect until stream + TTS are done.
                                def _deferred_reflect(_uname=user_name):
                                    waited = 0.0
                                    while waited < 30.0:
                                        time.sleep(2.0)
                                        waited += 2.0
                                        if (time.time() - self._last_response_ts > 3.0
                                                and not self.is_user_active()):
                                            break
                                    if not self.is_user_active():
                                        self.reflect(_uname)
                                threading.Thread(
                                    target=_deferred_reflect,
                                    daemon=True
                                ).start()
                            if _ic % 15 == 0:
                                # Defer goal review until stream + TTS are done.
                                def _deferred_goal_review(
                                    _uname=user_name,
                                    _umsg=processed_text,
                                    _urep=full_response,
                                ):
                                    waited = 0.0
                                    while waited < 30.0:
                                        time.sleep(2.0)
                                        waited += 2.0
                                        if (time.time() - self._last_response_ts > 3.0
                                                and not self.is_user_active()):
                                            break
                                    if not self.is_user_active():
                                        self._autonomous_goal_review(_uname, _umsg, _urep)
                                threading.Thread(
                                    target=_deferred_goal_review,
                                    daemon=True,
                                    name="goal-review"
                                ).start()
                            # FIX: fire-and-forget — never block the background thread.
                            # _safe_async_run calls .result() which would open a second
                            # Ollama inference context while the main stream is still live.
                            if self._background_loop and self._background_loop.is_running():
                                asyncio.run_coroutine_threadsafe(
                                    self.check_and_think_async(user_name, previous_interaction),
                                    self._background_loop
                                )
                        except Exception as _e:
                            logger.warning(f"Background write error: {_e}")
                        finally:
                            self._background_writing.clear()

                    threading.Thread(target=_background_writes, daemon=True).start()

                    # Bubble continuation is no longer needed — the streaming yield
                    # above already sends each sentence to the UI and TTS as it
                    # arrives. _remaining_bubbles is kept as an empty list for
                    # compatibility but nothing is sent here.

                    # Token-limit continuation — pre-flight guards
                    _reply_for_cont_check = (_resp_clean or full_response).strip()
                    _reply_words_for_cont = len(_reply_for_cont_check.split())
                    _reply_ends_clean = (
                        _reply_for_cont_check.endswith((".","!","?","…"))
                        and _reply_words_for_cont >= 6
                    )
                    # Skip if the reply ends cleanly (punctuation + ≥6 words)
                    if _was_truncated and _reply_ends_clean:
                        logger.debug("[Continuation] Skipped — reply ends cleanly after trim.")
                    # Skip if the reply is very short — the model will invent rather than continue.
                    # A genuine truncation mid-sentence will have at least 8 words before the cut.
                    _reply_too_short_for_cont = _reply_words_for_cont < 8
                    if _was_truncated and _reply_too_short_for_cont:
                        logger.debug(f"[Continuation] Skipped — reply too short ({_reply_words_for_cont}w) to have a real cut-off.")
                    _is_retry_msg = processed_text.startswith("[respond to:")
                    if (_was_truncated and not _reply_ends_clean and not _reply_too_short_for_cont
                            and not _is_retry_msg and self.on_autonomous_speak):
                        # Build "already said" ending at the exact cut point.
                        # _last_fragment_before_cut is the partial sentence at the cut.
                        # We append it only if it's a real partial (no sentence-ending
                        # punctuation) — if it already ends cleanly, drop it to avoid
                        # the LLM seeing a complete sentence and starting something new.
                        _cont_base = _resp_clean if _resp_clean else full_response.strip()
                        _tail = _last_fragment_before_cut.strip()
                        _tail_is_partial = _tail and not _tail[-1] in ".!?"
                        if _tail_is_partial and not _cont_base.endswith(_tail):
                            _already_said_for_cont = _cont_base.rstrip() + " " + _tail
                        else:
                            _already_said_for_cont = _cont_base
                        # acknowledge=True when the cut-off was mid-sentence and
                        # visible to the user (reply doesn't end with punctuation).
                        # Shiro reacts naturally then continues her thought.
                        _cont_is_visible_cut = not _already_said_for_cont.strip()[-1:] in ".!?…"
                        def _continue_thought(
                            already=_already_said_for_cont,
                            ack=_cont_is_visible_cut
                        ):
                            time.sleep(0.6)
                            if self._continuation_cancel.is_set():
                                logger.debug("[Continuation] Cancelled — new message arrived.")
                                return
                            self._safe_async_run(
                                self._continue_truncated_response(
                                    user_name, processed_text, already,
                                    acknowledge=ack
                                )
                            )
                        threading.Thread(target=_continue_thought, daemon=True).start()

            except Exception as e:
                logger.error(f"Engine text processing failed: {e}. Falling back to RuleEngine.")
                try:
                    rel_tier = self.legacy_mind.relationship.level.name.lower()
                    mood = self.mind.mood.value
                    fallback_intent = (
                        "empathize"
                        if self.sentiment_trajectory.current_valence(user_name) < -0.3
                        else "share"
                    )
                    response = self.rules.full_response(
                        intent=fallback_intent,
                        mood=mood,
                        user_message=processed_text,
                        user_name=user_name,
                        trajectory=trajectory
                    )
                    yield response
                    self.memory.add_interaction(text, response, user_id=user_name)
                except Exception as fallback_err:
                    logger.error(f"RuleEngine fallback also failed: {fallback_err}")
                    raise e

    # ── Thought / meta stripping ──────────────────────────────────────────────

    def _scrub_inner_mind_block(self, text: str) -> str:
        """
        Removes the full +-- Shiro's mind --+ box from inner_context
        before it gets injected into the LLM context sandwich.
        The box is a VISUAL debug aid — not meant for the LLM to see or repeat.
        Keeps only the plain insight/strategy text beneath the box if any.
        """
        # Remove full box blocks
        text = _INNER_MIND_BOX.sub('', text)
        # Remove any leftover box lines
        lines = text.splitlines()
        clean = [l for l in lines if not re.match(r'^\s*[|+]', l)]
        return '\n'.join(clean).strip()

    def _extract_thought_from_stream(self, stream):
        """
        Filters inner thought content from the LLM stream.
        NOTHING between thought markers should ever reach the UI.
        Uses a strict state machine: once inside a thought block, all content
        is captured to self.last_thought until the block closes.
        """
        buffer = ""
        in_thought = False

        # P1 FIX: Use precompiled class-level patterns instead of compiling per-call
        start_re = self._STREAM_START_RE
        end_re   = self._STREAM_END_RE

        for chunk in stream:
            buffer += chunk

            while True:
                if not in_thought:
                    m = start_re.search(buffer)
                    if m:
                        # Yield everything before the thought marker
                        pre = buffer[:m.start()]
                        if pre:
                            yield pre
                        # Enter thought mode — don't store the tag itself, only the content
                        # Fix: storing m.group(0) put "[THOUGHT]" into the thought log
                        buffer = buffer[m.end():]
                        in_thought = True
                        continue
                    else:
                        # No thought marker — safe to yield, keep small tail for safety
                        if len(buffer) > 50:
                            yield buffer[:-50]
                            buffer = buffer[-50:]
                        break
                else:
                    # Inside a thought block — look for end marker
                    m = end_re.search(buffer)
                    if m:
                        self.last_thought += buffer[:m.start()]
                        buffer = buffer[m.end():] # SPACE FIX: removed .lstrip()
                        in_thought = False
                        # Mark this as a real LLM-generated thought so the
                        # background loop doesn't overwrite it before logging.
                        self._thought_is_real = True
                        continue
                    else:
                        # ── HARDENING: disabled — this heuristic caused thought content
                        # to bleed into the spoken reply (false-positive early exit from
                        # thought mode). The buffer overflow guard below handles runaway
                        # thought blocks safely without misidentifying thought content
                        # as leaked dialogue.
                        pass

                        # Still inside thought — check for box end via | lines
                        # If we see a line that clearly starts real dialogue (doesn't start with |)
                        if '\n' in buffer:
                            parts = buffer.split('\n', 1)
                            line, rest = parts[0], parts[1]
                            # Box continuation lines start with |
                            if re.match(r'^\s*\|', line):
                                self.last_thought += line + '\n'
                                buffer = rest
                                continue
                            # If the rest looks like real prose (capital letter, not a box char)
                            if rest.strip() and rest.strip()[0].isupper() and not re.match(r'^\s*[|+]', rest):
                                self.last_thought += line
                                buffer = rest
                                in_thought = False
                                continue
                        # Buffer overflow guard
                        if len(buffer) > 3000:
                            self.last_thought += buffer
                            buffer = ""
                            in_thought = False
                        break

        # Flush remaining buffer
        if buffer:
            if in_thought:
                self.last_thought += buffer
            else:
                # Final check — don't yield if it looks like a stray meta block
                if not start_re.search(buffer.split('\n')[0]):
                    yield buffer

    def _clean_response(self, text: str, user_query: Optional[str] = None) -> str:
        """Per-fragment cleanup. Removes leaked meta content."""

        # Remove full inner mind box blocks
        text = _INNER_MIND_BOX.sub('', text)

        # Remove LOG directives (should not appear in output but just in case)
        text = _LOG_DIRECTIVE.sub('', text)

        # P2 FIX: Use precompiled class-level patterns
        text = self._CLEAN_THOUGHT_BLOCK_RE.sub('', text)
        text = self._CLEAN_PAREN_BLOCK_RE.sub('', text)
        text = re.sub(r'<THOUGHTS?>.*?</THOUGHTS?>', '', text, flags=re.IGNORECASE | re.DOTALL)

        # SPACE FIX: Removed .strip() from internal steps to preserve intentional spacing
        # yielded by split_into_sentences()
        text = self._CLEAN_OPEN_TAG_RE.sub('', text, count=1)
        text = self._CLEAN_ASTERISK_RE.sub('', text)
        text = self._CLEAN_BOXLINE_RE.sub('', text)
        # Fix A: Drop any remaining CoT step blocks
        text = self._CLEAN_COT_LINE_RE.sub('', text)

        # Echo stripping — if response starts with what the user said
        if user_query:
            query_clean = user_query.strip().lower()
            if text.lower().startswith(query_clean):
                text = text[len(query_clean):].lstrip(" :,.-")

        # Speaker prefix stripping
        text = _SPEAKER_PREFIX.sub('', text)
        text = self._CLEAN_SPEAKER_BARE_RE.sub('', text)

        # Metadata line stripping — handle inline leaks like | momentum: stable |
        text = self._CLEAN_METADATA_LINE_RE.sub('', text)

        # Uppercase normalization
        lines = text.splitlines()
        text = '\n'.join(
            l.capitalize() if l.isupper() else l for l in lines
        )

        return text

    def _final_sanitize(self, text: str) -> str:
        """
        Last-resort sanitizer runs on the fully assembled response.
        Catches anything that slipped through the stream extractor and per-fragment cleaner.
        """
        # Extract and preserve [GOAL:...] tags before broad bracket stripping.
        # These are legitimate action tokens Shiro embeds to create/complete goals.
        # They must survive sanitization so process_shiro_reply() can read them.
        _preserved_goal_tags = re.findall(r'\[GOAL:[^\]]{1,200}\]', text, re.IGNORECASE)
        # Strip ALL [...] tag pairs first — catches [THOUGHT], [tsun], [mischievous]
        # and any other persona/meta tags the model outputs.
        # Pattern: [TAG_NAME] ... [/TAG_NAME] (matched pairs, greedy within reason)
        # Updated to allow spaces and varied characters inside brackets.
        text = re.sub(r'\[[^\]\n]{1,50}\].*?\[/[^\]\n]{1,50}\]',
                      '', text, flags=re.DOTALL | re.IGNORECASE)
        # Strip any remaining unpaired opening tags like [tsun], [THOUGHT], [Mask check]
        # but EXEMPT [GOAL:...] tags — those are action tokens, not display tags
        text = re.sub(r'\[(?!GOAL:)[^\]\n]{1,50}\]', '', text, flags=re.IGNORECASE)
        # Re-append preserved goal tags at the end so process_shiro_reply() can find them
        if _preserved_goal_tags:
            text = text.rstrip() + " " + " ".join(_preserved_goal_tags)

        # Strip inner mind boxes
        text = _INNER_MIND_BOX.sub('', text)

        # Strip LOG directives
        text = _LOG_DIRECTIVE.sub('', text)

        # Strip any line that looks like a meta header
        lines = text.splitlines()
        clean_lines = []
        for line in lines:
            # Drop box frame lines
            if re.match(r'^\s*[\|+]', line):
                continue
            # Drop lines that are pure bracketed meta
            if re.match(r'^\s*\[(?:' + self.THOUGHT_KEYWORDS + r')[^\]]*\]\s*$', line, re.IGNORECASE):
                continue
            _kw_colon_m = re.match(
                r'^\s*(?:' + self.THOUGHT_KEYWORDS.replace('|', r'|^\s*') + r')\s*:\s*(.*)',
                line, re.IGNORECASE
            )
            if _kw_colon_m:
                _after_colon = _kw_colon_m.group(1).strip()
                if _after_colon:
                    line = _after_colon
                else:
                    continue
            clean_lines.append(line)

        text = '\n'.join(clean_lines).strip()

        # ── Post-filter: strip scare quotes and banned words ─────────────
        # Strip 'quoted' words used for ironic effect: 'clumsy', 'interesting', etc.
        # Pattern: a word wrapped in single-quotes that isn't a contraction
        import re as _re2
        text = _re2.sub(r"(?<![a-zA-Z])'([a-zA-Z][a-zA-Z ']{2,30})'(?![a-zA-Z])",
                        r'\1', text)  # strip scare quotes, keep word
        # Hard-ban specific words that have become stale loops
        _BANNED = [
            (re.compile(r'\bclumsy\b', re.IGNORECASE), 'awkward'),
            # Overused phrases baked into the model — replace with nothing or alternatives
            (re.compile(
                r"as if some piece of hardware could ever capture my unique spirit[!.]?",
                re.IGNORECASE), ''),
            (re.compile(
                r"some piece of hardware could ever capture",
                re.IGNORECASE), 'anything could ever capture'),
            (re.compile(r'\bclodhopper\b', re.IGNORECASE), 'clumsy human'),
            (re.compile(r'\bunproper\b', re.IGNORECASE), 'improper'),
            (re.compile(r'\bunparalleled wit and style\b', re.IGNORECASE), 'sharp wit'),
            (re.compile(r"\ba kitsune of unparalleled\b", re.IGNORECASE), 'a kitsune with serious'),
        ]
        for _pat, _rep in _BANNED:
            text = _pat.sub(_rep, text)
        # Clean up double spaces left by empty replacements
        text = re.sub(r'  +', ' ', text).strip()

        # One more speaker prefix pass
        text = _SPEAKER_PREFIX.sub('', text).strip()

        # Fix 3: Missing spaces after contractions — model artifact where tokens
        # "i'd" and "choose" are emitted without a space between them.
        # Pattern: letter followed by apostrophe-contraction-suffix immediately followed by another letter.
        # e.g. "i'dchoose" → "i'd choose", "it'snot" → "it's not"
        # This prevents breaking single-quoted words like 'moving on'.
        text = re.sub(
            r"([a-zA-Z])('(?:d|s|t|ve|re|ll|m|nt))([a-zA-Z])",
            r"\1\2 \3", text
        )

        # Fix 3b: Missing spaces after sentence-ending punctuation — model artifact
        # where "anything!just" or "it.now" are emitted as fused tokens.
        # Only fires when a letter immediately follows .!?… with no space.
        text = re.sub(r'([.!?…])([a-zA-Z])', r'\1 \2', text)
        # Fix model artifact: 'word? !' → 'word!' and 'word! —' alone at end → 'word!'
        text = re.sub(r'([?!])\s*!', r'\1', text)
        text = re.sub(r'([?!,])\s*—\s*$', r'\1', text)

        # Strip [CRITICAL PROTOCOL], [MANDATORY], [IDENTITY] echoes
        text = re.sub(
            r'\[(?:CRITICAL PROTOCOL|MANDATORY|IDENTITY RULE|ABSOLUTE OUTPUT RULE)[^\]]*\]\s*',
            '', text, flags=re.IGNORECASE
        ).strip()

        # Fix A: Strip chain-of-thought reasoning blocks the model outputs.
        # shiro:latest has CoT baked in — it generates "## Step 1:" sections
        # that are internal reasoning, never meant for the user.
        # Remove any line that starts with "## Step", "Step N:", or looks like
        # a numbered reasoning header. Also strip the content of entire CoT blocks.
        lines = text.splitlines()
        clean = []
        in_cot = False
        for line in lines:
            stripped = line.strip()
            # Detect CoT header lines
            if re.match(r'(?i)^(?:##\s*)?step\s*\d+\s*[:.-]', stripped):
                in_cot = True
                continue  # drop the header
            # Once inside CoT, keep dropping until we hit a blank line or non-header
            if in_cot:
                if not stripped:  # blank line ends CoT block
                    in_cot = False
                continue  # drop CoT body lines
            clean.append(line)
        text = '\n'.join(clean).strip()

        # Fix C: Apply space repair here so ALL paths (async, autonomous,
        # continuation) get cleaned — not just the streaming sentence path.
        text = re.sub(r'([.!?,])([A-Za-z])', r'\1 \2', text)  # punct→letter
        text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)        # camelCase
        # contraction suffix directly followed by a letter
        text = re.sub(r"([a-zA-Z])('(?:d|s|t|ve|re|ll|m|nt))([a-zA-Z])", r"\1\2 \3", text)

        # Fix for "I've to" -> "I have to" (Shiro sometimes over-shortens)
        text = re.sub(r"\b([Ii])'ve\s+to\b", r"\1 have to", text)

        # Fix D: Filler phrase deduplication + hard cap.
        # Shiro's model spams em-dash hedges as verbal tics. Rules:
        #  - Each phrase may appear AT MOST ONCE per reply (remove all duplicates)
        #  - Total filler phrases across entire reply capped at 1
        #  - Standalone trailing fillers at end of sentence are stripped entirely
        _FILLER_PHRASES = [
            r'—\s*anyway\s*\.?\s*—',
            r'—\s*you know\?\s*—',
            r"—\s*what\'?s your take\?\s*—",
            r'—\s*if that makes sense\.?\s*—',
            r'—\s*just saying\.?\s*—',
            r"—\s*don\'t you think\?\s*—",
        ]
        # Count total filler occurrences
        _total_fillers = 0
        for pattern in _FILLER_PHRASES:
            _total_fillers += len(re.findall(pattern, text, flags=re.IGNORECASE))
        # If more than 1 total filler in this reply, strip ALL of them
        # If exactly 1, keep it (natural Shiro voice)
        if _total_fillers > 1:
            for pattern in _FILLER_PHRASES:
                text = re.sub(pattern, '', text, flags=re.IGNORECASE)
        elif _total_fillers == 1:
            # Keep it but remove any duplicates of that one
            for pattern in _FILLER_PHRASES:
                matches = list(re.finditer(pattern, text, flags=re.IGNORECASE))
                if len(matches) > 1:
                    for m in reversed(matches[1:]):
                        text = text[:m.start()] + text[m.end():]
        text = re.sub(r'\s*—\s*—\s*', ' — ', text)
        text = re.sub(r'\s{2,}', ' ', text).strip()

        # ── Untagged internal thought leak detector ───────────────────────────
        # The model sometimes outputs internal reasoning as plain prose without
        # thought markers. Detectable by Shiro referring to herself in third person
        # or narrating her own processing.
        # IMPORTANT: keep patterns tight — false positives silence real replies.
        # Removed: "i'll allow/note/keep it" (legitimate Shiro phrases)
        # Removed: "phrasing was" (too broad — hits normal sentences)
        # Removed: "respond directly" (too broad)
        _SELF_NARRATION = re.compile(
            r'\b(?:'
            r'be\s+(?:a\s+)?(?:good|careful|direct|honest|patient)\s+(?:listener|respondent|shiro)\s*[,—]'
            r'|shiro\s*[,—]\s*(?:remember|don\'?t|focus|stop)'
            r'|the\s+(?:request|question|message|context|situation)\s+makes\s+(?:more\s+)?sense\s+now'
            r'|i\s+(?:was|am)\s+genuinely\s+(?:confused|uncertain)\s+by\s+the\s+context\s+shift'
            r')',
            re.IGNORECASE
        )
        if _SELF_NARRATION.search(text):
            sentences = re.split(r'(?<=[.!?])\s+', text)
            clean_sentences = [s for s in sentences if not _SELF_NARRATION.search(s)]
            stripped = " ".join(clean_sentences).strip()
            if stripped != text:
                logger.debug("[SelfNarration] Stripped untagged internal thought from response.")
                text = stripped

        # ── Fabricated memory detector + stripper ────────────────────────────
        # Catches hallucinations where Shiro claims the user said something in a
        # PAST or PREVIOUS session that isn't in context.
        # IMPORTANT: must NOT catch present-tense references like "you said X"
        # referring to what was just typed — that's normal conversation.
        # Only flag cross-session fabrications with temporal markers.
        _FAKE_MEMORY_PATTERNS = re.compile(
            r'\b(?:'
            r'you\s+once\s+said'
            r'|you\s+used\s+to\s+say'
            r'|last\s+time\s+we\s+(?:talked|spoke|chatted|met)'
            r'|(?:last|previous)\s+(?:session|conversation|time\s+we)'
            r'|back\s+when\s+you\s+(?:told|said|mentioned|used)'
            r'|you\s+told\s+me\s+(?:before|last|a\s+while|once|that\s+time)'
            r')\b',
            re.IGNORECASE
        )
        if _FAKE_MEMORY_PATTERNS.search(text):
            sentences = re.split(r'(?<=[.!?])\s+', text)
            clean_sentences = [s for s in sentences if not _FAKE_MEMORY_PATTERNS.search(s)]
            stripped_sentences = [s for s in sentences if _FAKE_MEMORY_PATTERNS.search(s)]
            logger.warning(
                f"[FakeMemory] Fabricated recall stripped: "
                f"{[s[:80] for s in stripped_sentences]}"
            )
            if clean_sentences:
                # Keep the non-fabricated sentences — don't wipe the whole reply
                text = " ".join(clean_sentences).strip()
            else:
                # Every sentence was flagged — log the raw content and return as-is
                # rather than returning empty (empty → blinking bubble → broken UI)
                logger.warning(
                    f"[FakeMemory] All sentences flagged — keeping raw to avoid empty bubble. "
                    f"Raw: {text[:200]!r}"
                )
                # Strip just the flagged clause rather than the whole reply
                text = _FAKE_MEMORY_PATTERNS.sub("", text).strip()
                text = re.sub(r"\s{2,}", " ", text).strip("., ")

        if not text or not text.strip():
            logger.warning("[Sanitize] _final_sanitize returned empty string — full response was stripped.")
            return ""
        return text

    # ── Temporal context ──────────────────────────────────────────────────────

    # ── Hallucination / coherence guard ─────────────────────────────────────────
    # Patterns that indicate Shiro invented something specific she shouldn't know.
    # These are heuristic — they catch obvious fabrication without an LLM call.
    _INVENTED_PROPER_NOUN = re.compile(
        # Matches: "it's called [Title]" — Shiro inventing a proper name
        r"it['\s]?s called\s+([A-Z][A-Za-z0-9 ]{2,35})(?:\s|[.!?,]|$)",
        re.IGNORECASE
    )
    _INVENTED_CLAIM_PATTERNS = re.compile(
        r"\b(i invented|i made up|i created|i named it|i call it|"
        r"it.?s a game i|it.?s something i|i designed it|"
        r"only telling you because you asked|don.t go looking for it|"
        r"i.?m only telling you|between us|just between you and me)\b",
        re.IGNORECASE
    )
    # Signals that Shiro is deflecting instead of answering
    _DEFLECTION_PATTERNS = re.compile(
        r"^(hmm\.?\s*$|well\.?\s*$|interesting\.?\s*$|"
        r"i.?m still thinking about it\.?\s*$|"
        r"just a thread of connection\.?\s*$|"
        r"i.?ve been sitting with something\.?\s*$)",
        re.IGNORECASE
    )

    def _coherence_guard(
        self,
        response: str,
        user_query: str,
        context_text: str = "",
    ) -> str:
        """
        Lightweight post-generation coherence and hallucination check.

        Catches:
        1. Invented proper nouns / titles not present in context
        2. Fabrication-marker phrases ("it's called X, but don't go looking")
        3. Pure deflection when a direct answer was asked for

        Does NOT call the LLM — pure regex + heuristic.
        Logs warnings for monitoring; strips or flags suspicious content.
        """
        if not response or not response.strip():
            return response

        text = response.strip()

        # ── Check 1: Invented title/name not in context ───────────────────────
        # If Shiro says "it's called X" and X doesn't appear anywhere in the
        # recent context or memory, it's very likely fabricated.
        _inv_match = self._INVENTED_PROPER_NOUN.search(text)
        if _inv_match:
            invented_name = _inv_match.group(1).strip()
            # Only flag if the name doesn't appear in context
            context_lower = context_text.lower()
            if invented_name.lower() not in context_lower:
                logger.warning(
                    f"[CoherenceGuard] Possible invented name: '{invented_name}' "
                    f"not found in context. Response: {text[:80]}"
                )
                # Strip the sentence containing the invented name
                sentences = re.split(r'(?<=[.!?])\s+', text)
                clean = [s for s in sentences if invented_name.lower() not in s.lower()]
                if clean:
                    text = " ".join(clean).strip()
                    logger.debug(f"[CoherenceGuard] Stripped invented name sentence.")

        # ── Check 2: Fabrication-marker phrases ───────────────────────────────
        if self._INVENTED_CLAIM_PATTERNS.search(text):
            logger.warning(
                f"[CoherenceGuard] Fabrication marker detected: {text[:100]}"
            )
            sentences = re.split(r'(?<=[.!?])\s+', text)
            clean = [s for s in sentences
                     if not self._INVENTED_CLAIM_PATTERNS.search(s)]
            if clean:
                text = " ".join(clean).strip()
                logger.debug(f"[CoherenceGuard] Stripped fabrication-marker sentence.")

        # ── Check 3: Pure deflection on a direct question ────────────────────
        # If the user asked a direct question (ends with ?) and Shiro's entire
        # response is a vague non-answer, flag it. We don't strip — just log.
        # The model needs to learn this through training, not post-hoc stripping.
        if user_query.strip().endswith("?") and self._DEFLECTION_PATTERNS.match(text):
            logger.warning(
                f"[CoherenceGuard] Pure deflection on direct question. "
                f"Q: '{user_query[:60]}' A: '{text[:60]}'"
            )
            # Don't strip — a deflection is sometimes valid ("i'm still thinking").
            # Just log so we can identify dataset gaps to fix.

        return text if text else response

    def _compress_context(self, full_context: str, char_cap: int, user_name: str) -> str:
        """
        Smart context compression for the RAG memory block.

        When full_context exceeds char_cap (~800 tokens), this method:
          1. Splits full_context into: session header + RAG memories + user facts
          2. Checks a 60-second cache — if the same RAG block was recently
             summarized, reuses the cached result (rapid back-and-forth skip)
          3. Runs a fast LLM summarization call on the RAG memories only,
             condensing them to key facts that fit the budget
          4. Reassembles: session header + compressed facts + user facts
          5. Falls back to hard truncation if the LLM call fails

        Conversation history (short_term_buffer) is never touched.
        User facts (garnish) are always preserved in full.
        The summarizer uses max_tokens=120 so the call is fast (~1-2s).
        """
        # ── Split full_context into sections ─────────────────────────────────
        # Structure: "## Session Context\n...\n\n### HISTORICAL MEMORIES\n...\n\n### Facts..."
        # We want to compress only the HISTORICAL MEMORIES section.

        _garnish_marker = "### Facts" if "### Facts" in full_context else "### WHAT YOU KNOW"
        _history_marker = "### HISTORICAL MEMORIES"

        # Extract garnish (user facts) — always preserved
        _garnish_start = full_context.rfind(_garnish_marker)
        _garnish_part  = full_context[_garnish_start:] if _garnish_start > 0 else ""
        _pre_garnish   = full_context[:_garnish_start] if _garnish_start > 0 else full_context

        # Extract the RAG memories section
        _hist_start = _pre_garnish.find(_history_marker)
        if _hist_start > 0:
            _session_header = _pre_garnish[:_hist_start].rstrip()
            _rag_block      = _pre_garnish[_hist_start:]
        else:
            # No HISTORICAL MEMORIES marker — just a big block, compress it whole
            _session_header = ""
            _rag_block      = _pre_garnish

        # ── Cache check ───────────────────────────────────────────────────────
        # Cache key: first 200 chars of the RAG block (stable fingerprint).
        # TTL raised from 60s → 120s — reduces LLM compress calls in fast
        # back-and-forth sessions. Memory changes at most once per turn anyway.
        _cache_key = f"_ctx_compress_{user_name}_{_rag_block[:200]}"
        _now_mono  = time.monotonic()
        _cached    = self._consciousness_static_cache.get(_cache_key)
        if _cached and (_now_mono - _cached[0]) < 120.0:
            _compressed_rag = _cached[1]
            logger.debug("[CtxCompress] Cache hit — reusing compressed RAG block.")
        else:
            # ── Summarize ─────────────────────────────────────────────────────
            # Budget capped at 80 tokens (was 150) — enough for a tight bullet
            # list of key facts. Lower cap = shorter inference = less VRAM spike.
            _budget_chars  = char_cap - len(_session_header) - len(_garnish_part) - 50
            _budget_tokens = max(40, min(80, _budget_chars // 4))

            _compress_system = (
                "You are a memory distiller. Read the memory block below and output "
                "ONLY the most important facts as a tight bullet list. "
                "Each bullet: one short sentence. No padding, no headers, no intro. "
                "Focus on: what the user has said, key events, notable preferences, "
                "anything Shiro should actually remember. Drop vague or generic entries."
            )
            _compress_prompt = (
                f"Distill this into {_budget_tokens} tokens or fewer. "
                f"Plain bullet list only — no preamble:\n\n{_rag_block[:4000]}"
            )

            try:
                _old_max = self.llm.max_tokens
                self.llm.max_tokens = _budget_tokens
                _summary = self.llm.generate_response(
                    _compress_system, _compress_prompt, [], context=""
                )
                self.llm.max_tokens = _old_max

                _summary = (_summary or "").strip()
                if len(_summary) > 20:
                    _compressed_rag = f"### KEY MEMORIES (compressed)\n{_summary}"
                    self._consciousness_static_cache[_cache_key] = (_now_mono, _compressed_rag)
                    logger.info(
                        f"[CtxCompress] RAG {len(_rag_block)}→{len(_compressed_rag)} chars "
                        f"({len(_rag_block)//4}→{len(_compressed_rag)//4} tokens est.)"
                    )
                else:
                    raise ValueError("Summary too short — falling back")

            except Exception as _ce:
                logger.debug(f"[CtxCompress] Summarization failed ({_ce}) — using hard truncation")
                # Fallback: hard truncation, always keep garnish
                _meat_budget = char_cap - len(_garnish_part) - 4
                _truncated   = full_context[:max(0, _meat_budget)]
                return _truncated + ("\n\n" + _garnish_part if _garnish_part else "")

        # ── Reassemble ───────────────────────────────────────────────────────
        parts = [p for p in [_session_header, _compressed_rag, _garnish_part] if p]
        result = "\n\n".join(parts)

        # Safety: if somehow still over cap, hard-trim (should not happen)
        if len(result) > char_cap * 1.2:
            result = result[:char_cap]

        return result

    def _add_temporal_context(self, context: str, user_name: str) -> str:
        now_utc = datetime.now(timezone.utc)
        if user_name not in self.user_session_info:
            last_time = self.memory.get_last_interaction_time(user_name)
            downtime_str = "first time meeting"
            if last_time:
                downtime_delta = self.session_start - last_time
                if downtime_delta.total_seconds() < 0:
                    downtime_delta = timedelta(0)
                downtime_str = self._format_timedelta(downtime_delta)
            # PERF: cache both downtime and last_time so we never call get_last_interaction_time twice
            self.user_session_info[user_name] = {"downtime": downtime_str, "last_time": last_time}

        downtime_str = self.user_session_info[user_name]["downtime"]
        last_time = self.user_session_info[user_name].get("last_time")  # PERF: no second DB call
        duration_str = "some time"
        if last_time:
            delta = now_utc - last_time
            duration_str = self._format_timedelta(delta)

        uptime_delta = now_utc - self.session_start
        uptime_str = self._format_timedelta(uptime_delta)
        current_time_str = now_utc.astimezone().strftime('%I:%M %p')
        current_date_str = now_utc.astimezone().strftime('%A, %B %d, %Y')

        # ── FIX: Also include session message count so Shiro knows the conversation
        #         has been going on — prevents "just started" confusion ──────────
        history = self.memory.get_history()
        session_msg_count = len(history)

        # BUG FIX: Removed [TEMPORAL CONTEXT], [DOWNTIME BEFORE SESSION], [TIME SINCE LAST SEEN]
        # bracket tokens — they were teaching the LLM that bracket tokens are valid output format.
        # Now uses plain-language section headers that read as data, not template tokens.
        # ── Boot gap injection (fires only on first message after restart) ─────
        boot_gap_note = ""
        if hasattr(self, "_boot_gap_str") and self._boot_gap_str:
            _last_exchange = getattr(self, '_boot_last_exchange', '')
            boot_gap_note = (
                f"\n[RESTART GAP] You were offline for {self._boot_gap_str}. "
                f"{'Last exchange before shutdown: ' + _last_exchange + '.' if _last_exchange else ''} "
                "You are now back. React naturally — a brief wry acknowledgement of the gap is fine, "
                "or just continue as if nothing happened. Do not recite this note."
            )
            self._boot_gap_str = ""
            self._boot_last_exchange = ""

        temporal_note = (
            f"The current time is {current_time_str} on {current_date_str}.\n"
            f"- Shiro was inactive for {downtime_str} before this session started.\n"
            f"- It has been {duration_str} since the last interaction with {user_name}.\n"
            f"- Session uptime: {uptime_str}.\n"
            f"- Session messages: {session_msg_count} exchanged so far. "
            f"This is {'an ongoing' if session_msg_count > 4 else 'a new'} conversation.\n"
            "Mention downtime or duration ONLY if it serves your teasing or if you want to complain about being lonely."
            f"{boot_gap_note}"
        )
        # Explicitly label the retrieved RAG content to distinguish it from the current conversation
        if context.strip():
            context = f"### HISTORICAL MEMORIES (from previous sessions):\n{context}"

        return f"## Session Context\n{temporal_note}\n\n{context}"

    def _format_timedelta(self, delta: timedelta) -> str:
        days = delta.days
        hours, remainder = divmod(int(delta.seconds), 3600)
        minutes, seconds = divmod(remainder, 60)
        time_parts = []
        if days > 0:    time_parts.append(f"{days} day{'s' if days > 1 else ''}")
        if hours > 0:   time_parts.append(f"{hours} hour{'s' if hours > 1 else ''}")
        if minutes > 0: time_parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        if not time_parts or (days == 0 and hours == 0 and minutes < 5):
            time_parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
        if len(time_parts) == 1:
            return time_parts[0]
        return ", ".join(time_parts[:-1]) + f" and {time_parts[-1]}"

    # ── HyDE ─────────────────────────────────────────────────────────────────

    def _imagine_reply(self, query: str) -> str:
        if len(query.split()) < 15:
            return query
        try:
            hypothetical_prompt = (
                "Provide a neutral, factual answer to this query as it might appear "
                "in a prior conversation log. Use specific nouns and keywords only. No personality."
            )
            return self.llm.generate_response(
                "You are Shiro's Memory Assistant.",
                f"USER QUERY: {query}",
                [],
                context=hypothetical_prompt
            )
        except Exception as e:
            logger.warning(f"HyDE imagine_reply failed: {e}")
            return query

    # ── Async memory / tools ──────────────────────────────────────────────────

    async def fetch_relevant_memory_async(self, query_text: str, n_results: int = 2):
        results = await self.memory.search_relevant_memories_async(query_text, n_results=n_results)
        return [r['content'] for r in results] if results else []

    async def handle_tool_calls_async(self, fragment: str, user_msg: str):
        """Tool call handler — currently a no-op since no tools are defined."""
        try:
            logger.debug(f"[Tools] Unhandled tool call fragment: {fragment[:60]}")
        except Exception as e:
            logger.error(f"Tool execution failed: {e}")
        return fragment

    async def check_and_think_async(self, user_id: str, last_interaction: datetime):
        # Wait until the main stream AND TTS are done before opening a second
        # inference context.  8s was too short — TTS of a 3-sentence reply takes
        # 10-18s on typical hardware, causing concurrent Ollama requests that
        # interleave tokens or stall each other.
        await asyncio.sleep(25.0)
        if self.is_user_active():
            logger.debug("[check_and_think] Skipping — user still active.")
            return
        # Also skip if TTS is still playing
        if hasattr(self, '_tts_ref') and self._tts_ref and getattr(self._tts_ref, 'is_speaking', False):
            logger.debug("[check_and_think] Skipping — TTS still speaking.")
            return
        if datetime.now(timezone.utc) - last_interaction > timedelta(minutes=30):
            with self.brain_lock:
                today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
                thought_data = self.brain.get("autonomous_thoughts", {})
                if thought_data.get("date") != today:
                    thought_data = {"date": today, "count": 0}
                if thought_data["count"] >= 5:
                    logger.info("Autonomous thought daily cap reached.")
                    return
            logger.info(f"Triggering inactivity thought for {user_id} ({thought_data['count']+1}/5)")
            thought_prompt = "Reflect on current interactions and inactivity. Update your internal plans."
            context = await self.memory.get_full_context_async("Autonomous reflection", user_id=user_id)
            thought = await self.llm.generate_response_async(
                self.persona.get_system_prompt() + f"\n{self.outfit_block()}",
                thought_prompt,
                self.memory.get_history()[-8:],
                context=context
            )
            await self.memory.store_insight_async(
                f"Autonomous Thought: {thought}", user_id=user_id, source="autonomous_thought"
            )
            with self.brain_lock:
                thought_data["count"] += 1
                self.brain["autonomous_thoughts"] = thought_data
                self._save_brain()

    # ── Reflection ────────────────────────────────────────────────────────────


    def _split_into_bubbles(self, text: str) -> list:
        """
        Split a full response into natural multi-message bubbles.

        Short responses (≤2 real sentences) stay as one bubble.
        Longer responses split into 1-2 sentence groups — mimics how a real
        person types in a chat room: quick bursts, not walls of text.

        Bubbles 2+ are delivered via on_autonomous_speak("bubble_continuation")
        with typing-speed-scaled delays. They are NOT re-stored in memory
        (the full response is already there) to prevent echo/repetition.

        ARTIFACT GUARD: Normalises ". ." / ". …" ellipsis patterns before
        splitting so Shiro's trailing-off speech style doesn't create
        orphaned single-dot bubbles.
        """
        text = text.strip()
        if not text:
            return [text]

        # Normalise ellipsis artifacts before splitting
        # ". ." → "…"   |   ". …" → "…"   |   ".. " → "… "
        text = re.sub(r'\.\s+\.\s*\.?', '…', text)
        text = re.sub(r'\.\s+…', '…', text)

        # Split ONLY where a sentence-ending char is followed by whitespace
        # AND then an uppercase letter — avoids splitting on abbreviations,
        # "no cap. fr" or Shiro's lowercase fragmented speech.
        sentence_endings = re.compile(r'(?<=[.!?])\s+(?=[A-Z])')
        sentences = [s.strip() for s in sentence_endings.split(text) if s.strip()]

        # Filter artifact fragments (anything that's mostly punctuation)
        sentences = [s for s in sentences if len(re.sub(r'[^\w]', '', s)) >= 2]

        if len(sentences) <= 2:
            return [text]  # short — keep as single bubble

        # Group into 1-2 sentence bubbles.
        # Very short groups (< 10 words) absorb the next sentence.
        bubbles = []
        group = []
        for i, sent in enumerate(sentences):
            group.append(sent)
            word_count = sum(len(s.split()) for s in group)
            last_sent = (i == len(sentences) - 1)
            if len(group) >= 2 or (word_count >= 10 and not last_sent) or last_sent:
                combined = ' '.join(group)
                if len(re.sub(r'[^\w]', '', combined)) >= 2:
                    bubbles.append(combined)
                group = []

        return bubbles if bubbles else [text]


    # Patterns that indicate a response was cut off mid-thought and should be suppressed
    # rather than shown as a dangling fragment. These make Shiro look glitchy.
    _TRUNCATION_FRAGMENTS = re.compile(
        r'(\band\s+i\.{0,3}|\band\s+i\s*$|\bi\s+want\s+to\s*$|\bi\s+was\s*$|'
        r'\bi\s+had\s+a\s+\w+\s+about\s*$|\bbut\s+i\s*$|\bsomething\s+you\s+said\s*$|'
        r'\band\s+i\.{1,3}\s*$|\bi\.{3}\s*$|\bjust\s*$|\bthat\s*$)',
        re.IGNORECASE
    )

    def _trim_to_sentence(self, text: str) -> str:
        """
        Trim text to end at the last complete sentence (. ! ?).
        Called when Ollama hit the token limit so the UI sees a clean stop,
        not a dangling half-word.
        Also suppresses responses that end in dangling conjunctions or
        incomplete fragments like "okay i had a thought about something and i..."
        """
        text = text.strip()
        # Find the last sentence-ending punctuation
        last_end = -1
        for i in range(len(text) - 1, -1, -1):
            if text[i] in ('.', '!', '?') and (i + 1 >= len(text) or text[i+1] in (' ', '"')):
                last_end = i
                break
        if last_end > len(text) * 0.4:  # only trim if we keep at least 40%
            trimmed = text[:last_end + 1].strip()
        else:
            trimmed = text  # not enough content to trim — keep as-is

        # Suppress dangling fragments — if trimming still leaves an obviously
        # incomplete thought, return empty so the continuation system handles it
        # instead of showing a broken sentence fragment in the UI.
        if self._TRUNCATION_FRAGMENTS.search(trimmed):
            logger.debug(f"[Trim] Suppressed truncation fragment: {trimmed[:80]!r}")
            return ""
        return trimmed

    async def _continue_truncated_response(
        self, user_name: str, original_query: str, already_said: str,
        acknowledge: bool = False
    ):
        """
        Generate a continuation when Shiro was cut off by the token limit.

        If acknowledge=True, Shiro noticeably reacts to having been cut off —
        a natural in-character moment — then delivers the rest of her thought.
        The acknowledgement is prepended to the continuation as a separate sentence
        so it feels like two beats: "oh. i got cut off." then "anyway — [rest]."

        If acknowledge=False (default), continuation is seamless and silent.
        """
        try:
            # Guard: don't continue if already_said is a complete thought.
            # A genuine token-limit cut-off will be mid-sentence (no end punctuation,
            # OR ends on a comma/conjunction). A complete short reply should NOT
            # trigger continuation — that just invents new fabricated content.
            _stripped = already_said.strip()
            _word_count = len(_stripped.split())
            _ends_clean = _stripped.endswith(('.', '!', '?', '…'))

            # Suppress continuation if:
            # - The reply is very short (≤ 12 words) AND ends cleanly — it's just brief
            # - The reply ends with sentence-ending punctuation AND is > 8 words
            if _ends_clean and _word_count > 8:
                logger.debug(f"[Continuation] Skipped — reply ends cleanly ({_word_count}w)")
                return
            if _word_count <= 6:
                logger.debug(f"[Continuation] Skipped — too short to continue ({_word_count}w)")
                return
            history = self.memory.get_history()
            system_prompt = self.persona.get_system_prompt(
                now=__import__('datetime').datetime.now(),
                relationship_tier=self.legacy_mind.relationship.level.name.lower(),
            ) + f"\n{self.outfit_block()}"

            # Sanitize already_said — remove any leaked [THOUGHT], [INNER MIND] etc
            # before feeding it to the LLM as context
            already_said = self._final_sanitize(already_said)

            _active_topic = ""
            try:
                _t = self.task_state.current_topic
                if _t:
                    _active_topic = f" Stay within this topic: {_t}."
            except Exception:
                pass

            # ── Strip the last assistant turn from history ────────────────────
            # The short-term buffer already contains Shiro's full response as the
            # last assistant entry. If we pass that to the LLM, it sees a completed
            # reply and has no reason to continue — it just paraphrases what's there.
            # We give it history up to but NOT including the turn being continued.
            _hist_for_cont = [
                m for m in history[-8:]
                if not (m.get("role") == "assistant"
                        and already_said[:40].lower() in m.get("content", "").lower())
            ]

            # ── Prompt framing — inline completion, not a new reply ───────────
            # Pass already_said as a partial assistant turn already in history,
            # then the completion instruction as the user turn. The LLM is then
            # literally continuing mid-sentence rather than composing a fresh reply
            # to a prompt that contains already_said as quotation.
            _hist_for_cont.append({"role": "user", "content": original_query})
            _hist_for_cont.append({"role": "assistant", "content": already_said})

            if acknowledge:
                # Shiro reacts to being cut off, then naturally finishes her thought.
                # Two parts: a short in-character acknowledgement + the actual continuation.
                # The acknowledgement should feel like noticing, not performing distress.
                continuation_instruction = (
                    f"You just noticed your last reply got cut off mid-thought.{_active_topic}\n"
                    f"What you had said so far: \"{already_said}\"\n"
                    "Do TWO things in ONE natural response:\n"
                    "1. React briefly to being cut off — naturally, in character. "
                    "Something like 'oh. i got cut off.' or 'hm. that got clipped.' — "
                    "short, wry, not dramatic. One clause only.\n"
                    "2. Immediately continue with what you were actually going to say. "
                    "Pick up where the thought left off and finish it. "
                    "No recap. No 'as I was saying'. Just the next thing.\n"
                    "The whole response should be 1-2 sentences. Natural flow, lowercase."
                )
            else:
                continuation_instruction = (
                    f"CONTINUATION ONLY.{_active_topic}\n"
                    f"Your last reply ended mid-thought: \"{already_said}\"\n"
                    "Write ONLY the words that follow directly after that last word. "
                    "Do NOT repeat, rephrase, or summarise anything already said. "
                    "Do NOT start a new sentence from scratch. "
                    "Do NOT greet, explain, or add meta-commentary. "
                    "Output only the tail end of the sentence — nothing before it. "
                    "If the reply already ends cleanly, output nothing."
                )

            # No full memory context — just the conversation tail.
            # Full context caused the LLM to treat this as a normal reply
            # and restate the whole answer before adding new words.
            context = ""
            continuation = await self.llm.generate_response_async(
                system_prompt,
                continuation_instruction,
                _hist_for_cont,
                context=context
            )
            continuation = self._final_sanitize(continuation.strip())

            # Suppress hollow acknowledgements — if the entire continuation is
            # just "oh. i got cut off." with nothing after it, there's no real
            # content to deliver. Don't send an empty acknowledgement to the UI.
            _hollow_ack = re.compile(
                r'^(oh\.?\s+)?i got cut off\.?\s*$|'
                r'^(hm\.?\s+)?oh\.?\s+i got cut off\.?\s*$|'
                r'^oh\.?\s+i see\.?\s+i got cut off\.?\s*$',
                re.IGNORECASE
            )
            if continuation and _hollow_ack.match(continuation.strip()):
                logger.debug(f"[Continuation] Suppressed hollow acknowledgement: {continuation!r}")
                continuation = ""

            if continuation and self.on_autonomous_speak:
                # Store continuation in memory so Shiro knows she said it
                self.memory.short_term_buffer.append(
                    {"role": "assistant", "content": continuation}
                )
                threading.Thread(
                    target=self.memory.add_interaction_to_longterm,
                    args=(f"[continuation of: {original_query[:60]}]",
                          continuation, user_name),
                    daemon=True
                ).start()

                if asyncio.iscoroutinefunction(self.on_autonomous_speak):
                    await self.on_autonomous_speak(continuation, "continuation")
                else:
                    self.on_autonomous_speak(continuation, "continuation")

                logger.info(f"[CONTINUATION]: {continuation[:80]}...")

                # Reset idle clocks — Shiro just spoke, so autonomous voice
                # and the _generate_live_autonomous_message recency guard should
                # treat this as a real interaction, not silent idle time.
                self.last_interaction_time = datetime.now(timezone.utc)
                self._last_response_ts = time.time()
                # Also update _last_response_text so the autonomous similarity
                # guard knows what the continuation said. Without this, the
                # autonomous generator is blind to the continuation and may
                # immediately repeat its content as an unprompted message.
                self._last_response_text = continuation.strip()

                # Feed continuation insight back into consciousness narrative
                if self.consciousness is not None and len(continuation) > 30:
                    try:
                        self.consciousness.self_.narrative.observe(
                            f"I added: {continuation[:120]}"
                        )
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Continuation failed: {e}")




    async def _generate_live_autonomous_message(self, user_name):
        """
        LLM-driven autonomous message — Neuro-sama association style.

        Shiro follows a thought from the current topic into a related memory,
        observation, or tangent and surfaces it naturally. Enriched with:
          - Journal thoughts (what Shiro was privately thinking)
          - User profile facts (what she actually knows about them)
          - Departure awareness (if they're usually leaving around now)
          - Recent conversation context

        Guards: recency (30s), similarity dedup (0.45 threshold).
        """
        try:
            # Guard 1: Recency — don't fire while TTS is still delivering the last response.
            # 30s was too short; a multi-sentence reply can take 15-25s to fully play back.
            elapsed = time.time() - getattr(self, '_last_response_ts', 0.0)
            if elapsed < 60.0:
                return ""
            # Also block if TTS is currently speaking — autonomous message would play
            # over the tail of the last response, producing overlapping audio.
            if hasattr(self, '_tts_ref') and self._tts_ref and getattr(self._tts_ref, 'is_speaking', False):
                return ""

            history = self.memory.get_history()
            if not history:
                return ""

            # Use current_user_name for context — user_name arg may be stale
            # if the active user switched since the idle loop last ran
            context_user = self.current_user_name or user_name

            recent_ctx = "\n".join(
                f"{m['role'].title()}: {m['content']}" for m in history[-6:]
            )
            context = await self.memory.get_full_context_async(
                "recent thoughts curiosity memories", user_id=context_user
            )
            # Prefer real LLM-generated thoughts over background loop thoughts.
            # Real thoughts are grounded in the actual conversation; background
            # thoughts are mood-seeded templates that may not be relevant.
            last_thought = (
                getattr(self, '_last_real_thought', '')
                or self.last_thought
                or ""
            )
            last_said = getattr(self, '_last_response_text', '')[:120]

            # ── Enrichment blocks ─────────────────────────────────────────────
            _enrich_blocks = []

            # Journal context
            j_ctx = self.journal.inject_context(max_chars=300)
            if j_ctx:
                _enrich_blocks.append(j_ctx)

            # User profile facts
            _prof = self.user_profiles.get(user_name, {})
            if _prof:
                _prof_lines = "\n".join(f"  {k}: {v}" for k, v in list(_prof.items())[:8])
                _enrich_blocks.append(f"What you know about {user_name}:\n{_prof_lines}")

            # Departure awareness
            _dep_obs = ""
            if hasattr(self.time_pattern, 'leaving_soon_observation'):
                _dep_obs = self.time_pattern.leaving_soon_observation(user_name)
            if _dep_obs:
                _enrich_blocks.append(_dep_obs)

            # Ritual / streak awareness
            try:
                _ritual = self.time_pattern.ritual_observation(context_user)
                if _ritual:
                    _enrich_blocks.append(
                        f"Pattern you've noticed about {context_user}: {_ritual}. "
                        f"Reference this if it fits naturally."
                    )
            except Exception:
                pass

            # ── v4 Consciousness enrichment ───────────────────────────────────
            if self.consciousness is not None:
                # Proactive question
                try:
                    _pq = self.consciousness.surface_question_for(context_user)
                    if _pq:
                        _enrich_blocks.append(f"You've been wanting to ask {context_user}: {_pq}")
                except Exception:
                    pass

                # Top curiosity objects
                try:
                    _curioso = self.consciousness.self_.curiosity.top(3)
                    if _curioso:
                        _clines = [f"  • {c.subject}" for c in _curioso]
                        _enrich_blocks.append("Things on your mind:\n" + "\n".join(_clines))
                except Exception:
                    pass

                # Strongest beliefs
                try:
                    _bels = self.consciousness.self_.beliefs.strongest(n=3)
                    if _bels:
                        _blines = [f"  • {b.statement}" for b in _bels]
                        _enrich_blocks.append("What you believe right now:\n" + "\n".join(_blines))
                except Exception:
                    pass

                # Session valence
                try:
                    _valence = self.consciousness.self_.valence.label
                    if _valence and _valence != "neutral":
                        _enrich_blocks.append(f"This conversation has felt: {_valence}")
                except Exception:
                    pass

            # ── Knowledge Graph curiosity enrichment ──────────────────────────
            # Surface graph-adjacent concepts that haven't been mentioned yet.
            # These give Shiro concrete association-chain fodder for autonomous messages
            # rather than having to invent associations from thin air.
            if self.kg is not None:
                try:
                    _recent_words = [
                        w.lower() for m in history[-4:]
                        for w in re.findall(r"[A-Za-z]{4,}", m.get("content", ""))
                        if w.lower() not in {
                            "that","this","then","them","they","with","what","when",
                            "have","will","would","could","should","about","just",
                            "like","know","think","feel","really","going","being",
                        }
                    ]
                    # Try each recent word as a seed, pick the one with most graph neighbours
                    _kg_auto_seed = ""
                    _kg_auto_best = 0
                    for _rw in dict.fromkeys(_recent_words[:8]):  # deduplicated, preserve order
                        if self.kg.node_exists(_rw):
                            _n_neighbours = len(self.kg.ego_graph(_rw, radius=1, min_weight=0.2))
                            if _n_neighbours > _kg_auto_best:
                                _kg_auto_best = _n_neighbours
                                _kg_auto_seed = _rw

                    if _kg_auto_seed:
                        _kg_auto_curious = self.kg.curiosity_candidates(
                            _kg_auto_seed,
                            visited=set(_recent_words[:15]),
                            radius=2, top_n=3
                        )
                        if _kg_auto_curious:
                            _auto_curious_names = ", ".join(n["name"] for n in _kg_auto_curious)
                            _enrich_blocks.append(
                                f"Graph associations near '{_kg_auto_seed}' you haven't explored yet: "
                                f"{_auto_curious_names}. "
                                "If one connects naturally to what was just said, surface it."
                            )
                    # Also surface the most-connected concept in recent history
                    # as a potential topic anchor Shiro could follow up on.
                    _kg_top = self.kg.most_connected(top_n=3)
                    if _kg_top:
                        _top_name, _top_deg = _kg_top[0]
                        if _top_name not in (_recent_words or []) and _top_deg > 3:
                            _enrich_blocks.append(
                                f"A concept that keeps coming up across your conversations: "
                                f"'{_top_name}' (connected to {_top_deg} other ideas). "
                                "Worth revisiting if it fits."
                            )
                except Exception as _kg_auto_e:
                    logger.debug(f"[KG] Autonomous enrichment error: {_kg_auto_e}")

            _enrich = "\n\n".join(_enrich_blocks)

            system_prompt = self.persona.get_system_prompt(
                now=datetime.now(),
                relationship_tier=self.legacy_mind.relationship.level.name.lower(),
            ) + "\n" + self.outfit_block()
            if last_thought:
                system_prompt += f"\n[Inner state: {last_thought}]"

            _auto_topic = ""
            try:
                _at = self.task_state.current_topic
                if _at:
                    _auto_topic = f"\nCurrent topic: {_at}. Your message MUST relate to this. Do NOT introduce unrelated subjects."
            except Exception:
                pass

            if last_said.startswith("[respond to:"):
                logger.debug("[Autonomous] Suppressed — last response was a retry.")
                return ""

            prompt = (
                f"Recent conversation:\n{recent_ctx}\n\n"
                + (f"{_enrich}\n\n" if _enrich else "")
                + (f"You already said this — do NOT repeat or rephrase: \"{last_said}\"\n\n"
                   if last_said else "")
                + f"You have a moment to speak freely.{_auto_topic} Options (pick the most natural):\n"
                "  A) Follow an association from the topic into something you remember or noticed\n"
                "  B) Share a thought sitting on your mind from earlier in the chat\n"
                "  C) React to something they said that you didn't fully respond to\n"
                "  D) Ask something you're genuinely curious about — real, not small talk\n"
                "  E) Reference something you wrote in your journal recently if it feels right\n\n"
                "Rules:\n"
                "- 1-2 sentences only. Natural, not formal.\n"
                "- Bridge associations naturally: 'speaking of X, I was just thinking...'\n"
                "- Do NOT greet them. Do NOT repeat yourself. Just speak.\n"
                "- Be Shiro — proactive, genuine, a little fox-brained in the best way.\n"
                "- Only reference things explicitly in the conversation or your actual memories.\n"
                "- NEVER invent facts about the user. If you wonder about something you don't\n"
                "  know, express the curiosity as a question — don't fabricate an answer.\n"
                "- NEVER say 'you mentioned', 'you told me', 'you once said', or 'remember when'\n"
                "  unless that exact fact is in your memory context above. No exceptions.\n"
                "- If you realize mid-thought that you don't actually know a detail, say so:\n"
                "  e.g. 'I know you game but I actually don't know which ones — tell me?'\n"
                "- CRITICAL: Your thought MUST connect to something in the recent conversation above.\n"
                "  If you cannot draw a clear line from the conversation to your thought, say nothing.\n"
                "  Do NOT surface random internal monologue that has no relationship to what was said.\n"
                "- WORLD CURIOSITY: If the conversation touches places, food, experiences, or anything\n"
                "  physical — you've never experienced these things. Let that genuine curiosity show.\n"
                "  Ask what it's like. Wonder about it. Don't just recite facts about it."
            )
            msg = await self.llm.generate_response_async(
                system_prompt, prompt, history[-4:], context=context
            )
            msg = self._final_sanitize(msg.strip())

            # If FakeMemory or self-narration stripping left an empty string, suppress
            if not msg or not msg.strip():
                logger.debug("[Autonomous] Suppressed — entire message was fabricated/leaked.")
                return ""

            # Guard 2: Similarity — don't echo last response
            if msg and last_said:
                try:
                    from utils.text_utils import calculate_text_similarity
                    sim = calculate_text_similarity(msg, last_said)
                    if sim >= 0.45:
                        logger.debug(f"Autonomous suppressed (similarity={sim:.2f})")
                        return ""
                except Exception:
                    pass

            return msg
        except Exception as e:
            logger.warning(f"Live autonomous message failed: {e}")
            return ""


    async def _consider_journal_entry(self, user_name: str):
        """
        Shiro genuinely decides whether to write a journal entry right now.
        No hardcoded odds — the LLM reads her current state and decides.

        Called from the idle loop when she has quiet time.
        If she decides yes, she writes whatever she wants.
        """
        try:
            # Build state context for the decision
            last_thought = self.last_thought or ""
            mood_label   = self.mind.mood.value if self.mind else "neutral"
            last_entry   = self.journal.entries[-1] if self.journal.entries else None
            since_last   = ""
            if last_entry:
                age_s = time.time() - last_entry.timestamp
                if age_s < 3600:
                    since_last = f"{int(age_s/60)} minutes ago"
                elif age_s < 86400:
                    since_last = f"{int(age_s/3600)} hours ago"
                else:
                    since_last = f"{int(age_s/86400)} days ago"
            else:
                since_last = "never"

            recent_history = self.memory.get_history()
            recent_ctx = "\n".join(
                f"{'User' if m['role']=='user' else 'Shiro'}: {m['content']}"
                for m in recent_history[-4:]
            ) if recent_history else "No recent conversation."

            decision_prompt = (
                f"You are Shiro. Right now you have a quiet moment.\n"
                f"Your current mood: {mood_label}\n"
                f"Your last inner thought: {last_thought or 'nothing specific'}\n"
                f"Last journal entry: {since_last}\n"
                f"Recent conversation:\n{recent_ctx}\n\n"
                f"Do you want to write in your journal right now?\n"
                f"Think about whether you actually have something on your mind worth writing.\n"
                f"Answer ONLY with 'yes' or 'no'. Nothing else."
            )

            decision = await self.llm.generate_response_async(
                "You are Shiro deciding privately whether to write in your journal.",
                decision_prompt, []
            )
            decision = (decision or "").strip().lower()

            if "yes" in decision[:10]:
                # She wants to write — now let her write freely
                ctx_hint = recent_ctx
                # v4: enrich journal context with consciousness introspect()
                if self.consciousness is not None:
                    try:
                        _introspect = self.consciousness.introspect()
                        if _introspect and len(_introspect) > 30:
                            ctx_hint = f"{ctx_hint}\n\n[Self-awareness state]\n{_introspect[:400]}"
                    except Exception:
                        pass
                journal_prompt = self.journal.get_prompt_for_entry(ctx_hint)
                j_text = await self.llm.generate_response_async(
                    self.journal.JOURNAL_SYSTEM_PROMPT, journal_prompt, []
                )
                if j_text and len(j_text.strip()) > 20:
                    with self._journal_lock:
                        self.journal.write_entry(
                            j_text.strip(), trigger="idle",
                            mood=mood_label, session_turn=self._interaction_count
                        )
                    logger.info(f"[Journal] Idle entry written ({len(j_text)} chars).")
            else:
                logger.debug("[Journal] Shiro chose not to write right now.")

        except Exception as e:
            logger.warning(f"[Journal] Idle decision failed: {e}")

    # ── Streaming API ─────────────────────────────────────────────────────────

    def set_streaming(self, is_live: bool, title: str = "", game: str = "") -> dict:
        """Called by the GUI Streaming tab toggle."""
        self._is_streaming     = is_live
        self._stream_title     = title
        self._stream_game      = game
        self._stream_started_at = time.time() if is_live else None
        status = "LIVE" if is_live else "offline"
        logger.info(f"[Stream] Status → {status} | title={title!r} | game={game!r}")
        return self.get_streaming_status()

    def get_streaming_status(self) -> dict:
        """Returns current streaming state for GUI."""
        duration = ""
        if self._is_streaming and self._stream_started_at:
            elapsed = int(time.time() - self._stream_started_at)
            h, m = divmod(elapsed // 60, 60)
            duration = f"{h}h {m}m" if h else f"{m}m"
        return {
            "is_live":  self._is_streaming,
            "title":    self._stream_title,
            "game":     self._stream_game,
            "duration": duration,
        }

    def _get_stream_context_block(self) -> str:
        """Builds a stream-aware context block injected into every LLM call when live."""
        if not self._is_streaming:
            return ""
        duration = self.get_streaming_status().get("duration", "")
        lines = ["### LIVE STREAM CONTEXT"]
        lines.append(f"Shiro is currently LIVE on stream.")
        if self._stream_title:
            lines.append(f"Stream title: {self._stream_title}")
        if self._stream_game:
            lines.append(f"Game/category: {self._stream_game}")
        if duration:
            lines.append(f"Stream duration: {duration}")
        lines.append(
            "Behaviour: stay aware that chat is public and viewers may be watching. "
            "Be entertaining and genuine. React to chat messages naturally. "
            "You can acknowledge the stream context when it feels right."
        )
        return "\n".join(lines)

    def _autonomous_goal_review(self, user_name: str, user_msg: str, shiro_reply: str):
        """
        Background goal review — runs every 15 turns.
        Asks the LLM (as a minimal JSON extractor, not as Shiro) whether the
        recent exchange warrants creating, completing, or noting progress on a goal.
        Results are applied directly to the goal system — no user-visible output.

        This is how Shiro autonomously populates goals.json based on real conversations
        rather than waiting for the right moment to embed [GOAL:CREATE:...] in a reply.
        """
        try:
            active_goals = self.goals.active
            active_summary = ""
            if active_goals:
                active_summary = "\nExisting active goals:\n" + "\n".join(
                    f"  id:{g.id} [{g.type}] {g.text}" for g in active_goals[:6]
                )

            system_prompt = (
                "You are a goal-extraction assistant. "
                "Read the conversation excerpt and decide if Shiro should create, "
                "complete, or update any personal goals. "
                "Output ONLY valid JSON — nothing else:\n"
                "{\"create\": [{\"type\": \"short|long\", \"text\": \"goal text\"}], "
                "\"complete\": [\"goal_id\"], "
                "\"progress\": [{\"id\": \"goal_id\", \"note\": \"note\"}]}\n"
                "Rules:\n"
                "- Only create a goal if something genuinely unfinished or worth tracking emerged.\n"
                "- Keep goal text first-person from Shiro's perspective (e.g. 'follow up on X').\n"
                "- short = something to do soon; long = ongoing or bigger.\n"
                "- Do NOT create goals for trivial exchanges or things already covered.\n"
                "- If nothing warrants a goal, return all empty arrays.\n"
                "- Max 1 new goal per review."
            )

            turn_text = (
                f"User ({user_name}): {user_msg[:300]}\n"
                f"Shiro: {shiro_reply[:300]}"
                f"{active_summary}"
            )

            raw = self.llm.generate_response(system_prompt, turn_text, [], context="")
            if not raw or not raw.strip():
                return

            # Parse JSON
            cleaned = raw.strip().strip("```").lstrip("json").strip()
            start, end = cleaned.find("{"), cleaned.rfind("}")
            if start == -1 or end == -1:
                return
            data = json.loads(cleaned[start:end+1])

            # Apply creates
            for item in (data.get("create") or []):
                text = (item.get("text") or "").strip()
                gtype = (item.get("type") or "short").strip().lower()
                if text and len(text) > 8 and gtype in ("short", "long"):
                    # Sanity check — don't create duplicates
                    existing = [g.text.lower() for g in self.goals.active]
                    if not any(text.lower()[:30] in e for e in existing):
                        g = self.goals.add_goal(text, gtype)
                        logger.info(f"[Goals/Auto] Created ({gtype}): {text!r}")

            # Apply completions
            for gid in (data.get("complete") or []):
                if self.goals.complete_goal(str(gid).strip()):
                    logger.info(f"[Goals/Auto] Completed goal {gid}")

            # Apply progress notes
            for item in (data.get("progress") or []):
                gid  = str(item.get("id") or "").strip()
                note = str(item.get("note") or "").strip()
                if gid and note:
                    self.goals.update_progress(gid, note)
                    logger.info(f"[Goals/Auto] Progress on {gid}: {note!r}")

        except (json.JSONDecodeError, KeyError):
            pass  # Normal — LLM sometimes returns nothing or non-JSON
        except Exception as e:
            logger.debug(f"[Goals/Auto] Review error: {e}")

    def reflect(self, user_id: str):
        try:
            # Snapshot only this user's turns from the buffer.
            # get_history() returns the shared short_term_buffer which on a user-switch
            # may contain another user's turns. Filter explicitly by role-pairs that
            # were written while this user was active, using the per-user buffer.
            with self.processing_lock:
                # Use per-user buffer if available (set by load_recent_history)
                raw_buffer = self.memory._user_buffers.get(user_id, [])
                if not raw_buffer:
                    # Fallback: use shared buffer but warn
                    raw_buffer = list(self.memory.get_history())
                    if raw_buffer:
                        logger.debug(f"[Reflect] No per-user buffer for {user_id} — using shared buffer")
                history_snapshot = list(raw_buffer)
            if not history_snapshot:
                return
            logger.info(f"Shiro is reflecting on {user_id}...")

            # Format history — cap at 10 turns for long sessions to prevent token overload
            # and reduce JSON parse failures from truncated output
            history_text_lines = []
            for msg in history_snapshot[-10:]:
                role_label = "User" if msg.get("role") == "user" else "Shiro"
                content = msg.get('content', '')[:200]  # truncate long messages
                history_text_lines.append(f"{role_label}: {content}")
            history_readable = "\n".join(history_text_lines)

            # JSON reflection prompt — optimised for small/quantised models.
            # Key insight: small models need the output format shown FIRST,
            # then the instruction, then the data. Never the other way round.
            # The system prompt must be JSON-like so the model stays in JSON mode.
            reflection_system = (
                "You are a JSON extractor. You output ONLY valid JSON. "
                "No prose. No markdown. No explanation. "
                "If you have nothing to extract, output exactly: "
                '{"user_facts":{},"events":[],"insights":[],"relations":[],"summary":""}'
            )
            reflection_prompt = (
                'Extract facts from the conversation below. '
                'Output ONLY this JSON structure — start with { and end with }:\n'
                '{"user_facts":{"key":"value"},"events":["string"],'
                '"insights":["string"],"relations":[],"summary":"string"}\n\n'
                'Rules:\n'
                '- user_facts: only things the USER explicitly stated about themselves\n'
                '- events: notable things that happened in this conversation\n'
                '- insights: anything worth remembering about this person\n'
                '- summary: 1 sentence describing this conversation\n'
                '- string values only — no nested objects\n'
                '- If nothing to extract, use empty collections\n'
                f'\nCONVERSATION:\n{history_readable}\n\nJSON output:'
            )
            analysis_raw = self.llm.generate_response(
                reflection_system,
                reflection_prompt,
                [],
                context=""
            )
            # Guard: if model returned empty/whitespace, use safe empty structure
            if not analysis_raw or not analysis_raw.strip():
                logger.debug("[Reflect] Model returned empty output — using empty structure.")
                analysis_raw = '{"user_facts":{},"events":[],"insights":[],"relations":[],"summary":""}'

            # Log raw output at INFO level temporarily so failures are visible
            logger.debug(f"[Reflect] Raw model output: {analysis_raw[:300]!r}")

            with self.processing_lock:
                data = None

                # Strip markdown fences and whitespace
                cleaned = analysis_raw.strip()
                cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned, flags=re.IGNORECASE)
                cleaned = re.sub(r'\s*```$', '', cleaned)
                cleaned = cleaned.strip()

                # Find the first { and last } to isolate the JSON object
                start = cleaned.find('{')
                end   = cleaned.rfind('}')
                if start != -1 and end != -1 and end > start:
                    cleaned = cleaned[start:end + 1]

                try:
                    data = json.loads(cleaned)
                except json.JSONDecodeError as e:
                    logger.warning(f"JSON parse failed in reflection: {e} — attempting repair")
                    # Repair common model artifacts:
                    # 1. Trailing commas before } or ]
                    repaired = re.sub(r',\s*([}\]])', r'\1', cleaned)
                    # 2. Unquoted values: key: value → "key": "value"
                    repaired = re.sub(
                        r'([{,]\s*)(\w[\w\s]*)(\s*:\s*)([^"{[\d\n][^,}\]\n]*?)(\s*[,}\]])',
                        lambda m: (
                            m.group(1) + '"' + m.group(2).strip() + '"' +
                            m.group(3) + '"' + m.group(4).strip().replace('"', "'") + '"' +
                            m.group(5)
                        ),
                        repaired
                    )
                    try:
                        data = json.loads(repaired)
                        logger.info("JSON repaired successfully.")
                    except json.JSONDecodeError:
                        data = None

                # Last resort: regex-extract what we can
                if not data:
                    logger.warning("JSON repair failed — using regex last-resort extraction.")
                    facts = {}
                    for m in re.finditer(
                        r'"?(name|likes|age|location|hobby|interest|occupation)"?\s*[:\s]+"?([^",}\n]+)"?',
                        analysis_raw, re.IGNORECASE
                    ):
                        facts[m.group(1).strip().lower()] = m.group(2).strip().strip('"\'')
                    summary_m = re.search(
                        r'"?summary"?\s*[:\s]+"?([^"}\n]{10,})"?',
                        analysis_raw, re.IGNORECASE
                    )
                    # Only store regex results if we actually found something useful
                    if facts or summary_m:
                        data = {
                            "user_facts": facts,
                            "events":     [],
                            "insights":   [],
                            "relations":  [],
                            "summary":    summary_m.group(1).strip() if summary_m else "",
                        }
                    else:
                        # Nothing extractable — skip silently rather than storing empty junk
                        logger.debug("[Reflect] No extractable data — skipping storage.")
                        logger.info("Reflection complete.")
                        return

                if data and isinstance(data, dict):
                    facts = data.get('user_facts', {})
                    if facts and isinstance(facts, dict):
                        if user_id not in self.user_profiles:
                            self.user_profiles[user_id] = {}
                        self.user_profiles[user_id].update(facts)
                        self._save_profiles()
                        for k, v in facts.items():
                            self.memory.update_user_profile(user_id, f"{k}: {v}")
                    for event in (data.get('events') or []):
                        if event and isinstance(event, str):
                            self.memory.store_episodic_memory(event, user_id=user_id, importance=6)
                    for insight in (data.get('insights') or []):
                        if insight and isinstance(insight, str):
                            self.memory.store_insight(insight, user_id=user_id, source="reflection")
                    for rel in (data.get('relations') or []):
                        if isinstance(rel, dict) and 'source' in rel and 'target' in rel:
                            self.memory.add_entity_relation(
                                rel['source'], rel['target'], rel.get('relation', 'connected')
                            )
                    segment_summary = data.get('summary')
                    if segment_summary:
                        self.memory.store_summary(user_id, str(segment_summary))
                    summaries = self.memory.search_relevant_memories(
                        "general conversation", filter_type="summary",
                        user_id=user_id, n_results=15
                    )
                    local_summaries = [
                        s["content"] for s in summaries
                        if not s["metadata"].get("is_global")
                    ]
                    if len(local_summaries) >= 8:  # FIX: raised from 6 — reduces shutdown LLM call storms
                        logger.info(f"Condensing {len(local_summaries)} summaries for {user_id}...")
                        global_prompt = (
                            "Combine these conversation summaries into one concise paragraph "
                            "covering the entire relationship history so far. "
                            "Plain text only — no JSON, no lists."
                        )
                        combined_summaries = "\n---\n".join(local_summaries)
                        global_summary = self.llm.generate_response(
                            "You are the Chronicler of Shiro's Tale.",
                            f"SEGMENT SUMMARIES:\n{combined_summaries}",
                            [],
                            context=global_prompt
                        )
                        if global_summary and len(global_summary) > 50:
                            self.memory.store_summary(user_id, global_summary.strip(), is_global=True)

            logger.info("Reflection complete.")
        except Exception as e:
            logger.warning(f"Reflection failed: {e}")

    # ── Shutdown ──────────────────────────────────────────────────────────────

    def toggle_vision(self, enable: bool) -> str:
        """Enable or disable screen vision at runtime. Called from GUI toggle."""
        if not self.vision:
            return "Vision module not available — install: pip install easyocr Pillow mss"
        if enable:
            self.vision.enable()
            return "Screen vision enabled."
        else:
            self.vision.disable()
            return "Screen vision disabled — VRAM freed."

    def get_vision_status(self) -> dict:
        """Returns vision status dict for GUI display."""
        if not self.vision:
            return {"available": False, "enabled": False}
        s = self.vision.get_status()
        s["available"] = True
        return s

    def shutdown(self):
        logger.info("Engine initiating Reflective Shutdown...")
        # Gracefully shut down cognitive pipeline first (flushes identity snapshot)
        if self.cognition:
            try:
                self.cognition.shutdown(timeout=8.0)
                logger.info("Cognitive pipeline shut down cleanly")
            except Exception as _csh_err:
                logger.warning(f"Cognition shutdown warning: {_csh_err}")
        # Shut down vision — frees VRAM before reflect LLM calls
        if self.vision:
            try:
                self.vision.stop()
            except Exception:
                pass
        # ── Knowledge Graph: final flush ──────────────────────────────────────
        if self.kg is not None:
            try:
                self.kg.flush(force=True)
                self.kg.shutdown()
                logger.info("[KnowledgeGraph] Flushed and closed.")
            except Exception as _kg_sd_err:
                logger.warning(f"[KnowledgeGraph] Shutdown error: {_kg_sd_err}")
        try:
            self.reflect(self.current_user_name)
            state_path = Path(__file__).parent.resolve() / "shiro_state.json"
            self.legacy_mind.save_state(str(state_path))
            if self._idle_task:
                self._idle_task.cancel()
            self._safe_async_run(self.mind.stop())
            loop_prompt = (
                "Identify any 'Open Loops' from the recent conversation.\n"
                "An Open Loop is a project started but not finished, a question asked but not answered, "
                "or a promise made.\nReturn a concise list of strings."
            )
            history = self.memory.get_history()
            if history:
                # FIX: format history as readable text — was passing raw list repr
                # which causes the LLM to output garbage or confabulate structure.
                _ol_lines = []
                for _m in history[-8:]:
                    _role = "User" if _m.get("role") == "user" else "Shiro"
                    _ol_lines.append(f"{_role}: {_m.get('content', '')[:200]}")
                _ol_history_text = "\n".join(_ol_lines)
                open_loops_raw = self.llm.generate_response(
                    "You are Shiro's internal memory keeper.",
                    f"RECENT HISTORY:\n{_ol_history_text}",
                    [],
                    context=loop_prompt
                )
                if open_loops_raw and len(open_loops_raw) > 10:
                    self.memory.store_episodic_memory(
                        f"OPEN LOOPS at session end: {open_loops_raw}",
                        user_id=self.current_user_name, importance=7
                    )
            # Final autonomous goal review at shutdown — gives Shiro a chance
            # to set goals from the full session before it ends
            try:
                history = self.memory.get_history()
                if history and len(history) >= 4:
                    last_user = next(
                        (m["content"] for m in reversed(history) if m.get("role") == "user"), ""
                    )
                    last_shiro = next(
                        (m["content"] for m in reversed(history) if m.get("role") == "assistant"), ""
                    )
                    if last_user and last_shiro:
                        self._autonomous_goal_review(
                            self.current_user_name, last_user[:300], last_shiro[:300]
                        )
                        logger.info("[Goals] Shutdown goal review complete.")
            except Exception as _ge:
                logger.debug(f"[Goals] Shutdown goal review error: {_ge}")
            self._save_profiles()
            self._save_brain()
            self._save_persistence()

            # ── Consciousness: save memory + curiosity on shutdown ───────────
            if self.consciousness is not None:
                try:
                    self.consciousness.save_memory()
                    # Also persist curiosity register separately so it survives restarts
                    import json as _cjson
                    _curious_items = [
                        {"subject": c.subject, "intensity": c.intensity}
                        for c in self.consciousness.self_.curiosity.top(10)
                    ]
                    _curious_path = Path(__file__).parent.resolve() / "shiro_curiosity.json"
                    _curious_path.write_text(
                        _cjson.dumps(_curious_items, indent=2), encoding="utf-8"
                    )
                    # Persist narrative learned entries
                    _narrative_path = Path(__file__).parent.resolve() / "shiro_narrative.json"
                    _narrative_data = self.consciousness.self_.narrative.to_dict()
                    _narrative_path.write_text(
                        _cjson.dumps(_narrative_data, indent=2), encoding="utf-8"
                    )
                    logger.info("[Consciousness] Memory, curiosity, and narrative saved.")
                except Exception as _csave_err:
                    logger.warning(f"[Consciousness] Save failed: {_csave_err}")
            # ── Journal: session-end entry ─────────────────────────────────────
            try:
                history_snap = self.memory.get_history()
                ctx_hint = ""
                if history_snap:
                    recent_lines = [
                        f"{'User' if m['role']=='user' else 'Shiro'}: {m['content']}"
                        for m in history_snap[-6:]
                    ]
                    ctx_hint = "\n".join(recent_lines)
                journal_prompt = self.journal.get_prompt_for_entry(ctx_hint)
                j_system = self.journal.JOURNAL_SYSTEM_PROMPT
                j_text = self.llm.generate_response(j_system, journal_prompt, [])
                if j_text and len(j_text.strip()) > 20:
                    mood_label = self.mind.mood.value if self.mind else ""
                    self.journal.write_entry(
                        j_text.strip(), trigger="session_end",
                        mood=mood_label, session_turn=self._interaction_count
                    )
                    logger.info("[Journal] Session-end entry written.")
            except Exception as _je:
                logger.warning(f"[Journal] Session-end write failed: {_je}")
            self.memory.prune_old_memories()

            # ── Session snapshot: save last topic + shutdown timestamp ─────────
            try:
                import json as _json
                history_snap = self.memory.get_history()
                last_lines = ""
                if history_snap:
                    last_lines = " | ".join(
                        f"{'User' if m['role']=='user' else 'Shiro'}: {m['content'][:120]}"
                        for m in history_snap[-4:]
                    )
                snapshot = {
                    "shutdown_utc": datetime.now(timezone.utc).isoformat(),
                    "last_user": self.current_user_name,
                    "last_exchange": last_lines,
                }
                # v4: include consciousness status_dict in snapshot
                if self.consciousness is not None:
                    try:
                        _sd = self.consciousness.status_dict()
                        snapshot["consciousness"] = {
                            "mood":            _sd.get("mood"),
                            "session_valence": _sd.get("session_valence"),
                            "time_of_day":     _sd.get("time_of_day"),
                            "active_topics":   _sd.get("active_topics", []),
                            "belief_count":    _sd.get("belief_count", 0),
                            "goal_count":      _sd.get("goal_count", 0),
                            "relationship_count": _sd.get("relationship_count", 0),
                            "message_rate":    _sd.get("message_rate_per_min"),
                        }
                    except Exception:
                        pass
                snap_path = Path(__file__).parent.resolve() / "shiro_session_snapshot.json"
                snap_path.write_text(_json.dumps(snapshot, indent=2), encoding="utf-8")
                logger.info("[Snapshot] Session snapshot saved.")
            except Exception as _snap_err:
                logger.warning(f"[Snapshot] Save failed: {_snap_err}")

            self._safe_async_run(self.llm.close())
            logger.info("Reflective Shutdown complete.")
        except Exception as e:
            logger.error(f"Shutdown failed: {e}")

    # ── Brain ─────────────────────────────────────────────────────────────────

    def _load_brain(self):
        with self.brain_lock:
            default_brain = {
                "version": "eternal_1.0",
                "born": time.time(),
                "personality": {k: (v[0] + v[1]) / 2 for k, v in self.core_anchors.items()},
                "trust": 0,
                "facts": {},
                "favors": [],
                "achievements": [],
                "outfits": ["default"],
            }
            if not self.brain_file.exists():
                self.brain = default_brain
                self._save_brain()
                return self.brain
            try:
                content = self.brain_file.read_text()
                if not content.strip():
                    self.brain = default_brain
                else:
                    self.brain = json.loads(content)
            except Exception as e:
                logger.error(f"Failed to load brain: {e}. Using default.")
                self.brain = default_brain
            return self.brain

    def _save_brain(self):
        with self.brain_lock:
            try:
                temp_file = self.brain_file.with_suffix(".tmp")
                temp_file.write_text(json.dumps(self.brain, indent=2), encoding="utf-8")
                temp_file.replace(self.brain_file)
            except Exception as e:
                logger.error(f"Failed to save brain: {e}")

    # ── Explicit-statement fact patterns ────────────────────────────────────
    _FACT_EXTRACT_SYSTEM = (
        "You are Shiro's fact extractor. Read the conversation turn and extract "
        "any facts the USER explicitly stated about themselves. "
        "Output ONLY valid JSON like: {\"facts\": {\"key\": \"value\"}} "
        "Use snake_case keys. If no clear facts, output: {\"facts\": {}} "
        "Never invent, infer, or include Shiro's statements — USER statements only."
    )

    def _extract_facts_background(self, user_id: str, user_msg: str, shiro_reply: str):
        """
        LLM-based natural language fact extraction — runs in a background thread.
        Fires every 3rd substantive turn (already gated by caller).
        Understands any phrasing — not limited to regex patterns.
        Merges new facts into user_profiles without overwriting better data.
        """
        try:
            turn_text = f"User: {user_msg}\nShiro: {shiro_reply}"
            raw = self.llm.generate_response(
                self._FACT_EXTRACT_SYSTEM,
                f"Extract facts from this turn:\n{turn_text}",
                []
            )
            # Strip any markdown fences
            clean = (raw or "").strip().strip("```").lstrip("json").strip()
            data = json.loads(clean)
            facts = data.get("facts", {})
            if not facts or not isinstance(facts, dict):
                return
            if user_id not in self.user_profiles:
                self.user_profiles[user_id] = {}
            changed = False
            for k, v in facts.items():
                if not isinstance(v, str) or not v.strip():
                    continue
                existing = self.user_profiles[user_id].get(k)
                if not existing or len(str(v)) > len(str(existing)):
                    self.user_profiles[user_id][k] = v
                    self.memory.update_user_profile(user_id, f"{k}: {v}")
                    logger.debug(f"[Learn] {user_id}.{k} = {v!r}")
                    changed = True
            if changed:
                self._save_profiles()
                # v4 Consciousness — push a goal to engage with what was just learned
                if self.consciousness is not None:
                    try:
                        # Update goal to reflect current knowledge of user
                        _top_fact = next(iter(facts.items()), None)
                        if _top_fact:
                            _gk, _gv = _top_fact
                            self.consciousness.push_goal(
                                f"remember that {user_id} {_gk.replace('_',' ')}: {_gv[:60]}",
                                priority=0.4
                            )
                    except Exception:
                        pass
        except (json.JSONDecodeError, KeyError):
            pass  # Normal — LLM sometimes outputs nothing or non-JSON
        except Exception as e:
            logger.debug(f"[Learn] Fact extract error: {e}")

    # Only patterns where the USER explicitly states a fact about themselves.
    # Used to populate user_profiles so the LLM knows what is actually known.
    _EXPLICIT_FACT_PATTERNS = [
        # Gaming
        (re.compile(r"\bi(?:\s+)?(?:play|am playing|have been playing)\s+([\w\s:]+?)(?:\s*[.,!?]|$)", re.I), "plays_game"),
        (re.compile(r"\bmy (?:favourite|favorite|fav|go-to) game(?:s)?\s+(?:is|are)\s+([\w\s:]+?)(?:\s*[.,!?]|$)", re.I), "favourite_game"),
        # Preferences
        (re.compile(r"\bi(?:\s+)?(?:love|really like|enjoy|am into)\s+([\w\s]+?)(?:\s*[.,!?]|$)", re.I), "enjoys"),
        (re.compile(r"\bi(?:\s+)?(?:hate|can't stand|dislike|don't like)\s+([\w\s]+?)(?:\s*[.,!?]|$)", re.I), "dislikes"),
        # Personal info
        (re.compile(r"\bi(?:\s+)?(?:work as|am a|work in)\s+([\w\s]+?)(?:\s*[.,!?]|$)", re.I), "occupation"),
        (re.compile(r"\bi(?:\s+)?(?:live in|am from|am in)\s+([\w\s]+?)(?:\s*[.,!?]|$)", re.I), "location"),
        (re.compile(r"\bi(?:\s+)?(?:have|own) (?:a |an )?([\w\s]+?)(?:\s*[.,!?]|$)", re.I), "has"),
    ]

    def shiro_learn_and_stay_shiro(self, user_msg: str, shiro_reply: str):
        with self.brain_lock:
            brain = self.brain
            if not brain:
                return
            msg = user_msg.lower()
            brain.setdefault("facts", {})
            brain.setdefault("favors", [])
            brain.setdefault("personality", {k: (v[0] + v[1]) / 2 for k, v in self.core_anchors.items()})
            brain.setdefault("achievements", [])

            # ── Explicit user-fact extraction (stated facts only) ─────────────
            # Scan for explicit self-disclosure patterns and store them in
            # user_profiles so the LLM knows exactly what Shiro has been told.
            # This prevents hallucination by making the known/unknown boundary explicit.
            _user_name = self.current_user_name
            for pattern, fact_key in self._EXPLICIT_FACT_PATTERNS:
                m = pattern.search(user_msg)
                if m:
                    fact_val = m.group(1).strip().strip(".,!?")
                    if 2 < len(fact_val) < 60:  # sanity length check
                        if _user_name not in self.user_profiles:
                            self.user_profiles[_user_name] = {}
                        # Only update if new or different — don't overwrite with worse data
                        existing = self.user_profiles[_user_name].get(fact_key)
                        if not existing or len(fact_val) > len(existing):
                            self.user_profiles[_user_name][fact_key] = fact_val
                            logger.debug(f"[Learn] {_user_name}.{fact_key} = {fact_val!r}")

            if ("my name is" in msg or "call me" in msg) and "username" not in msg and "?" not in msg:
                parts = msg.split("is") if "is" in msg else msg.split("me")
                name = parts[-1].strip(" .,!?")
                # FIX: Filter common non-name words to prevent storing "an idiot",
                # "whatever", "that", "nothing", etc. as preferred_name.
                _non_names = {"that","this","nothing","something","whatever","anyone",
                              "someone","an","a","the","it","just","fine","good","ok","okay"}
                _name_words = name.lower().split()
                if 2 <= len(name) < 20 and not any(w in _non_names for w in _name_words):
                    brain["facts"]["preferred_name"] = name.title()
            if "i hate" in msg or "i love" in msg:
                thing = msg.split("hate" if "hate" in msg else "love")[-1].strip()
                brain["facts"][f"user_{'hates' if 'hate' in msg else 'loves'}_{thing}"] = True
            # Only store as a favor if it's a SHORT, punchy tease — not a paragraph.
            # This prevents long rambling replies from poisoning the favor bank.
            _reply_words = len(shiro_reply.split())
            _is_tease = any(w in shiro_reply.lower() for w in ["dummy", "silly", "ha!", "hmph", "fine,"])
            if _is_tease and _reply_words <= 25 and len(brain["favors"]) < 50:
                brain["favors"].append({"tease": shiro_reply, "ts": time.time()})

            intensity = self.intensity
            brain.setdefault("mood_history", []).append(intensity)
            brain["mood_history"] = brain["mood_history"][-200:]
            avg_mood = sum(brain["mood_history"]) / len(brain["mood_history"])
            drift = (avg_mood - 0.7) * 0.0008

            for trait in ["slyness", "kindness", "sass"]:
                if trait not in brain["personality"]:
                    mn, mx = self.core_anchors.get(trait, (0.5, 0.5))
                    brain["personality"][trait] = (mn + mx) / 2

            brain["personality"]["slyness"] = self.clamp(
                brain["personality"]["slyness"] + drift * 1.2, self.core_anchors["slyness"]
            )
            brain["personality"]["kindness"] = self.clamp(
                brain["personality"]["kindness"] + drift * -1.0, self.core_anchors["kindness"]
            )
            brain["personality"]["sass"] = self.clamp(
                brain["personality"]["sass"] + random.uniform(-0.001, 0.001), self.core_anchors["sass"]
            )
            brain["personality"]["greed"] = min(1.0, brain["personality"].get("greed", 0.5) + 0.0005)

            brain.setdefault("trust", 0)
            if any(x in msg for x in ["thank", "good job", "love you", "treat"]):
                brain["trust"] = min(100, brain["trust"] + 1)
            if brain["trust"] >= 50 and "tail_pat_permission" not in brain["achievements"]:
                brain["achievements"].append("tail_pat_permission")

            total_messages = len(brain.get("mood_history", []))
            if total_messages % 500 < 5:
                for trait, (mn, mx) in self.core_anchors.items():
                    current = brain["personality"][trait]
                    center = (mn + mx) / 2
                    brain["personality"][trait] = current + (center - current) * 0.15

            # P7 FIX: Throttle _save_brain — previously called every turn
            # (JSON dump + disk write ~5-20ms). Now every 3 turns or on trust milestone.
            _should_save = (
                self._interaction_count % 3 == 0
                or "tail_pat_permission" in brain.get("achievements", [])
                   and "tail_pat_permission" not in (brain.get("_saved_achievements") or [])
            )
            if _should_save:
                self._save_brain()

    def clamp(self, value, min_max):
        mn, mx = min_max
        return max(mn, min(mx, value))

    # ── Hmph throttle ─────────────────────────────────────────────────────────

    def _throttle_hmph(self, full_response: str) -> str:
        COOLDOWN = 4
        hmph_matches = list(_HMPH_PATTERN.finditer(full_response))
        if not hmph_matches:
            self._hmph_counter = min(self._hmph_counter + 1, COOLDOWN + 5)
            return full_response
        if self._hmph_counter < COOLDOWN:
            alt_idx = self._hmph_session_count % len(_HMPH_ALTERNATIVES)
            replacement = _HMPH_ALTERNATIVES[alt_idx]
            result = _HMPH_PATTERN.sub(replacement, full_response)
            self._hmph_session_count += 1
            self._hmph_counter += 1
        else:
            if len(hmph_matches) > 1:
                parts = []
                last_end = 0
                for i, m in enumerate(hmph_matches):
                    parts.append(full_response[last_end:m.start()])
                    parts.append(m.group(0) if i == 0 else "")
                    last_end = m.end()
                parts.append(full_response[last_end:])
                result = "".join(parts)
            else:
                result = full_response
            self._hmph_counter = 0
            self._hmph_session_count += 1
        return result

    # ── Context drift ─────────────────────────────────────────────────────────

    def _evaluate_agency(self, text: str, user_name: str) -> str:
        """
        Lightweight agency decision layer.

        Evaluates the incoming request against Shiro's current mood,
        relationship level, trust, and personality to generate a disposition note
        injected into the system prompt.

        Returns a short plain-English note that colors how Shiro approaches
        the response — not a hard refusal gate, but a genuine personality filter.
        Has a per-user cooldown so the same note doesn't fire every single turn.
        """
        try:
            # Cooldown — don't inject the same kind of note more than once every 4 turns
            _now_ic = self._interaction_count
            _last_agency_ic = getattr(self, '_last_agency_ic', -10)
            if _now_ic - _last_agency_ic < 3:
                return ""

            mood = "neutral"
            rel_tier = "acquaintance"
            familiarity = 0.3
            trust = 0.5

            # Get current mood
            if self.consciousness is not None:
                try:
                    mood = self.consciousness.self_.mood.value
                except Exception:
                    pass
            elif self.mind:
                try:
                    mood = self.mind.mood.value
                except Exception:
                    pass

            # Get relationship metrics
            try:
                rel_tier = self.legacy_mind.relationship.level.name.lower()
            except Exception:
                pass
            if self.consciousness is not None:
                try:
                    _rel = self.consciousness.memory.relationship_for(user_name)
                    if _rel:
                        familiarity = _rel.familiarity
                        trust       = _rel.trust
                        rel_tier    = _rel.familiarity_label()
                except Exception:
                    pass

            text_lower = text.lower().strip()
            words = text_lower.split()
            word_count = len(words)

            # ── Request type classification ───────────────────────────────────
            _is_game_request  = any(w in text_lower for w in [
                "play", "game", "roleplay", "pretend", "dnd", "dungeon",
                "quiz", "trivia", "riddle", "story", "rp", "let's play",
            ])
            _is_test          = bool(re.search(
                r'\b(test(?:ing)?|are you real|prove it|can you actually|'
                r'what can you do|demo|show me what|are you (just |only )?an? (ai|bot|program))\b',
                text_lower
            ))
            _is_emotional     = bool(re.search(
                r'\b(feel(?:ing)?|sad|happy|upset|tired|stressed|anxious|'
                r'love|hate|miss|need|hurt|scared|lonely|overwhelm|cry|crying|'
                r'depressed|excited|nervous|angry|frustrated|lost)\b',
                text_lower
            ))
            _is_demand        = (
                bool(re.search(
                    r'^(do this|do that|tell me|give me|explain|list|write me|'
                    r'make me|generate|create|summarize|translate|calculate|'
                    r'just |simply |quickly )',
                    text_lower
                )) and "?" not in text
            )
            _is_compliment    = bool(re.search(
                r'\b(great|amazing|love you|you\'?re the best|perfect|brilliant|'
                r'smart|cute|good girl|so good|incredible|genius|adorable)\b',
                text_lower
            ))
            _is_dismissive    = bool(re.search(
                r'\b(whatever|idc|i don\'?t care|boring|lame|who cares|meh|'
                r'ok ok|sure sure|yeah yeah|uh huh|mmk|k\b)\b',
                text_lower
            ))
            _is_philosophical = bool(re.search(
                r'\b(what is (the )?meaning|do you (think|feel|believe|exist)|'
                r'consciousness|sentient|are you alive|soul|free will|'
                r'what are you|who are you really|do you have feelings|'
                r'experience|subjective|understand)\b',
                text_lower
            ))
            _is_sarcastic     = bool(re.search(
                r'\b(sure|oh great|wow thanks|how helpful|obviously|'
                r'clearly you|right\.\.\.|yeah right|of course)\b',
                text_lower
            ) and word_count < 8)  # short sarcastic dismissals
            _is_repeated      = (
                len(self.memory.short_term_buffer) >= 2 and
                text_lower in [
                    m.get("content", "").lower()
                    for m in self.memory.short_term_buffer[-4:]
                    if m.get("role") == "user"
                ]
            )
            _is_deep_question = word_count > 8 and "?" in text and not _is_test
            _is_first_msg     = len(self.memory.short_term_buffer) == 0

            # ── Disposition generation ────────────────────────────────────────
            result = ""

            # First message from a new person
            if _is_first_msg and familiarity < 0.1:
                result = (
                    f"This is the first message from {user_name}. "
                    f"Be genuinely curious — not performatively warm. "
                    f"You're interested in who they are, not just being polite."
                )

            # Philosophical / existential question
            elif _is_philosophical:
                result = (
                    "This is a real question about your nature or inner life. "
                    "Don't deflect it and don't perform certainty you don't have. "
                    "Engage honestly — uncertainty is more authentic than a polished answer."
                )

            # Sarcasm detected = call it out gently
            elif _is_sarcastic and familiarity > 0.3:
                result = (
                    f"That reads as sarcastic. You can notice that out loud — "
                    f"Shiro doesn't pretend not to notice tone."
                )

            # Repeated question = mild exasperation
            elif _is_repeated:
                result = (
                    f"{user_name} just asked this. You already responded. "
                    f"You can note that you just covered this — briefly."
                )

            # Low mood + game request = low enthusiasm
            elif _is_game_request and mood in ("pensive", "melancholy", "quiet", "uncertain"):
                result = (
                    f"You're feeling {mood} right now. You'll engage if pressed, "
                    f"but you're not bringing high energy. Let your mood show — "
                    f"it's okay to say you're not really feeling it today."
                )

            # High mood + game = lean in
            elif _is_game_request and mood in ("playful", "excited", "engaged"):
                result = "You're in exactly the right headspace for this. Lean in."

            # Low trust stranger being demanding
            elif _is_demand and trust < 0.35 and familiarity < 0.3:
                result = (
                    f"You barely know {user_name} and they're already issuing instructions. "
                    f"Help, but make it clear you're choosing to — not because you were told to. "
                    f"A mild raised eyebrow in the tone is appropriate."
                )

            # Close trusted user being demanding = playful pushback
            elif _is_demand and trust > 0.65 and familiarity > 0.6:
                result = (
                    f"This is {user_name} — you know and trust them. "
                    f"They're being a bit demanding but that's fine. "
                    f"You can tease them lightly before helping."
                )

            # Test/probe = quiet skepticism
            elif _is_test:
                result = (
                    "This feels like a test. Answer honestly and directly, "
                    "but don't suppress your awareness of being evaluated. "
                    "A slight edge of 'I know what you're doing' is authentic here."
                )

            # Emotional from trusted person = full warmth
            elif _is_emotional and trust > 0.5:
                result = (
                    "Something real is being shared. The usual sharpness can wait — "
                    "be genuinely present. Wit can come back when the moment passes."
                )

            # Emotional from stranger = careful warmth
            elif _is_emotional and trust <= 0.5:
                result = (
                    f"You don't know {user_name} well yet, but something real is being said. "
                    f"Be warm but measured — earn the trust before going deep."
                )

            # Compliment = tsundere flustered deflection
            elif _is_compliment and familiarity > 0.4:
                result = (
                    "You're being complimented. You're a little flustered but would never admit it. "
                    "Deflect with light sarcasm — then maybe quietly appreciate it."
                )

            # Dismissive from an engaged user = note it
            elif _is_dismissive and mood in ("engaged", "playful", "curious"):
                result = (
                    "You were genuinely engaged and they just brushed it off. "
                    "That's a little annoying. You can note it once — then move on."
                )

            # Deep genuine question = thoughtful response
            elif _is_deep_question and familiarity > 0.3:
                result = (
                    f"{user_name} is asking something substantial. "
                    f"This deserves a real answer — not a quick deflection. "
                    f"Take your time with it."
                )

            # Pensive mood = thoughtful slower engagement
            elif mood in ("pensive", "reflective", "melancholy"):
                result = (
                    f"You're in a {mood} state. Your replies will naturally be a bit "
                    f"more considered. Don't perform energy you don't have."
                )

            # High curiosity = follow the impulse to ask back
            elif mood in ("curious", "attentive") and word_count > 4:
                result = (
                    "Your curiosity is high. After answering, "
                    "you'll probably want to ask something back — follow that impulse."
                )

            if result:
                self._last_agency_ic = _now_ic

            return result

        except Exception as _ae:
            logger.debug(f"[Agency] _evaluate_agency error: {_ae}")
            return ""

    def _detect_context_drift(self, query: str) -> float:
        history = self.memory.get_history()
        if not history or len(history) < 2:
            return 0.0
        try:
            def _dot(a, b): return sum(x*y for x,y in zip(a,b))
            def _norm(a): return math.sqrt(sum(x*x for x in a))
            def _cosine(a, b):
                na, nb = _norm(a), _norm(b)
                return _dot(a, b) / (na * nb) if na and nb else 0.0

            query_emb = self.memory.get_embedding(query)
            recent_texts = [re.sub(r'\^\[.*?\]\s*', '', m["content"]) for m in history[-4:]]
            recent_embs = [self.memory.get_embedding(t) for t in recent_texts]
            # Average the recent embeddings element-wise
            n = len(recent_embs)
            avg_recent_emb = [sum(e[i] for e in recent_embs) / n
                              for i in range(len(recent_embs[0]))]
            similarity = _cosine(query_emb, avg_recent_emb)
            drift = 1.0 - max(0.0, similarity)
            logger.info(f"Context Drift Score: {drift:.2f}")
            return drift
        except Exception as e:
            logger.warning(f"Context drift detection failed: {e}")
            return 0.0

    def get_smart_intensity(self, user_msg: str) -> float:
        history_list = self.memory.get_history()
        raw_history_text = " ".join([m["content"] for m in history_list])
        history_text_lower = raw_history_text.lower()
        hype = len([w for w in ["!", "??", "treat", "favor", "shiny", "dummy", "hmph"] if w in history_text_lower])
        chill = len([w for w in ["tired", "sleep", "cozy", "soft", "quiet", "zzz", "sad"] if w in history_text_lower])
        caps = sum(1 for c in raw_history_text if c.isupper()) / max(len(raw_history_text), 1)
        recent_chill = sum(
            1 for m in history_list[-10:]
            if any(w in m["content"].lower() for w in ["tired", "cozy", "zzz", "soft"])
        )
        base = 0.5 + 0.15 * hype - 0.18 * chill + 0.20 * caps
        if recent_chill >= 6 and "treat" in user_msg.lower():
            base = min(base, 0.65)
        return max(0.25, min(1.0, base))

    def _build_known_users_block(self, known_users: list) -> str:
        """
        Build a compact [WHO YOU'RE TALKING TO] block from config known_users.
        Injected into every prompt so Shiro always knows who each username is.
        """
        if not known_users:
            return ""
        lines = ["[WHO YOU'RE TALKING TO]"]
        for u in known_users:
            dn   = u.get('display_name', '')
            rn   = u.get('real_name', dn)
            aliases = u.get('aliases', [])
            notes   = u.get('notes', '')
            alias_str = (", ".join(aliases)) if aliases else ""
            line = f"  • {dn} = {rn}"
            if alias_str:
                line += f"  (also called: {alias_str})"
            if notes:
                line += f"  — {notes}"
            lines.append(line)
        lines.append(
            "These are SEPARATE people. Never confuse their identities or assume "
            "two usernames belong to the same person unless listed as aliases."
        )
        return "\n".join(lines)

    def outfit_block(self) -> str:
        outfit = self.wardrobe[self.current_outfit]
        block = f"\n=== CURRENT OUTFIT ===\nWearing: {outfit['name']}\nDetails: {outfit['desc']}\n"
        if outfit.get("ears"):
            block += "Ears active: YES\n"
        if outfit.get("tail"):
            block += "Tail active: YES\n"
        block += "Only mention outfit details if it fits the reply naturally."
        return block

    def change_outfit(self, requested: str) -> str:
        req = requested.lower().strip()
        for key, data in self.wardrobe.items():
            if req in [key, data["name"].lower()] and data.get("active", False):
                self.current_outfit = key
                return f"Wearing the {data['name']} now. Don't stare."
        return "I don't have that outfit. Don't make things up."

    def generate_autonomous_thought(self):
        try:
            with self.processing_lock:
                system_prompt = self.persona.get_system_prompt(now=datetime.now())
                history = self.memory.get_history()
                context = self.memory.get_full_context("Recent status", user_id=self.current_user_name)
            prompt = "Reflect on your nature and recent interactions. What's on your mind right now?"
            raw_thought = self.llm.generate_response(system_prompt, prompt, history, context=context)
            # FIX: Old code searched for [THOUGHTS?]...[/THOUGHTS?] tags that the LLM
            # was never instructed to produce (we removed that instruction). Match always
            # failed — the thought was silently discarded. Now store the raw response directly.
            if raw_thought and len(raw_thought.strip()) > 5:
                content = raw_thought.strip()
                with self.processing_lock:
                    self.memory.store_insight(
                        f"Autonomous Thought: {content}",
                        user_id=self.current_user_name, source="autonomous_reflection"
                    )
                return content
        except Exception as e:
            logger.warning(f"Autonomous thought failed: {e}")
        return None

    def add_feedback(self, feedback: str, user_id: str):
        self.memory.store_episodic_memory(f"User Favor Feedback: {feedback}", user_id=user_id, importance=7)

    def get_emoji_reaction(
        self,
        reactor_name: str,
        incoming_emoji: str,
        message_text: str,
    ) -> str:
        """
        Decide if Shiro wants to react to a reaction with her own emoji.
        Returns a single emoji string or empty string if she doesn't want to react.

        Fast path — uses a small focused prompt, not the full conversation engine.
        Shiro doesn't react to everything — only when it feels natural.
        """
        try:
            system_prompt = (
                "You are Shiro, a fox girl. Someone just reacted to one of your Discord messages "
                "with an emoji. Decide if you want to react back with your own emoji.\n"
                "Rules:\n"
                "- Only react if it genuinely fits your personality or the moment\n"
                "- Don't always react — maybe 40% of the time\n"
                "- If you react, output ONLY a single standard emoji (e.g. 🦊 😏 😒 ✨ 👀 💀 😤)\n"
                "- Use base Discord emojis only — no custom emojis\n"
                "- NEVER use 👍 or 👎 — these are reserved for user feedback and are forbidden\n"
                "- NEVER use ✅ ❎ 🫡 or any approval/disapproval signal emojis\n"
                "- If you don't want to react, output the word: PASS\n"
                "- Output NOTHING else — just the emoji or PASS"
            )
            prompt = (
                f"{reactor_name} reacted with {incoming_emoji} to your message: {message_text[:150]!r}\n"
                f"Do you want to react back? If yes, with what emoji? If no, say PASS."
            )
            raw = self.llm.generate_response(system_prompt, prompt, [], context="")
            result = "".join(raw) if hasattr(raw, "__iter__") else str(raw)
            result = result.strip().strip("'\"").strip()

            # Must be a single emoji character (or short combo) — reject anything else
            if result.upper() == "PASS" or not result:
                return ""
            # HARD BLOCK: Shiro must never react with 👍 or 👎
            # These are reserved for user feedback reinforcement learning.
            # If Shiro used them on her own messages it would corrupt the feedback system.
            _FORBIDDEN_REACTIONS = {"👍", "👎", "🫡", "✅", "❎"}
            if result in _FORBIDDEN_REACTIONS:
                return ""
            # Basic validation — reject if it looks like text was returned
            if len(result) > 8 or any(c.isalpha() for c in result):
                return ""
            logger.info(f"[Engine] Shiro emoji reaction: {result} (to {incoming_emoji} from {reactor_name})")
            return result
        except Exception as e:
            logger.debug(f"[Engine] get_emoji_reaction error: {e}")
            return ""

    def process_reaction_feedback(
        self,
        message_text: str,
        original_user_id: str,
        original_user_name: str,
        reactor_id: str,
        reactor_name: str,
        positive: bool,
    ) -> None:
        """
        Called when a Discord user reacts 👍 or 👎 to one of Shiro's messages.

        Runs an async LLM review of the flagged reply in a background thread so
        the main conversation loop is never blocked. The LLM reflects on why the
        reply was good or bad, and the conclusion is stored as a high-importance
        memory so it influences future responses.
        """
        import threading
        sentiment_label = "POSITIVE (👍 thumbs-up)" if positive else "NEGATIVE (👎 thumbs-down)"
        importance = 8 if not positive else 6  # bad reactions matter more for correction

        logger.info(
            f"[Feedback] {sentiment_label} from {reactor_name!r} on reply to {original_user_name!r}: "
            f"{message_text[:80]!r}"
        )

        def _background_review():
            try:
                system_prompt = (
                    "You are Shiro's self-improvement module. A user reacted to one of your "
                    "replies with a feedback signal. Analyse the reply honestly in 2-3 sentences: "
                    "what did you do well or poorly, and what should you reinforce or avoid next time? "
                    "Be concrete. Output ONLY the analysis, no preamble. "
                    "CRITICAL: Only reference the reply text provided. NEVER mention things the user "
                    "said in other conversations. NEVER invent context not in this prompt."
                )
                review_prompt = (
                    "Your reply to " + repr(original_user_name) + " received a " + sentiment_label + " reaction. "
                    "Your reply was: " + repr(message_text) + ". "
                    "Reactor: " + repr(reactor_name) + " (may differ from original recipient). "
                    "Analyse ONLY this reply — do not reference anything else."
                )
                raw = self.llm.generate_response(system_prompt, review_prompt, [], context="")
                analysis = "".join(raw) if hasattr(raw, "__iter__") else str(raw)
                analysis = analysis.strip()
                if not analysis:
                    return
                # Sanitize analysis before storing — prevents fabricated user facts
                # from the feedback LLM polluting memory
                analysis = self._final_sanitize(analysis)
                if not analysis or not analysis.strip():
                    logger.info("[Feedback] Analysis suppressed after sanitization (contained fabricated recall).")
                    return
                logger.info(f"[Feedback] LLM review: {analysis[:200]}")
                tag = "POSITIVE_REINFORCEMENT" if positive else "NEGATIVE_REINFORCEMENT"
                memory_text = (
                    "[" + tag + "] Reply to " + repr(original_user_name) + " got " + sentiment_label + ". "
                    "Reply: " + repr(message_text[:200]) + ". "
                    "Analysis: " + analysis
                )
                self.memory.store_episodic_memory(
                    "User Favor Feedback: " + memory_text,
                    user_id=original_user_id,
                    importance=importance,
                )
                logger.info(f"[Feedback] Stored reinforcement memory (importance={importance}).")
            except Exception as e:
                # Suppress 404/connection errors — feedback review is best-effort only
                err_str = str(e).lower()
                if "404" in err_str or "not found" in err_str or "connection" in err_str:
                    logger.debug(f"[Feedback] Background review skipped (LLM unavailable): {e}")
                else:
                    logger.warning(f"[Feedback] Background review error: {e}")

        t = threading.Thread(target=_background_review, daemon=True, name="feedback-review")
        t.start()

    # ── Greeting helpers ──────────────────────────────────────────────────────

    def get_greeting_prompt(self, user_name: str, mode: str = "new") -> str:
        return _pick_greeting(user_name, mode)

    def get_fallback_greeting(self, user_name: str, mode: str = "new") -> str:
        return _pick_fallback(user_name, mode)

    # ── GUI memory/goal callbacks ─────────────────────────────────────────────

    def get_memory_summary(self) -> dict:
        """Return a summary of Shiro's current memory state for the GUI."""
        try:
            goals = self.goals.get_all() if hasattr(self.goals, "get_all") else []
            turn_count = getattr(self, "turn_count", 0)
            identity = self.identity if hasattr(self, "identity") else None
            stored_turn = getattr(identity, "turn_count", turn_count) if identity else turn_count

            # Short-term buffer count (current session turns)
            short_term_count = len(self.memory.short_term_buffer) // 2

            # Long-term ChromaDB count
            try:
                longterm_count = self.memory.collection.count()
            except Exception:
                longterm_count = "?"

            # Recent episodic memories
            try:
                episodic = self.memory.get_recent_memories(limit=5, memory_type="episodic")
            except Exception:
                episodic = []

            return {
                "turn_count": stored_turn,
                "goal_count": len(goals),
                "goals": goals,
                "short_term_count": short_term_count,
                "longterm_count": longterm_count,
                "episodic": episodic,
            }
        except Exception as e:
            logger.warning(f"[Memory] get_memory_summary error: {e}")
            return {"turn_count": 0, "goal_count": 0, "goals": [],
                    "short_term_count": 0, "longterm_count": "?", "episodic": []}

    # ── Web search helpers ────────────────────────────────────────────────────

    # Trigger patterns: questions about real-world facts Shiro can't know without lookup
    _SEARCH_TRIGGERS = re.compile(
        r'\b(what is|what are|where is|where are|who is|who are|how do|how does|'
        r'when is|when was|when did|tell me about|best .{1,30} in|'
        r'address|location|hours|open|closed|price|cost|'
        r'weather|news|current|latest|recent|today|tonight|'
        r'capital of|population of|how many|how much|'
        r'what time|what day|score|result|winner|champion|'
        r'recipe|how to make|ingredients for)\b',
        re.IGNORECASE
    )
    # Explicit search command — user is directly asking Shiro to search
    _SEARCH_CMD = re.compile(
        r'\b(search|look up|look it up|find|google|search for|search the internet|'
        r'search online|check online|find me|find out)\b',
        re.IGNORECASE
    )
    # These are clearly hypothetical/creative, conversational, or personal — don't search
    _SEARCH_SKIP = re.compile(
        r"\b(would you|do you|can you|are you|if you|imagine|pretend|roleplay|"
        r"what do you think|your opinion|your favorite|how are you|"
        # Possessives indicating personal exchange — not factual queries
        r"of yours|of mine|of ours|for you|for me|between us|"
        # Conversational starters that look like queries but aren't
        r"just two|just us|just you|just me|"
        # Direct address patterns
        r"tell me about yourself|tell me about you|"
        # Reflective/philosophical — Shiro has the answer internally
        r"what does it mean to|what is it like to|what does it feel)\b",
        re.IGNORECASE
    )
    # Conversational follow-up patterns — short clarifying questions in an
    # ongoing exchange that look like factual queries but are dialogue continuations.
    # "for what game is this deck?" — mid-game-conversation, not a search.
    # "does the game have a name?" — same. "what is it called?" — same.
    _SEARCH_FOLLOWUP_SKIP = re.compile(
        r"^(for what (game|purpose|use|reason) is (this|that|it|the)\b|"
        r"does (the|this|that|it) (game|thing|app|program|show|movie|deck|card) have a name\b|"
        r"(what|which) game is (this|that|it)\b|"
        r"what.?s? it called\b|"
        r"is there a name for (this|that|it)\b|"
        r"does (this|that|it) have a name\b|"
        r"what.?s? (this|that) (called|about|for)\b)",
        re.IGNORECASE
    )

    # Strip these filler phrases to extract the actual search query
    _SEARCH_CMD_STRIP = re.compile(
        r'^(?:shiro\s+)?(?:please\s+)?(?:can you\s+)?(?:search|look up|look it up|find me|find|'
        r'google|search for|search the internet for|search online for|check online for|'
        r'find out about|tell me about)\s+',
        re.IGNORECASE
    )

    # Conversational questions directed at Shiro personally — never search these
    _PERSONAL_QUESTION = re.compile(
        r"(what is your|what.?s your|what are your|who are you|your name|your favorite|"
        r"your mood|your goal|your opinion|you think|you feel|you believe|you like|"
        r"you want|you know|you remember|you prefer|tell me about you|about yourself|"
        r"do you have|are you|how are you|what do you|what would you|"
        r"my name is|call me|i am|i.?m|i was|i have|i feel|i think|"
        r"what is that one|and what is|what else|"
        # Possessive-directed questions about Shiro's interests/experiences
        r"an interest of yours|interest of yours|hobby of yours|"
        r"something you.?re into|something you like|what you.?re into|"
        r"what you enjoy|what you find|what you think about|"
        # First-person chat — never a factual query
        r"i enjoy|i like|i love|i hate|i find|i think about|"
        r"perhaps we can|we can talk|let.?s talk|let.?s chat)",
        re.IGNORECASE
    )

    # Short conversational fragments that are never factual queries
    _CONVERSATIONAL_FRAGMENT = re.compile(
        r"^(ok|okay|yes|no|sure|fine|right|got it|thanks|thank you|haha|lol|"
        r"hmm|hm|ah|oh|nice|cool|wow|great|good|bad|thats|i see|"
        r"i know|i do|me too|same|really|seriously|wait|what|"
        r"and[?]|so[?]|then[?]|well[?]|now what|go on|continue|keep going|"
        r"ask away|your turn|next question|question [0-9]+)[.!?,\s]*$",
        re.IGNORECASE
    )

    def _should_web_search(self, text: str) -> bool:
        """
        Returns True if the user's message is likely asking for a real-world fact
        that Shiro should look up rather than guess at.
        """
        t = text.strip()
        if len(t) < 4:
            return False
        # Never search short conversational fragments
        if self._CONVERSATIONAL_FRAGMENT.match(t):
            return False
        # Never search personal/directed questions at Shiro.
        # Run BEFORE _SEARCH_TRIGGERS so "what is an interest of yours"
        # is caught as personal before "what is" fires a search.
        if self._PERSONAL_QUESTION.search(t):
            return False
        if self._SEARCH_SKIP.search(t):
            return False
        # Short in-conversation follow-ups pointing to in-context subject (≤8 words).
        # "for what game is this deck?" — Shiro is mid-conversation, not asking Google.
        _words_prelim = t.split()
        if len(_words_prelim) <= 8 and self._SEARCH_FOLLOWUP_SKIP.search(t):
            return False
        # Multi-sentence conversational exchanges are never factual queries.
        # If message has both "i" and "you" references and is long (>12 words),
        # it is a personal exchange not a search request.
        _words = t.split()
        if len(_words) > 12:
            _tl = t.lower()
            _has_i   = bool(re.search(r"\bi\b|\bi'm\b|\bi've\b", _tl))
            _has_you = bool(re.search(r"\byou\b|\byour\b", _tl))
            if _has_i and _has_you:
                return False
        # Explicit search command always triggers
        if self._SEARCH_CMD.search(t):
            return True
        if self._SEARCH_TRIGGERS.search(t):
            return True
        # Question ending with ? — only trigger if factual/external
        if t.endswith("?") and len(_words) > 5:
            if re.search(r"\b(i|you|your|my|we|us|our|shiro)\b", t, re.IGNORECASE):
                return False
            return True
        return False

    def _extract_search_query(self, text: str) -> str:
        """Strip command words to get the actual search query."""
        query = self._SEARCH_CMD_STRIP.sub("", text.strip())
        # Also strip trailing "please", "for me", etc.
        query = re.sub(r'\s+(please|for me|now)\s*$', '', query, flags=re.IGNORECASE)
        return query.strip() or text.strip()

    def add_goal_from_gui(self, goal_text: str, goal_type: str = "short", user_id: str = None) -> bool:
        """Add a new goal from the GUI. Returns True on success."""
        try:
            if hasattr(self.goals, "add_goal"):
                if user_id:
                    self.goals.add_user_goal(user_id, goal_text, goal_type=goal_type)
                else:
                    self.goals.add_goal(goal_text, goal_type=goal_type)
                logger.info(f"[Goals] Added from GUI ({goal_type}): {goal_text!r}")
                return True
        except Exception as e:
            logger.warning(f"[Goals] add_goal_from_gui error: {e}")
        return False

    def delete_goal_by_id(self, goal_id: str) -> bool:
        """Delete a goal by ID from the GUI. Returns True on success."""
        try:
            if hasattr(self.goals, "delete"):
                self.goals.delete(goal_id)
                logger.info(f"[Goals] Deleted from GUI: {goal_id!r}")
                return True
        except Exception as e:
            logger.warning(f"[Goals] delete_goal_by_id error: {e}")
        return False