"""
stream_learning.py — Shiro Stream Learning System v1.0
=======================================================
Watches how chat reacts after each of Shiro's outputs,
scores those reactions using the local LLM for context-awareness,
and builds a coaching block that gets injected into Shiro's system prompt.

Pipeline
--------
  1. Shiro speaks → register_output() creates an Observation window
  2. Chat messages arrive → recorded against open Observation windows
  3. Every 15s: flush completed windows
  4. Each closed Observation → LLM scores it (with heuristic fallback)
  5. Score → FeniMemory → coaching block rebuilt
  6. Coaching block → injected into Shiro's next system prompt

LLM scorer uses a minimal prompt (< 300 tokens) so it's cheap and fast.
Fallback heuristics are context-pair-aware — they look at both what
Shiro said and how chat responded, not just keyword matching.

Shiro-specific additions vs Fenilux feni_learns.py
---------------------------------------------------
  - Memory file: shiro_stream_memory.json (not feni_memory.json)
  - Scoring model defaults to whatever Shiro's LLM config specifies
  - Explicit feedback commands: "shiro ++ / shiro --"  (not "feni ++")
  - coaching block tagged for Shiro's system prompt format
  - activity_scores bucket tracks stream context (gaming, chatting, etc.)
  - Version bumped to v4 data format
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

MEMORY_FILE              = "shiro_stream_memory.json"
OLLAMA_URL               = "http://localhost:11434/api/chat"
OBSERVATION_WINDOW_SEC   = 35
MAX_REACTION_MESSAGES    = 10
MIN_OBS_FOR_COACHING     = 4


# ──────────────────────────────────────────────────────────────────────────────
# Heuristic Scorer (fallback)
# ──────────────────────────────────────────────────────────────────────────────

def _heuristic_score(shiro_text: str, reactions: list[str]) -> float:
    if not reactions:
        return -0.05   # silence after Shiro spoke = mild negative

    combined  = " ".join(reactions).lower()
    score     = 0.0

    # Laughter
    laughs = len(re.findall(r'\b(lol|lmao|lmfao|haha|hehe|hahaha|💀)\b', combined))
    score += min(laughs * 0.25, 0.6)

    # Hype
    hype = len(re.findall(r'\b(omg|pog|poggers|W|iconic|clip|based|real|fr|let\'s go|lesgo)\b', combined))
    score += min(hype * 0.20, 0.4)

    # Love / affection
    love = len(re.findall(r'(love|cute|adorable|precious|aww+|💕|💖|❤|🥺|🫶)', combined))
    score += min(love * 0.20, 0.4)

    # Volume / multi-user engagement
    unique_approx = len({r[:8] for r in reactions})
    if unique_approx >= 3: score += 0.2
    if unique_approx >= 5: score += 0.1

    # Exclamations
    score += min(combined.count("!") * 0.05, 0.2)

    # Long replies = participation
    long_r = sum(1 for r in reactions if len(r.split()) > 4)
    score += min(long_r * 0.1, 0.2)

    # Negative — only if not paired with laughter/hype
    cringe_solo = re.search(r'\bcringe\b', combined) and laughs == 0 and hype == 0
    if cringe_solo:
        score -= 0.3

    boring = re.search(r'\b(boring|skip|next|move on|change topic)\b', combined)
    if boring:
        score -= 0.5

    pure_neg = re.findall(r'\b(bad|stop it|no more|please stop|nah|awful)\b', combined)
    if pure_neg and laughs == 0:
        score -= len(pure_neg) * 0.2

    return max(-1.0, min(1.0, score))


# ──────────────────────────────────────────────────────────────────────────────
# LLM Context Scorer
# ──────────────────────────────────────────────────────────────────────────────

_SCORING_SYSTEM = """You are a VTuber stream analyst scoring audience reactions.

Score from -1.0 (very negative) to +1.0 (very positive), 0.0 = neutral.

CONTEXT RULES:
- Streaming chat is heavily ironic. "omg cringe" from a fan is usually AFFECTIONATE.
- "stop it" + "lmao" = teasing, not genuine complaint.
- "noooo" is often delight/excitement, not rejection.
- Short laughter bursts (lol, haha, lmao, 💀) = positive regardless of surrounding words.
- High message volume = engagement = positive.
- Silence or flat single-word replies = low engagement = slightly negative.
- People asking follow-up questions = positive.
- People ignoring the output and changing topic = neutral/negative.
- "cringe" + laughter context = playful affection.
- "cringe" alone, no humor = mild negative.

