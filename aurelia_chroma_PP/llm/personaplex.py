from __future__ import annotations
import yaml
import logging
from pathlib import Path
from typing import Dict, Any, List
from config import settings

logger = logging.getLogger(__name__)

class PersonaPlex:
    def __init__(self, modules_dir: Path = settings.persona_modules_dir):
        self.modules_dir = modules_dir
        self.active_modules: List[str] = []
        self._ensure_dir()

    def _ensure_dir(self):
        self.modules_dir.mkdir(parents=True, exist_ok=True)

    def load_module(self, name: str) -> Dict[str, Any]:
        path = self.modules_dir / f"{name}.yaml"
        if not path.exists():
            logger.warning(f"Persona module not found: {name}")
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.error(f"Failed to load persona module {name}: {e}")
            return {}

    def save_module(self, name: str, data: Dict[str, Any]):
        path = self.modules_dir / f"{name}.yaml"
        try:
            with open(path, "w", encoding="utf-8") as f:
                yaml.dump(data, f)
            logger.info(f"Saved persona module: {name}")
        except Exception as e:
            logger.error(f"Failed to save persona module {name}: {e}")

    def activate_module(self, name: str):
        if name not in self.active_modules:
            self.active_modules.append(name)
            logger.info(f"Activated persona module: {name}")

    def deactivate_module(self, name: str):
        if name in self.active_modules:
            self.active_modules.remove(name)
            logger.info(f"Deactivated persona module: {name}")

    def get_merged_persona_data(self, base_data: Dict[str, Any]) -> Dict[str, Any]:
        """Merges base persona data with all active modules."""
        import copy
        merged = copy.deepcopy(base_data)
        for mod_name in self.active_modules:
            mod_data = self.load_module(mod_name)
            if mod_data:
                self._merge_dicts(merged, mod_data)
        return merged

    def get_active_voice_prompt(self) -> str:
        """Returns the voice prompt from the last activated module, or default."""
        voice = "NATF0" # NVIDIA PersonaPlex default natural female voice
        for mod_name in reversed(self.active_modules):
            mod_data = self.load_module(mod_name)
            if "voice_prompt" in mod_data:
                return mod_data["voice_prompt"]
        return voice

    def _merge_dicts(self, base: Dict[str, Any], overlay: Dict[str, Any]):
        """Recursively merges overlay into base."""
        for key, value in overlay.items():
            if isinstance(value, dict) and key in base and isinstance(base[key], dict):
                self._merge_dicts(base[key], value)
            elif isinstance(value, list) and key in base and isinstance(base[key], list):
                # Append to lists, avoiding duplicates if possible (for simple types)
                for item in value:
                    if item not in base[key]:
                        base[key].append(item)
            else:
                # Override for other types
                base[key] = value

    def handle_command(self, command_text: str) -> bool:
        """
        Parses commands like [PERSONAPLEX: action=create, name=scholar, data={...}]
        Returns True if a change occurred that requires prompt regeneration.
        """
        import re

        # Look for the PERSONAPLEX tag
        pattern = r"\[PERSONAPLEX:\s*(.*?)\]"
        matches = re.findall(pattern, command_text, re.IGNORECASE)

        if not matches:
            return False

        changed = False
        for content in matches:
            # Extract fields from content: action=..., name=..., data=...
            action_match = re.search(r"action=(\w+)", content, re.IGNORECASE)
            name_match = re.search(r"name=([\w-]+)", content, re.IGNORECASE)
            voice_match = re.search(r"voice=([\w-]+)", content, re.IGNORECASE)
            data_match = re.search(r"data=(.*)", content, re.IGNORECASE)

            if not action_match or not name_match:
                logger.warning(f"Invalid Personaplex command content: {content}")
                continue

            action = action_match.group(1).lower()
            name = name_match.group(1)

            if action in ("create", "update"):
                if data_match or voice_match:
                    data = {}
                    if data_match:
                        data_str = data_match.group(1).strip()
                        try:
                            # Try parsing as YAML
                            data = yaml.safe_load(data_str)
                            if not isinstance(data, dict):
                                 logger.warning(f"Personaplex data must be a dictionary, got {type(data)}")
                                 data = {}
                        except Exception as e:
                            logger.error(f"Failed to parse Personaplex data: {e}")

                    if voice_match:
                        data["voice_prompt"] = voice_match.group(1).upper()

                    if data:
                        self.save_module(name, data)
                        self.activate_module(name)
                        changed = True
                else:
                    logger.warning(f"Action '{action}' requires data or voice field.")

            elif action == "activate":
                self.activate_module(name)
                changed = True
            elif action == "deactivate":
                self.deactivate_module(name)
                changed = True
            else:
                logger.warning(f"Unknown Personaplex action: {action}")

        return changed
