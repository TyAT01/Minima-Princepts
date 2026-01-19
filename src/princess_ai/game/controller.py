"""Game/action model controller."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class GameAction:
    name: str
    payload: dict


class GameController:
    def decide(self, state: dict) -> GameAction | None:
        return None
