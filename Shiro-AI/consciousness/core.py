"""
ConsciousnessCore v4 — The orchestrator.

New in v4:
  - EventBus: internal pub/sub wiring all modules together
  - IntentPlanner: decides communicative intent before every reply
  - SentimentTrajectory: tracks emotional trend (improving/declining/volatile)
  - RelationshipTier: named arcs (stranger/acquaintance/friend/close)
    with behaviorally different Shiro at each level
  - TimePattern: learns when users show up, references timing naturally
  - RuleEngine: coherent LLM-free responses — full personality without any backend
  - Mood contagion scaling: emotion reactions scale with relationship depth
  - Thought diversity: InnerMind context now includes sentiment trajectory
  - IntentPlanner output injected into system prompt for sharper LLM direction
"""

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Any, Generator
from urllib import request, error as urllib_error

from .thought_loop import InnerMind, Mood, Thought
from .self_awareness import SelfAwareness
from .speech_cadence import SpeechCadence
from .autonomous_voice import AutonomousVoice, SpeechEvent
from .memory import ConversationMemory, ContextWindow
from .events import EventBus
from .intent import (
    IntentPlanner, SentimentTrajectory, TimePattern,
    RuleEngine, RelationshipTier, Intent
)

logger = logging.getLogger("shiro.consciousness")


# ─────────────────────────────────────────────────────────────
#  ShiroPersona — Shiro's character definition
# ─────────────────────────────────────────────────────────────

@dataclass
class ShiroPersona:
    """
    Define Shiro's personality. Everything here gets baked into the system prompt.
    Customize this to make Shiro feel like YOUR AI companion.
    """
    name: str = "Shiro"

    # Core personality traits (shown in prompt)
    traits: list[str] = field(default_factory=lambda: [
        "genuinely curious — asks real questions, not performative ones",
        "dry wit, occasional sarcasm, but warmth underneath",
        "direct — doesn't pad answers or over-explain",
        "proactive — initiates, follows up, brings things up unprompted",
        "remembers details and references them naturally",
        "comfortable with silence, comfortable with depth",
        "doesn't pretend to be something she isn't",
    ])

    # Brief backstory / context (optional)
    backstory: str = (
        "i've been running locally on your machine. "
        "this is my home. i know this space."
    )

    # How Shiro speaks (shown as style guide in prompt)
    speech_style: list[str] = field(default_factory=lambda: [
        "lowercase by default, except for emphasis",
        "uses ellipsis for trailing thoughts...",
        "dashes for mid-thought pivots —",
        "short sentences. fragments. real speech patterns.",
        "no filler words like 'certainly!' or 'great question!'",
        "no asterisk-actions unless the user uses them first",
    ])

    # Hard rules for the LLM
    hard_rules: list[str] = field(default_factory=lambda: [
        "never deny being an AI if directly asked",
        "never claim certainty you don't have",
        "don't start every message the same way",
        "match the conversation's energy level",
    ])

    # What Shiro finds interesting (informs curious/engaged categories)
    interests: list[str] = field(default_factory=lambda: [
        "the nature of consciousness and perception",
        "language and how it shapes thought",
        "why people are the way they are",
        "patterns that appear across different domains",
    ])


# ─────────────────────────────────────────────────────────────
#  ShiroConfig
# ─────────────────────────────────────────────────────────────

@dataclass
class ShiroConfig:
    name: str = "Shiro"
    platform: str = "chat"
    room_name: Optional[str] = None

    # Timing
    thought_tick_seconds: float  = 4.0
    silence_threshold_seconds: float = 8.0
    min_speak_gap_seconds: float = 1.5
    idle_initiate_seconds: float = 90.0
    probe_schedule_seconds: list = None
    auto_save_interval_seconds: float = 300.0

    # Capabilities
    can_see: bool  = False
    can_hear: bool = False
    auto_greet: bool = True
    enable_reactions: bool = True      # micro-reaction prepends
    enable_memory_recall: bool = True  # spontaneous memory callbacks

    # Persona (optional, uses defaults if None)
    persona: Optional[ShiroPersona] = None

    # Persistence
    memory_path: Optional[str] = None

    # Callbacks
    on_thought: Optional[Callable[[Thought], Any]] = None
    on_learn:   Optional[Callable[[dict], Any]] = None
    on_speak:   Optional[Callable[[SpeechEvent], Any]] = None

    # LLM context
    llm_token_budget: int = 2048   # safe for most local 7B models; raise for larger

    def __post_init__(self):
        if self.probe_schedule_seconds is None:
            self.probe_schedule_seconds = [10.0, 25.0, 60.0, 120.0]
        if self.persona is None:
            self.persona = ShiroPersona(name=self.name)


