"""Rate limiting and spam control utilities."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict


@dataclass(slots=True)
class RateLimitConfig:
    per_channel_cooldown_s: float = 1.5
    global_cooldown_s: float = 0.3
    reply_budget_per_minute: int = 20


@dataclass(slots=True)
class BudgetState:
    remaining: int
    reset_at: float


class RateLimiter:
    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self._config = config or RateLimitConfig()
        self._last_global = 0.0
        self._last_channel: Dict[str, float] = {}
        self._budgets: Dict[str, BudgetState] = {}

    def allow(self, channel_key: str) -> bool:
        now = time.monotonic()
        if now - self._last_global < self._config.global_cooldown_s:
            return False
        last_channel = self._last_channel.get(channel_key, 0.0)
        if now - last_channel < self._config.per_channel_cooldown_s:
            return False
        budget = self._budgets.get(channel_key)
        if not budget or now >= budget.reset_at:
            self._budgets[channel_key] = BudgetState(
                remaining=self._config.reply_budget_per_minute,
                reset_at=now + 60,
            )
            budget = self._budgets[channel_key]
        if budget.remaining <= 0:
            return False
        budget.remaining -= 1
        self._last_global = now
        self._last_channel[channel_key] = now
        return True
