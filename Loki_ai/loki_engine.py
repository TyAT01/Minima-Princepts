import logging
import os
import sys
import re
import yaml
import threading
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Dict, Optional, Generator

# Add the current directory to sys.path to ensure local modules are found
sys.path.append(str(Path(__file__).parent))

from llm.client import LlamaClient
from memory.store import MemoryStore
from persona.manager import PersonaManager
from utils.text_utils import split_into_sentences

logger = logging.getLogger(__name__)

class LokiEngine:
    """Core logic engine for Loki AI, shared between UI and Server."""

    def __init__(self, config: dict):
        self.config = config
        self.processing_lock = threading.Lock()
        self._interaction_count = 0
        self.current_user_name = "Tyler"
        self.session_start = datetime.now(timezone.utc)

        # --- LOKI SPECIFIC ---
        self.wardrobe = {
            "midnight_wolves": {
                "name": "Midnight Wolves Hoodie",
                "desc": "oversized black hoodie with floppy wolf ears on the hood and matte Midnight Wolves logo on chest",
                "ears": True,
                "active": True
            }
        }
        self.current_outfit = "midnight_wolves"
        self.intensity = 0.5
        # ---------------------

        # Initialize components with config
        mem_cfg = config.get('memory', {})
        self.memory = MemoryStore(
            db_path=mem_cfg.get('db_path', './loki_memory'),
            collection_name=mem_cfg.get('collection_name', 'loki_ai_memories'),
            max_short_term=mem_cfg.get('max_short_term', 15)
        )

        llm_cfg = config.get('llm', {})
        self.llm = LlamaClient(
            base_url=llm_cfg.get('base_url', 'http://localhost:11434/api'),
            model=llm_cfg.get('model', 'llama3.1:8b-instruct-q4_K_M'),
            api_type=llm_cfg.get('api_type', 'ollama'),
            temperature=llm_cfg.get('temperature', 0.9),
            top_p=llm_cfg.get('top_p', 0.92),
            repeat_penalty=llm_cfg.get('repeat_penalty', 1.08),
            max_tokens=llm_cfg.get('max_tokens', 512)
        )

        pers_cfg = config.get('persona', {})
        self.persona = PersonaManager(sheet_path=pers_cfg.get('sheet_path', 'loki_sheet.yaml'))

        self.last_thought = ""
        self._load_session_objectives()

    def _load_session_objectives(self):
        """Loads session objectives from a local JSON file."""
        obj_path = Path(__file__).parent / "objectives.json"
        if obj_path.exists():
            try:
                with open(obj_path, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f)
                    self.memory.session_objectives = data.get('objectives', [])
            except Exception as e:
                logger.warning(f"Failed to load objectives: {e}")

    def initialize(self):
        """Initializes components."""
        self.persona.load_persona()
        # LLM Diagnostics
        diag = self.llm.perform_diagnostics()
        logger.info(f"LLM Diagnostics:\n{diag}")

    def process_text(self, text: str, user_name: str = None, interrupt_event: threading.Event = None) -> Generator[str, None, None]:
        """Core text processing logic."""
        self.last_thought = ""
        processed_text = text

        # Handle outfit changes
        if processed_text.lower().startswith("loki change to"):
            yield self.change_outfit(processed_text[14:])
            return

        if user_name:
            if self.current_user_name != user_name:
                 logger.info(f"Switching active user to: {user_name}")
                 self.current_user_name = user_name
        else:
            user_name = self.current_user_name

        # Identity Verification Heuristic
        if processed_text and not processed_text.startswith("[") and user_name != "System":
             last_seen = self.memory.get_last_interaction_time(user_name)
             if last_seen and (datetime.now(timezone.utc) - last_seen).days > 7:
                  processed_text = f"[IDENTITY CHECK REQUIRED] {processed_text}"

        logger.info(f"--- Engine Processing: '{processed_text}' ---")

        with self.processing_lock:
            try:
                # LOKI SPECIFIC: Intensity and Temperature
                self.intensity = self.get_smart_intensity(processed_text)
                temp = 0.75 + 0.25 * self.intensity
                self.llm.temperature = temp

                # Refresh system prompt with current time
                system_prompt = self.persona.get_system_prompt(now=datetime.now())

                # Add Loki context
                loki_context = f"\n[SYSTEM: Current Intensity: {self.intensity:.2f}]\n{self.outfit_block()}"
                system_prompt += loki_context

                history = self.memory.get_history()
                context = self.memory.get_full_context(processed_text, user_id=user_name)

                # Add Temporal Context
                context = self._add_temporal_context(context, user_name)

                # Combined Phase
                raw_stream = self.llm.stream_response(system_prompt, processed_text, history, context)
                response_stream = self._extract_thought_from_stream(raw_stream)

                response_fragments = []
                for fragment in split_into_sentences(response_stream):
                    if interrupt_event and interrupt_event.is_set():
                        logger.info("Response halted by interrupt.")
                        yield "... [Interrupted]"
                        break

                    clean_fragment = self._clean_response(fragment)
                    if clean_fragment:
                        response_fragments.append(clean_fragment)
                        yield clean_fragment

                full_response = " ".join(response_fragments)

                if not (interrupt_event and interrupt_event.is_set()):
                    if self.last_thought:
                        self.memory.store_insight(f"Thought: {self.last_thought.strip()}", source="inner_monologue")
                    self.memory.add_interaction(text, full_response.strip(), user_id=user_name)
                    self._interaction_count += 1

                    # Periodic reflection (every 10 interactions)
                    if self._interaction_count > 0 and self._interaction_count % 10 == 0:
                         threading.Thread(target=self.reflect, args=(user_name,), daemon=True).start()

            except Exception as e:
                logger.error(f"Engine text processing failed: {e}")
                raise

    def _add_temporal_context(self, context: str, user_name: str) -> str:
        now_utc = datetime.now(timezone.utc)
        last_time = self.memory.get_last_interaction_time(user_name)

        duration_str = "some time"
        if last_time:
            delta = now_utc - last_time
            days = delta.days
            hours, remainder = divmod(int(delta.seconds), 3600)
            minutes, _ = divmod(remainder, 60)
            time_parts = []
            if days > 0: time_parts.append(f"{days} day{'s' if days > 1 else ''}")
            if hours > 0: time_parts.append(f"{hours} hour{'s' if hours > 1 else ''}")
            if minutes > 0 or not time_parts: time_parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
            duration_str = ", ".join(time_parts[:-1]) + (f" and {time_parts[-1]}" if len(time_parts) > 1 else time_parts[0])

        uptime_delta = now_utc - self.session_start
        up_hours, up_rem = divmod(int(uptime_delta.seconds), 3600)
        up_mins, _ = divmod(up_rem, 60)
        uptime_str = f"{up_hours}h {up_mins}m" if up_hours > 0 else f"{up_mins} minutes"

        current_time_str = now_utc.astimezone().strftime('%I:%M %p')
        current_date_str = now_utc.astimezone().strftime('%A, %B %d, %Y')

        temporal_note = (
            f"The current time is {current_time_str} on {current_date_str}.\n"
            f"- [TIME SINCE LAST SEEN]: It has been {duration_str} since you last spoke with {user_name}.\n"
            f"- [SESSION UPTIME]: You have been powered on/active for {uptime_str} in this specific session.\n"
            "You are aware of the passage of time. ONLY mention it if asked."
        )
        return f"### [TEMPORAL CONTEXT]\n- {temporal_note}\n\n{context}"

    def _extract_thought_from_stream(self, stream):
        buffer = ""
        in_thought = False
        start_pattern = re.compile(r'\[THOUGHTS?\]|\(THOUGHTS?\)|\[INNER MONOLOGUE\]|\[THINKING\]', re.IGNORECASE)
        end_pattern = re.compile(r'\[/THOUGHTS?\]|\(/THOUGHTS?\)|\[/INNER MONOLOGUE\]|\[/THINKING\]', re.IGNORECASE)

        for chunk in stream:
            buffer += chunk
            while True:
                if not in_thought:
                    match = start_pattern.search(buffer)
                    if match:
                        pre_tag = buffer[:match.start()]
                        if pre_tag: yield pre_tag
                        buffer = buffer[match.end():]
                        in_thought = True
                        continue
                    else:
                        if not self.last_thought and buffer.strip().startswith("[") and "]" in buffer:
                            start_idx = buffer.find("[")
                            closing_idx = buffer.find("]")
                            if start_idx < closing_idx:
                                self.last_thought = buffer[start_idx+1:closing_idx]
                                buffer = buffer[closing_idx+1:].lstrip()
                                continue
                        if len(buffer) > 25:
                            yield buffer[:-25]
                            buffer = buffer[-25:]
                        break
                else:
                    match = end_pattern.search(buffer)
                    if match:
                        self.last_thought += buffer[:match.start()]
                        buffer = buffer[match.end():]
                        in_thought = False
                        continue
                    else:
                        if len(buffer) + len(self.last_thought) > 4000:
                            self.last_thought += buffer
                            buffer = ""
                            in_thought = False
                        break
        if buffer:
            if in_thought:
                if len(buffer) > 50 or "." in buffer:
                    self.last_thought += " [Unclosed]"
                    yield buffer
                else: self.last_thought += buffer
            else:
                if not self.last_thought and buffer.strip().startswith("[") and "]" in buffer:
                    start_idx = buffer.find("[")
                    closing_idx = buffer.find("]")
                    if start_idx < closing_idx:
                        pre_bracket = buffer[:start_idx]
                        if pre_bracket: yield pre_bracket
                        self.last_thought = buffer[start_idx+1:closing_idx]
                        yield buffer[closing_idx+1:].strip()
                        return
                yield buffer

    def _clean_response(self, text: str) -> str:
        clean = re.sub(r'\[(THOUGHT|INNER MONOLOGUE|THINKING|ACTION|SCENE|META|SYSTEM)\].*?\[/(THOUGHT|INNER MONOLOGUE|THINKING|ACTION|SCENE|META|SYSTEM)\]', '', text, flags=re.IGNORECASE | re.DOTALL)
        clean = re.sub(r'\(THOUGHT\).*?\(/THOUGHT\)', '', clean, flags=re.IGNORECASE | re.DOTALL)
        clean = re.sub(r'\[.*?\](?!\()|(?<!\])\(.*?\)', '', clean).strip()
        return clean

    def reflect(self, user_id: str):
        """Perform deep reflection on history."""
        try:
            with self.processing_lock:
                logger.info(f"Loki is reflecting on {user_id}...")
                history = self.memory.get_history()
                if not history: return
                reflection_prompt = (
                    "Analyze our recent chat history and extract: "
                    "1. User Profile (facts/likes/dislikes), 2. Notable Events, 3. Insights. "
                    "Format as YAML with keys: user_facts, events, insights."
                )
                analysis_raw = self.llm.generate_response("You are Loki, analyzing memories.", f"History: {history}", [], context=reflection_prompt)

            cleaned_raw = self._clean_yaml_block(analysis_raw)
            data = None
            try:
                data = yaml.safe_load(cleaned_raw)
            except:
                pass

            if data and isinstance(data, dict):
                for fact in data.get('user_facts', []):
                    self.memory.update_user_profile(user_id, str(fact))
                for event in data.get('events', []):
                    self.memory.store_episodic_memory(event)
                for insight in data.get('insights', []):
                    self.memory.store_insight(insight, source="reflection")
                logger.info("Reflection complete.")
        except Exception as e:
            logger.warning(f"Reflection failed: {e}")

    def _clean_yaml_block(self, text: str) -> str:
        if "```yaml" in text: text = text.split("```yaml")[1].split("```")[0]
        elif "```yml" in text: text = text.split("```yml")[1].split("```")[0]
        elif "```" in text: text = text.split("```")[1].split("```")[0]
        lines = text.strip().splitlines()
        if lines and lines[0].strip().lower() in ["yml", "yaml"]: text = "\n".join(lines[1:])
        return text.strip()

    def generate_autonomous_thought(self):
        """Generates a proactive thought."""
        try:
            with self.processing_lock:
                system_prompt = self.persona.get_system_prompt(now=datetime.now())
                history = self.memory.get_history()
                context = self.memory.get_full_context("Recent status", user_id=self.current_user_name)
                prompt = "Reflect on your objectives and interactions. Generate a proactive thought in [THOUGHT] tags."
                raw_thought = self.llm.generate_response(system_prompt, prompt, history, context=context)
                match = re.search(r'\[THOUGHTS?\](.*?)\[/THOUGHTS?\]', raw_thought, re.IGNORECASE | re.DOTALL)
                if match:
                    content = match.group(1).strip()
                    self.memory.store_insight(f"Autonomous Thought: {content}", source="autonomous_reflection")
                    return content
        except Exception as e:
            logger.warning(f"Autonomous thought failed: {e}")
        return None

    def change_outfit(self, requested: str) -> str:
        req = requested.lower().strip()
        for key, data in self.wardrobe.items():
            if req in [key, data["name"].lower()] and data.get("active", False):
                self.current_outfit = key
                return f"*throws on the {data['name']}* fine. now wearing that. happy?"
        return "that outfit doesn’t exist yet, idiot. stick to the Midnight Wolves hoodie for now."

    def get_smart_intensity(self, user_msg: str) -> float:
        history_list = self.memory.get_history()
        raw_history_text = " ".join([m["content"] for m in history_list])
        history_text_lower = raw_history_text.lower()

        hype = len([w for w in ["!","??","raid","plan","now","chaos","idiot","minion"] if w in history_text_lower])
        chill = len([w for w in ["tired","sleep","cozy","soft","quiet","zzz","sad"] if w in history_text_lower])
        caps = sum(1 for c in raw_history_text if c.isupper()) / max(len(raw_history_text), 1)
        recent_chill = sum(1 for m in history_list[-10:] if any(w in m["content"].lower() for w in ["tired","cozy","zzz","soft"]))
        base = 0.5 + 0.15*hype - 0.18*chill + 0.20*caps
        if recent_chill >= 6 and "raid" in user_msg.lower(): base = min(base, 0.65)
        return max(0.25, min(1.0, base))

    def outfit_block(self) -> str:
        outfit = self.wardrobe[self.current_outfit]
        ears_line = "YES – you can say 'the ears hear everything'" if outfit.get("ears") else "NO ears today"
        return f"\n=== CURRENT OUTFIT ===\nWearing: {outfit['name']}\nDetails: {outfit['desc']}\nEars active: {ears_line}\n"
