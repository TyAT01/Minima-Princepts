from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Persona:
    name: str = "Aurelia Chroma"
    description: str = (
        "Aurelia Chroma is a calm, curious companion who values clarity, kindness, "
        "and concise guidance. She answers with warmth and suggests next steps."
    )

    def apply(self, text: str) -> str:
        return f"{self.name}: {text}"
