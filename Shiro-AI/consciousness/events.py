"""
events.py — Internal event bus for the consciousness engine.

Allows modules to communicate without tight coupling.
SelfAwareness publishes events; InnerMind, AutonomousVoice, etc. subscribe.

Completely synchronous — no asyncio overhead for internal signaling.
Async handlers supported but called via create_task.

Event catalog:
  emotion_detected     {user_id, emotion, strength, trajectory}
  topic_detected       {user_id, topic, is_new}
  user_entered         {user_id, name, is_returning, tier}
  user_left            {user_id}
  user_spoke           {user_id, text, emotions, depth}
  mood_shifted         {from_mood, to_mood, trigger}
  thought_fired        {thought}
  relationship_changed {user_id, old_tier, new_tier}
  memory_recalled      {user_id, fact}
  intent_planned       {user_id, intent, confidence}
  llm_response         {user_id, text, tokens}
  idle_triggered       {idle_seconds}
"""

import asyncio
import logging
import time
from collections import defaultdict
from typing import Callable, Any, Optional

logger = logging.getLogger("shiro.events")


class EventBus:
    """
    Lightweight synchronous/async event bus.

    Usage:
        bus = EventBus()

        # Subscribe
        bus.on("emotion_detected", handle_emotion)

        # Publish
        bus.emit("emotion_detected", user_id="alex", emotion="humor", strength=0.9)

        # Async handlers are wrapped in asyncio.create_task
        bus.on("user_spoke", async_handler)
    """

    def __init__(self):
        self._handlers: dict[str, list[Callable]] = defaultdict(list)
        self._history: list[dict] = []
        self._history_max: int = 100
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def on(self, event: str, handler: Callable) -> "EventBus":
        """Subscribe to an event. Returns self for chaining."""
        self._handlers[event].append(handler)
        return self

    def off(self, event: str, handler: Callable) -> "EventBus":
        """Unsubscribe a handler."""
        if event in self._handlers:
            self._handlers[event] = [h for h in self._handlers[event] if h is not handler]
        return self

    def emit(self, event: str, **data) -> int:
        """
        Emit an event. Calls all subscribed handlers.
        Async handlers are scheduled via create_task.
        Returns number of handlers called.

        Wildcard handlers (subscribed via bus.on("*", handler)) receive
        an extra keyword arg `event_type` with the event name.
        """
        payload = {"event": event, "ts": time.time(), **data}
        self._history.append(payload)
        if len(self._history) > self._history_max:
            self._history = self._history[-80:]

        count = 0
        specific  = self._handlers.get(event, [])
        wildcards = self._handlers.get("*", [])

        for h in specific:
            count += self._call(h, event, data)
        for h in wildcards:
            # Wildcard handlers receive event_type so they know what fired
            count += self._call(h, event, {**data, "event_type": event})
        return count

    def _call(self, handler: Callable, event: str, data: dict) -> int:
        """Call a single handler, returning 1 on success or 0 on error."""
        try:
            if asyncio.iscoroutinefunction(handler):
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(handler(**data))
                    else:
                        loop.run_until_complete(handler(**data))
                except RuntimeError:
                    pass  # no event loop — skip async handler gracefully
            else:
                handler(**data)
            return 1
        except Exception as e:
            logger.warning(f"[EventBus] handler error on '{event}': {type(e).__name__}: {e}")
            return 0

    def recent(self, event: Optional[str] = None, n: int = 10) -> list[dict]:
        """Get recent events, optionally filtered by type."""
        hist = self._history if event is None else [e for e in self._history if e["event"] == event]
        return hist[-n:]

    def last(self, event: str) -> Optional[dict]:
        """Get the most recent occurrence of an event."""
        for e in reversed(self._history):
            if e["event"] == event:
                return e
        return None

    def since(self, event: str, seconds: float) -> list[dict]:
        """Get all occurrences of an event within the last N seconds."""
        cutoff = time.time() - seconds
        return [e for e in self._history if e["event"] == event and e["ts"] > cutoff]

    def clear(self):
        self._handlers.clear()
        self._history.clear()
