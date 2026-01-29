from __future__ import annotations
import yaml
import os
import logging
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class PersonaplexManager:
    """Manages modular persona facets for Aurelia."""

    def __init__(self, modules_dir: str):
        self.modules_dir = Path(modules_dir)
        self.modules_dir.mkdir(parents=True, exist_ok=True)
        self.active_modules: List[str] = ["core", "squire"]

    def load_combined_persona(self) -> Dict[str, Any]:
        """Loads and merges all active persona modules."""
        combined = {"character": {}, "personality_traits": {}, "speech_patterns": {}}

        for mod_name in self.active_modules:
            file_path = self.modules_dir / f"{mod_name}.yaml"
            if not file_path.exists():
                logger.warning(f"Persona module {mod_name} not found at {file_path}")
                continue

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}

                    # Merge logic
                    if "character" in data:
                        combined["character"].update(data["character"])
                    if "personality_traits" in data:
                        combined["personality_traits"].update(data["personality_traits"])
                    if "speech_patterns" in data:
                        if isinstance(data["speech_patterns"], dict):
                            combined["speech_patterns"].update(data["speech_patterns"])
                        else:
                            combined["speech_patterns"]["style"] = data["speech_patterns"]
            except Exception as e:
                logger.error(f"Error loading persona module {mod_name}: {e}")

        return combined

    def modify_module(self, module_name: str, updates: Dict[str, Any]):
        """Modifies or creates a persona module."""
        file_path = self.modules_dir / f"{module_name}.yaml"

        current_data = {}
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    current_data = yaml.safe_load(f) or {}
            except Exception:
                pass

        # Simple recursive update
        self._deep_update(current_data, updates)

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                yaml.dump(current_data, f, sort_keys=False)
            logger.info(f"Successfully modified persona module: {module_name}")
            if module_name not in self.active_modules:
                self.active_modules.append(module_name)
        except Exception as e:
            logger.error(f"Failed to save persona module {module_name}: {e}")

    def _deep_update(self, base: Dict[str, Any], updates: Dict[str, Any]):
        for k, v in updates.items():
            if isinstance(v, dict) and k in base and isinstance(base[k], dict):
                self._deep_update(base[k], v)
            else:
                base[k] = v