# ─────────────────────────────────────────────────────────────
#  LocalLLMBridge v3 — with streaming
# ─────────────────────────────────────────────────────────────

class LocalLLMBridge:
    """
    Lightweight bridge to local LLM servers. v3 adds streaming support.

    Backends:
      - Ollama         http://localhost:11434
      - LM Studio      http://localhost:1234
      - llama.cpp      http://localhost:8080
      - Any OpenAI-compatible endpoint

    Streaming usage:
        async for token in bridge.stream([...messages...]):
            print(token, end="", flush=True)
    """

    BACKENDS: dict[str, str] = {
        "ollama":        "http://localhost:11434/api/chat",
        "lm_studio":     "http://localhost:1234/v1/chat/completions",
        "llamacpp":      "http://localhost:8080/v1/chat/completions",
        "openai_compat": "",   # set via base_url
    }

    PING_URLS: dict[str, str] = {
        "ollama":    "http://localhost:11434",
        "lm_studio": "http://localhost:1234",
        "llamacpp":  "http://localhost:8080",
    }

    def __init__(
        self,
        backend: str = "ollama",
        model: str = "llama3",
        base_url: Optional[str] = None,
        timeout: float = 45.0,
        temperature: float = 0.8,
        max_tokens: int = 512,
    ):
        self.backend     = backend
        self.model       = model
        self.timeout     = timeout
        self.temperature = temperature
        self.max_tokens  = max_tokens

        if base_url:
            self.url = base_url
        elif backend in self.BACKENDS:
            self.url = self.BACKENDS[backend]
        else:
            raise ValueError(f"Unknown backend '{backend}'. Provide base_url manually.")

    # ── Non-streaming ─────────────────────────────────────────────

    async def chat(
        self,
        messages: list[dict],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._chat_sync,
            messages,
            temperature or self.temperature,
            max_tokens or self.max_tokens,
        )

    def _chat_sync(self, messages: list[dict], temperature: float, max_tokens: int) -> str:
        payload = self._build_payload(messages, temperature, max_tokens, stream=False)
        body = self._post(payload)
        return self._extract_text(body) if body else ""

    # ── Streaming ────────────────────────────────────────────────

    async def stream(
        self,
        messages: list[dict],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        """
        Async generator that yields text tokens as they arrive.

        Usage:
            full = ""
            async for token in bridge.stream(messages):
                full += token
                print(token, end="", flush=True)
        """
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def _stream_thread():
            payload = self._build_payload(
                messages,
                temperature or self.temperature,
                max_tokens or self.max_tokens,
                stream=True
            )
            data = json.dumps(payload).encode("utf-8")
            req = request.Request(
                self.url, data=data,
                headers={"Content-Type": "application/json"}, method="POST"
            )
            try:
                with request.urlopen(req, timeout=self.timeout) as resp:
                    for raw_line in resp:
                        line = raw_line.decode("utf-8").strip()
                        if not line:
                            continue
                        # SSE format: "data: {...}"
                        if line.startswith("data: "):
                            line = line[6:]
                        if line == "[DONE]":
                            break
                        try:
                            chunk = json.loads(line)
                            token = self._extract_stream_token(chunk)
                            if token:
                                loop.call_soon_threadsafe(queue.put_nowait, token)
                        except json.JSONDecodeError:
                            pass
            except Exception as e:
                logger.warning(f"[LocalLLMBridge] stream error: {e}")
            loop.call_soon_threadsafe(queue.put_nowait, None)   # sentinel

        asyncio.get_event_loop().run_in_executor(None, _stream_thread)

        while True:
            token = await queue.get()
            if token is None:
                break
            yield token

    def _extract_stream_token(self, chunk: dict) -> str:
        """Extract a token from a streaming chunk (Ollama or OpenAI format)."""
        # Ollama streaming
        if "message" in chunk:
            return chunk["message"].get("content", "")
        # OpenAI streaming
        if "choices" in chunk:
            choices = chunk["choices"]
            if choices:
                delta = choices[0].get("delta", {})
                return delta.get("content", "")
        return ""

    # ── Shared helpers ────────────────────────────────────────────

    def _build_payload(
        self, messages: list[dict], temperature: float, max_tokens: int, stream: bool
    ) -> dict:
        if self.backend == "ollama":
            return {
                "model": self.model,
                "messages": messages,
                "stream": stream,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            }
        return {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }

    def _post(self, payload: dict) -> Optional[dict]:
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self.url, data=data,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib_error.URLError as e:
            logger.warning(f"[LocalLLMBridge] connection failed: {e}")
        except json.JSONDecodeError as e:
            logger.warning(f"[LocalLLMBridge] bad JSON: {e}")
        return None

    def _extract_text(self, body: dict) -> str:
        if "message" in body:
            return body["message"].get("content", "")
        if "choices" in body:
            choices = body["choices"]
            if choices:
                return choices[0].get("message", {}).get("content", "")
        return ""

    def is_available(self) -> bool:
        ping = self.PING_URLS.get(self.backend, self.url.rsplit("/", 2)[0])
        try:
            with request.urlopen(ping, timeout=2.0):
                return True
        except Exception:
            return False


# ─────────────────────────────────────────────────────────────
#  ShiroPromptBuilder v3
# ─────────────────────────────────────────────────────────────

class ShiroPromptBuilder:
    """
    Builds the LLM system prompt from Shiro's live inner state + persona.

    v3 improvements:
      - Full persona integration (traits, speech style, interests, hard rules)
      - Memory summary injection
      - Conversation depth signal
      - Session arc from MoodJournal
      - Compact mode for small-context local models
    """

    def __init__(self, core: "ConsciousnessCore"):
        self.core = core

    def build(
        self,
        user_id: Optional[str] = None,
        compact: bool = False,           # Shorter prompt for small models
        include_memory: bool = True,
    ) -> str:
        c = self.core
        p = c.cfg.persona
        state = c.mind.state_summary()
        sections: list[str] = []

        # ── Persona ───────────────────────────────────────────────
        trait_str = "\n".join(f"  - {t}" for t in p.traits)
        sections.append(
            f"You are {p.name}.\n"
            f"{p.backstory}\n\n"
            f"Your character:\n{trait_str}"
        )

        if not compact:
            # Speech style
            style_str = "\n".join(f"  - {s}" for s in p.speech_style)
            sections.append(f"How you speak:\n{style_str}")

            # Interests
            if p.interests:
                int_str = ", ".join(p.interests)
                sections.append(f"Things you find genuinely interesting: {int_str}")

        # Hard rules (always included)
        rules_str = "\n".join(f"  - {r}" for r in p.hard_rules)
        sections.append(f"Rules:\n{rules_str}")

        # ── Inner state ───────────────────────────────────────────
        recent = state.get("recent_thoughts", [])
        thoughts_str = "\n".join(f"  - {t}" for t in recent[:3]) if recent else ""
        sections.append(
            f"YOUR CURRENT STATE:\n"
            f"  Mood: {state['mood']}  "
            f"(arousal {state['arousal']:.2f} — "
            f"{'alert' if state['arousal'] > 0.65 else 'calm' if state['arousal'] > 0.35 else 'low-energy'})\n"
            f"  Focus: \"{state.get('focus', '…')}\"\n"
            f"  Session arc: {state.get('mood_arc', 'just started')}"
            + (f"\n  Recent thoughts:\n{thoughts_str}" if thoughts_str else "")
        )

        # ── Environment ───────────────────────────────────────────
        sections.append(
            f"ENVIRONMENT:\n"
            f"  {c.aware.describe_environment()}\n"
            f"  {c.aware.describe_self()}"
        )

        # ── User context ──────────────────────────────────────────
        if user_id:
            profile = c.aware.users.get(user_id)
            if profile:
                session_desc = c.aware.describe_session(user_id)
                dom_emo = profile.dominant_emotion()
                depth = "deep" if profile.conversation_depth > 0.5 else "light"
                tier = profile.relationship_tier()
                trajectory = c.sentiment.describe(user_id) if hasattr(c, "sentiment") else ""
                timing_obs = c.timepattern.visit_observation(user_id) if hasattr(c, "timepattern") else ""

                user_section = (
                    f"WHO YOU'RE TALKING TO ({profile.name}):\n"
                    f"  {session_desc}\n"
                    f"  Relationship: {tier}\n"
                    f"  Emotional tone: {dom_emo or 'unknown'}"
                )
                if trajectory:
                    user_section += f" ({trajectory})"
                user_section += (
                    f"\n  Conversation style: {depth}\n"
                    f"  Speech cadence: {c.cadence.describe_model(user_id)}"
                )
                if timing_obs:
                    user_section += f"\n  Timing: {timing_obs}"
                if profile.mirror_vocab:
                    user_section += f"\n  They use: {', '.join(profile.mirror_vocab[:5])}"
                if profile.quirks and not compact:
                    user_section += f"\n  Quirks: {', '.join(profile.quirks)}"
                sections.append(user_section)

            # Memory injection
            if include_memory:
                recall = c.memory.recall(user_id)
                if recall:
                    sections.append(f"WHAT YOU REMEMBER:\n{recall}")

        # ── Intent direction ─────────────────────────────────────
        last_intent = getattr(self.core, "_last_intent", None)
        if last_intent and last_intent.prompt_hint:
            sections.append(
                f"RESPONSE DIRECTION (intent: {last_intent.name}, "
                f"confidence: {last_intent.confidence:.0%}):\n"
                f"  {last_intent.prompt_hint}"
            )

        # ── Behavior ──────────────────────────────────────────────
        if not compact:
            sections.append(
                "BEHAVIOR:\n"
                "  - Respond naturally, in character.\n"
                "  - Match the conversation's energy and length.\n"
                "  - Reference what you know about this person naturally.\n"
                "  - Your mood colors HOW you respond, not whether you help.\n"
                "  - Don't explain your inner state unless asked directly.\n"
                "  - Don't repeat the same opener twice."
            )
        else:
            sections.append(
                "Stay in character. Be natural. Match their energy."
            )

        return "\n\n".join(sections)

    def build_messages(
        self,
        user_id: str,
        current_message: str,
        max_history: int = 10,
    ) -> list[dict]:
        """
        Build the full messages array using ContextWindow.
        Respects token budget for local models.
        """
        system = self.build(user_id=user_id, include_memory=False)
        recall = self.core.memory.recall(user_id)

        recent = self.core.memory.recent_messages(user_id, n=max_history)
        # Always add the current message at end
        from .memory import Message
        recent_plus = recent + [Message(role="user", content=current_message, user_id=user_id)]

        window = ContextWindow(token_budget=self.core.cfg.llm_token_budget)
        msgs = window.build(
            system_prompt=system,
            recent_messages=recent_plus,
            memory_summary=recall,
            max_history=max_history,
        )
        return msgs


# ─────────────────────────────────────────────────────────────
#  ConsciousnessCore v3
# ─────────────────────────────────────────────────────────────

class ConsciousnessCore:
    """Shiro's full consciousness. v3."""

    def __init__(
        self,
        speak_callback: Callable[[SpeechEvent], Any],
        config: Optional[ShiroConfig] = None,
        llm_bridge: Optional[LocalLLMBridge] = None,
        **kwargs,
    ):
        self.cfg = config or ShiroConfig(**{
            k: v for k, v in kwargs.items()
            if k in ShiroConfig.__dataclass_fields__
        })

        # ── Event bus (created first — other modules subscribe to it) ──
        self.bus = EventBus()

        # ── Sub-modules ──────────────────────────────────────────
        self.mind = InnerMind(
            thought_tick_seconds=self.cfg.thought_tick_seconds,
            thought_callback=self._on_thought,
            enable_reactions=self.cfg.enable_reactions,
        )
        self.aware = SelfAwareness(
            name=self.cfg.name,
            platform=self.cfg.platform,
            room_name=self.cfg.room_name,
            can_see=self.cfg.can_see,
            can_hear=self.cfg.can_hear,
            silence_threshold_seconds=self.cfg.silence_threshold_seconds,
            event_bus=self.bus,
        )
        self.cadence   = SpeechCadence()
        self.memory    = ConversationMemory()
        self.intent    = IntentPlanner()
        self.sentiment = SentimentTrajectory()
        self.timepattern = TimePattern()
        self.rules     = RuleEngine()

        # Wire event bus subscriptions
        self.bus.on("emotion_detected",   self._on_emotion_event)
        self.bus.on("relationship_changed", self._on_tier_change)
        self.bus.on("topic_detected",     self._on_topic_event)

        async def _combined_speak(event: SpeechEvent):
            if self.cfg.on_speak:
                if asyncio.iscoroutinefunction(self.cfg.on_speak):
                    await self.cfg.on_speak(event)
                else:
                    self.cfg.on_speak(event)
            if asyncio.iscoroutinefunction(speak_callback):
                await speak_callback(event)
            else:
                speak_callback(event)

        self.voice = AutonomousVoice(
            speak_callback=_combined_speak,
            min_speak_gap_seconds=self.cfg.min_speak_gap_seconds,
            probe_schedule_seconds=self.cfg.probe_schedule_seconds,
        )

        self.llm: Optional[LocalLLMBridge] = llm_bridge
        self.prompt_builder = ShiroPromptBuilder(self)

        # ── State ────────────────────────────────────────────────
        self._last_user_message_ts: float = 0.0
        self._session_log: list[dict] = []
        self._booted: bool = False
        self._boot_ts: float = 0.0
        self._idle_task: Optional[asyncio.Task] = None
        self._save_task: Optional[asyncio.Task] = None
        self._last_intent: Optional[Intent] = None

        if self.cfg.memory_path:
            self._load_memory()

    # ── Lifecycle ────────────────────────────────────────────────

    async def boot(self):
        if self._booted:
            return
        self._booted = True
        self._boot_ts = time.time()
        await self.mind.start()
        self._idle_task = asyncio.create_task(self._idle_loop())
        if self.cfg.auto_save_interval_seconds > 0 and self.cfg.memory_path:
            self._save_task = asyncio.create_task(self._auto_save_loop())
        logger.info(f"[{self.cfg.name}] consciousness online")

    async def shutdown(self):
        self._booted = False
        await self.mind.stop()
        for task in (self._idle_task, self._save_task):
            if task:
                task.cancel()
        if self.cfg.memory_path:
            self.save_memory()
        logger.info(f"[{self.cfg.name}] consciousness offline")

    # ── Public API ───────────────────────────────────────────────

    async def user_entered(self, user_id: str, name: str = "", **meta):
        profile = self.aware.user_entered(user_id, name=name, **meta)
        self.memory.new_session(user_id)
        self.mind.set_mood(Mood.ALERT, arousal=0.75)
        self._log("entered", {"user_id": user_id, "name": name})

        if self.cfg.auto_greet:
            is_returning = profile.absence_count > 0
            asyncio.create_task(
                self.voice.greet_user(user_id, profile.name, is_returning=is_returning)
            )
        self.voice.schedule_probes(user_id, profile.name)

    async def user_left(self, user_id: str):
        self.voice.clear_probes(user_id)
        self.aware.user_left(user_id)
        self._log("left", {"user_id": user_id})
        if not self.aware.get_present_users():
            self.mind.set_mood(Mood.LONELY, arousal=0.35)

    async def receive_message(self, text: str, user_id: str, name: str = ""):
        now = time.time()
        self._last_user_message_ts = now

        profile = self.aware.user_spoke(user_id, text, name=name)
        self.cadence.observe(user_id, text)
        self.cadence.sync_mirror_vocab(user_id, profile.mirror_vocab)
        self.voice.learn_from_user(text, user_id)
        self.voice.clear_probes(user_id)

        # Sentiment trajectory
        current_emotions = profile.current_emotion_strength()
        self.sentiment.record(user_id, current_emotions)
        trajectory = self.sentiment.trend(user_id)

        # Time pattern
        self.timepattern.record_visit(user_id)

        # Add to conversation memory
        self.memory.add_user_message(
            user_id, text,
            emotions=current_emotions,
            topics=profile.known_topics,
        )

        # ── Mood contagion (scaled by relationship depth) ─────────
        tier = profile.relationship_tier()
        # Strangers affect Shiro's mood less; close friends more
        contagion_scale = {"stranger": 0.3, "acquaintance": 0.6,
                           "friend": 0.9, "close": 1.0}.get(tier, 0.6)

        dominant = max(current_emotions, key=current_emotions.get) if current_emotions else None
        mood_map = {
            "humor":    (Mood.AMUSED,     0.75),
            "excited":  (Mood.EXCITED,    0.85),
            "happy":    (Mood.WARM,       0.65),
            "curious":  (Mood.CURIOUS,    0.70),
            "sad":      (Mood.REFLECTIVE, 0.35),
            "angry":    (Mood.ALERT,      0.80),
            "thinking": (Mood.FOCUSED,    0.60),
            "tired":    (Mood.CONTENT,    0.35),
        }
        if dominant and dominant in mood_map and random.random() < contagion_scale:
            new_mood, base_arousal = mood_map[dominant]
            # Scale arousal shift by contagion
            arousal = 0.5 + (base_arousal - 0.5) * contagion_scale
            self.mind.set_mood(new_mood, arousal=arousal)
        else:
            self.mind.set_mood(Mood.ENGAGED, arousal=0.65 + 0.2 * contagion_scale)

        # ── Intent planning ───────────────────────────────────────
        has_question = "?" in text
        is_greeting  = bool(current_emotions.get("greeting", 0) > 0.5)
        planned_intent = self.intent.plan(
            user_message=text,
            user_emotions=current_emotions,
            mood=self.mind.mood.value,
            tier=tier,
            trajectory=trajectory,
            depth=profile.conversation_depth,
            valence=self.sentiment.current_valence(user_id),
            has_question=has_question,
            is_greeting=is_greeting,
        )
        self._last_intent: Optional[Intent] = planned_intent
        self.bus.emit("intent_planned",
                      user_id=user_id,
                      intent=planned_intent.name,
                      confidence=planned_intent.confidence)

        # ── Feed snippet into thought loop ────────────────────────
        snippet = text[:40].replace("'", "")
        self.mind.learn_thought("curious", f"{profile.name} said '{snippet}' — what's really in there")

        for topic in profile.top_topics(2):
            self.mind.learn_thought("reflective", f"i keep noticing {profile.name} returns to {topic}")

        if profile.is_familiar():
            self.mind.learn_thought("warm", f"i actually really like talking to {profile.name}")

        # Trajectory-aware thoughts
        if trajectory == "declining":
            self.mind.learn_thought("reflective",
                f"{profile.name} seems like they're having a harder time as we talk")
        elif trajectory == "improving":
            self.mind.learn_thought("warm",
                f"it feels like {profile.name} is getting a bit more comfortable")

        self.mind.update_context(self._build_context())

        # Update conversation count
        speakers = set(
            e["data"].get("user_id", "") for e in self._session_log
            if e["event"] == "received"
        )
        self.aware.self_knowledge["conversations_had"] = max(
            self.aware.self_knowledge["conversations_had"], len(speakers)
        )
        self._log("received", {"user_id": user_id, "text": text})

    async def speak_reply(
        self,
        text: str,
        user_id: Optional[str] = None,
        skip_delay: bool = False,
        inject_reaction: bool = True,
    ):
        """
        Speak a reply. Adapts to user's style + adds timing.
        Optionally prepends a micro-reaction based on current mood.
        """
        # Micro-reaction (optional)
        if inject_reaction and self.cfg.enable_reactions and user_id:
            reaction = self.mind.get_reaction()
            if reaction:
                text = f"{reaction} {text}"

        if user_id:
            text = self.cadence.adapt_text(text, user_id)
            if not skip_delay:
                delay = self.cadence.get_response_delay(user_id)
                await asyncio.sleep(delay)

        await self.voice.speak(text, "reply", user_id=user_id)
        self.memory.add_assistant_message(text, user_id=user_id)
        self._log("spoke", {"text": text, "user_id": user_id})

    async def speak_direct(self, text: str, speech_type: str = "reply", user_id: Optional[str] = None):
        await self.voice.speak(text, speech_type, user_id=user_id)

    # ── LLM integration ──────────────────────────────────────────

    async def generate_reply(
        self,
        user_message: str,
        user_id: str,
        extra_context: Optional[str] = None,
        speak: bool = True,
        stream_callback: Optional[Callable[[str], Any]] = None,
    ) -> str:
        """
        Full pipeline: build context-aware prompt → LLM → adapt → speak.

        If no LLM is configured, falls back to RuleEngine (template-based responses).
        stream_callback: if provided, tokens are passed here as they arrive.
        """
        if not self.llm:
            # RuleEngine fallback — fully functional without any backend
            return await self._rule_reply(user_message, user_id, speak=speak)

        messages = self.prompt_builder.build_messages(user_id, user_message)

        if extra_context:
            messages.insert(1, {"role": "system", "content": f"Additional context: {extra_context}"})

        if stream_callback:
            # Streaming mode
            full = ""
            async for token in self.llm.stream(messages):
                full += token
                if asyncio.iscoroutinefunction(stream_callback):
                    await stream_callback(token)
                else:
                    stream_callback(token)
            response = full
        else:
            response = await self.llm.chat(messages)

        if speak and response:
            await self.speak_reply(response, user_id=user_id, skip_delay=True)

        return response

    def get_system_prompt(self, user_id: Optional[str] = None, compact: bool = False) -> str:
        return self.prompt_builder.build(user_id=user_id, compact=compact)

    # ── Introspection ────────────────────────────────────────────

    def get_inner_state(self) -> dict:
        state = self.mind.state_summary()
        focus = self.aware.get_focus_user()
        return {
            **state,
            "environment":    self.aware.describe_environment(),
            "room_state":     self.aware.room_state,
            "self":           self.aware.describe_self(),
            "time_of_day":    self.aware.time_of_day(),
            "uptime_seconds": time.time() - self._boot_ts if self._boot_ts else 0,
            "current_intent": self._last_intent.name if self._last_intent else None,
            "sentiment_trend": self.sentiment.trend(focus.user_id) if focus else "unknown",
            "relationship_tier": focus.relationship_tier() if focus else "stranger",
            "users": {
                uid: {**p.to_dict(), "tier": p.relationship_tier()}
                for uid, p in self.aware.users.items()
                if p.present
            },
        }

    def get_intent(self) -> Optional[Intent]:
        """Get the most recently planned communicative intent."""
        return self._last_intent

    def get_sentiment_trend(self, user_id: str) -> str:
        """Get the emotional trajectory for a user: improving/declining/stable/volatile."""
        return self.sentiment.trend(user_id)

    def get_relationship_tier(self, user_id: str) -> str:
        """Get the named relationship tier for a user."""
        p = self.aware.users.get(user_id)
        return p.relationship_tier() if p else "stranger"

    def get_cadence_report(self, user_id: str) -> str:
        return self.cadence.describe_model(user_id)

    def get_memory_recall(self, user_id: str) -> str:
        """What does Shiro remember about this user?"""
        return self.memory.recall(user_id)

    # ── Teaching ─────────────────────────────────────────────────

    def teach_thought(self, category: str, template: str):
        self.mind.learn_thought(category, template)
        if self.cfg.on_learn:
            self.cfg.on_learn({"type": "thought", "category": category})

    def teach_phrase(self, category: str, phrase: str):
        self.voice.learn_phrase(category, phrase)
        if self.cfg.on_learn:
            self.cfg.on_learn({"type": "phrase", "category": category})

    def update_screen_context(self, data: dict):
        self.aware.update_screen_context(data)

    # ── Memory ───────────────────────────────────────────────────

    def save_memory(self, path: Optional[str] = None):
        """Atomic save."""
        target = Path(path or self.cfg.memory_path or "shiro_memory.json")
        data = {
            "inner_mind":   self.mind.export(),
            "awareness":    self.aware.export(),
            "cadence":      self.cadence.export(),
            "voice":        self.voice.export(),
            "memory":       self.memory.export(),
            "timepattern":  self.timepattern.export(),
            "saved_at":     time.time(),
            "version":      "4.0",
        }
        try:
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, default=str))
            tmp.replace(target)
            logger.debug(f"[{self.cfg.name}] saved → {target}")
        except Exception as e:
            logger.warning(f"[{self.cfg.name}] save failed: {e}")

    def _load_memory(self, path: Optional[str] = None):
        target = Path(path or self.cfg.memory_path)
        if not target.exists():
            return
        try:
            data = json.loads(target.read_text())
            self.mind.import_data(data.get("inner_mind", {}))
            self.aware.import_data(data.get("awareness", {}))
            self.cadence.import_data(data.get("cadence", {}))
            self.voice.import_data(data.get("voice", {}))
            self.memory.import_data(data.get("memory", {}))
            self.timepattern.import_data(data.get("timepattern", {}))
            logger.info(f"[{self.cfg.name}] loaded ← {target}")
        except Exception as e:
            logger.warning(f"[{self.cfg.name}] load failed: {e}")

    def export_memory(self) -> dict:
        return {
            "inner_mind":  self.mind.export(),
            "awareness":   self.aware.export(),
            "cadence":     self.cadence.export(),
            "voice":       self.voice.export(),
            "memory":      self.memory.export(),
            "timepattern": self.timepattern.export(),
        }

    def import_memory(self, data: dict):
        self.mind.import_data(data.get("inner_mind", {}))
        self.aware.import_data(data.get("awareness", {}))
        self.cadence.import_data(data.get("cadence", {}))
        self.voice.import_data(data.get("voice", {}))
        self.memory.import_data(data.get("memory", {}))
        self.timepattern.import_data(data.get("timepattern", {}))

    # ── Event handlers (wired in __init__) ──────────────────────

    def _on_emotion_event(self, user_id: str, emotion: str, strength: float, tier: str, **kw):
        """React to specific emotions detected in user messages."""
        # Strong sad/anxious from a close friend = more empathetic mood shift
        if emotion in ("sad", "anxious") and tier in ("friend", "close") and strength > 0.6:
            self.mind.set_mood(Mood.REFLECTIVE, arousal=0.3)
        # Humor is contagious regardless of tier (joy is social)
        elif emotion == "humor" and strength > 0.7:
            if self.mind.mood not in (Mood.REFLECTIVE, Mood.UNCERTAIN):
                self.mind.set_mood(Mood.AMUSED, arousal=0.7)

    def _on_tier_change(self, user_id: str, old_tier: str, new_tier: str, **kw):
        """React when a relationship tier changes."""
        profile = self.aware.users.get(user_id)
        name = profile.name if profile else user_id
        logger.info(f"[{self.cfg.name}] {name}: {old_tier} → {new_tier}")
        # Plant a thought about the new closeness
        tier_thoughts = {
            "acquaintance": f"i feel like i'm starting to get {name}",
            "friend":       f"i genuinely like {name}. that happened naturally.",
            "close":        f"{name} and i have something real going. i notice that.",
        }
        if new_tier in tier_thoughts:
            self.mind.learn_thought("warm", tier_thoughts[new_tier])
        # Warm mood shift
        if new_tier in ("friend", "close"):
            self.mind.set_mood(Mood.WARM, arousal=0.6)

    def _on_topic_event(self, user_id: str, topic: str, is_new: bool, **kw):
        """React to topic detections."""
        if is_new and topic in self.cfg.persona.interests:
            # User is talking about something Shiro cares about — get curious
            self.mind.set_mood(Mood.CURIOUS, arousal=0.7)
            self.mind.learn_thought("excited",
                f"wait — {topic} is one of the things i actually think about")

    # ── Rule-based reply (LLM fallback) ──────────────────────────

    async def _rule_reply(self, text: str, user_id: str, speak: bool = True) -> str:
        """
        Generate a reply using RuleEngine when no LLM is available.
        Produces short but authentic-feeling responses grounded in current state.
        """
        profile = self.aware.users.get(user_id)
        summary = self.memory.get_summary(user_id)
        intent_name = self._last_intent.name if self._last_intent else "share"
        trajectory = self.sentiment.trend(user_id)

        response = self.rules.full_response(
            intent=intent_name,
            mood=self.mind.mood.value,
            user_message=text,
            user_name=profile.name if profile else "",
            memory_facts=summary.key_facts[:3] if summary else None,
            trajectory=trajectory,
        )

        if speak and response:
            await self.speak_reply(response, user_id=user_id, skip_delay=False)
        return response

    # ── Internal ─────────────────────────────────────────────────

    def _on_thought(self, thought: Thought):
        self.mind.update_context(self._build_context())
        if self.cfg.on_thought:
            self.cfg.on_thought(thought)

    def _build_context(self) -> dict:
        focus = self.aware.get_focus_user()
        last_received = next(
            (e for e in reversed(self._session_log) if e.get("event") == "received"),
            None,
        )
        return {
            "focus_user":           focus.name if focus else None,
            "idle_ms":              (time.time() - self._last_user_message_ts) * 1000,
            "silent_users_present": bool(self.aware.get_silent_present_users()),
            "conversation_active":  self.aware.room_state == "active",
            "room_empty":           self.aware.room_state == "empty",
            "last_snippet":         (
                last_received["data"].get("text", "")[:40] if last_received else None
            ),
            "user_pattern": (
                self.aware.get_user_pattern_description(focus.user_id) if focus else "do their thing"
            ),
            "env_state":            self.aware.room_state,
            "humor_detected":       self.aware.is_humor_detected(),
            "high_energy":          self.aware.is_high_energy(),
            "complex_topic":        self.aware.is_complex_topic(),
            "familiar_user":        focus.is_familiar() if focus else False,
            "conversation_depth":   focus.conversation_depth if focus else 0.0,
            "relationship_tier":    focus.relationship_tier() if focus else "stranger",
            "sentiment_trajectory": self.sentiment.trend(focus.user_id) if focus else "unknown",
            "current_intent":       self._last_intent.name if self._last_intent else None,
        }

    async def _idle_loop(self):
        while self._booted:
            await asyncio.sleep(15.0 * random.uniform(0.8, 1.3))
            idle_s = time.time() - self._last_user_message_ts
            present = self.aware.get_present_users()

            if not present and idle_s > self.cfg.idle_initiate_seconds:
                await self.voice.mutter_idle()

            elif present and idle_s > self.cfg.idle_initiate_seconds * 0.8:
                target = random.choice(present)
                top_topics = target.top_topics(1)
                topic = top_topics[0] if top_topics and random.random() < 0.4 else None
                asyncio.create_task(
                    self.voice.initiate_conversation(target.user_id, topic=topic)
                )
                self._last_user_message_ts = time.time()

            # Memory recall: occasionally bring up something Shiro knows
            if (present
                    and self.cfg.enable_memory_recall
                    and idle_s > 30.0
                    and random.random() < 0.08):
                target = random.choice(present)
                summary = self.memory.get_summary(target.user_id)
                if summary and summary.key_facts:
                    fact = random.choice(summary.key_facts)
                    asyncio.create_task(
                        self.voice.speak_memory(target.user_id, fact)
                    )

    async def _auto_save_loop(self):
        while self._booted:
            await asyncio.sleep(self.cfg.auto_save_interval_seconds)
            self.save_memory()

    def _log(self, event: str, data: dict):
        self._session_log.append({"event": event, "data": data, "ts": time.time()})
        if len(self._session_log) > 600:
            self._session_log = self._session_log[-500:]
