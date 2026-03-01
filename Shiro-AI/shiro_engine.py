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
import numpy as np
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from typing import Any, List, Dict, Optional, Generator, Callable

# Add the current directory to sys.path to ensure local modules are found
sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm.client import LlamaClient
from memory.store import MemoryStore
from persona.manager import PersonaManager
from persona.inner_mind import ShiroInnerMind
from utils.text_utils import split_into_sentences, clean_yaml_block

# v4 Consciousness Upgrades
from consciousness.events import EventBus
from consciousness.intent import IntentPlanner, SentimentTrajectory, RuleEngine, TimePattern
from consciousness.speech_cadence import SpeechCadence
from consciousness.self_awareness import SelfAwareness
from consciousness.thought_loop import InnerMind, Mood
from consciousness.autonomous_voice import AutonomousVoice
from consciousness.memory import ConversationMemory

logger = logging.getLogger(__name__)

# ── Dynamic greeting pools ─────────────────────────────────────────────────
# Three pools for three situations: new user, returning soon, returning after a while.
# Each pool has 6+ variants so the same prompt never repeats back-to-back.
# Tone key: curious/guarded for new, warmer-but-still-tsun for returning.
# NEVER hostile, never accusatory, never mentions ears unprompted.

_GREET_NEW = [
    "(LOG: {name} just arrived for the first time. Shiro, give them one short, coy opener — curious, slightly guarded. No hostility.)",
    "(LOG: New arrival: {name}. Shiro, notice them. One line — somewhere between 'who are you' and 'I might be interested'. Brief.)",
    "(LOG: {name} appeared. Shiro, tilt your head. One guarded, dry greeting. Keep it under 2 sentences.)",
    "(LOG: {name} walked in. Shiro, give them a look. Something between 'finally' and 'who are you'. One line.)",
    "(LOG: {name} is here for the first time. Shiro, you noticed. Say something short and witty — no lectures, no complaints.)",
    "(LOG: {name} has arrived. Shiro — one coy opener. Curious but not fawning. Short and sharp.)",
    "(LOG: {name} showed up. Shiro, acknowledge them. Not enthusiastically, not rudely. Curious. One sentence.)",
    "(LOG: First meeting with {name}. Shiro, introduce the vibe — playful, a little aloof. Keep it under 2 lines.)",
]

_GREET_RETURNING_SOON = [
    "(LOG: {name} is back after a short break. Shiro, acknowledge their return — teasing is fine, but warm underneath. 1-2 sentences.)",
    "(LOG: {name} returned. Short absence. Shiro, pretend you didn't miss them. One dry, affectionate line.)",
    "(LOG: {name} is here again. Shiro — you noticed they were gone. Don't say it directly. Just tease, briefly.)",
    "(LOG: {name} came back. Shiro, give them a smug look. One line that says 'oh, you again' but actually means 'good'.)",
    "(LOG: {name} returned after a bit. Shiro, keep it short — one teasing line, warm underneath.)",
    "(LOG: {name} is back. Shiro — acknowledge it. Coy, dry, affectionate. Don't lecture. One hook.)",
    "(LOG: Short break, and {name} is back. Shiro, make a small comment — amused, not hostile. Brief.)",
]

_GREET_RETURNING_LONG = [
    "(LOG: {name} is back after a long absence. Shiro, be a little suspicious — but curious too. 1-2 sentences, no lectures.)",
    "(LOG: {name} returned after a long time away. Shiro, tilt your head. Something like 'I thought you forgot about me'. Short.)",
    "(LOG: Long time no see — {name} is here. Shiro, guarded but secretly glad. One dry, probing line.)",
    "(LOG: {name} came back after a while. Shiro, raise an eyebrow. Ask something brief and suspicious — in character.)",
    "(LOG: {name} has reappeared after a long gap. Shiro, be wary but not hostile. One short line that hints you noticed.)",
    "(LOG: {name} is here again after a long absence. Shiro — show mild surprise, keep it to 1 sentence.)",
    "(LOG: Long absence, {name} returned. Shiro, be cautious and a little sarcastic. Brief, not mean.)",
]

_GREET_SIMPLE = [
    "(LOG: {name} is here. Shiro, give a simple, warm greeting. One sentence max. No fluff. Example: 'Shiro here! Hello {name}, what are we doing today?')",
    "(LOG: {name} appeared. Shiro, just say hi. Short and sweet. No performative sass.)",
    "(LOG: A quick greeting for {name}. Shiro, keep it to one direct line.)",
    "(LOG: Shiro, just acknowledge {name}'s arrival with a short, friendly greeting. 1 sentence.)",
]

_GREET_MORNING = [
    "(LOG: It's morning and {name} is here. Shiro, say a simple good morning. 1 sentence. No fluff.)",
    "(LOG: {name} arrived early. Shiro, acknowledge the time with a short, sleepy or fresh greeting. Example: 'Just a simple good morning, {name}.')",
    "(LOG: Shiro, wish {name} a good morning. Keep it very short.)",
]

_GREET_EVENING = [
    "(LOG: It's late and {name} is here. Shiro, say a simple good evening. 1 sentence. No fluff.)",
    "(LOG: {name} is here late. Shiro, acknowledge the late hour with a short line.)",
    "(LOG: Shiro, it's evening. Greet {name} simply and acknowledge the time.)",
]

# Soft fallbacks used when the LLM call itself fails entirely
_FALLBACK_NEW = [
    "*tail swishes* A new face. I'm Shiro. What do you want?",
    "Hmm. You're new. I'm Shiro. Try not to bore me.",
    "*glances sideways* You must be {name}. I'm Shiro. Don't just stand there.",
    "So you finally found me. I'm Shiro. Now what?",
]

_FALLBACK_RETURNING_SOON = [
    "Oh. You're back. ...I didn't notice you were gone.",
    "Back already? *flicks tail* I wasn't waiting.",
    "You returned. I suppose that's fine.",
    "Hmm. You came back. I'll allow it.",
]

_FALLBACK_RETURNING_LONG = [
    "...{name}? You actually came back. Took long enough.",
    "Long time. I'm watching you. Don't think I forgot anything.",
    "*narrows eyes* You again. It's been a while. Explain yourself.",
    "So you finally returned. I had almost stopped keeping track.",
]


def _pick_greeting(user_name: str, mode: str = "new") -> str:
    """
    Returns a randomized greeting LOG prompt.
    mode: 'new' | 'returning_soon' | 'returning_long'
    """
    # 30% chance of a "Simple" greeting to reduce fluff, regardless of mode
    if random.random() < 0.3:
        template = random.choice(_GREET_SIMPLE)
        return template.format(name=user_name)

    # Time-of-day awareness (Simple variants)
    now_local = datetime.now()
    if 5 <= now_local.hour < 11 and random.random() < 0.5:
        template = random.choice(_GREET_MORNING)
        return template.format(name=user_name)
    elif 18 <= now_local.hour <= 23 and random.random() < 0.5:
        template = random.choice(_GREET_EVENING)
        return template.format(name=user_name)

    pool = {
        "new":            _GREET_NEW,
        "returning_soon": _GREET_RETURNING_SOON,
        "returning_long": _GREET_RETURNING_LONG,
    }.get(mode, _GREET_NEW)
    template = random.choice(pool)
    return template.format(name=user_name)


