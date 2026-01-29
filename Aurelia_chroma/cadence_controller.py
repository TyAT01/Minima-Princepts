from __future__ import annotations

import time
import math
import random
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple


# -----------------------------
# Data shapes
# -----------------------------

@dataclass
class ChatMessage:
    user: str
    text: str
    ts: float
    source: str = "unknown"
    is_high_signal: bool = False
    sentiment_hint: Optional[str] = None


@dataclass
class StreamSignals:
    """
    Feed this each tick.
    """
    now: float
    chat_messages: List[ChatMessage] = field(default_factory=list)

    # 0..1: how intense the current stream moment is
    event_intensity: float = 0.0

    # 0..1: how focused Aurelia should be (higher => more silence)
    focus_level: float = 0.0

    # 0..1: voice activity detection
    voice_room_activity: float = 0.0

    # optional: if she is "mid-sentence" or TTS still playing
    tts_busy: bool = False

    # optional: mode
    mode: str = "default"  # "default" | "gameplay" | "just_chatting"


@dataclass
class SpeechIntent:
    """
    What the cadence controller decides.
    """
    kind: str
    urgency: float
    energy: float
    target_message: Optional[ChatMessage] = None
    suggested_duration_s: float = 0.0
    meta: Dict[str, Any] = field(default_factory=dict)


# -----------------------------
# Utilities
# -----------------------------

logger = logging.getLogger(__name__)

def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def ema(prev: float, x: float, alpha: float) -> float:
    return (1 - alpha) * prev + alpha * x


# -----------------------------
# Cadence Controller
# -----------------------------

