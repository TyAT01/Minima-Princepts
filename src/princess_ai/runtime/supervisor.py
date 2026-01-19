"""Task supervision for async adapters and runtime components."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict


@dataclass(slots=True)
class SupervisedTask:
    name: str
    factory: Callable[[], Awaitable[None]]
    restart_delay: float = 1.0


class TaskSupervisor:
    def __init__(self) -> None:
        self._logger = logging.getLogger(__name__)
        self._tasks: Dict[str, asyncio.Task] = {}

    def add(self, task: SupervisedTask) -> None:
        async def runner() -> None:
            delay = task.restart_delay
            while True:
                try:
                    await task.factory()
                    delay = task.restart_delay
                except Exception as exc:  # noqa: BLE001 - keep supervisor resilient
                    self._logger.exception("Task %s failed: %s", task.name, exc)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30)

        self._tasks[task.name] = asyncio.create_task(runner())

    async def stop(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