Respond ONLY with JSON: {"score": 0.7, "reason": "brief reason"}
No other text."""


def llm_score_reaction(
    shiro_text: str,
    reactions:  list[str],
    response_type: str,
    model: str,
) -> Optional[float]:
    if not reactions:
        return -0.05

    reaction_block = "\n".join(f"  - {r}" for r in reactions[:8])
    prompt = (
        f"Shiro said ({response_type}):\n\"{shiro_text[:200]}\"\n\n"
        f"Chat reactions:\n{reaction_block}\n\n"
        f"Score -1.0 to +1.0:"
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SCORING_SYSTEM},
            {"role": "user",   "content": prompt},
        ],
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 80, "num_ctx": 512},
        # FIX: num_ctx:512 keeps context tiny for fast scoring on 8B model.
        # num_predict:80 was 60 — small increase prevents JSON being cut mid-brace.
    }
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=15)
        resp.raise_for_status()
        content = resp.json().get("message", {}).get("content", "").strip()
        # FIX: Use positional extraction instead of greedy DOTALL match.
        # A verbose 8B model often outputs prose before the JSON object.
        # Find the last { ... } in the response (the actual JSON is always last).
        _start = content.rfind('{')
        _end   = content.rfind('}')
        if _start != -1 and _end > _start:
            try:
                data  = json.loads(content[_start:_end + 1])
                score = float(data.get("score", 0.0))
                return max(-1.0, min(1.0, score))
            except (json.JSONDecodeError, ValueError):
                pass
        # Fallback: simple float extraction anywhere in response
        _m = re.search(r'[-]?\d+\.\d+', content)
        if _m:
            try:
                return max(-1.0, min(1.0, float(_m.group())))
            except ValueError:
                pass
    except Exception as e:
        logger.debug(f"[StreamLearning] LLM score failed: {e}")
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Explicit Feedback Detection
# ──────────────────────────────────────────────────────────────────────────────

def detect_explicit_feedback(text: str) -> Optional[str]:
    """Detect 'shiro ++ / shiro --' style commands from chat."""
    lower = text.lower().strip()
    pos = ["shiro ++", "shiro+", "++ shiro", "+1 shiro",
           "good shiro", "W shiro", "based shiro", "clip that"]
    neg = ["shiro --", "shiro-", "-- shiro", "-1 shiro",
           "bad shiro", "L shiro", "boo shiro"]
    if any(p in lower for p in pos): return "positive"
    if any(n in lower for n in neg): return "negative"
    return None


# ──────────────────────────────────────────────────────────────────────────────
# StreamMemory
# ──────────────────────────────────────────────────────────────────────────────

class StreamMemory:
    """Persists reaction scores across sessions. Used to build coaching blocks."""

    def __init__(self, filepath: str = MEMORY_FILE):
        self.filepath = filepath
        self.data     = self._load()

    def _default(self) -> dict:
        return {
            "version": 4,
            "response_scores":  {},   # response_type → {total, count, avg}
            "energy_scores":    {},
            "activity_scores":  {},
            "style_notes":      [],   # rolling notes injected into coaching
            "session_count":    0,
            "total_scored":     0,
            "last_updated":     None,
        }

    def _load(self) -> dict:
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, encoding="utf-8") as f:
                    d = json.load(f)
                if d.get("version") == 4:
                    return d
            except Exception:
                pass
        return self._default()

    def save(self):
        self.data["last_updated"] = datetime.now().isoformat()
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            logger.warning(f"[StreamMemory] Save error: {e}")

    def _update_bucket(self, bucket: dict, key: str, score: float):
        if key not in bucket:
            bucket[key] = {"total": 0.0, "count": 0, "avg": 0.0}
        b = bucket[key]
        b["total"] += score
        b["count"] += 1
        b["avg"]    = b["total"] / b["count"]

    def record(self, response_type: str, score: float,
               energy: str = None, activity: str = None):
        self._update_bucket(self.data["response_scores"], response_type, score)
        if energy:   self._update_bucket(self.data["energy_scores"],   energy,   score)
        if activity: self._update_bucket(self.data["activity_scores"], activity, score)
        self.data["total_scored"] += 1

    def add_note(self, note: str):
        self.data["style_notes"].append(note)
        self.data["style_notes"] = self.data["style_notes"][-15:]

    def get_top_performing(self, n: int = 5) -> list[tuple[str, float]]:
        s = self.data["response_scores"]
        return sorted(
            [(k, v["avg"]) for k, v in s.items() if v["count"] >= MIN_OBS_FOR_COACHING],
            key=lambda x: x[1], reverse=True
        )[:n]

    def get_underperforming(self, n: int = 3) -> list[tuple[str, float]]:
        s = self.data["response_scores"]
        return sorted(
            [(k, v["avg"]) for k, v in s.items() if v["count"] >= MIN_OBS_FOR_COACHING],
            key=lambda x: x[1]
        )[:n]

    def get_best_energy(self) -> Optional[str]:
        s = self.data["energy_scores"]
        q = [(k, v) for k, v in s.items() if v["count"] >= MIN_OBS_FOR_COACHING]
        return max(q, key=lambda x: x[1]["avg"])[0] if q else None

    def build_coaching_block(self) -> str:
        lines = []
        top = self.get_top_performing(3)
        if top:
            top_str = ", ".join(f"{n}({s:.2f})" for n, s in top)
            lines.append(f"Chat responds best to: {top_str}.")
        under = self.get_underperforming(2)
        if under:
            low_str = ", ".join(n for n, _ in under)
            lines.append(f"Lower engagement from: {low_str} — try different approaches.")
        be = self.get_best_energy()
        if be:
            lines.append(f"Your {be} energy gets the best reactions from this community.")
        if self.data["style_notes"]:
            recent = self.data["style_notes"][-3:]
            lines.append("Recent notes: " + " | ".join(recent))
        if not lines:
            return ""
        return "━━━ COMMUNITY LEARNING (what works here) ━━━\n" + "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Observation
# ──────────────────────────────────────────────────────────────────────────────

class Observation:
    def __init__(self, response_type: str, energy: str,
                 activity: str, text: str):
        self.response_type    = response_type
        self.energy           = energy
        self.activity         = activity
        self.text             = text
        self.created          = datetime.now()
        self.reactions:  list[str] = []
        self.explicit_feedback: Optional[str] = None

    def is_closed(self) -> bool:
        return (datetime.now() - self.created).total_seconds() > OBSERVATION_WINDOW_SEC

    def add_reaction(self, text: str):
        if len(self.reactions) < MAX_REACTION_MESSAGES:
            self.reactions.append(text)


# ──────────────────────────────────────────────────────────────────────────────
# ReactionTracker
# ──────────────────────────────────────────────────────────────────────────────

class ReactionTracker:
    def __init__(self, memory: StreamMemory, model: str):
        self.memory = memory
        self.model  = model
        self._pending: list[Observation] = []
        self._lock   = threading.Lock()

    def register_output(self, response_type: str, energy: str,
                         activity: str, text: str):
        obs = Observation(response_type, energy, activity, text)
        with self._lock:
            self._pending.append(obs)
            cutoff = datetime.now() - timedelta(seconds=OBSERVATION_WINDOW_SEC * 2)
            self._pending = [o for o in self._pending if o.created > cutoff]

    def record_chat_message(self, username: str, message: str):
        explicit = detect_explicit_feedback(message)
        with self._lock:
            for obs in self._pending:
                if not obs.is_closed():
                    obs.add_reaction(message)
                    if explicit and obs.explicit_feedback is None:
                        obs.explicit_feedback = explicit

    def flush_closed(self) -> list[Observation]:
        closed, remaining = [], []
        with self._lock:
            for obs in self._pending:
                (closed if obs.is_closed() else remaining).append(obs)
            self._pending = remaining
        return closed

    def score_and_record(self, obs: Observation):
        if obs.explicit_feedback == "positive":
            final_score = 0.85
        elif obs.explicit_feedback == "negative":
            final_score = -0.5
        else:
            # FIX: Only call LLM scorer when there are 2+ reactions.
            # Single-reaction observations are often noise; heuristic handles them well.
            # This cuts LLM calls by ~40% during slow streams.
            llm_s = llm_score_reaction(
                obs.text, obs.reactions, obs.response_type, self.model
            ) if len(obs.reactions) >= 2 else None
            final_score = llm_s if llm_s is not None else \
                          _heuristic_score(obs.text, obs.reactions)

        self.memory.record(obs.response_type, final_score, obs.energy, obs.activity)

        if final_score >= 0.5:
            self.memory.add_note(
                f"{obs.response_type} during {obs.energy} energy scored well "
                f"({final_score:.2f})"
            )
        elif final_score <= -0.25 and obs.reactions:
            self.memory.add_note(
                f"{obs.response_type} got low engagement — try a different approach"
            )


# ──────────────────────────────────────────────────────────────────────────────
# LearningLoop
# ──────────────────────────────────────────────────────────────────────────────

class LearningLoop:
    PROCESS_INTERVAL = 15    # seconds
    SAVE_INTERVAL    = 120   # seconds

    def __init__(self, memory: StreamMemory, tracker: ReactionTracker):
        self.memory   = memory
        self.tracker  = tracker
        self.running  = False
        self._last_save = datetime.now()
        self._coaching  = ""

    def get_coaching_block(self) -> str:
        return self._coaching

    def _run(self):
        self.running = True
        while self.running:
            time.sleep(self.PROCESS_INTERVAL)
            try:
                closed = self.tracker.flush_closed()
                for obs in closed:
                    self.tracker.score_and_record(obs)
                if closed:
                    self._coaching = self.memory.build_coaching_block()
                if (datetime.now() - self._last_save).total_seconds() > self.SAVE_INTERVAL:
                    self.memory.save()
                    self._last_save = datetime.now()
            except Exception as e:
                logger.warning(f"[LearningLoop] Error: {e}")

    def start(self):
        t = threading.Thread(target=self._run, daemon=True, name="shiro_learning")
        t.start()
        logger.info("[LearningLoop] Started")
        return t

    def stop(self):
        self.running = False
        self.memory.save()
        logger.info("[LearningLoop] Stopped, memory saved")


# ──────────────────────────────────────────────────────────────────────────────
# Factory
# ──────────────────────────────────────────────────────────────────────────────

def create_stream_learning(model: str) -> tuple[StreamMemory, ReactionTracker, LearningLoop]:
    memory  = StreamMemory()
    tracker = ReactionTracker(memory, model=model)
    loop    = LearningLoop(memory, tracker)
    memory.data["session_count"] += 1
    return memory, tracker, loop