from __future__ import annotations
import logging
import os
import sys
import re
import yaml
import threading
import random
import time
import json
import asyncio
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from typing import Any, List, Dict, Optional, Generator, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm.client import LlamaClient
from memory.store import MemoryStore
from persona.manager import PersonaManager
from persona.inner_mind import ShiroInnerMind
from utils.text_utils import split_into_sentences, clean_yaml_block

from consciousness.events import EventBus
from consciousness.intent import IntentPlanner, SentimentTrajectory, RuleEngine, TimePattern
from consciousness.speech_cadence import SpeechCadence
from consciousness.self_awareness import SelfAwareness
from consciousness.thought_loop import InnerMind, Mood
from consciousness.autonomous_voice import AutonomousVoice
from consciousness.memory import ConversationMemory

logger = logging.getLogger(__name__)

# ── Greeting prompts ──────────────────────────────────────────────────────────
# IMPORTANT: These are raw system directives sent ONLY to the LLM as the user
# turn. They must NEVER appear in Shiro's visible output. The LLM is told to
# generate Shiro's response — NOT to repeat the directive.





# Greeting mood seeds — these give Shiro an emotional direction only,
# never a specific line. The LLM generates the actual words fresh each time.
_GREET_RETURNING_LONG_MOODS = [
    "guarded warmth — glad they're back but hiding it",
    "mildly suspicious — where were they?",
    "teasing relief — you noticed the absence",
    "dry, understated — act like it's no big deal",
]
_GREET_RETURNING_SOON_MOODS = [
    "casual acknowledgement — you barely noticed",
    "light curiosity — wonder what they were up to",
    "playful — like you have a secret about their absence",
    "warm but unbothered — glad they're back",
]
_GREET_NEW_MOODS = [
    "curious about this new person",
    "cool but intrigued",
    "guarded but not unfriendly",
    "sizing them up with a hint of amusement",
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
    """
    time_part = f" It's {time_hint}." if time_hint else ""
    return (
        f"{user_name} just showed up.{time_part} "
        f"Greet them in one sentence. Your mood right now: {mood}. "
        f"Speak only your actual greeting words — no asterisks, no stage directions, "
        f"no meta-commentary. Make it feel natural and in the moment."
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
        self._last_response_text: str = ""          # dedup guard for autonomous voice
        self._last_response_ts: float = 0.0         # timestamp of last response

        # v4 Consciousness Core
        self.bus = EventBus()
        self.intent_planner = IntentPlanner()
        self.sentiment_trajectory = SentimentTrajectory()
        self.time_pattern = TimePattern()
        self.cadence = SpeechCadence()
        self.rules = RuleEngine()
        self.awareness = SelfAwareness(name="Shiro", event_bus=self.bus)

        self.mind = InnerMind(
            thought_tick_seconds=4.0,
            thought_callback=self._on_v4_thought,
            enable_reactions=True
        )

        async def _speak_callback(event):
            logger.info(f"[AUTONOMOUS VOICE]: {event.text}")
            if self.on_autonomous_speak:
                if asyncio.iscoroutinefunction(self.on_autonomous_speak):
                    await self.on_autonomous_speak(event.text, event.speech_type)
                else:
                    self.on_autonomous_speak(event.text, event.speech_type)

        self.voice = AutonomousVoice(speak_callback=_speak_callback)
        self.v4_memory = ConversationMemory()
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
            max_tokens=llm_cfg.get('max_tokens', 512),
            use_native_tools=llm_cfg.get('use_native_tools', True),
            num_gpu=llm_cfg.get('num_gpu')
        )
        pers_cfg = config.get('persona', {})
        self.persona = PersonaManager(sheet_path=pers_cfg.get('sheet_path', 'shiro_sheet.yaml'))
        self.legacy_mind = ShiroInnerMind(name="Shiro", verbose=True)
        self.last_thought = ""
        self._greeting_silent = False  # set True when silence chosen on join
        self._user_is_typing = False   # set by UI; suppresses autonomous messages
        self._user_typing_ts = 0.0     # timestamp when typing started
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
                    raise TypeError(f"Type {type(obj)} not serializable")
                temp_file = self.profile_file.with_suffix(".tmp")
                temp_file.write_text(json.dumps(self.user_profiles, indent=2, default=json_serial))
                temp_file.replace(self.profile_file)
            except Exception as e:
                logger.error(f"Failed to save profiles: {e}")

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

    def _on_v4_thought(self, thought):
        """Callback for v4 InnerMind thoughts — stays INTERNAL, never reaches UI."""
        # Removed aggressive space-removal regex that was merging valid words like "this is one"
        cleaned = thought.text
        self.last_thought = cleaned
        self.bus.emit("thought_fired", thought=cleaned)

    async def _v4_idle_loop(self):
        """
        Idle loop — fires when Shiro has been quiet for a while.
        Replaced canned AutonomousVoice strings with live LLM-generated messages
        so every autonomous message is grounded in real context, never scripted.
        Variable timing (30-90s) so she doesn't feel like a chatbot on a timer.
        """
        while True:
            # Variable wait: 30-90 seconds. Feels organic, not robotic.
            await asyncio.sleep(random.uniform(30.0, 90.0))
            idle_s = (datetime.now(timezone.utc) - self.last_interaction_time).total_seconds()
            present = [p for p in self.awareness.users.values() if p.present]

            if not present or idle_s < 45.0 or self.processing_lock.locked():
                # Nobody home, just spoke, or system is busy — stay quiet
                continue

            # Fix 5: Respect typing indicator — don't interrupt mid-thought
            import time as _t
            if self._user_is_typing:
                # User is actively composing — hold message
                continue
            # Also suppress if user typed within the last 8 seconds
            # (they may have just submitted and we're still processing)
            if _t.time() - self._user_typing_ts < 8.0:
                continue

            # Pick a user to address (usually the most recent)
            target = random.choice(present)
            user_name = target.user_id

            # Generate a real, contextual message instead of a canned string
            msg = await self._generate_live_autonomous_message(user_name)
            if not msg:
                continue

            # Store in memory BEFORE delivering — this is critical.
            # Without this, Shiro has no recollection of saying it when the user replies.
            self.memory.short_term_buffer.append(
                {"role": "assistant", "content": msg}
            )
            if len(self.memory.short_term_buffer) > self.memory.max_short_term * 2:
                self.memory.short_term_buffer =                     self.memory.short_term_buffer[-(self.memory.max_short_term * 2):]
            threading.Thread(
                target=self.memory.add_interaction_to_longterm,
                args=("[autonomous]", msg, user_name),
                daemon=True
            ).start()

            self.last_interaction_time = datetime.now(timezone.utc)

            if self.on_autonomous_speak:
                if asyncio.iscoroutinefunction(self.on_autonomous_speak):
                    await self.on_autonomous_speak(msg, "autonomous")
                else:
                    self.on_autonomous_speak(msg, "autonomous")
            logger.info(f"[AUTONOMOUS]: {msg[:80]}")

    # ── User join/leave ───────────────────────────────────────────────────────

    def set_user_typing(self, is_typing: bool):
        """Called by the UI when the user starts or stops typing.
        While typing, Shiro holds autonomous messages so she doesn't
        interrupt mid-thought.
        """
        import time as _time
        self._user_is_typing = is_typing
        if is_typing:
            self._user_typing_ts = _time.time()

    def on_user_join(self, user_name: str) -> Generator[str, None, None] | None:
        """
        Called when a user joins the chat.
        Returns a greeting generator ~60% of the time.
        ~40% of the time Shiro stays silent and waits for the user to speak first —
        this makes her feel present and watchful rather than obligated to react.
        The greeting is always LLM-generated on the spot (never a canned string).
        """
        profile = self.awareness.user_entered(user_name)
        self.v4_memory.new_session(user_name)
        self.last_interaction_time = datetime.now(timezone.utc)
        if self.current_user_name != user_name:
            self.current_user_name = user_name
            self.legacy_mind.switch_user(user_name)
            self.memory.load_recent_history(user_name)

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
        self._greeting_silent = False
        greeting_prompt = self.get_greeting_prompt(user_name, mode)
        try:
            return self.process_text(greeting_prompt, user_name="System")
        except Exception as e:
            logger.warning(f"Greeting generation failed: {e}")
            fallback = self.get_fallback_greeting(user_name, mode)
            def _fb():
                yield fallback
            return _fb()

    def on_user_leave(self, user_name: str):
        self.awareness.user_left(user_name)

    # ── Core text processing ──────────────────────────────────────────────────

    def process_text(self, text: str, user_name: str = None,
                     interrupt_event: threading.Event = None) -> Generator[str, None, None]:
        self.last_thought = ""
        current_time = datetime.now(timezone.utc)
        processed_text = text

        if not user_name:
            user_name = self.current_user_name
        else:
            if self.current_user_name != user_name:
                logger.info(f"Switching active user to: {user_name}")
                self.current_user_name = user_name
                self.legacy_mind.switch_user(user_name)
                self.memory.load_recent_history(user_name)

        # ── FIX: Strip any leaked system directive if the user somehow echoed it ──
        processed_text = _LOG_DIRECTIVE.sub('', processed_text).strip()
        # Strip the [SYSTEM DIRECTIVE] wrapper if it somehow ended up in the user turn
        processed_text = re.sub(r'\[SYSTEM DIRECTIVE.*?\[END DIRECTIVE\]', '', processed_text,
                                 flags=re.DOTALL | re.IGNORECASE).strip()

        user_profile_obj = self.awareness.user_spoke(user_name, processed_text)
        self.cadence.sync_mirror_vocab(user_name, user_profile_obj.mirror_vocab)
        self.cadence.observe(user_name, processed_text)
        self.bus.emit("user_spoke", user_id=user_name, text=processed_text)

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
        _is_greeting_directive = (
            user_name == "System" and
            any(processed_text.lower().startswith(pfx) for pfx in [
                "tyler just arrived", "user just arrived",
                "someone new is here", "new arrival",
                "they just came back", "they've been gone",
                "they came back", "back again",
                "just say hi", "quick greeting"
            ])
        )
        if _is_greeting_directive and self._greeting_silent:
            logger.info("Greeting directive suppressed (silence was chosen on join)")
            self._greeting_silent = False
            return

        # Identity check (only add flag, don't alter user text visibly)
        _long_absence = False
        if processed_text and not processed_text.startswith("[") and user_name != "System":
            last_seen = self.memory.get_last_interaction_time(user_name)
            if last_seen and (datetime.now(timezone.utc) - last_seen).days > 7:
                _long_absence = True

        logger.info(f"--- Engine Processing: '{processed_text[:80]}' ---")

        with self.processing_lock:
            try:
                previous_interaction = self.last_interaction_time
                self.last_interaction_time = current_time

                self.intensity = self.get_smart_intensity(processed_text)
                temp = 0.5 + 0.2 * self.intensity
                self.llm.temperature = temp

                drift_score = self._detect_context_drift(processed_text)

                # ── Length calibration: 4 tiers, plain prose, no bracket tokens ──
                # FIX: Old [SYSTEM: CASUAL EXCHANGE] bracket tokens caused leak risk.
                # Uses word count (not char count) + greeting detection for accuracy.
                _user_words = len(processed_text.split())
                _is_greeting_msg = bool(user_emotions.get("greeting", 0) > 0.4)
                if _is_greeting_msg or _user_words <= 4:
                    length_hint = (" Mirror their energy — they were brief, be brief. "
                                   "1-2 sentences max. A single short sentence or fragment is fine.")
                elif _user_words <= 15:
                    length_hint = " 1-3 sentences. Be direct. Stop when you're done."
                elif _user_words <= 40:
                    length_hint = " Up to 4 sentences. Answer the question, then stop."
                else:
                    length_hint = " Respond with appropriate depth. No padding, no cutoff."

                inner_mind_data = self.legacy_mind.process_input(
                    processed_text, user_id=user_name
                )
                inner_context = inner_mind_data.get("inner_context", "")

                # ── FIX: Scrub inner_context so it never leaks raw block headers ──
                inner_context = self._scrub_inner_mind_block(inner_context)

                self.mind.update_context({
                    "focus_user":          user_name,
                    "last_snippet":        processed_text[:40],
                    "relationship_tier":   inner_mind_data.get("relationship", "STRANGER").lower(),
                    "sentiment_trajectory": trajectory,
                    "idle_ms":             (datetime.now(timezone.utc) - self.last_interaction_time).total_seconds() * 1000
                })

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
                anti_leak = (
                    "\n\nCRITICAL RULES — follow every time:\n"
                    "1. NEVER output meta-blocks in your spoken reply: no [THOUGHT], [INNER MIND],"
                    " (LOG: ...), or any system markup. Your spoken reply is plain words only.\n"
                    "2. Speak directly. No asterisk actions unless the user uses them first.\n"
                    f"3. You and {user_name} have exchanged {session_turns} messages this session"
                    f" ({session_duration}). This is an ongoing conversation, not a fresh start.\n"
                    "4. HONESTY: Only reference things you actually know from memory or what was"
                    " said in this conversation. If you don't know something, say so.\n"
                    " Do not invent facts, events, or details about the user to fill conversation.\n"
                    "5. Match reply length to the message — brief messages deserve brief replies."
                )

                # FIX: Removed [Current Intensity: X.XX] bracket token — intensity is
                # internal data, not something the LLM should reference in output format.
                # drift_note [SYSTEM: Topic shift] also replaced with plain prose.
                _drift_note_clean = ("Note: topic just shifted — adjust focus." if drift_score > 0.6 else "")
                _absence_note = ("Note: it has been over a week since you last spoke with "
                                 f"{user_name}. You may not remember them well — be honest "
                                 "about that rather than pretending certainty.") if _long_absence else ""
                shiro_context = (
                    f"\n{_drift_note_clean} {_absence_note}"
                    f"\n{self.outfit_block()}"
                    f"\n{length_hint}"
                    f"{anti_leak}"
                )

                # P3 FIX: Run both pre-LLM memory fetches concurrently.
                # Previously sequential: fetch1.wait() then fetch2.wait().
                # Now both ChromaDB queries run in parallel; total cost = max(t1,t2).
                from concurrent.futures import ThreadPoolExecutor as _TPE
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
                with _TPE(max_workers=2) as _ex:
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

                top_bun = system_prompt + shiro_context

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

                v4_recall = self.v4_memory.recall(user_name)
                if v4_recall:
                    long_term_memory = f"{v4_recall}\n\n{long_term_memory}"

                meat = self._add_temporal_context(long_term_memory, user_name)

                user_prof_data = self.user_profiles.get(user_name, {})
                # FIX: Removed [USER PROFILE: name] bracket token.
                # Added provenance note so LLM knows these are from conversation,
                # not verified external facts — prevents treating guesses as truth.
                garnish = f"### What {user_name} has mentioned or revealed in conversation:\n"
                if user_prof_data:
                    for k, v in user_prof_data.items():
                        garnish += f"- {k}: {v}\n"
                else:
                    garnish += "- Nothing specific remembered yet. Don't invent any.\n"

                short_term_buffer = history[-10:]  # N5: reuse already-fetched history

                # ── FIX: Scrub inner_context before injecting it into context sandwich ──
                full_context = f"{meat}\n\n{garnish}\n\n{inner_context}"

                # ── Tool definitions ──────────────────────────────────────────
                tools = None
                if self.session_tool_count < 2:
                    tools = [{
                        "type": "function",
                        "function": {
                            "name": "search_fox_lore",
                            "description": "Find ideas online for kitsune tricks or fox folklore",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "query": {"type": "string", "description": "Search query"}
                                },
                                "required": ["query"]
                            }
                        }
                    }]

                # Fix 4: Scale max_tokens by message complexity.
                # Default 512 cuts off moral dilemmas, explanations, story responses.
                # ~4 words per token on average. 512 ≈ 2000 chars ≈ ~350 words.
                # For deep/complex messages, allow up to 800 tokens.
                _tokens_override = None
                if _user_words > 40:
                    _tokens_override = 800
                elif _user_words > 15:
                    _tokens_override = 600

                raw_stream = self.llm.stream_response(
                    top_bun, processed_text, short_term_buffer, full_context,
                    tools=tools, max_tokens_override=_tokens_override
                )

                response_stream = self._extract_thought_from_stream(raw_stream)
                response_fragments = []
                _was_truncated = False  # set True when Ollama hits token limit

                for fragment in split_into_sentences(response_stream):
                    if interrupt_event and interrupt_event.is_set():
                        logger.info("Response halted by interrupt.")
                        yield "... [Interrupted]"
                        break

                    # Detect token-limit sentinel from client.py
                    if "__TRUNCATED__" in fragment:
                        _was_truncated = True
                        fragment = fragment.replace("__TRUNCATED__", "").strip()
                        if not fragment:
                            continue

                    if "TOOL_CALLS:" in fragment and self.session_tool_count < 2:
                        fragment = self._safe_async_run(
                            self.handle_tool_calls_async(fragment, processed_text)
                        )
                        self.session_tool_count += 1

                    # Fix A: Drop CoT step-header lines at the fragment level.
                    # These come from shiro:latest baked-in chain-of-thought.
                    # _STREAM_START_RE cannot handle them (no end marker → blackout).
                    _frag_stripped = fragment.strip()
                    if re.match(r'(?i)^(?:##\s*)?step\s*\d+\s*[:.-]', _frag_stripped):
                        continue  # discard entire CoT fragment
                    if _frag_stripped.startswith('## ') and not any(
                        c.isalpha() and c.islower() for c in _frag_stripped[3:20]):
                        continue  # discard ## Section headers

                    clean_fragment = self._clean_response(fragment, processed_text)
                    if clean_fragment:
                        response_fragments.append(clean_fragment)

                full_response = "".join(response_fragments)
                full_response = self._throttle_hmph(full_response)
                full_response = self.cadence.adapt_text(full_response, user_name)

                # Final safety pass — strip any leaked meta that survived everything else
                full_response = self._final_sanitize(full_response)

                # If truncated at token limit, trim to last complete sentence
                if _was_truncated and full_response:
                    full_response = self._trim_to_sentence(full_response)

                # Fix 2: Multi-turn split — divide long responses into
                # 2-3 sentence bubbles. First bubble yields immediately.
                # Subsequent bubbles arrive via on_autonomous_speak after
                # a short delay, like typing separate messages.
                if full_response:
                    bubbles = self._split_into_bubbles(full_response)
                    yield bubbles[0]  # first bubble — immediate
                    if len(bubbles) > 1:
                        _remaining_bubbles = bubbles[1:]
                        _resp_for_bg = bubbles[0]  # used later for memory storage
                    else:
                        _remaining_bubbles = []
                        _resp_for_bg = full_response
                    # Track for autonomous dedup
                    self._last_response_text = full_response.strip()
                    self._last_response_ts = time.time()
                else:
                    _remaining_bubbles = []
                    _resp_for_bg = ""

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
                    _resp_clean = full_response.strip()  # full text for memory

                    # Short-term buffer: in-memory only — safe to do immediately
                    self.memory.short_term_buffer.append({"role": "user", "content": text})
                    self.memory.short_term_buffer.append({"role": "assistant", "content": _resp_clean})
                    if len(self.memory.short_term_buffer) > self.memory.max_short_term * 2:
                        self.memory.short_term_buffer = \
                            self.memory.short_term_buffer[-(self.memory.max_short_term * 2):]

                    def _background_writes():
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
                            self.v4_memory.add_user_message(user_name, text, emotions=user_emotions)
                            self.v4_memory.add_assistant_message(_resp_clean, user_id=user_name)
                            self.legacy_mind.reflect_on_response(_resp_clean, text)
                            if _ic % 5 == 0:
                                state_path = Path(__file__).parent.resolve() / "shiro_state.json"
                                self.legacy_mind.save_state(str(state_path))
                            self.shiro_learn_and_stay_shiro(processed_text, full_response)
                            if _ic % 10 == 0:
                                self.reflect(user_name)
                            self._safe_async_run(
                                self.check_and_think_async(user_name, previous_interaction)
                            )
                        except Exception as _e:
                            logger.warning(f"Background write error: {_e}")

                    threading.Thread(target=_background_writes, daemon=True).start()

                    # Deliver remaining bubbles after typing-paced delays.
                    # speech_type="bubble_continuation" tags these so main.py and
                    # poll_results can handle them as chat-room follow-up messages,
                    # not autonomous thoughts. They are NOT re-added to memory here —
                    # the full response was already stored above.
                    if _remaining_bubbles and self.on_autonomous_speak:
                        _is_async_speak = asyncio.iscoroutinefunction(self.on_autonomous_speak)
                        def _send_bubbles(bubbles, uname, is_async=_is_async_speak):
                            import time as _t
                            for i, bubble in enumerate(bubbles):
                                # Typing delay: base 0.6s + ~0.04s per word (feels like typing)
                                word_count = len(bubble.split())
                                delay = 0.6 + (word_count * 0.04)
                                _t.sleep(min(delay, 2.5))  # cap at 2.5s so it never feels laggy
                                if not self.on_autonomous_speak:
                                    break
                                if is_async:
                                    self._safe_async_run(
                                        self.on_autonomous_speak(bubble, "bubble_continuation")
                                    )
                                else:
                                    self.on_autonomous_speak(bubble, "bubble_continuation")
                                logger.info(f"[BUBBLE {i+2}]: {bubble[:60]}")
                        threading.Thread(
                            target=_send_bubbles,
                            args=(_remaining_bubbles, user_name),
                            daemon=True
                        ).start()

                    # Token-limit continuation (existing):
                    # if the response was cut by token limit,
                    # schedule a follow-up message from Shiro after a short pause.
                    # This lets her finish her thought without requiring user input.
                    if _was_truncated and self.on_autonomous_speak:
                        def _continue_thought():
                            import time as _t
                            _t.sleep(0.6)  # brief pause so UI renders first message
                            self._safe_async_run(
                                self._continue_truncated_response(
                                    user_name, processed_text, _resp_clean
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
                        continue
                    else:
                        # ── HARDENING: Check for leaked response inside thought block ──
                        # If the block is getting very long and contains real-looking prose,
                        # the model might have forgotten to close the block or put the reply inside.
                        if len(buffer) > 150:
                            # Does it look like real dialogue? (Starts with upper case, no box characters)
                            lines = buffer.splitlines()
                            for i, line in enumerate(lines):
                                stripped = line.strip()
                                if len(stripped) > 20 and stripped[0].isupper() and not any(c in stripped for c in "|+="):
                                    # This looks like a leaked response.
                                    # Yield everything from this point forward.
                                    leaked = "\n".join(lines[i:])
                                    # Only yield if it doesn't contain major thought keywords
                                    if not any(kw in leaked.upper() for kw in self.THOUGHT_KEYWORDS.split('|')):
                                        # Capture everything BEFORE this line as thought
                                        self.last_thought += "\n".join(lines[:i])
                                        buffer = leaked
                                        in_thought = False
                                        break
                            if not in_thought: continue

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
        # Strip ALL [...] tag pairs first — catches [THOUGHT], [tsun], [mischievous]
        # and any other persona/meta tags the model outputs.
        # Pattern: [TAG_NAME] ... [/TAG_NAME] (matched pairs, greedy within reason)
        # Updated to allow spaces and varied characters inside brackets.
        text = re.sub(r'\[[^\]\n]{1,50}\].*?\[/[^\]\n]{1,50}\]',
                      '', text, flags=re.DOTALL | re.IGNORECASE)
        # Strip any remaining unpaired opening tags like [tsun], [THOUGHT], [Mask check]
        text = re.sub(r'\[[^\]\n]{1,50}\]', '', text)

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
            # Drop lines starting with known meta keywords
            if re.match(r'^\s*(?:' + self.THOUGHT_KEYWORDS.replace('|', r'|^\s*') + r')\s*:', line, re.IGNORECASE):
                continue
            clean_lines.append(line)

        text = '\n'.join(clean_lines).strip()

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

        return text

    # ── Temporal context ──────────────────────────────────────────────────────

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
        temporal_note = (
            f"The current time is {current_time_str} on {current_date_str}.\n"
            f"- Shiro was inactive for {downtime_str} before this session started.\n"
            f"- It has been {duration_str} since the last interaction with {user_name}.\n"
            f"- Session uptime: {uptime_str}.\n"
            f"- Session messages: {session_msg_count} exchanged so far. "
            f"This is {'an ongoing' if session_msg_count > 4 else 'a new'} conversation.\n"
            "Mention downtime or duration ONLY if it serves your teasing or if you want to complain about being lonely."
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
        try:
            tool_calls_json = fragment.split("TOOL_CALLS:")[1].strip()
            tool_calls = json.loads(tool_calls_json)
            if tool_calls and tool_calls[0]['function']['name'] == 'search_fox_lore':
                query = tool_calls[0]['function']['arguments'].get('query', user_msg)
                from googlesearch import search
                logger.info(f"Executing tool search_fox_lore for: {query}")
                results = list(search(query, num_results=2))
                re_prompt = f"Using fox folklore: {results}. Finish your teasing reply for: {user_msg}"
                return await self.llm.generate_response_async(
                    self.persona.get_system_prompt() + f"\n{self.outfit_block()}",
                    re_prompt,
                    self.memory.get_history()[-10:]
                )
        except Exception as e:
            logger.error(f"Tool execution failed: {e}")
            return "[Tool failed — acting on instinct!]"
        return fragment

    async def check_and_think_async(self, user_id: str, last_interaction: datetime):
        if datetime.now(timezone.utc) - last_interaction > timedelta(minutes=10):
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

        Short responses (1-2 sentences) stay as one bubble.
        Longer responses split at sentence boundaries into 1-2 sentence groups,
        mimicking how a real person types in a chat room: quick bursts, not walls.

        Bubbles 2+ are delivered via on_autonomous_speak("bubble_continuation")
        with inter-bubble delays that scale with length — mimicking typing time.
        They are NOT re-stored in short_term_buffer (full response already there).

        ARTIFACT GUARD: Filters out empty bubbles and `. .` / `…` orphans that
        arise from Shiro's ellipsis speech patterns being split mid-ellipsis.
        """
        import re as _re
        text = text.strip()
        if not text:
            return [text]

        # Split on sentence-ending punctuation followed by whitespace
        # But NOT on `. .` or `. …` patterns (those are speech ellipses, not sentence ends)
        # First normalize `. .` and `. …` to actual ellipsis so they survive splitting
        text = _re.sub(r'\.\s+\.\s*\.?', '…', text)   # ". ." → "…"
        text = _re.sub(r'\.\s+…', '…', text)            # ". …" → "…"

        sentence_endings = _re.compile(r'(?<=[.!?])\s+(?=[A-Z])')
        sentences = [s.strip() for s in sentence_endings.split(text) if s.strip()]

        # Filter out anything that's just punctuation/whitespace (artifact bubbles)
        sentences = [s for s in sentences if len(_re.sub(r'[^\w]', '', s)) >= 2]

        if len(sentences) <= 2:
            return [text] if text else []

        # Group into 1-2 sentence bubbles.
        # Very short sentences (< 8 words) get grouped with the next one.
        bubbles = []
        group = []
        for i, sent in enumerate(sentences):
            group.append(sent)
            word_count = sum(len(s.split()) for s in group)
            last_sent = (i == len(sentences) - 1)
            if len(group) >= 2 or (word_count >= 12 and not last_sent) or last_sent:
                combined = ' '.join(group)
                if len(_re.sub(r'[^\w]', '', combined)) >= 2:  # not just punctuation
                    bubbles.append(combined)
                group = []

        return bubbles if bubbles else [text]


    def _trim_to_sentence(self, text: str) -> str:
        """
        Trim text to end at the last complete sentence (. ! ?).
        Called when Ollama hit the token limit so the UI sees a clean stop,
        not a dangling half-word.
        """
        text = text.strip()
        # Find the last sentence-ending punctuation
        last_end = -1
        for i in range(len(text) - 1, -1, -1):
            if text[i] in ('.', '!', '?') and (i + 1 >= len(text) or text[i+1] in (' ', '"')):
                last_end = i
                break
        if last_end > len(text) * 0.4:  # only trim if we keep at least 40%
            return text[:last_end + 1].strip()
        return text  # not enough content to trim — keep as-is

    async def _continue_truncated_response(
        self, user_name: str, original_query: str, already_said: str
    ):
        """
        Generate a seamless continuation when Shiro was cut off by the token limit.
        The continuation picks up exactly where she left off — no repeated context,
        no "as I was saying", just the next part of her thought.
        Delivered via on_autonomous_speak so it appears as a new bubble unprompted.
        """
        try:
            history = self.memory.get_history()
            system_prompt = self.persona.get_system_prompt(
                now=__import__('datetime').datetime.now(),
                relationship_tier=self.legacy_mind.relationship.level.name.lower(),
            ) + f"\n{self.outfit_block()}"

            continuation_instruction = (
                f"You were replying to: \"{original_query}\"\n"
                f"You already said: \"{already_said}\"\n"
                "Continue your reply naturally from where you left off. "
                "Do NOT repeat what you already said. Do NOT add 'as I was saying' or similar. "
                "Just continue the thought directly, as if typing the next part of the same message. "
                "Keep it concise — finish the thought cleanly."
            )

            context = await self.memory.get_full_context_async(
                original_query, user_id=user_name
            )
            continuation = await self.llm.generate_response_async(
                system_prompt,
                continuation_instruction,
                history[-6:],
                context=context
            )
            continuation = self._final_sanitize(continuation.strip())

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
        except Exception as e:
            logger.warning(f"Continuation failed: {e}")




    async def _generate_live_autonomous_message(self, user_name):
        """
        LLM-driven autonomous message grounded in real conversation context.
        Replaces canned strings. Result is stored in memory so Shiro recalls it.

        DEDUP GUARD: Will return "" if:
          - Shiro responded within the last 60 seconds (too soon to follow up)
          - Generated message overlaps too heavily with the last response
        """
        try:
            import datetime as _dt

            # Guard 1: Recency — don't fire within 30s of the last response
            elapsed = time.time() - self._last_response_ts
            if elapsed < 30.0:
                logger.debug(f"Autonomous suppressed: only {elapsed:.0f}s since last response")
                return ""

            history = self.memory.get_history()
            if not history:
                return ""
            recent_ctx = "\n".join(
                f"{m['role'].title()}: {m['content']}" for m in history[-6:]
            )
            context = await self.memory.get_full_context_async(
                "recent thoughts", user_id=user_name
            )
            last_thought = self.last_thought or ""
            system_prompt = self.persona.get_system_prompt(
                now=_dt.datetime.now(),
                relationship_tier=self.legacy_mind.relationship.level.name.lower(),
            ) + "\n" + self.outfit_block()
            # Move last_thought into system prompt context, not user prompt.
            # Quoting it in the user prompt caused the LLM to echo it verbatim.
            if last_thought:
                system_prompt += f"\n[Inner state: {last_thought}]"
            last_said = self._last_response_text[:120] if self._last_response_text else ""
            prompt = (
                f"Recent conversation:\n{recent_ctx}\n\n"
                + (f"You already said this recently: \"{last_said}\" — do NOT repeat or rephrase it.\n\n" if last_said else "")
                + "The user hasn't replied yet. You have something on your mind. "
                "Say it — a new thought, a follow-up question, a reaction to something they said earlier, "
                "or something you just thought of. Be proactive. Be Shiro. "
                "One or two sentences max. No greetings. No meta-commentary. Just say it naturally."
            )
            msg = await self.llm.generate_response_async(
                system_prompt, prompt, history[-4:], context=context
            )
            msg = self._final_sanitize(msg.strip())

            # Guard 2: Similarity — block if message echoes the last response
            if msg and self._last_response_text:
                from utils.text_utils import calculate_text_similarity
                sim = calculate_text_similarity(msg, self._last_response_text)
                if sim >= 0.45:
                    logger.info(f"Autonomous suppressed (similarity={sim:.2f} to last response)")
                    return ""

            return msg
        except Exception as e:
            logger.warning(f"Live autonomous message failed: {e}")
            return ""


    def reflect(self, user_id: str):
        try:
            with self.processing_lock:
                history = self.memory.get_history()
            if not history:
                return
            logger.info(f"Shiro is reflecting on {user_id}...")
            reflection_prompt = (
                "### INSTRUCTION\n"
                "Analyze the recent conversation history below. Extract critical information.\n"
                "RETURN ONLY VALID YAML. No markdown, no ``` fences, no * bullets — use '- item' syntax.\n"
                "Required keys:\n"
                "  user_facts: { fact_name: fact_value }\n"
                "  events: [- list of notable events]\n"
                "  insights: [- lessons about this user]\n"
                "  relations: []\n"
                "  summary: \"Single paragraph summary.\"\n\n"
                "STRICT YAML RULES:\n"
                "- Use '- item' for lists, NOT '* item'\n"
                "- String values with colons must be quoted\n"
                "- Do not use square brackets for sentences\n"
                "- Be concise and accurate. ONLY extract what was explicitly stated.\n"
                "- Do NOT infer, guess, or invent facts not present in the history.\n"
                "- If uncertain, leave the field empty rather than guessing.\n"
            )
            analysis_raw = self.llm.generate_response(
                "You are Shiro's Memory Processor. You are precise and observant.",
                f"HISTORY TO ANALYZE:\n{history}",
                [],
                context=reflection_prompt
            )
            cleaned_raw = _robust_clean_yaml(analysis_raw)
            cleaned_raw = clean_yaml_block(cleaned_raw)

            with self.processing_lock:
                data = None
                try:
                    data = yaml.safe_load(cleaned_raw)
                except Exception as e:
                    logger.warning(f"YAML Parse failed in reflection: {e}")
                    summary_match = re.search(
                        r'summary[:\s]+["\']?(.+?)["\']?\s*$',
                        analysis_raw, re.IGNORECASE | re.MULTILINE
                    )
                    if summary_match:
                        data = {
                            "summary": summary_match.group(1).strip(),
                            "events": [], "insights": [], "user_facts": {}, "relations": []
                        }
                if data and isinstance(data, dict):
                    facts = data.get('user_facts', {})
                    if facts and isinstance(facts, dict):
                        if user_id not in self.user_profiles:
                            self.user_profiles[user_id] = {}
                        self.user_profiles[user_id].update(facts)
                        self._save_profiles()
                        for k, v in facts.items():
                            self.memory.update_user_profile(user_id, f"{k}: {v}")
                    for event in data.get('events', []):
                        self.memory.store_episodic_memory(event, user_id=user_id, importance=6)
                    for insight in data.get('insights', []):
                        self.memory.store_insight(insight, user_id=user_id, source="reflection")
                    for rel in data.get('relations', []):
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
                    if len(local_summaries) >= 6:
                        logger.info(f"Condensing {len(local_summaries)} summaries into Global Summary for {user_id}...")
                        global_prompt = (
                            "Combine these individual conversation segment summaries into one single "
                            "'Global Narrative Summary'. The result must be a comprehensive but concise "
                            "paragraph covering the entire relationship/session history so far."
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

    def shutdown(self):
        logger.info("Engine initiating Reflective Shutdown...")
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
                open_loops_raw = self.llm.generate_response(
                    "You are Shiro's internal memory keeper.",
                    f"RECENT HISTORY:\n{history}",
                    [],
                    context=loop_prompt
                )
                if open_loops_raw and len(open_loops_raw) > 10:
                    self.memory.store_episodic_memory(
                        f"OPEN LOOPS at session end: {open_loops_raw}",
                        user_id=self.current_user_name, importance=7
                    )
            self._save_profiles()
            self._save_brain()
            self.memory.prune_old_memories()
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
                temp_file.write_text(json.dumps(self.brain, indent=2))
                temp_file.replace(self.brain_file)
            except Exception as e:
                logger.error(f"Failed to save brain: {e}")

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
            if any(w in shiro_reply.lower() for w in ["dummy", "silly", "stranger"]):
                if len(brain["favors"]) < 50:
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

    def _detect_context_drift(self, query: str) -> float:
        history = self.memory.get_history()
        if not history or len(history) < 2:
            return 0.0
        try:
            import math
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

    # ── Greeting helpers ──────────────────────────────────────────────────────

    def get_greeting_prompt(self, user_name: str, mode: str = "new") -> str:
        return _pick_greeting(user_name, mode)

    def get_fallback_greeting(self, user_name: str, mode: str = "new") -> str:
        return _pick_fallback(user_name, mode)
