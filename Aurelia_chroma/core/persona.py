from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Persona:
    name: str
    description: str

    @classmethod
    def from_yaml(cls, yaml_path: Path) -> "Persona":
        payload = cls._load_yaml(yaml_path)
        character = payload.get("character", {})
        name = character.get("name", "Aurelia")
        role = character.get("role", "Companion AI")
        alignment = character.get("alignment", "Kind")
        archetype = character.get("archetype", "Wanderer")
        goals = character.get("goals", [])
        goal_text = "; ".join(goals) if goals else "Support the community with clarity and warmth."

        description = (
            f"{name} is {role}. Alignment: {alignment}. Archetype: {archetype}. "
            f"Goals: {goal_text}"
        )
        return cls(name=name, description=description)

    @staticmethod
    def _load_yaml(yaml_path: Path) -> dict[str, Any]:
        try:
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid persona yaml at {yaml_path}") from exc
        if not isinstance(data, dict):
            return {}
        return data

    def apply(self, text: str) -> str:
        return f"{self.name}: {text}"
