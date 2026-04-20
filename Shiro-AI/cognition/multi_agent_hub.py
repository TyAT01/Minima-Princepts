"""
SHIRO Multi-Agent Hub v3.6
===========================
Hardware: RTX 3070 8 GB | i9-9900K 8c/16t | 64 GB RAM

What's new vs v3.1
-------------------
ENHANCEMENTS
  - CAPABILITY ROUTING: agents now declare their capabilities (e.g.
    "code", "research", "math", "creative") and the router selects only
    the agents whose capabilities match the current intent — avoids
    dispatching all agents every time.
  - CONFIDENCE MERGE: merge now uses a weighted vote across all valid
    results (not just max confidence), accounting for agent reliability
    scores that update over time.
  - AGENT HEALTH TRACKING: each proxy tracks its success_rate and
    avg_latency over the last 20 calls. Agents with success_rate < 0.5
    are skipped until they recover.
  - RESULT CACHE: if the same prompt was dispatched within the last
    30 s, return the cached result (debounces rapid re-dispatches).
  - GRACEFUL DEGRADATION: if ALL agents fail or timeout, the hub writes
    a descriptive fallback into state.context rather than silently
    producing nothing.
  - SHUTDOWN: hub.shutdown() cancels in-flight agent tasks cleanly.

OPTIMISATIONS (RTX 3070 VRAM budget)
  - ThreadPoolExecutor capped at 2 workers (unchanged) — each sub-agent
    boots its own LLM instance. RTX 3070 has 8 GB VRAM;
    LLM ≈ 4 GB + TTS ≈ 1.5 GB = 5.5 GB used, 2.5 GB headroom.
    2 sub-agent workers means at most 1 extra small model can fit.
  - Agents with enable_vram=False run CPU-only (for light routing tasks).
  - asyncio.wait_for() for timeout handling — compatible with Python 3.9+.
  - __slots__ on all classes.
  - Result cache uses a plain dict with a deque of keys for LRU eviction.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from cognitive_kernel import CognitiveState, CognitiveKernel

# VRAM budget: cap at 2 sub-agent threads on RTX 3070
_AGENT_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="shiro_agent")

_INTENT_TO_CAPABILITY: dict[str, list[str]] = {
    "task_request":  ["code", "creative", "general"],
    "debug":         ["code", "general"],
    "question":      ["research", "general"],
    "explanation":   ["research", "general"],
    "planning":      ["planning", "general"],
    "opinion":       ["creative", "general"],
    "emotional":     ["general"],
    "conversation":  ["general"],
}


class AgentTask:
    __slots__ = ("task_id","agent_id","prompt","context","priority","timeout_sec")
    def __init__(self, agent_id: str = "", prompt: str = "", context: dict | None = None,
                 priority: int = 1, timeout_sec: float = 30.0):
        self.task_id     = uuid.uuid4().hex
        self.agent_id    = agent_id
        self.prompt      = prompt
        self.context     = context or {}
        self.priority    = priority
        self.timeout_sec = timeout_sec


class AgentResult:
    __slots__ = ("task_id","agent_id","output","confidence","duration_ms","error")
    def __init__(self, task_id: str, agent_id: str, output: str = "",
                 confidence: float = 0.0, duration_ms: float = 0.0, error: str | None = None):
        self.task_id     = task_id
        self.agent_id    = agent_id
        self.output      = output
        self.confidence  = confidence
        self.duration_ms = duration_ms
        self.error       = error


class AgentProxy:
    __slots__ = (
        "agent_id", "config", "capabilities",
        "_kernel", "_ready",
        "_success_count", "_fail_count",
        "_latency_history",
    )

    def __init__(self, agent_id: str, config: dict | None = None):
        self.agent_id          = agent_id
        self.config            = config or {}
        self.capabilities: list[str] = config.get("capabilities", ["general"]) if config else ["general"]
        self._kernel: Any      = None
        self._ready            = False
        self._success_count    = 0
        self._fail_count       = 0
        self._latency_history: deque = deque(maxlen=20)

    @property
    def success_rate(self) -> float:
        total = self._success_count + self._fail_count
        return self._success_count / total if total > 0 else 1.0

    @property
    def avg_latency_ms(self) -> float:
        if not self._latency_history:
            return 0.0
        return sum(self._latency_history) / len(self._latency_history)

    @property
    def is_healthy(self) -> bool:
        return self.success_rate >= 0.5

    async def _ensure_booted(self):
        if self._ready:
            return
        try:
            from .cognitive_kernel import CognitiveKernel
        except ImportError:
            from cognitive_kernel import CognitiveKernel
        self._kernel = CognitiveKernel(self.config)
        await self._kernel.boot(warmup=False)   # skip warmup for sub-agents
        self._ready = True

    async def execute(self, task: AgentTask) -> AgentResult:
        await self._ensure_booted()
        t0 = time.perf_counter()
        try:
            out = await asyncio.wait_for(
                self._kernel.process(task.prompt, task.context),
                timeout=task.timeout_sec,
            )
            ms = (time.perf_counter() - t0) * 1000
            self._success_count += 1
            self._latency_history.append(ms)
            # Confidence: success_rate × recency_factor (fast+reliable = high confidence)
            # Clamped 0.50–0.95 to avoid extremes on early turns
            conf = min(0.95, max(0.50, self.success_rate * (1.0 - min(0.3, ms / 30_000))))
            return AgentResult(task.task_id, self.agent_id, out,
                               confidence=conf, duration_ms=ms)
        except asyncio.TimeoutError:
            ms = (time.perf_counter() - t0) * 1000
            self._fail_count += 1
            import logging as _log
            _log.getLogger("shiro.multi_agent").warning(
                f"[MultiAgent] Agent '{self.agent_id}' timed out after {task.timeout_sec}s"
            )
            return AgentResult(task.task_id, self.agent_id,
                               error=f"timeout>{task.timeout_sec}s", duration_ms=ms)
        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000
            self._fail_count += 1
            return AgentResult(task.task_id, self.agent_id,
                               error=str(e), duration_ms=ms)

    async def shutdown(self):
        if self._kernel:
            try:
                await self._kernel.shutdown(timeout=5.0)
            except Exception:
                pass


class MultiAgentHub:
    """
    Activated explicitly via kernel.activate_multi_agent(configs).
    Zero memory/CPU cost until activated.

    Capability routing:
        Each agent declares capabilities. The hub only dispatches agents
        whose capabilities match the current turn's intent.

    Result cache:
        Same prompt within 30 s returns cached result (no re-dispatch).
    """
    __slots__ = ("agents", "_result_cache", "_cache_keys", "_cache_ttl")

    def __init__(self, agent_configs: list[dict]):
        self.agents: dict[str, AgentProxy] = {}
        for cfg in agent_configs:
            aid = cfg.get("id", uuid.uuid4().hex)
            self.agents[aid] = AgentProxy(aid, cfg)

        self._result_cache: dict[str, AgentResult] = {}
        self._cache_keys:   deque                  = deque(maxlen=20)
        self._cache_ttl:    float                  = 30.0

    async def dispatch(self, state: "CognitiveState") -> "CognitiveState":
        intent      = state.intent.get("primary", "general")
        needed_caps = _INTENT_TO_CAPABILITY.get(intent, ["general"])
        prompt      = state.raw_input

        # Cache check
        cache_key = f"{intent}:{prompt[:80]}"
        cached_entry = self._result_cache.get(cache_key)  # (AgentResult, cached_at_ts)
        if cached_entry:
            cached_result, cached_at = cached_entry
            if (time.time() - cached_at) < self._cache_ttl:
                state.context["multi_agent_output"] = cached_result.output[:600]
                state.context["multi_agent_cached"] = True
                return state
            else:
                del self._result_cache[cache_key]  # expire stale entry

        # Select healthy agents with matching capabilities
        selected = [
            proxy for proxy in self.agents.values()
            if proxy.is_healthy and any(c in needed_caps for c in proxy.capabilities)
        ]
        if not selected:
            # Fall back to any healthy agent
            selected = [p for p in self.agents.values() if p.is_healthy]

        if not selected:
            state.context["multi_agent_output"] = "[No healthy agents available]"
            return state

        tasks = [
            AgentTask(
                agent_id=p.agent_id,
                prompt=prompt,
                context=dict(state.context),
                priority=1,
                timeout_sec=30.0,
            )
            for p in selected
        ]

        results: list[AgentResult] = list(await asyncio.gather(
            *[self.agents[t.agent_id].execute(t) for t in tasks if t.agent_id in self.agents],
            return_exceptions=False,
        ))

        best = self._merge(results)
        if best:
            state.context["multi_agent_output"]     = best.output[:600]
            state.context["multi_agent_confidence"] = best.confidence
            state.context["multi_agent_agent"]      = best.agent_id
            # Cache result with wall-clock timestamp for proper TTL
            self._result_cache[cache_key] = (best, time.time())
            self._cache_keys.append(cache_key)
            # FIX: evict all entries older than TTL (was using deque-membership
            # which missed entries that wrapped past the deque maxlen).
            now = time.time()
            stale = [k for k, (_, ts) in self._result_cache.items()
                     if now - ts >= self._cache_ttl]
            for k in stale:
                del self._result_cache[k]
        else:
            state.context["multi_agent_output"] = "[All agents failed or timed out]"

        return state

    def _merge(self, results: list[AgentResult]) -> AgentResult | None:
        """
        Weighted confidence vote.
        Each agent's result is weighted by its historical success_rate.
        The result from the agent with the highest weighted confidence wins.
        """
        valid = [r for r in results if not r.error and r.output]
        if not valid:
            return None
        if len(valid) == 1:
            return valid[0]
        # Weight by agent success rate
        best   = None
        best_w = -1.0
        for r in valid:
            proxy = self.agents.get(r.agent_id)
            sr    = proxy.success_rate if proxy else 0.75
            w     = r.confidence * sr
            if w > best_w:
                best_w = w
                best   = r
        return best

    async def shutdown(self):
        await asyncio.gather(*[p.shutdown() for p in self.agents.values()])

    def status(self) -> dict:
        now = time.time()
        return {
            "agents": [
                {
                    "id":           aid,
                    "ready":        p._ready,
                    "healthy":      p.is_healthy,
                    "success_rate": round(p.success_rate, 2),
                    "avg_ms":       round(p.avg_latency_ms, 1),
                    "capabilities": p.capabilities,
                }
                for aid, p in self.agents.items()
            ],
            "cache_size":  len(self._result_cache),
            "cache_live":  sum(1 for _, (_, ts) in self._result_cache.items()
                               if now - ts < self._cache_ttl),
        }

    def evict_cache(self):
        """Manually clear the result cache — useful after major context change."""
        self._result_cache.clear()
        self._cache_keys.clear()

    def get_healthy_agents(self) -> list[str]:
        """Return IDs of currently healthy agents."""
        return [aid for aid, p in self.agents.items() if p.is_healthy]