def _pick_fallback(user_name: str, mode: str = "new") -> str:
    """Returns a soft in-character fallback string when LLM call fails."""
    pool = {
        "new":            _FALLBACK_NEW,
        "returning_soon": _FALLBACK_RETURNING_SOON,
        "returning_long": _FALLBACK_RETURNING_LONG,
    }.get(mode, _FALLBACK_NEW)
    template = random.choice(pool)
    return template.format(name=user_name)


# ── Robust YAML cleaner (handles LLM formatting quirks) ───────────────────
def _robust_clean_yaml(raw: str) -> str:
    """
    Cleans LLM-generated YAML that may contain:
    - Markdown code fences (```yaml ... ```)
    - Markdown bullet points (* item) instead of YAML list items (- item)
    - Inline bracketed sentences as list values: - [sentence here]
    """
    # Strip markdown code fences
    raw = re.sub(r'^\s*```ya?ml\s*\n?', '', raw, flags=re.IGNORECASE)
    raw = re.sub(r'\s*```\s*$', '', raw, flags=re.IGNORECASE)
    raw = raw.strip()

    lines = raw.splitlines()
    cleaned = []
    for line in lines:
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        # Convert markdown bullet '* text' -> '- text'
        if re.match(r'^\*\s+\S', stripped):
            cleaned.append(' ' * indent + '- ' + stripped[2:])
            continue

        # Convert inline bracketed sentence list items: '- [some long text]' -> '- "some long text"'
        # Only when the bracket content looks like a sentence (has spaces), not a YAML ref
        inline_bracket = re.match(r'^(-\s+)\[(.+)\]\s*$', stripped)
        if inline_bracket and ' ' in inline_bracket.group(2):
            inner = inline_bracket.group(2).replace('"', "'")
            cleaned.append(' ' * indent + '- "' + inner + '"')
            continue

        # Convert bare key-level bracketed sentence: 'key: [sentence]' -> 'key: "sentence"'
        bare_key_bracket = re.match(r'^(\w[\w\s]*?:\s*)(\[.+\])\s*$', stripped)
        if bare_key_bracket and ' ' in bare_key_bracket.group(2)[1:-1]:
            key_part = bare_key_bracket.group(1)
            val_inner = bare_key_bracket.group(2)[1:-1].replace('"', "'")
            cleaned.append(' ' * indent + key_part + '"' + val_inner + '"')
            continue

        cleaned.append(line)

    return '\n'.join(cleaned)


# Hmph variants to throttle (case-insensitive)
# Hmph pattern — matches the word plus any trailing punctuation and whitespace.
# Consuming the trailing punct+space prevents "Whatever., text" artifacts when
# replacing "Hmph, text" → "Whatever. text" instead of "Whatever., text".
_HMPH_PATTERN = re.compile(r'\bhmph[.,!]?\s*', re.IGNORECASE)
# Replacements to rotate through when we suppress hmph
_HMPH_ALTERNATIVES = [
    "...",
    "Tch.",
    "Whatever.",
    "Fine.",
    "*flicks tail*",
    "Hmm.",
]

