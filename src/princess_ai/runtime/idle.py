"""Idle activity planning for autonomous runtime behaviors."""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(slots=True)
class IdleActivity:
    name: str
    prompts: tuple[str, ...]
    weight: float = 1.0


class IdleActivityPlanner:
    """Selects an idle activity prompt to keep Aurelia active while alone."""

    def __init__(self, activities: tuple[IdleActivity, ...] | None = None) -> None:
        self._activities = activities or (
            IdleActivity(
                name="daydream",
                prompts=(
                    "Daydream about future stream moments and summarize a fun idea.",
                    "Imagine a cozy off-stream scene and share a short reflection.",
                ),
                weight=1.0,
            ),
            IdleActivity(
                name="dream",
                prompts=(
                    "Enter a brief dream state and describe the symbols you notice.",
                    "Sleep lightly and report a whimsical dream fragment.",
                ),
                weight=0.8,
            ),
            IdleActivity(
                name="self_reflection",
                prompts=(
                    "Reflect on recent interactions and note a growth opportunity.",
                    "Review your goals and describe one improvement you want to pursue.",
                ),
                weight=1.2,
            ),
            IdleActivity(
                name="self_test",
                prompts=(
                    "Run a brief self-test on reasoning, memory recall, and safety checks.",
                    "Simulate a mini regression test and report any anomalies.",
                ),
                weight=1.0,
            ),
            IdleActivity(
                name="system_check",
                prompts=(
                    "Run a quick self-check on memory, safety, and response quality.",
                    "Audit recent runtime behavior and summarize any adjustments needed.",
                ),
                weight=1.1,
            ),
        )

    def choose(self, last_activity: str | None = None) -> tuple[str, str]:
        choices = list(self._activities)
        if last_activity:
            choices = [activity for activity in choices if activity.name != last_activity] or choices
        weights = [activity.weight for activity in choices]
        activity = random.choices(choices, weights=weights, k=1)[0]
        prompt = random.choice(activity.prompts)
        return activity.name, prompt
