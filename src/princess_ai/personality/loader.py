"""Personality sheet loader for Aurelia Vale."""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from princess_ai.context.builder import Persona
from princess_ai.personality.layer import PersonaPolicy


class PersonalityLoadError(RuntimeError):
    """Raised when the personality sheet cannot be loaded."""


@dataclass(slots=True)
class PersonalityProfile:
    persona: Persona
    policy: PersonaPolicy


class PersonalityLoader:
    def __init__(self, sheet_path: Path) -> None:
        self._sheet_path = sheet_path
        self._logger = logging.getLogger(__name__)

    def load(self) -> PersonalityProfile:
        try:
            raw_text = self._sheet_path.read_text(encoding="utf-8")
        except OSError as exc:
            message = f"Unable to read personality sheet at {self._sheet_path}: {exc}"
            self._logger.error(message)
            raise PersonalityLoadError(message) from exc

        try:
            data = yaml.safe_load(raw_text) or {}
        except yaml.YAMLError as exc:
            message = f"Invalid YAML in personality sheet at {self._sheet_path}: {exc}"
            self._logger.error(message)
            raise PersonalityLoadError(message) from exc

        if not isinstance(data, dict) or "character" not in data:
            message = f"Personality sheet missing required 'character' section: {self._sheet_path}"
            self._logger.error(message)
            raise PersonalityLoadError(message)

        character = data.get("character", {})
        if not isinstance(character, dict):
            message = f"Invalid 'character' section in personality sheet: {self._sheet_path}"
            self._logger.error(message)
            raise PersonalityLoadError(message)

        name = str(character.get("name", "Aurelia Vale")).strip() or "Aurelia Vale"
        description = self._build_description(character)
        safety = self._build_safety_line(data)
        llm_constraints = self._build_llm_constraints(character)
        response_marker = f"{name} Response:"
        persona = Persona(
            name=name,
            description=description,
            safety=safety,
            response_marker=response_marker,
            llm_constraints=llm_constraints,
        )
        policy = PersonaPolicy(boundaries=safety)
        return PersonalityProfile(persona=persona, policy=policy)

    def _build_description(self, character: dict[str, Any]) -> str:
        role = self._safe_text(character.get("role"))
        alignment = self._safe_text(character.get("alignment"))
        archetype = self._safe_text(character.get("archetype"))
        nicknames = character.get("nicknames", []) or []
        nickname_text = ", ".join(
            self._safe_text(item) for item in nicknames if self._safe_text(item)
        )
        core_identity = character.get("core_identity", {}) or {}
        motivation = self._safe_text(core_identity.get("motivation"))
        self_awareness = self._safe_text(core_identity.get("self_awareness"))
        appearance = character.get("appearance", {}) or {}
        distinct_traits = self._safe_text(appearance.get("distinct_traits"))
        outfit_style = self._safe_text(appearance.get("outfit_style"))
        personality_traits = character.get("personality_traits", []) or []
        trait_lines = []
        for item in personality_traits:
            trait = self._safe_text(item.get("trait") if isinstance(item, dict) else "")
            description = self._safe_text(
                item.get("description") if isinstance(item, dict) else ""
            )
            if trait and description:
                trait_lines.append(f"{trait}: {description}")
        speech_patterns = character.get("speech_patterns", {}) or {}
        speech_style = self._safe_text(speech_patterns.get("style"))
        speech_examples = speech_patterns.get("examples", []) or []
        example_text = "; ".join(
            self._safe_text(example)
            for example in speech_examples
            if self._safe_text(example)
        )

        details = []
        if nickname_text:
            details.append(f"Nicknames: {nickname_text}.")
        if role:
            details.append(f"Role: {role}.")
        if alignment:
            details.append(f"Alignment: {alignment}.")
        if archetype:
            details.append(f"Archetype: {archetype}.")
        if self_awareness:
            details.append(f"Self-awareness: {self_awareness}")
        if motivation:
            details.append(f"Motivation: {motivation}")
        if distinct_traits:
            details.append(f"Distinct traits: {distinct_traits}.")
        if outfit_style:
            details.append(f"Style: {outfit_style}.")
        if speech_style:
            details.append(f"Speech style: {speech_style}")
        if example_text:
            details.append(f"Speech examples: {example_text}")
        if trait_lines:
            details.append("Personality traits: " + "; ".join(trait_lines))

        fallback = "Aurelia Vale is a warm, curious, and self-aware streaming companion."
        return " ".join(details).strip() or fallback

    def _build_safety_line(self, data: dict[str, Any]) -> str:
        meta = data.get("meta", {}) or {}
        tag = self._safe_text(meta.get("tag"))
        base = "Keep the tone friendly, safe, and appropriate for a public stream."
        if tag:
            return f"{base} Persona tag: {tag}."
        return base

    def _build_llm_constraints(self, character: dict[str, Any]) -> str:
        constraints = character.get("llm_logic_constraints", {}) or {}
        if not isinstance(constraints, dict):
            return ""
        profile = os.getenv("PRINCESS_LLM_PROFILE", "").strip()
        if profile and profile in constraints:
            return self._safe_text(constraints.get(profile))
        if "llama_3_8b" in constraints:
            return self._safe_text(constraints.get("llama_3_8b"))
        for value in constraints.values():
            text = self._safe_text(value)
            if text:
                return text
        return ""

    @staticmethod
    def _safe_text(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()
