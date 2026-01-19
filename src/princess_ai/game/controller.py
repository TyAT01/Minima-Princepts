"""Game/action model controller."""

from __future__ import annotations

import logging
from dataclasses import dataclass


@dataclass(slots=True)
class GameAction:
    name: str
    payload: dict


class GameController:
    def decide(self, state: dict) -> GameAction | None:
        logger = logging.getLogger(__name__)
        try:
            actions = state.get("actions", []) if isinstance(state, dict) else []
            if not actions:
                return None
            first = actions[0]
            if isinstance(first, dict):
                return GameAction(name=str(first.get("name", "unknown")), payload=first)
            return GameAction(name=str(first), payload={"value": first})
        except Exception as exc:  # noqa: BLE001 - keep game controller resilient
            logger.exception("Failed to decide game action: %s", exc)
            return None