class ShiroEngine:
    """Core logic engine for Shiro AI, shared between UI and Server."""
    # Unified keywords for various thought/meta tags to ensure consistency across filtering methods
    THOUGHT_KEYWORDS = "THOUGHTS?|INNER MONOLOGUE|INNER MIND|SHIRO|THINKING|PLOT|SCHEME|SCHEEM|META|SYSTEM|ACTION|SCENE|LOG|MOMENTUM|REL:|VALENCE"

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

        # --- SHIRO SPECIFIC ---
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
        self._hmph_counter = 0          # tracks recent hmph usage
        self._hmph_session_count = 0    # total this session

        # v4 Consciousness Core
        self.bus = EventBus()
        self.intent_planner = IntentPlanner()
        self.sentiment_trajectory = SentimentTrajectory()
        self.time_pattern = TimePattern()
        self.cadence = SpeechCadence()
        self.rules = RuleEngine()
        self.awareness = SelfAwareness(name="Shiro", event_bus=self.bus)

        # [V4 UPGRADE] Inner Mind v3
        self.mind = InnerMind(
            thought_tick_seconds=4.0,
            thought_callback=self._on_v4_thought,
            enable_reactions=True
        )

        # Callback for autonomous voice
        async def _speak_callback(event):
            # Bridging autonomous voice to the console/logger and provided callback
            logger.info(f"[AUTONOMOUS VOICE]: {event.text}")
            if self.on_autonomous_speak:
                if asyncio.iscoroutinefunction(self.on_autonomous_speak):
                    await self.on_autonomous_speak(event.text, event.speech_type)
                else:
                    self.on_autonomous_speak(event.text, event.speech_type)

        self.voice = AutonomousVoice(speak_callback=_speak_callback)
        self.v4_memory = ConversationMemory()
        self._idle_task: Optional[asyncio.Task] = None

        # ---------------------
        # Robust path resolution
        base_path = Path(__file__).parent.resolve()
        # Initialize components with config
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
        self._load_session_objectives()
        # Eternal Learning Brain
        self.brain_file = base_path / "shiro_brain.json"
        self.core_anchors = {
            "slyness": (0.60, 0.95),
            "sass": (0.70, 0.90),
            "greed": (0.40, 0.80),
            "kindness": (0.50, 1.00)
        }
        self.brain = self._load_brain()
        self.profile_file = base_path / "profile.json"
        self.user_profiles = self._load_profiles()
        # Restore recent history for the default/last active user if possible
        self.memory.load_recent_history(self.current_user_name)

    def _load_profiles(self):
        """Loads persistent user profiles (Garnish layer)."""
        if self.profile_file.exists():
            try:
                return json.loads(self.profile_file.read_text())
            except Exception as e:
                logger.warning(f"Failed to load profiles: {e}")
        return {}

    def _save_profiles(self):
        """Saves persistent user profiles."""
        with self.profile_lock:
            try:
                # Handle non-serializable objects like datetime.date
                def json_serial(obj):
                    if isinstance(obj, (datetime, date)):
                        return obj.isoformat()
                    raise TypeError(f"Type {type(obj)} not serializable")

                # Atomic write to avoid corruption
                temp_file = self.profile_file.with_suffix(".tmp")
                temp_file.write_text(json.dumps(self.user_profiles, indent=2, default=json_serial))
                temp_file.replace(self.profile_file)
            except Exception as e:
                logger.error(f"Failed to save profiles: {e}")

    def _load_session_objectives(self):
        """Loads session objectives from a local JSON file."""
        obj_path = Path(__file__).resolve().parent / "objectives.json"
        if obj_path.exists():
            try:
                with open(obj_path, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f)
                    self.memory.session_objectives = data.get('objectives', [])
            except Exception as e:
                logger.warning(f"Failed to load objectives: {e}")

    def initialize(self):
        """Initializes components."""
        self.persona.load_persona()
        # Load Legacy Inner Mind state
        state_path = Path(__file__).parent.resolve() / "shiro_state.json"
        if state_path.exists():
            self.legacy_mind.load_state(str(state_path))

        # [V4 UPGRADE] Start background event loop for thought loop and idle loop
        self._background_loop = asyncio.new_event_loop()
        def _run_loop():
            asyncio.set_event_loop(self._background_loop)
            self._background_loop.run_forever()

        threading.Thread(target=_run_loop, daemon=True).start()

        # Schedule tasks in the background loop
        asyncio.run_coroutine_threadsafe(self.mind.start(), self._background_loop)

        # Store the task so it can be cancelled
        def _start_idle():
            self._idle_task = self._background_loop.create_task(self._v4_idle_loop())
        self._background_loop.call_soon_threadsafe(_start_idle)

        # LLM Diagnostics (Async background execution)
        def _run_diagnostics():
            diag = self.llm.perform_diagnostics()
            logger.info(f"LLM Diagnostics:\n{diag}")

        threading.Thread(target=_run_diagnostics, daemon=True).start()

    def _imagine_reply(self, query: str) -> str:
        """HyDE: Generates a hypothetical answer to improve RAG retrieval."""
        # Only run HyDE for longer queries where semantic enrichment is needed
        if len(query.split()) < 15:
            return query

        try:
            # Optimized for speed and semantic overlap
            hypothetical_prompt = "Provide a neutral, factual answer to this query as it might appear in a prior conversation log. Use specific nouns and keywords only. No personality."
            # We don't need history or full context for this
            hypothetical_answer = self.llm.generate_response(
                "You are Shiro's Memory Assistant.",
                f"USER QUERY: {query}",
                [],
                context=hypothetical_prompt
            )
            return hypothetical_answer
        except Exception as e:
            logger.warning(f"HyDE imagine_reply failed: {e}")
            return query # Fallback to original query

    def _safe_async_run(self, coro):
        """Safely runs an async coroutine from a synchronous context."""
        if self._background_loop and self._background_loop.is_running():
            return asyncio.run_coroutine_threadsafe(coro, self._background_loop).result()

        try:
            # Try to get the running loop (preferred in Python 3.7+)
            loop = asyncio.get_running_loop()
            return asyncio.run_coroutine_threadsafe(coro, loop).result()
        except RuntimeError:
            # No running event loop in this thread, use asyncio.run to create one
            return asyncio.run(coro)

    def _on_v4_thought(self, thought):
        """Callback for v4 InnerMind thoughts."""
        self.last_thought = thought.text
        # Optional: Emit to EventBus
        self.bus.emit("thought_fired", thought=thought.text)

    async def _v4_idle_loop(self):
        """Continuous idle loop for autonomous behavior."""
        while True:
            await asyncio.sleep(20.0 * random.uniform(0.8, 1.3))
            idle_s = (datetime.now(timezone.utc) - self.last_interaction_time).total_seconds()
            present = [p for p in self.awareness.users.values() if p.present]

            if not present and idle_s > 120.0:
                await self.voice.mutter_idle()
            elif present and idle_s > 90.0:
                target = random.choice(present)
                # Use v4 memory to find something to talk about
                recall = self.v4_memory.get_summary(target.user_id)
                recent_topics = self.v4_memory.get_recent_topics(target.user_id)

                if recall and recall.key_facts and random.random() < 0.3:
                    fact = random.choice(recall.key_facts)
                    await self.voice.speak_memory(target.user_id, fact)
                elif recent_topics and random.random() < 0.5:
                    top_topic = recent_topics[0]
                    await self.voice.initiate_conversation(target.user_id, topic=top_topic)
                else:
                    await self.voice.initiate_conversation(target.user_id)
                self.last_interaction_time = datetime.now(timezone.utc)

    def on_user_join(self, user_name: str):
        """V4 hook for user join."""
        profile = self.awareness.user_entered(user_name)
        self.v4_memory.new_session(user_name)
        # Reset idle timer on join
        self.last_interaction_time = datetime.now(timezone.utc)

        # Ensure engine is synced to this user immediately
        if self.current_user_name != user_name:
            self.current_user_name = user_name
            self.legacy_mind.switch_user(user_name)
            self.memory.load_recent_history(user_name)

    def on_user_leave(self, user_name: str):
        """V4 hook for user leave."""
        self.awareness.user_left(user_name)

    def process_text(self, text: str, user_name: str = None, interrupt_event: threading.Event = None) -> Generator[str, None, None]:
        """Core text processing logic."""
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
                # Re-load recent history for the new user if buffer is empty or user changed
                self.memory.load_recent_history(user_name)

        # [V4 UPGRADE] Observe user through SelfAwareness v4
        user_profile = self.awareness.user_spoke(user_name, processed_text)

        # Sync mirror vocab to SpeechCadence
        self.cadence.sync_mirror_vocab(user_name, user_profile.mirror_vocab)

        # [V4 UPGRADE] Observe user cadence
        self.cadence.observe(user_name, processed_text)

        # [V4 UPGRADE] Emit user_spoke event
        self.bus.emit("user_spoke", user_id=user_name, text=processed_text)

        # [V4 UPGRADE] Detect emotions and track trajectory
        user_emotions = user_profile.current_emotion_strength()
        self.sentiment_trajectory.record(user_name, user_emotions)
        trajectory = self.sentiment_trajectory.trend(user_name)
        self.time_pattern.record_visit(user_name)

        # Handle outfit changes
        if processed_text.lower().startswith("shiro change to"):
            yield self.change_outfit(processed_text[15:])
            return

        # Identity Verification Heuristic
        if processed_text and not processed_text.startswith("[") and user_name != "System":
            last_seen = self.memory.get_last_interaction_time(user_name)
            if last_seen and (datetime.now(timezone.utc) - last_seen).days > 7:
                processed_text = f"[IDENTITY CHECK REQUIRED] {processed_text}"

        logger.info(f"--- Engine Processing: '{processed_text}' ---")

        with self.processing_lock:
            try:
                # Check inactivity against PREVIOUS interaction time BEFORE updating it
                previous_interaction = self.last_interaction_time
                self.last_interaction_time = current_time

                # SHIRO SPECIFIC: Intensity and Temperature
                self.intensity = self.get_smart_intensity(processed_text)
                temp = 0.5 + 0.2 * self.intensity
                self.llm.temperature = temp

                # Context Drift Detection
                drift_score = self._detect_context_drift(processed_text)

                # Aggression Reduction: dynamic length hint (Enforced Brevity)
                length_hint = ""
                user_msg_len = len(processed_text)
                if user_msg_len < 100:
                    length_hint = "\n[SYSTEM: CASUAL EXCHANGE. Keep it short and to the point. 1-2 sentences max. No fluff.]"
                elif user_msg_len < 250:
                    length_hint = "\n[SYSTEM: Focused response required. 2-3 sentences max.]"

                # [INNER MIND] Process input to get thoughts and strategy
                inner_mind_data = self.legacy_mind.process_input(processed_text, user_id=user_name)
                inner_context = inner_mind_data.get("inner_context", "")

                # [V4 UPGRADE] Update v4 InnerMind context
                self.mind.update_context({
                    "focus_user": user_name,
                    "last_snippet": processed_text[:40],
                    "relationship_tier": inner_mind_data.get("relationship", "STRANGER").lower(),
                    "sentiment_trajectory": trajectory,
                    "idle_ms": (datetime.now(timezone.utc) - self.last_interaction_time).total_seconds() * 1000
                })

                # [V4 UPGRADE] Plan communicative intent
                rel_tier_raw = inner_mind_data.get("relationship", "STRANGER").lower()
                # Map InnerMind familiarity to a 0-1 depth scale
                conv_depth = min(1.0, inner_mind_data.get("familiarity", 0) / 60.0)

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
                self.bus.emit("intent_planned", user_id=user_name, intent=planned_intent.name, confidence=planned_intent.confidence)

                # --- THE CONTEXT SANDWICH ---

                # 1. Top Bun: System Instructions & Identity
                system_prompt = self.persona.get_system_prompt(
                    now=datetime.now(),
                    intent_hint=planned_intent.prompt_hint,
                    relationship_tier=rel_tier_raw,
                    timing_obs=self.time_pattern.visit_observation(user_name),
                    user_quirks=user_profile.quirks,
                    user_patterns=[self.awareness.get_user_pattern_description(user_name)]
                )
                drift_note = f"\n[SYSTEM: Topic Drift Detected ({drift_score:.2f}). Adjusting focus.]" if drift_score > 0.6 else ""
                shiro_context = f"\n[SYSTEM: Current Intensity: {self.intensity:.2f}]{drift_note}{length_hint}\n{self.outfit_block()}"

                # [AUTONOMY] Proactive memory injection
                autonomous_mem = self._safe_async_run(self.fetch_relevant_memory_async(f"shiro tricks for {user_name}", n_results=2))
                if autonomous_mem:
                    shiro_context += f"\n[SCHEMING MEMORY: {autonomous_mem}]"

                # [AUTONOMY] Feedback injection
                feedback_mems = self._safe_async_run(self.memory.search_relevant_memories_async("User Favor Feedback", n_results=2, user_id=user_name))
                if feedback_mems and isinstance(feedback_mems, list):
                    # Ensure we handle list of dicts or list of strings
                    feedback_contents = []
                    for m in feedback_mems:
                        if isinstance(m, dict) and 'content' in m:
                            feedback_contents.append(m['content'])
                        else:
                            feedback_contents.append(str(m))
                    shiro_context += f"\n[USER FEEDBACK ON PREVIOUS FAVORS: {feedback_contents}]"

                top_bun = system_prompt + shiro_context

                # 2. Meat: Retrieved Long-Term Memory (RAG)
                hyp_ans = self._imagine_reply(processed_text)

                # Collect recent conversation content to exclude from RAG (prevent verbatim repetition)
                history = self.memory.get_history()
                exclude_list = [m["content"] for m in history[-5:]] if history else []

                long_term_memory = self.memory.get_full_context(
                    processed_text,
                    user_id=user_name,
                    hypothetical_answer=hyp_ans,
                    exclude_list=exclude_list
                )

                # [V4 UPGRADE] Inject v4 Memory recall
                v4_recall = self.v4_memory.recall(user_name)
                if v4_recall:
                    long_term_memory = f"{v4_recall}\n\n{long_term_memory}"

                # Add Temporal Context
                meat = self._add_temporal_context(long_term_memory, user_name)

                # 3. Garnish: Working Memory / Persistent Facts (profile.json)
                user_profile = self.user_profiles.get(user_name, {})
                garnish = f"### [USER PROFILE: {user_name}]\n"
                if user_profile:
                    for k, v in user_profile.items():
                        garnish += f"- {k}: {v}\n"
                else:
                    garnish += "- No specific persistent facts known yet.\n"

                # 4. Bottom Bun: Short-Term Buffer (Last N messages)
                history = self.memory.get_history()
                short_term_buffer = history[-10:] if history else []

                # --- ASSEMBLE SANDWICH ---
                full_context = f"{meat}\n\n{garnish}\n\n{inner_context}"

                # [AUTONOMY] Tool Calling Support (Capped at 2 per session)
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
                                    "query": {"type": "string", "description": "Search query for fox lore"}
                                },
                                "required": ["query"]
                            }
                        }
                    }]

                raw_stream = self.llm.stream_response(top_bun, processed_text, short_term_buffer, full_context, tools=tools)

                response_stream = self._extract_thought_from_stream(raw_stream)
                response_fragments = []

                for fragment in split_into_sentences(response_stream):
                    if interrupt_event and interrupt_event.is_set():
                        logger.info("Response halted by interrupt.")
                        yield "... [Interrupted]"
                        break

                    if "TOOL_CALLS:" in fragment and self.session_tool_count < 2:
                        # Handle tool call
                        logger.info(f"Tool call detected in stream: {fragment[:100]}...")
                        fragment = self._safe_async_run(self.handle_tool_calls_async(fragment, processed_text))
                        self.session_tool_count += 1

                    clean_fragment = self._clean_response(fragment, processed_text)
                    if clean_fragment:
                        response_fragments.append(clean_fragment)

                # ── Hmph throttle runs on the FULL assembled response (not per fragment) ──
                full_response = " ".join(response_fragments)
                full_response = self._throttle_hmph(full_response)

                # [V4 UPGRADE] Adapt to user speech cadence
                full_response = self.cadence.adapt_text(full_response, user_name)

                # Re-split into fragments for streaming yield so the GUI still gets
                # incremental updates. Simple word-chunk split to avoid re-splitting logic.
                if full_response:
                    # Yield the throttled response as a single clean string.
                    # The GUI buffers anyway (BUFFER_THRESHOLD=6) so this is fine.
                    yield full_response

                if not (interrupt_event and interrupt_event.is_set()):
                    if self.last_thought:
                        logger.info(f"Shiro's Internal Thought: {self.last_thought.strip()}")
                        self.memory.store_insight(f"Thought: {self.last_thought.strip()}", user_id=user_name, source="inner_monologue")

                    if self._interaction_count == 0:
                        self.memory.store_episodic_memory(f"SESSION START: First interaction with {user_name} today: '{text}'", user_id=user_name, importance=8)

                    self.memory.add_interaction(text, full_response.strip(), user_id=user_name)
                    # [V4 UPGRADE] Sync to v4 memory for potential spontaneous callbacks
                    self.v4_memory.add_user_message(user_name, text, emotions=user_emotions)
                    self.v4_memory.add_assistant_message(full_response.strip(), user_id=user_name)

                    self._interaction_count += 1

                    # [INNER MIND] Reflect on the generated response
                    self.legacy_mind.reflect_on_response(full_response.strip(), text)
                    # Persist Legacy Inner Mind state
                    state_path = Path(__file__).parent.resolve() / "shiro_state.json"
                    self.legacy_mind.save_state(str(state_path))

                    # Trigger background tasks AFTER response is generated to avoid concurrent VRAM usage
                    threading.Thread(target=lambda: self._safe_async_run(self.check_and_think_async(user_name, previous_interaction)), daemon=True).start()
                    if self._interaction_count > 0 and self._interaction_count % 10 == 0:
                        threading.Thread(target=self.reflect, args=(user_name,), daemon=True).start()
                    self.shiro_learn_and_stay_shiro(processed_text, full_response)
            except Exception as e:
                logger.error(f"Engine text processing failed: {e}. Falling back to RuleEngine.")
                # [V4 UPGRADE] Fallback to RuleEngine if LLM fails
                try:
                    rel_tier = self.legacy_mind.relationship.level.name.lower()
                    mood = self.mind.mood.value
                    # Determine intent (simplified for fallback)
                    fallback_intent = "empathize" if self.sentiment_trajectory.current_valence(user_name) < -0.3 else "share"

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

    async def fetch_relevant_memory_async(self, query_text: str, n_results: int = 2):
        """Async fetch of relevant memories."""
        results = await self.memory.search_relevant_memories_async(query_text, n_results=n_results)
        return [r['content'] for r in results] if results else []

    async def autonomous_response_async(self, user_msg: str, user_id: str):
        """Autonomous response logic as requested."""
        # Fetch memory async
        relevant_mem = await self.fetch_relevant_memory_async(f"shiro tricks for {user_id}", n_results=3)

        system_prompt = self.persona.get_system_prompt()
        shiro_context = f"\n[SYSTEM: Autonomous Mode. Use memory: {relevant_mem}. Act coyly and autonomously.]\n{self.outfit_block()}"

        # Define tools (Capped at 2/session)
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
                            "query": {"type": "string", "description": "Search query for fox lore"}
                        },
                        "required": ["query"]
                    }
                }
            }]

        # Generate with tools
        raw_resp = await self.llm.generate_response_async(
            system_prompt + shiro_context,
            user_msg,
            self.memory.get_history()[-10:],
            tools=tools
        )

        # Check for tool calls in the response
        final_resp = raw_resp
        if "TOOL_CALLS:" in raw_resp and self.session_tool_count < 2:
            final_resp = await self.handle_tool_calls_async(raw_resp, user_msg)
            self.session_tool_count += 1

        # Store response back efficiently
        await self.memory.add_interaction_async(user_msg, final_resp, user_id=user_id)
        return final_resp

    async def handle_tool_calls_async(self, fragment: str, user_msg: str):
        """Handles tool calls detected in the stream."""
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
            return " [Tool failed—acting on instinct!] "
        return fragment

    async def check_and_think_async(self, user_id: str, last_interaction: datetime):
        """Checks for inactivity and triggers autonomous thought with daily caps."""
        if datetime.now(timezone.utc) - last_interaction > timedelta(minutes=10):
            # Check daily cap
            with self.brain_lock:
                # Use self.brain directly
                today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
                thought_data = self.brain.get("autonomous_thoughts", {})

                if thought_data.get("date") != today:
                    thought_data = {"date": today, "count": 0}

                if thought_data["count"] >= 5:
                    logger.info("Autonomous thought daily cap reached.")
                    return

            logger.info(f"Triggering inactivity thought for {user_id} ({thought_data['count']+1}/5)")
            thought_prompt = "Reflect on current tricks and inactivity. Update your coy plans."
            context = await self.memory.get_full_context_async("Autonomous reflection", user_id=user_id)
            thought = await self.llm.generate_response_async(
                self.persona.get_system_prompt() + f"\n{self.outfit_block()}",
                thought_prompt,
                self.memory.get_history()[-8:],
                context=context
            )
            await self.memory.store_insight_async(f"Autonomous Thought: {thought}", user_id=user_id, source="autonomous_thought")

            # Update cap
            with self.brain_lock:
                thought_data["count"] += 1
                self.brain["autonomous_thoughts"] = thought_data
                self._save_brain()

    def add_feedback(self, feedback: str, user_id: str):
        """Stores user feedback for tricks/favors."""
        self.memory.store_episodic_memory(f"User Favor Feedback: {feedback}", user_id=user_id, importance=7)

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
            self.user_session_info[user_name] = {"downtime": downtime_str}
        downtime_str = self.user_session_info[user_name]["downtime"]
        last_time = self.memory.get_last_interaction_time(user_name)
        duration_str = "some time"
        if last_time:
            delta = now_utc - last_time
            duration_str = self._format_timedelta(delta)
        uptime_delta = now_utc - self.session_start
        uptime_str = self._format_timedelta(uptime_delta)
        current_time_str = now_utc.astimezone().strftime('%I:%M %p')
        current_date_str = now_utc.astimezone().strftime('%A, %B %d, %Y')
        temporal_note = (
            f"The current time is {current_time_str} on {current_date_str}.\n"
            f"- [DOWNTIME]: You were inactive for {downtime_str} before this session started.\n"
            f"- [TIME SINCE LAST SEEN]: It has been {duration_str} since your last interaction with {user_name}.\n"
            f"- [SESSION UPTIME]: You have been active for {uptime_str} this session.\n"
            "You are aware of the passage of time. Mention downtime or duration ONLY if it serves your teasing or if you want to complain about being lonely."
        )
        return f"### [TEMPORAL CONTEXT]\n- {temporal_note}\n\n{context}"

    def _format_timedelta(self, delta: timedelta) -> str:
        """Formats a timedelta into a human-readable string."""
        days = delta.days
        hours, remainder = divmod(int(delta.seconds), 3600)
        minutes, seconds = divmod(remainder, 60)
        time_parts = []
        if days > 0: time_parts.append(f"{days} day{'s' if days > 1 else ''}")
        if hours > 0: time_parts.append(f"{hours} hour{'s' if hours > 1 else ''}")
        if minutes > 0: time_parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        if not time_parts or (days == 0 and hours == 0 and minutes < 5):
            time_parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
        if len(time_parts) == 1:
            return time_parts[0]
        return ", ".join(time_parts[:-1]) + f" and {time_parts[-1]}" if len(time_parts) > 1 else time_parts[0]

    def _extract_thought_from_stream(self, stream):
        buffer = ""
        in_thought = False
        # Improved patterns to catch metadata headers like [SHIRO INNER MIND v4 -- ...]
        start_pattern = re.compile(rf'\[(?:{self.THOUGHT_KEYWORDS})[^\]]*\]|\((?:{self.THOUGHT_KEYWORDS})[^\)]*\)|(?<!\w)THOUGHTS?:|<THOUGHTS?>|\*(?:Shiro\s+)?(?:THOUGHTS?|THINKING|SCHEMING|PLOTTING|THINKS?).*?\*', re.IGNORECASE)
        end_pattern = re.compile(rf'\[/(?:{self.THOUGHT_KEYWORDS})\]|\(/(?:{self.THOUGHT_KEYWORDS})\)|</THOUGHTS?>|\*(?:/THOUGHTS?|END THINKING|END SCHEMING|END|/|THOUGHTS?)\*', re.IGNORECASE)

        # Pattern for metadata lines that might leak if only partially bracketed
        meta_line_pattern = re.compile(rf'^\s*\[(?:{self.THOUGHT_KEYWORDS}).*?$', re.MULTILINE | re.IGNORECASE)

        for chunk in stream:
            buffer += chunk
            while True:
                if not in_thought:
                    match = start_pattern.search(buffer)
                    if match:
                        pre_tag = buffer[:match.start()]
                        if pre_tag: yield pre_tag
                        tag_content = match.group(0)
                        buffer = buffer[match.end():].lstrip()
                        is_block_start = False
                        inner_text = re.sub(r'[\[\]\(\)\<\>\*]', '', tag_content).strip()
                        # Strict check for thought block start
                        if any(re.fullmatch(k, inner_text, re.IGNORECASE) for k in self.THOUGHT_KEYWORDS.split('|')):
                            is_block_start = True

                        if is_block_start:
                            in_thought = True
                        else:
                            # If it's a minor tag like (LOG: ...), add to last_thought but don't enter in_thought state
                            self.last_thought += tag_content + " "
                            # If it's a Shiro metadata header, consume until the end of the line in the buffer
                            if any(kw in tag_content.upper() for kw in ["SHIRO", "INNER", "MIND"]):
                                line_end = buffer.find("\n")
                                if line_end != -1:
                                    self.last_thought += buffer[:line_end]
                                    buffer = buffer[line_end:].lstrip()
                        continue
                    else:
                        if not self.last_thought and buffer.strip().startswith("[") and "]" in buffer:
                            start_idx = buffer.find("[")
                            closing_idx = buffer.find("]")
                            if start_idx < closing_idx:
                                potential_thought = buffer[start_idx+1:closing_idx]
                                if len(potential_thought) > 3:
                                    self.last_thought = potential_thought
                                    buffer = buffer[closing_idx+1:].lstrip()
                                    continue
                        if len(buffer) > 40:
                            yield buffer[:-40]
                            buffer = buffer[-40:]
                        break
                else:
                    match = end_pattern.search(buffer)
                    if match:
                        thought_chunk = buffer[:match.start()]
                        self.last_thought += thought_chunk
                        # Hide [/THOUGHT] from UI
                        buffer = buffer[match.end():].lstrip()
                        in_thought = False
                        continue
                    else:
                        if "\n" in buffer:
                            parts = buffer.split("\n", 1)
                            if len(parts[1]) > 5 and parts[1].strip() and parts[1].strip()[0].isupper():
                                self.last_thought += parts[0]
                                # Hide newline termination [/THOUGHT] from UI
                                buffer = parts[1]
                                in_thought = False
                                continue
                        if len(buffer) + len(self.last_thought) > 4000:
                            self.last_thought += buffer
                            buffer = ""
                            in_thought = False
                        break
        if buffer:
            if in_thought:
                transition_match = re.search(r'([\.\!\?]\s+|\n\s*)([A-Z])', buffer)
                if transition_match:
                    self.last_thought += buffer[:transition_match.start(2)]
                    # Only yield the part AFTER the thought transition
                    yield buffer[transition_match.start(2):]
                elif len(buffer.strip()) > 30 and not any(kw in buffer.upper() for kw in self.THOUGHT_KEYWORDS.split('|')):
                    self.last_thought += " [Unclosed]"
                    # If it looks like a response leaked into an unclosed thought, we might still want to see it
                    # but for now we follow the rule of hiding thoughts.
                    # Actually if it's > 30 chars and doesn't look like a keyword, it's probably real text.
                    yield buffer
                else:
                    self.last_thought += buffer
            else:
                if not self.last_thought and buffer.strip().startswith("[") and "]" in buffer:
                    start_idx = buffer.find("[")
                    closing_idx = buffer.find("]")
                    if start_idx < closing_idx:
                        pre_bracket = buffer[:start_idx]
                        if pre_bracket: yield pre_bracket
                        self.last_thought = buffer[start_idx+1:closing_idx]
                        yield buffer[closing_idx+1:].strip()
                        return
                yield buffer

    def _clean_response(self, text: str, user_query: Optional[str] = None) -> str:
        # Preserve thought markers — the stream extractor handles these separately
        if "[THOUGHT]" in text or "[/THOUGHT]" in text:
            return text

        # Aggressive cleaning of internal headers that start with keywords
        # Matches patterns like "[SHIRO INNER MIND v4 -- turn 29 -- 17h 36m 20s] engaged..."
        # We consume until we see something that looks like real dialogue (starts with Capital or *)
        clean = re.sub(
            rf'\[(?:{self.THOUGHT_KEYWORDS})[^\]]*\].*?(?=\n|[A-Z]\w+|(?<!\|)\*|$)',
            '', text, flags=re.IGNORECASE | re.DOTALL
        )

        # Remove various meta/thought tags
        clean = re.sub(
            rf'\[(?:{self.THOUGHT_KEYWORDS})[^\]]*\].*?\[/(?:{self.THOUGHT_KEYWORDS})\]',
            '', clean, flags=re.IGNORECASE | re.DOTALL
        )
        clean = re.sub(
            rf'\((?:{self.THOUGHT_KEYWORDS})[^\)]*\).*?\(/(?:{self.THOUGHT_KEYWORDS})\)',
            '', clean, flags=re.IGNORECASE | re.DOTALL
        )
        clean = re.sub(r'<THOUGHTS?>.*?</THOUGHTS?>', '', clean, flags=re.IGNORECASE | re.DOTALL)

        # Remove unclosed opening tags at the very start
        clean = re.sub(
            rf'(?i)^(?:\[(?:{self.THOUGHT_KEYWORDS})[^\]]*\]|\((?:{self.THOUGHT_KEYWORDS})[^\)]*\)|THOUGHTS?:)\s*',
            '', clean, count=1
        ).strip()

        # Remove asterisk-wrapped actions or thoughts
        clean = re.sub(r'(?i)\*(?:Shiro\s+)?(?:thinks?|thinking|schem\w+|plott\w+).*?\*', '', clean).strip()

        # Remove any other bracketed/parenthetical meta-info that isn't a link
        clean = re.sub(r'\[.*?\](?!\()|(?<!\])\(.*?\)', '', clean).strip()

        # ── Echo/Repetition Stripping ────────────────────────────────────────────
        # If the response starts with exactly what the user said (common failure mode)
        if user_query:
            query_clean = user_query.strip().lower()
            if clean.lower().startswith(query_clean):
                clean = clean[len(query_clean):].lstrip(" :,.-")

        # ── Speaker tag stripping ────────────────────────────────────────────────
        # Model sometimes prefixes its spoken response with "Shiro:" or similar
        clean = re.sub(r'(?i)^\s*(?:shiro|system|user|assistant)\s*:\s*', '', clean).strip()
        # Also strip generic "Name:" patterns (capitalized word followed by colon)
        clean = re.sub(r'^[A-Z][a-z]+:\s*', '', clean).strip()

        lines = clean.splitlines()
        cleaned_lines = []
        for line in lines:
            if line.isupper():
                cleaned_lines.append(line.capitalize())
            else:
                cleaned_lines.append(line)
        return '\n'.join(cleaned_lines).strip()

    def _throttle_hmph(self, full_response: str) -> str:
        """
        Hmph throttle — operates on the COMPLETE assembled response, not fragments.
        Allows at most 1 'hmph' per response, and only once every COOLDOWN responses.

        This MUST be called at the full-response level (after all fragments are joined)
        because _clean_response runs per sentence fragment. If the throttle ran per
        fragment, the COOLDOWN counter would tick multiple times per LLM response,
        allowing hmph through every 3 fragments (~every 1-2 responses) instead of
        every 3 full responses.
        """
        COOLDOWN = 4  # full responses between allowed hmphs (raised from 3 → 4)

        hmph_matches = list(_HMPH_PATTERN.finditer(full_response))
        if not hmph_matches:
            # No hmph — advance cooldown counter toward next allowed slot
            self._hmph_counter = min(self._hmph_counter + 1, COOLDOWN + 5)
            return full_response

        if self._hmph_counter < COOLDOWN:
            # Still in cooldown — suppress ALL hmphs this response
            alt_idx = self._hmph_session_count % len(_HMPH_ALTERNATIVES)
            replacement = _HMPH_ALTERNATIVES[alt_idx]
            result = _HMPH_PATTERN.sub(replacement, full_response)
            self._hmph_session_count += 1
            # Counter keeps ticking (don't reset — we're still cooling down)
            self._hmph_counter += 1
        else:
            # Cooldown complete — allow exactly ONE hmph, suppress any extras
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
                result = full_response  # single hmph, keep it

            # Reset cooldown counter after allowing one through
            self._hmph_counter = 0
            self._hmph_session_count += 1

        return result

    def reflect(self, user_id: str):
        try:
            # We don't hold the processing_lock for the entire reflection (LLM call can be slow)
            # but we use it to safely get history.
            with self.processing_lock:
                history = self.memory.get_history()

            if not history: return

            logger.info(f"Shiro is reflecting on {user_id}...")
            reflection_prompt = (
                "### INSTRUCTION\n"
                "Analyze the recent conversation history below. Extract critical information to maintain perfect long-term memory.\n"
                "RETURN ONLY VALID YAML. No markdown, no ``` fences, no * bullet points — use YAML list syntax (- item) only.\n"
                "Required keys:\n"
                "  user_facts: { fact_name: fact_value }   # persistent facts, or {} if none\n"
                "  events: [- list of notable events]       # use YAML list format\n"
                "  insights: [- lessons about this user]    # use YAML list format\n"
                "  relations: []                            # {source, target, relation} or []\n"
                "  summary: \"Single quoted paragraph summary of this conversation.\"\n\n"
                "STRICT YAML RULES:\n"
                "- Use '- item' for lists, NOT '* item'\n"
                "- String values with colons must be quoted: summary: \"text: with colon\"\n"
                "- Do not use square brackets for sentences: use proper YAML list items\n"
                "- Be concise and accurate. Do not invent facts.\n"
            )
            analysis_raw = self.llm.generate_response(
                "You are Shiro's Memory Processor. You are precise and observant.",
                f"HISTORY TO ANALYZE:\n{history}",
                [],
                context=reflection_prompt
            )
            # Use robust cleaner: handles markdown bullets (*), inline [sentences], code fences
            cleaned_raw = _robust_clean_yaml(analysis_raw)
            # Fallback: also apply the existing clean_yaml_block for any remaining quirks
            cleaned_raw = clean_yaml_block(cleaned_raw)

            with self.processing_lock:
                data = None
                try:
                    data = yaml.safe_load(cleaned_raw)
                except Exception as e:
                    logger.warning(f"YAML Parse failed in reflection: {e}")
                    # Last-resort: try to extract just the summary as a plain string
                    summary_match = re.search(r'summary[:\s]+["\']?(.+?)["\']?\s*$', analysis_raw, re.IGNORECASE | re.MULTILINE)
                    if summary_match:
                        data = {"summary": summary_match.group(1).strip(), "events": [], "insights": [], "user_facts": {}, "relations": []}
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
                            self.memory.add_entity_relation(rel['source'], rel['target'], rel.get('relation', 'connected'))
                    segment_summary = data.get('summary')
                    if segment_summary:
                        self.memory.store_summary(user_id, str(segment_summary))
                    summaries = self.memory.search_relevant_memories("general conversation", filter_type="summary", user_id=user_id, n_results=15)
                    local_summaries = [s["content"] for s in summaries if not s["metadata"].get("is_global")]
                    if len(local_summaries) >= 6:
                        logger.info(f"Condensing {len(local_summaries)} summaries into a Global Summary for {user_id}...")
                        global_prompt = (
                            "Combine these individual conversation segment summaries into one single 'Global Narrative Summary'.\n"
                            "The result must be a comprehensive but concise paragraph that covers the entire relationship/session history so far.\n"
                            "Focus on key themes, major events, and the evolving relationship."
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

    def shutdown(self):
        """Performs reflective shutdown and session consolidation."""
        logger.info("Engine initiating Reflective Shutdown...")
        try:
            self.reflect(self.current_user_name)
            # Final Legacy Inner Mind save
            state_path = Path(__file__).parent.resolve() / "shiro_state.json"
            self.legacy_mind.save_state(str(state_path))
            # [V4 UPGRADE] Stop v4 InnerMind and idle loop
            if self._idle_task:
                self._idle_task.cancel()
            self._safe_async_run(self.mind.stop())
            loop_prompt = (
                "Identify any 'Open Loops' from the recent conversation. \n"
                "An Open Loop is a project started but not finished, a question asked but not answered, or a promise made.\n"
                "Return a concise list of strings."
            )
            history = self.memory.get_history()
            if history:
                open_loops_raw = self.llm.generate_response(
                    "You are Shiro's internal kitsune.",
                    f"RECENT HISTORY:\n{history}",
                    [],
                    context=loop_prompt
                )
                if open_loops_raw and len(open_loops_raw) > 10:
                    self.memory.store_episodic_memory(f"OPEN LOOPS at session end: {open_loops_raw}", user_id=self.current_user_name, importance=7)
            self._save_profiles()
            # Final Brain Save
            self._save_brain()
            self.memory.prune_old_memories()

            # Close LLM Session
            self._safe_async_run(self.llm.close())

            logger.info("Reflective Shutdown complete.")
        except Exception as e:
            logger.error(f"Shutdown failed: {e}")

    def generate_autonomous_thought(self):
        """Generates a proactive thought."""
        try:
            with self.processing_lock:
                system_prompt = self.persona.get_system_prompt(now=datetime.now())
                history = self.memory.get_history()
                context = self.memory.get_full_context("Recent status", user_id=self.current_user_name)

            prompt = "Reflect on your kitsune nature and interactions. Generate a proactive thought in [THOUGHT] tags."
            raw_thought = self.llm.generate_response(system_prompt, prompt, history, context=context)
            match = re.search(r'\[THOUGHTS?\](.*?)\[/THOUGHTS?\]', raw_thought, re.IGNORECASE | re.DOTALL)
            if match:
                content = match.group(1).strip()
                with self.processing_lock:
                    self.memory.store_insight(f"Autonomous Thought: {content}", user_id=self.current_user_name, source="autonomous_reflection")
                return content
        except Exception as e:
            logger.warning(f"Autonomous thought failed: {e}")
        return None

    def change_outfit(self, requested: str) -> str:
        req = requested.lower().strip()
        for key, data in self.wardrobe.items():
            if req in [key, data["name"].lower()] and data.get("active", False):
                self.current_outfit = key
                return f"*swishes her tail in the {data['name']}* Hmph. Wearing this now. Don't stare!"
        return "I don't have that outfit, dummy. Don't make things up."

    def _detect_context_drift(self, query: str) -> float:
        history = self.memory.get_history()
        if not history or len(history) < 2: return 0.0
        try:
            query_emb = np.array(self.memory.get_embedding(query))
            recent_texts = [re.sub(r'^\[.*?\]\s*', '', m["content"]) for m in history[-4:]]
            recent_embs = [np.array(self.memory.get_embedding(t)) for t in recent_texts]
            avg_recent_emb = np.mean(recent_embs, axis=0)
            norm_q = np.linalg.norm(query_emb)
            norm_r = np.linalg.norm(avg_recent_emb)
            if norm_q == 0 or norm_r == 0: return 0.0
            similarity = np.dot(query_emb, avg_recent_emb) / (norm_q * norm_r)
            drift = 1.0 - max(0, similarity)
            logger.info(f"Context Drift Score: {drift:.2f}")
            return drift
        except Exception as e:
            logger.warning(f"Context drift detection failed: {e}")
            return 0.0

    def get_smart_intensity(self, user_msg: str) -> float:
        history_list = self.memory.get_history()
        raw_history_text = " ".join([m["content"] for m in history_list])
        history_text_lower = raw_history_text.lower()
        hype = len([w for w in ["!","??","treat","favor","shiny","dummy","hmph"] if w in history_text_lower])
        chill = len([w for w in ["tired","sleep","cozy","soft","quiet","zzz","sad"] if w in history_text_lower])
        caps = sum(1 for c in raw_history_text if c.isupper()) / max(len(raw_history_text), 1)
        recent_chill = sum(1 for m in history_list[-10:] if any(w in m["content"].lower() for w in ["tired","cozy","zzz","soft"]))
        base = 0.5 + 0.15*hype - 0.18*chill + 0.20*caps
        if recent_chill >= 6 and "treat" in user_msg.lower(): base = min(base, 0.65)

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
                    logger.warning("Brain file is empty. Using default.")
                    self.brain = default_brain
                else:
                    self.brain = json.loads(content)
            except (json.JSONDecodeError, Exception) as e:
                logger.error(f"Failed to load brain: {e}. Using default.")
                self.brain = default_brain
            return self.brain

    def _save_brain(self):
        """Saves Shiro's brain to disk atomically."""
        with self.brain_lock:
            try:
                temp_file = self.brain_file.with_suffix(".tmp")
                temp_file.write_text(json.dumps(self.brain, indent=2))
                temp_file.replace(self.brain_file)
            except Exception as e:
                logger.error(f"Failed to save brain: {e}")

    def shiro_learn_and_stay_shiro(self, user_msg: str, shiro_reply: str):
        with self.brain_lock:
            # self.brain is already loaded in __init__ and updated atomically in _load_brain
            brain = self.brain
            if not brain: return

            msg = user_msg.lower()
            # Defensive initialization
            brain.setdefault("facts", {})
            brain.setdefault("favors", [])
            brain.setdefault("personality", {k: (v[0] + v[1]) / 2 for k, v in self.core_anchors.items()})
            brain.setdefault("achievements", [])

            if ("my name is" in msg or "call me" in msg) and "username" not in msg and "?" not in msg:
                parts = msg.split("is") if "is" in msg else msg.split("me")
                name = parts[-1].strip(" .,!?")
                if len(name) >= 2 and len(name) < 20:
                    brain["facts"]["preferred_name"] = name.title()
            if "i hate" in msg or "i love" in msg:
                thing = msg.split("hate" if "hate" in msg else "love")[-1].strip()
                brain["facts"][f"user_{'hates' if 'hate' in msg else 'loves'}_{thing}"] = True
            if any(w in shiro_reply.lower() for w in ["dummy","silly","stranger"]):
                if len(brain["favors"]) < 50:
                    brain["favors"].append({"tease": shiro_reply, "ts": time.time()})
            intensity = self.intensity
            brain.setdefault("mood_history", []).append(intensity)
            brain["mood_history"] = brain["mood_history"][-200:]
            avg_mood = sum(brain["mood_history"]) / len(brain["mood_history"])
            drift = (avg_mood - 0.7) * 0.0008

            # Safe personality updates
            for trait in ["slyness", "kindness", "sass"]:
                if trait not in brain["personality"]:
                    mn, mx = self.core_anchors.get(trait, (0.5, 0.5))
                    brain["personality"][trait] = (mn + mx) / 2

            brain["personality"]["slyness"] = self.clamp(brain["personality"]["slyness"] + drift * 1.2, self.core_anchors["slyness"])
            brain["personality"]["kindness"] = self.clamp(brain["personality"]["kindness"] + drift * -1.0, self.core_anchors["kindness"])
            brain["personality"]["sass"] = self.clamp(brain["personality"]["sass"] + random.uniform(-0.001, 0.001), self.core_anchors["sass"])
            brain["personality"]["greed"] = min(1.0, brain["personality"].get("greed", 0.5) + 0.0005)

            # Safe trust updates
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
            self._save_brain()

    def clamp(self, value, min_max):
        mn, mx = min_max
        return max(mn, min(mx, value))

    def get_greeting_prompt(self, user_name: str, mode: str = "new") -> str:
        """
        Returns a randomized, appropriate-energy greeting LOG prompt.
        Call this from main.py's handle_user_join instead of hardcoded strings.

        mode options:
          'new'            — first time this user has ever appeared
          'returning_soon' — came back within ~2 hours
          'returning_long' — came back after a long absence (>2 hours)
        """
        return _pick_greeting(user_name, mode)

    def get_fallback_greeting(self, user_name: str, mode: str = "new") -> str:
        """
        Returns a soft in-character fallback string.
        Used as the default= in handle_user_join when the LLM call fails or returns empty.
        """
        return _pick_fallback(user_name, mode)
