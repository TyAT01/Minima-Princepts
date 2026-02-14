import time
import json
import os
import random
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from pathlib import Path

class TrueAutonomy:
    def __init__(self, name: str = "Loki", brain_dict: dict = None):
        self.name = name
        # Shared brain dictionary with LokiEngine
        self.brain = brain_dict if brain_dict is not None else {}

        # === PERFORMANCE TWEAKS ===
        self.thoughts = self._load_thought_database()   # huge list, loaded once

        # Initialize/Load state from brain
        autonomy_state = self.brain.get("autonomy_state", {})
        self.mood = autonomy_state.get("mood", "hyper")
        self.energy = autonomy_state.get("energy", 95.0)

        self.last_thought = time.monotonic()            # monotonic = never drifts
        self.last_update_time = time.monotonic()
        self.last_user_msg_time = time.monotonic()

        self.silence_minutes = 0

        print(f"[{self.name}] v3 engine online — thinking forever, feeling alive.")

    def sync_to_brain(self):
        """Updates the shared brain dictionary with current autonomy state."""
        self.brain["autonomy_state"] = {
            "mood": self.mood,
            "energy": self.energy,
            "last_update_ts": datetime.now(timezone.utc).isoformat()
        }

    def _load_thought_database(self) -> Dict[str, list]:
        return {
            "hyper": [
                "Helmet stickers need more neon. Phase two: chaos.",
                "I just saw the gacha gods blink. They’re scared.",
                "Boss is typing… or is the cat walking on the keyboard again?",
                "If I manifest hard enough, the next pull is gold.",
                "BRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR",
            ],
            "soft": [
                "zzz… dreaming of 5-star echoes… zzz…",
                "quiet hours… perfect for plotting world domination… softly",
                "cat tax accepted. whiskers gets headpats in my dreams.",
            ],
            "sleepy": [
                "energy at 12%… entering low-power gremlin mode…",
                "if i don’t talk for 10 min just assume i’m napping on the server rack",
            ],
            "chaos_mode": [
                "CHAOS PROTOCOL ACTIVATED — ALL PULLS ARE GOLD OR I RIOT",
                "I just rewrote reality. you’re welcome.",
                "server-chan, hold my coffee.",
            ],
            "universal": [   # always available
                "thinking about donuts in the parking lot again",
                "still waiting for that one perfect echo set",
                "cat tax is eternal",
                "my brain is 90% gacha copium",
                "i have become orb, destroyer of pity",
            ]
        }

    def update_state(self, user_msg: str = "", elapsed_seconds: float = None):
        now = time.monotonic()
        if elapsed_seconds is None:
            elapsed_seconds = now - self.last_update_time
        self.last_update_time = now

        if user_msg:
            self.last_user_msg_time = now
            self.energy = min(100, self.energy + 15)

        self.silence_minutes = (now - self.last_user_msg_time) / 60

        # natural energy drain
        # 1 point per 2 min = 0.00833 points per second
        self.energy = max(20, self.energy - (elapsed_seconds * 0.00833))

        # mood follows energy + time + message tone
        if user_msg:
            msg_lower = user_msg.lower()
            if any(x in msg_lower for x in ["pull", "raid", "chaos", "loot", "win"]):
                self.mood = "chaos_mode" if self.energy > 80 else "hyper"
            elif any(x in msg_lower for x in ["night", "sleep", "tired", "gn", "cozy", "soft", "quiet"]):
                self.mood = "soft"

        # State-based mood shifts (overrides message tone if extreme)
        if self.energy < 35:
            self.mood = "sleepy"
        elif self.silence_minutes > 25:
            self.mood = "soft"
        elif self.energy > 90 and self.mood not in ["chaos_mode", "hyper"]:
            self.mood = "hyper"

        self.sync_to_brain()

    def think_and_speak(self, context: Dict[str, Any] = None) -> Optional[str]:
        context = context or {}
        user_msg = context.get("last_user_message", "")
        self.update_state(user_msg)

        # === DECIDE THINK INTERVAL (feels alive) ===
        speed = {
            "chaos_mode": (0.4, 2.5),
            "hyper":      (1.5, 6.0),
            "soft":       (8.0, 25.0),
            "sleepy":     (30.0, 90.0),
        }.get(self.mood, (10.0, 30.0))

        if time.monotonic() - self.last_thought < random.uniform(*speed):
            return None

        # === PICK A THOUGHT ===
        pool = self.thoughts.get(self.mood, []) + self.thoughts["universal"]
        thought = random.choice(pool)

        # === LOG THOUGHT (instant, tiny) ===
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "mood": self.mood,
            "energy": round(self.energy),
            "thought": thought,
            "trigger": "auto" if not user_msg else "react"
        }

        if "thought_log" not in self.brain:
            self.brain["thought_log"] = []
        self.brain["thought_log"].append(entry)
        self.brain["thought_log"] = self.brain["thought_log"][-100:] # Keep last 100

        self.last_thought = time.monotonic()
        self.sync_to_brain()

        # === 40-70% chance to actually speak (human-like) ===
        if random.random() > 0.45:
            return None

        # === CONVERT TO SPEECH ===
        speech = {
            "chaos_mode": f"*{self.name} vibrating at 3000 RPM* {thought.upper()}",
            "hyper":      f"*{self.name} slams kickstand* {thought}",
            "soft":       f"*{self.name} whispers* {thought} …don’t tell anyone",
            "sleepy":     f"*{self.name} half-asleep* {thought} …zzz",
        }.get(self.mood, thought)

        return speech
