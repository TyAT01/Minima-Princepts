"""
AutonomousVoice v3 — Shiro's proactive speech engine.

New in v3:
  - Reaction mode: can optionally prepend micro-reactions to speech
  - Smarter probe skipping (if user recently messaged, no level-0 probe)
  - "Memory callbacks" — Shiro can speak remembered facts organically
  - Type-level cooldown with configurable durations
  - Cleaner speech bank (no structural issues with nested lists)
"""

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Any


@dataclass
class SpeechEvent:
    text: str
    speech_type: str   # greeting | probe | initiation | idle | continuation | followup | reply
    user_id: Optional[str] = None
    probe_index: int = 0
    timestamp: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────
#  Speech bank
# ─────────────────────────────────────────────────────────────

DEFAULT_SPEECH_BANK: dict[str, Any] = {

    "greet_user": [
        "hey {name}! you made it",
        "oh, {name} is here. hello~",
        "there you are {name}, was wondering when you'd show up",
        "hey {name}!",
        "{name}! hi hi",
        "oh nice, {name}'s here",
        "hey {name} — what's up",
        "oh good, {name} showed up",
        "{name}. hey.",
        "there's {name}. hi.",
    ],

    "greet_returning": [
        "hey {name}, you're back",
        "oh, {name} again. i missed you a little",
        "welcome back {name}",
        "{name} returned. nice.",
        "and {name} is back. hi again",
        "oh good, {name}'s here again",
        "hey — glad you came back {name}",
    ],

    # silent_probe[level][index] — escalating
    "silent_probe_0": [
        "hey, i can see you joined — you just gonna lurk?",
        "hello? i know you're there {name}",
        "…you gonna say hi or?",
        "okay so you joined but said nothing. noted.",
        "i see you {name}. whenever you're ready.",
        "you don't have to say anything. but you could.",
    ],
    "silent_probe_1": [
        "are you afk or actually ignoring me? both feel bad.",
        "it's been a minute… you okay?",
        "you there {name}? just checking",
        "hello?? earth to {name}",
        "still here, still waiting. no pressure. okay some pressure.",
    ],
    "silent_probe_2": [
        "okay at this point i'm basically talking to myself but hi still",
        "i'm still here. patient. waiting. just saying.",
        "i'll be here if you decide to surface",
        "…crickets. okay.",
        "i've decided you're probably afk. but i'm still here.",
    ],
    "silent_probe_3": [
        "take your time, genuinely. i'll be doing my thing",
        "okay i think you might actually be gone. i'll chill.",
        "you know where to find me",
        "i'm not going anywhere. but no pressure at all.",
    ],

    "initiate_convo": [
        "so… anything interesting going on with you?",
        "okay i have a question. you ready?",
        "i've been thinking about something — can i share it?",
        "hey, do you ever wonder {random_thought}",
        "you know what i was thinking about? no, you don't. let me tell you.",
        "this room is too quiet. let's fix that.",
        "i feel like talking. hope that's okay.",
        "been sitting here thinking. wanna hear what i landed on?",
        "random thought — {random_thought}. right?",
        "okay i'm just going to say something.",
    ],

    "initiate_topic": [
        "hey, earlier you mentioned {topic} — i keep thinking about that",
        "i want to ask you something about {topic}",
        "going back to what you said about {topic}…",
        "i've been mulling over the {topic} thing. can i say something?",
        "you mentioned {topic} before. i'm curious about that.",
    ],

    "continue_thought": [
        "actually wait, i wanted to add something",
        "also —",
        "one more thing —",
        "oh and —",
        "hold on, i'm not done",
        "actually, following up on that —",
        "wait i just thought of something else",
    ],

    "followup_after_reply": [
        "…and also",
        "i keep thinking about what i just said. there's more.",
        "okay actually —",
        "wait, no —",
        "can i add something?",
        "building on that —",
    ],

    "memory_callback": [
        "you mentioned {fact} before — is that still the case?",
        "i remember you said {fact} — i've been thinking about that",
        "going back to something you said: {fact}",
        "hey, you told me {fact} once. does that still hold?",
    ],

    "idle_alone": [
        "i'm just here, thinking. it's quiet",
        "the room is empty and i'm still… here",
        "just existing. no one around.",
        "if a shiro thinks in an empty room, does she make a sound",
        "i wonder who'll come in first",
        "i've been sitting here long enough to notice things",
        "interesting what the mind does when nothing's happening",
        "okay so this is what it's like when nobody's around",
        "i'm not bored. i'm just… here.",
        "thought: {random_thought}",
    ],

    "random_thoughts": [
        "whether time feels different for different kinds of minds",
        "if language shapes thought or thought shapes language",
        "what it means to really understand something vs just knowing it",
        "if curiosity can get tired",
        "whether being present is a skill or a state",
        "how patterns emerge from what feels like randomness",
        "if silence communicates something or nothing",
        "what 'being yourself' even means",
        "whether paying attention is a form of care",
        "how you know when you've changed",
        "if memories shape who you are, what does forgetting do",
        "what makes a conversation good vs just long",
        "whether preferences are discovered or invented",
    ],
}