class AureliaCadenceController:
    """
    Autonomous dynamic cadence controller for Aurelia.
    Refined with 'pro streamer' metrics (more talkative, shorter gaps).
    """

    def __init__(
        self,
        seed: Optional[int] = None,
        # 'Pro' streamer limits
        min_gap_s: float = 1.6,
        soft_gap_s: float = 3.2,
        max_silence_s: float = 15.0,     # Aurelia hates dead air (Pro: ~10s/min)
        # Burst tuning
        burst_max_items: int = 4,        # slightly higher for 'pro' feel
        burst_window_s: float = 12.0,
        # Energy wave tuning
        energy_wave_period_s: float = 75.0,
    ):
        self.rng = random.Random(seed)

        self.min_gap_s = float(min_gap_s)
        self.soft_gap_s = float(soft_gap_s)
        self.max_silence_s = float(max_silence_s)

        self.burst_max_items = int(burst_max_items)
        self.burst_window_s = float(burst_window_s)
        self.energy_wave_period_s = float(energy_wave_period_s)

        # Internal state
        self.last_spoke_ts: float = 0.0
        self.last_intent_kind: str = "NONE"

        self._chat_rate_ema: float = 0.0
        self._hype_ema: float = 0.0
        self._focus_ema: float = 0.0

        self._burst_count: int = 0
        self._burst_started_ts: float = 0.0

        self._seen_msg_ids: set[Tuple[str, float, str]] = set()
        self._seen_msg_ids_ordered: List[Tuple[str, float, str]] = []
        self._message_buffer: List[ChatMessage] = []

        self._baseline_energy: float = 0.60  # slightly higher base for 'pro'

        self._last_update_ts: Optional[float] = None

    def update(self, signals: StreamSignals) -> None:
        """Update state with new chat messages + signals."""
        now = float(signals.now)

        if self._last_update_ts is None:
            self._last_update_ts = now

        dt = max(1e-3, now - self._last_update_ts)
        self._last_update_ts = now

        # ingest messages
        for m in signals.chat_messages:
            msg_id = (m.user, m.ts, m.text)
            if msg_id in self._seen_msg_ids:
                continue
            self._seen_msg_ids.add(msg_id)
            self._seen_msg_ids_ordered.append(msg_id)
            self._message_buffer.append(m)

        # limit seen IDs growth
        if len(self._seen_msg_ids) > 1000:
            to_remove = self._seen_msg_ids_ordered[:200]
            for r_id in to_remove:
                self._seen_msg_ids.discard(r_id)
            self._seen_msg_ids_ordered = self._seen_msg_ids_ordered[200:]

        # chat rate estimate
        msg_count = len(signals.chat_messages)
        inst_rate = msg_count / dt
        self._chat_rate_ema = ema(self._chat_rate_ema, inst_rate, alpha=0.15)

        # hype & focus
        self._hype_ema = ema(self._hype_ema, clamp(signals.event_intensity), alpha=0.12)
        self._focus_ema = ema(self._focus_ema, clamp(signals.focus_level), alpha=0.10)

        # energy wave
        wave = 0.5 + 0.5 * math.sin((now % self.energy_wave_period_s) / self.energy_wave_period_s * 2 * math.pi)
        self._baseline_energy = clamp(0.40 + 0.35 * wave + 0.25 * self._hype_ema)

        # expire burst
        if self._burst_count > 0 and (now - self._burst_started_ts) > self.burst_window_s:
            self._burst_count = 0
            self._burst_started_ts = 0.0

        # trim buffer
        if len(self._message_buffer) > 100:
            self._message_buffer = self._message_buffer[-60:]

    def maybe_emit_intent(self, signals: StreamSignals) -> Optional[SpeechIntent]:
        now = float(signals.now)

        if signals.tts_busy:
            return None

        time_since_spoke = now - self.last_spoke_ts
        silence_pressure = clamp((time_since_spoke - self.max_silence_s) / 8.0)

        chat_rate = self._chat_rate_ema
        chat_activity = clamp(chat_rate / 0.50)

        focus = self._focus_ema
        hype = self._hype_ema

        desire = (
            0.30 * chat_activity +
            0.40 * hype +
            0.40 * silence_pressure +
            0.15 * clamp(signals.voice_room_activity)
        )
        desire *= (1.0 - 0.70 * focus)

        hi_signal_waiting = self._count_high_signal_pending() > 0
        if hi_signal_waiting:
            desire = clamp(desire + 0.22)

        # Respect gaps
        urgent = (hype > 0.70) or (silence_pressure > 0.60) or hi_signal_waiting
        if not urgent and time_since_spoke < self.min_gap_s:
            return None

        if not urgent and time_since_spoke < self._adaptive_soft_gap_s(chat_activity, focus):
            return None

        intent_kind = self._decide_intent_kind(
            now=now,
            desire=desire,
            chat_activity=chat_activity,
            hype=hype,
            focus=focus,
            silence_pressure=silence_pressure,
            mode=signals.mode,
        )
        if intent_kind is None:
            return None

        speech_intent = self._build_intent(intent_kind, now, chat_activity, hype, focus, silence_pressure)

        # side effects
        self.last_spoke_ts = now
        self.last_intent_kind = speech_intent.kind
        self._update_burst_state(now, speech_intent)

        return speech_intent

    def update_config(self, **kwargs) -> Dict[str, Any]:
        """Dynamically update cadence parameters with clamping."""
        updates = {}
        if "min_gap_s" in kwargs:
            self.min_gap_s = clamp(float(kwargs["min_gap_s"]), 1.0, 5.0)
            updates["min_gap_s"] = self.min_gap_s
        if "soft_gap_s" in kwargs:
            self.soft_gap_s = clamp(float(kwargs["soft_gap_s"]), 2.0, 10.0)
            updates["soft_gap_s"] = self.soft_gap_s
        if "max_silence_s" in kwargs:
            self.max_silence_s = clamp(float(kwargs["max_silence_s"]), 5.0, 60.0)
            updates["max_silence_s"] = self.max_silence_s
        if "burst_max_items" in kwargs:
            self.burst_max_items = int(clamp(float(kwargs["burst_max_items"]), 1, 8))
            updates["burst_max_items"] = self.burst_max_items

        if updates:
            logger.info(f"Cadence Controller Config Updated: {updates}")
        return updates

    def pop_consumed_message(self, msg: ChatMessage) -> None:
        if msg is None: return
        try:
            # We use a filter instead of remove to handle potential mismatches
            self._message_buffer = [m for m in self._message_buffer if (m.user, m.ts, m.text) != (msg.user, msg.ts, msg.text)]
        except Exception:
            pass

    # ------------- Decision logic -------------

    def _adaptive_soft_gap_s(self, chat_activity: float, focus: float) -> float:
        base = self.soft_gap_s
        base = base * (1.0 - 0.50 * chat_activity)
        base = base * (1.0 + 0.70 * focus)
        jitter = self.rng.uniform(-0.4, 0.4)
        return max(self.min_gap_s, base + jitter)

    def _decide_intent_kind(
        self,
        now: float,
        desire: float,
        chat_activity: float,
        hype: float,
        focus: float,
        silence_pressure: float,
        mode: str,
    ) -> Optional[str]:
        desire = clamp(desire)

        if desire < 0.20 and silence_pressure < 0.20:
            return None

        gameplay_bias = 1.0 if mode == "gameplay" else 0.0
        pending = len(self._message_buffer)
        hi_pending = self._count_high_signal_pending()

        w_react = 0.05 + 0.60 * hype + 0.20 * chat_activity
        w_reply = 0.15 + 0.50 * chat_activity + 0.35 * (1.0 if pending > 0 else 0.0) + 0.40 * (1.0 if hi_pending > 0 else 0.0)
        w_riff  = 0.08 + 0.30 * chat_activity + 0.40 * hype + 0.15 * (1.0 - focus) - 0.20 * gameplay_bias
        w_fill  = 0.12 + 0.60 * silence_pressure + 0.20 * (1.0 - chat_activity)

        w_fill *= (1.0 - 0.70 * chat_activity)
        w_riff *= (1.0 - 0.80 * focus)

        if self._burst_count >= self.burst_max_items:
            w_reply *= 0.25
            w_fill *= 0.50

        if pending == 0 and hype < 0.30 and silence_pressure < 0.25:
            w_fill *= 0.30

        if desire < 0.30 and silence_pressure < 0.30 and pending == 0 and hype < 0.4:
            return None

        return self._weighted_pick([
            ("REACT", w_react),
            ("REPLY", w_reply),
            ("RIFF",  w_riff),
            ("FILLER", w_fill),
        ])

    def _build_intent(self, kind: str, now: float, chat_activity: float, hype: float, focus: float, silence_pressure: float) -> SpeechIntent:
        energy = clamp(self._baseline_energy + 0.30 * hype - 0.25 * focus + self.rng.uniform(-0.05, 0.05))
        urgency = clamp(0.25 + 0.60 * hype + 0.40 * silence_pressure + 0.30 * chat_activity)

        if kind == "REPLY":
            msg = self._pick_message_to_reply(now)
            dur = self._suggest_duration(kind, chat_activity, hype, focus)
            return SpeechIntent(kind="REPLY", urgency=urgency, energy=energy, target_message=msg, suggested_duration_s=dur,
                               meta={"style": "direct_reply", "cadence": "burst"})

        if kind == "RIFF":
            seed_msg = self._pick_message_to_riff(now)
            dur = self._suggest_duration(kind, chat_activity, hype, focus)
            return SpeechIntent(kind="RIFF", urgency=urgency, energy=energy, target_message=seed_msg, suggested_duration_s=dur,
                               meta={"style": "narrative_riff", "cadence": "flow"})

        if kind == "REACT":
            dur = self._suggest_duration(kind, chat_activity, hype, focus)
            return SpeechIntent(kind="REACT", urgency=urgency, energy=clamp(energy + 0.20), target_message=None, suggested_duration_s=dur,
                               meta={"style": "hype_reaction", "cadence": "short"})

        if kind == "FILLER":
            dur = self._suggest_duration(kind, chat_activity, hype, focus)
            f_type = self._pick_filler_type(chat_activity, hype, focus)
            return SpeechIntent(kind="FILLER", urgency=urgency, energy=clamp(energy - 0.05), target_message=None, suggested_duration_s=dur,
                               meta={"style": f_type, "cadence": "keep_alive"})

        return SpeechIntent(kind="BREATHE", urgency=0.0, energy=energy)

    def _suggest_duration(self, kind: str, chat_activity: float, hype: float, focus: float) -> float:
        if kind == "REACT": return self.rng.uniform(1.0, 3.5)
        if kind == "FILLER": return self.rng.uniform(1.5, 5.0)
        if kind == "REPLY":
            base = 5.0 + 7.0 * (1.0 - chat_activity) + 4.0 * hype
            return clamp(base * (1.0 - 0.30 * focus), 3.0, 18.0)
        if kind == "RIFF":
            base = 12.0 + 15.0 * (0.5 + hype) + 8.0 * (1.0 - focus)
            return clamp(base, 10.0, 40.0)
        return 0.0

    def _pick_filler_type(self, chat_activity: float, hype: float, focus: float) -> str:
        options = ["inner_monologue", "checking_gear", "adjusting_cape", "polishing_armor", "checking_loot_pouch"]
        if hype > 0.5:
            options += ["teasing_chat", "anticipation"]
        if chat_activity < 0.2:
            options += ["chat_bait_question", "existential_reflection"]
        return self.rng.choice(options)

    def _count_high_signal_pending(self) -> int:
        return sum(1 for m in self._message_buffer if m.is_high_signal)

    def _pick_message_to_reply(self, now: float) -> Optional[ChatMessage]:
        if not self._message_buffer: return None
        pool = [m for m in self._message_buffer if (now - m.ts) <= 45.0]
        if not pool: pool = self._message_buffer[-10:]

        scored = []
        for m in pool:
            score = clamp(1.0 - ((now - m.ts) / 60.0)) * 0.5 + (0.4 if m.is_high_signal else 0.0) + self.rng.uniform(0, 0.2)
            scored.append((score, m))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]

    def _pick_message_to_riff(self, now: float) -> Optional[ChatMessage]:
        if not self._message_buffer: return None
        hi = [m for m in self._message_buffer if m.is_high_signal and (now - m.ts) <= 90.0]
        return self.rng.choice(hi) if hi else self.rng.choice(self._message_buffer[-15:])

    def _update_burst_state(self, now: float, intent: SpeechIntent) -> None:
        if intent.kind == "REPLY":
            if self._burst_count == 0: self._burst_started_ts = now
            self._burst_count += 1
        elif intent.kind in ("FILLER", "RIFF"):
            self._burst_count = max(0, self._burst_count - 1)

        if (now - self._burst_started_ts) > self.burst_window_s:
            self._burst_count = 0

    def _weighted_pick(self, items: List[Tuple[str, float]]) -> Optional[str]:
        total = sum(max(0.0, w) for _, w in items)
        if total <= 0: return None
        r = self.rng.random() * total
        acc = 0.0
        for name, w in items:
            acc += max(0.0, w)
            if r <= acc: return name
        return items[-1][0]