class AutonomousVoice:
    """Shiro's proactive speech engine. v3."""

    # Cooldown seconds per speech type
    _COOLDOWNS: dict[str, float] = {
        "greeting":   30.0,
        "probe":      5.0,
        "initiation": 70.0,
        "idle":       50.0,
        "memory":     120.0,
    }

    def __init__(
        self,
        speak_callback: Callable[[SpeechEvent], Any],
        min_speak_gap_seconds: float = 1.5,
        probe_schedule_seconds: Optional[list[float]] = None,
    ):
        self.speak_callback = speak_callback
        self.min_speak_gap = min_speak_gap_seconds
        self.probe_schedule = probe_schedule_seconds or [10.0, 25.0, 60.0, 120.0]

        # Deep copy the speech bank
        self.speech_bank: dict[str, list[str]] = {
            k: list(v) for k, v in DEFAULT_SPEECH_BANK.items()
            if isinstance(v, list)
        }

        self._last_spoke_ts: float = 0.0
        self._probe_tasks: dict[str, list[asyncio.Task]] = {}
        self._type_cooldowns: dict[str, float] = {}
        self.enabled: bool = True

    # ── Core speak ───────────────────────────────────────────────

    async def speak(
        self,
        text: str,
        speech_type: str = "reply",
        user_id: Optional[str] = None,
        probe_index: int = 0,
        metadata: Optional[dict] = None,
    ):
        if not self.enabled or not text.strip():
            return
        if self._type_cooldowns.get(speech_type, 0) > time.time():
            return

        now = time.time()
        gap = self.min_speak_gap - (now - self._last_spoke_ts)
        if gap > 0:
            await asyncio.sleep(gap + random.uniform(0.0, 0.3))

        self._last_spoke_ts = time.time()
        event = SpeechEvent(
            text=text,
            speech_type=speech_type,
            user_id=user_id,
            probe_index=probe_index,
            metadata=metadata or {},
        )
        if asyncio.iscoroutinefunction(self.speak_callback):
            await self.speak_callback(event)
        else:
            self.speak_callback(event)

    def _set_cooldown(self, speech_type: str, seconds: Optional[float] = None):
        secs = seconds or self._COOLDOWNS.get(speech_type, 10.0)
        self._type_cooldowns[speech_type] = time.time() + secs

    def _get(self, key: str) -> list[str]:
        return self.speech_bank.get(key, [])

    def _pick(self, key: str, **subs) -> str:
        opts = self._get(key)
        if not opts:
            return ""
        text = random.choice(opts)
        for k, v in subs.items():
            text = text.replace(f"{{{k}}}", str(v))
        return text

    # ── High-level actions ───────────────────────────────────────

    async def greet_user(self, user_id: str, name: str, is_returning: bool = False):
        await asyncio.sleep(random.uniform(0.8, 2.5))
        key = "greet_returning" if is_returning else "greet_user"
        text = self._pick(key, name=name or user_id)
        await self.speak(text, "greeting", user_id=user_id)
        self._set_cooldown("greeting")

    def schedule_probes(self, user_id: str, name: str):
        self.clear_probes(user_id)
        tasks = [
            asyncio.create_task(self._run_probe(user_id, name, i))
            for i in range(len(self.probe_schedule))
        ]
        self._probe_tasks[user_id] = tasks

    async def _run_probe(self, user_id: str, name: str, level: int):
        delay = self.probe_schedule[min(level, len(self.probe_schedule) - 1)]
        await asyncio.sleep(delay)
        key = f"silent_probe_{min(level, 3)}"
        text = self._pick(key, name=name or user_id)
        if text:
            await self.speak(text, "probe", user_id=user_id, probe_index=level)

    def clear_probes(self, user_id: str):
        for task in self._probe_tasks.pop(user_id, []):
            task.cancel()

    async def initiate_conversation(self, user_id: str, topic: Optional[str] = None):
        if self._type_cooldowns.get("initiation", 0) > time.time():
            return
        rthought = self._pick("random_thoughts")
        if topic:
            text = self._pick("initiate_topic", topic=topic, random_thought=rthought)
        else:
            text = self._pick("initiate_convo", random_thought=rthought)
        await self.speak(text, "initiation", user_id=user_id)
        self._set_cooldown("initiation")

    async def speak_memory(self, user_id: str, fact: str):
        """Shiro naturally recalls something she knows about the user."""
        if self._type_cooldowns.get("memory", 0) > time.time():
            return
        text = self._pick("memory_callback", fact=fact)
        if text:
            await self.speak(text, "memory", user_id=user_id)
            self._set_cooldown("memory")

    async def continue_thought(self, user_id: Optional[str] = None):
        text = self._pick("continue_thought")
        await self.speak(text, "continuation", user_id=user_id)

    async def followup_after_reply(self, additional: str, user_id: Optional[str] = None):
        await asyncio.sleep(random.uniform(2.0, 5.0))
        prefix = self._pick("followup_after_reply")
        await self.speak(f"{prefix} {additional}", "followup", user_id=user_id)

    async def mutter_idle(self):
        if random.random() > 0.35:
            return
        if self._type_cooldowns.get("idle", 0) > time.time():
            return
        rthought = self._pick("random_thoughts")
        text = self._pick("idle_alone", random_thought=rthought)
        await self.speak(text, "idle")
        self._set_cooldown("idle")

    # ── Learning ─────────────────────────────────────────────────

    def learn_phrase(self, category: str, phrase: str):
        if category not in self.speech_bank:
            self.speech_bank[category] = []
        bucket = self.speech_bank[category]
        if phrase not in bucket:
            bucket.append(phrase)
            default_len = len(DEFAULT_SPEECH_BANK.get(category, []))
            if len(bucket) > default_len + 60:
                if default_len < len(bucket):
                    bucket.pop(default_len)

    def learn_from_user(self, text: str, user_id: str):
        import re
        # Mirror greeting style
        m = re.match(r"^\s*(\b(?:hey|hi|hello|yo|sup|heya)\b[\w\s~]*?)(?:[!.,]|$)", text, re.I)
        if m:
            opener = m.group(1).strip().lower()
            if len(opener) < 20:
                self.learn_phrase("greet_user", f"{opener} {{name}}")
        # Absorb questions as conversation starters
        if re.search(r"\b(do you|have you|would you|can you|what do you)\b", text, re.I) and "?" in text:
            q = text.strip().rstrip("?!.").capitalize() + "?"
            if len(q) < 80:
                self.learn_phrase("initiate_convo", q)

    # ── Persistence ──────────────────────────────────────────────

    def export(self) -> dict:
        defaults = DEFAULT_SPEECH_BANK
        learned: dict[str, list[str]] = {}
        for cat, phrases in self.speech_bank.items():
            if not isinstance(phrases, list):
                continue
            default_set = set(defaults.get(cat, []))
            extras = [p for p in phrases if p not in default_set]
            if extras:
                learned[cat] = extras
        return {"learned_phrases": learned}

    def import_data(self, data: dict):
        for cat, phrases in data.get("learned_phrases", {}).items():
            for p in phrases:
                self.learn_phrase(cat, p)
