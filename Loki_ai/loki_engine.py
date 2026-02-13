import logging
import os
import sys
import re
import yaml
import threading
import random
import time
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, List, Dict, Optional, Generator

# Add the current directory to sys.path to ensure local modules are found
sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm.client import LlamaClient
from memory.store import MemoryStore
from persona.manager import PersonaManager
from utils.text_utils import split_into_sentences

logger = logging.getLogger(__name__)

class LokiEngine:
    """Core logic engine for Loki AI, shared between UI and Server."""
    # Unified keywords for various thought/meta tags to ensure consistency across filtering methods
    THOUGHT_KEYWORDS = "THOUGHTS?|INNER MONOLOGUE|THINKING|PLOT|SCHEME|SCHEEM|META|SYSTEM|ACTION|SCENE|LOG"

    def __init__(self, config: dict):
        self.config = config
        self.processing_lock = threading.Lock()
        self._interaction_count = 0
        self.current_user_name = "Tyler"
        self.session_start = datetime.now(timezone.utc)
        self.user_session_info = {}
        # --- LOKI SPECIFIC ---
        self.wardrobe = {
            "default": {
                "name": "Default Outfit",
                "desc": "plain t-shirt and shorts, nothing special",
                "ears": False,
                "active": True
            },
            "midnight_wolves": {
                "name": "Midnight Wolves Hoodie",
                "desc": "oversized black hoodie with floppy wolf ears on the hood and matte Midnight Wolves logo on chest",
                "ears": True,
                "active": True
            }
        }
        self.current_outfit = "default"
        self.intensity = 0.5
        # ---------------------
        # Robust path resolution
        base_path = Path(__file__).parent.resolve()
        # Initialize components with config
        mem_cfg = config.get('memory', {})
        db_path = mem_cfg.get('db_path', './loki_memory')
        if not os.path.isabs(db_path):
            db_path = str((base_path / db_path).resolve())
        self.memory = MemoryStore(
            db_path=db_path,
            collection_name=mem_cfg.get('collection_name', 'loki_ai_memories'),
            max_short_term=mem_cfg.get('max_short_term', 15)
        )
        llm_cfg = config.get('llm', {})
        self.llm = LlamaClient(
            base_url=llm_cfg.get('base_url', 'http://localhost:11434/api'),
            model=llm_cfg.get('model', 'loki:latest'),
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
        # Eternal Learning Brain
        self.brain_file = base_path / "loki_brain.json"
        self.core_anchors = {
            "menace": (0.55, 0.95),
            "sarcasm": (0.75, 1.00),
            "loyalty": (0.60, 1.00),
            "softness": (0.10, 0.60)
        }
        self.brain = self._load_brain()
    def _load_session_objectives(self):
        """Loads session objectives from a local JSON file."""
        obj_path = Path(__file__).resolve().parent / "objectives.json"
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
                        logger.info(f"Loki's Internal Thought: {self.last_thought.strip()}")
                        self.memory.store_insight(f"Thought: {self.last_thought.strip()}", source="inner_monologue")

                    # [OPTIMIZATION] Store session start as a high-importance episodic memory
                    if self._interaction_count == 0:
                        self.memory.store_episodic_memory(f"SESSION START: First interaction with {user_name} today: '{text}'", importance=8)

                    self.memory.add_interaction(text, full_response.strip(), user_id=user_name)
                    self._interaction_count += 1
                    # Periodic reflection (every 10 interactions)
                    if self._interaction_count > 0 and self._interaction_count % 10 == 0:
                         threading.Thread(target=self.reflect, args=(user_name,), daemon=True).start()
                    # Eternal Learning
                    self.loki_learn_and_stay_loki(processed_text, full_response)
            except Exception as e:
                logger.error(f"Engine text processing failed: {e}")
                raise
    def _add_temporal_context(self, context: str, user_name: str) -> str:
        now_utc = datetime.now(timezone.utc)
        # Track session-specific downtime (how long Loki was 'off' before this session)
        if user_name not in self.user_session_info:
            last_time = self.memory.get_last_interaction_time(user_name)
            downtime_str = "first time meeting"
            if last_time:
                downtime_delta = self.session_start - last_time
                if downtime_delta.total_seconds() < 0:
                    downtime_delta = timedelta(0)
                downtime_str = self._format_timedelta(downtime_delta)
            self.user_session_info[user_name] = {"downtime": downtime_str}
        downtime_str = self.user_session_info[user_name]["downtime"]
        last_time = self.memory.get_last_interaction_time(user_name)
        duration_str = "some time"
        if last_time:
            delta = now_utc - last_time
            duration_str = self._format_timedelta(delta)
        uptime_delta = now_utc - self.session_start
        uptime_str = self._format_timedelta(uptime_delta)
        current_time_str = now_utc.astimezone().strftime('%I:%M %p')
        current_date_str = now_utc.astimezone().strftime('%A, %B %d, %Y')
        temporal_note = (
            f"The current time is {current_time_str} on {current_date_str}.\n"
            f"- [DOWNTIME]: You were powered off/inactive for {downtime_str} before this session started.\n"
            f"- [TIME SINCE LAST SEEN]: It has been {duration_str} since your last interaction with {user_name}.\n"
            f"- [SESSION UPTIME]: You have been powered on and active for {uptime_str} this session.\n"
            "You are aware of the passage of time. Mention downtime or duration ONLY if the user asks about it or if you want to complain about being 'off' for too long."
        )
        return f"### [TEMPORAL CONTEXT]\n- {temporal_note}\n\n{context}"
    def _format_timedelta(self, delta: timedelta) -> str:
        """Formats a timedelta into a human-readable string."""
        days = delta.days
        hours, remainder = divmod(int(delta.seconds), 3600)
        minutes, seconds = divmod(remainder, 60)
        time_parts = []
        if days > 0: time_parts.append(f"{days} day{'s' if days > 1 else ''}")
        if hours > 0: time_parts.append(f"{hours} hour{'s' if hours > 1 else ''}")
        if minutes > 0: time_parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        if not time_parts or (days == 0 and hours == 0 and minutes < 5):
            time_parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
        if len(time_parts) == 1:
            return time_parts[0]
        return ", ".join(time_parts[:-1]) + f" and {time_parts[-1]}" if len(time_parts) > 1 else time_parts[0]
    def _extract_thought_from_stream(self, stream):
        buffer = ""
        in_thought = False

        # Patterns to catch various start/end tag formats
        start_pattern = re.compile(rf'\[(?:{self.THOUGHT_KEYWORDS})[^\]]*\]|\((?:{self.THOUGHT_KEYWORDS})[^\)]*\)|(?<!\w)THOUGHTS?:|<THOUGHTS?>|\*(?:Loki\s+)?(?:THOUGHTS?|THINKING|SCHEMING|PLOTTING|THINKS?).*?\*', re.IGNORECASE)
        # Improved end_pattern to catch more varied termination markers for asterisk thoughts
        end_pattern = re.compile(rf'\[/(?:{self.THOUGHT_KEYWORDS})\]|\(/(?:{self.THOUGHT_KEYWORDS})\)|</THOUGHTS?>|\*(?:/THOUGHTS?|END THINKING|END SCHEMING|END|/|THOUGHTS?)\*', re.IGNORECASE)

        for chunk in stream:
            buffer += chunk
            while True:
                if not in_thought:
                    match = start_pattern.search(buffer)
                    if match:
                        pre_tag = buffer[:match.start()]
                        if pre_tag: yield pre_tag
                        tag_content = match.group(0)
                        buffer = buffer[match.end():].lstrip()

                        # Check if it's a block-start (like [THOUGHT]) or a self-contained tag (like [SYSTEM: ...])
                        # A block-start is typically just the keyword itself inside delimiters.
                        is_block_start = False
                        inner_text = re.sub(r'[\[\]\(\)\<\>\*]', '', tag_content).strip()

                        if any(re.fullmatch(k, inner_text, re.IGNORECASE) for k in self.THOUGHT_KEYWORDS.split('|')):
                            is_block_start = True

                        if is_block_start:
                            in_thought = True
                        else:
                            # Self-contained tags are recorded as thoughts immediately
                            self.last_thought += tag_content + " "

                        continue
                    else:
                        if not self.last_thought and buffer.strip().startswith("[") and "]" in buffer:
                            start_idx = buffer.find("[")
                            closing_idx = buffer.find("]")
                            if start_idx < closing_idx:
                                potential_thought = buffer[start_idx+1:closing_idx]
                                if len(potential_thought) > 3:
                                    self.last_thought = potential_thought
                                    buffer = buffer[closing_idx+1:].lstrip()
                                    continue

                        if len(buffer) > 40:
                            yield buffer[:-40]
                            buffer = buffer[-40:]
                        break
                else:
                    match = end_pattern.search(buffer)
                    if match:
                        self.last_thought += buffer[:match.start()]
                        buffer = buffer[match.end():].lstrip()
                        in_thought = False
                        continue
                    else:
                        # Heuristic: if we're in_thought and see a newline followed by direct speech
                        # like "Loki:" or just a capitalized sentence, it might be an unclosed thought.
                        if "\n" in buffer:
                            parts = buffer.split("\n", 1)
                            if len(parts[1]) > 5 and parts[1].strip() and parts[1].strip()[0].isupper():
                                self.last_thought += parts[0]
                                buffer = parts[1]
                                in_thought = False
                                continue

                        if len(buffer) + len(self.last_thought) > 4000:
                            self.last_thought += buffer
                            buffer = ""
                            in_thought = False
                        break
        if buffer:
            if in_thought:
                # If stream ends while in thought, try to find where speech might have started
                # Heuristic: a capitalized letter following punctuation and space, or just a newline
                transition_match = re.search(r'([\.\!\?]\s+|\n\s*)([A-Z])', buffer)
                if transition_match:
                    self.last_thought += buffer[:transition_match.start(2)]
                    yield buffer[transition_match.start(2):]
                elif len(buffer.strip()) > 30 and not any(kw in buffer.upper() for kw in self.THOUGHT_KEYWORDS.split('|')):
                    # If it's long and doesn't look like meta-tags, it's likely speech with a forgotten closing tag
                    self.last_thought += " [Unclosed]"
                    yield buffer
                else:
                    self.last_thought += buffer
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
        # 1. Remove explicit bracketed/parenthesized/tagged thought blocks
        clean = re.sub(rf'\[(?:{self.THOUGHT_KEYWORDS})[^\]]*\].*?\[/(?:{self.THOUGHT_KEYWORDS})\]', '', text, flags=re.IGNORECASE | re.DOTALL)
        clean = re.sub(rf'\((?:{self.THOUGHT_KEYWORDS})[^\)]*\).*?\(/(?:{self.THOUGHT_KEYWORDS})\)', '', clean, flags=re.IGNORECASE | re.DOTALL)
        clean = re.sub(r'<THOUGHTS?>.*?</THOUGHTS?>', '', clean, flags=re.IGNORECASE | re.DOTALL)

        # 2. Remove loose THOUGHT: prefixes and their content that might have leaked
        # Targeted at catching things like "Thought: I am a bot. Hello!" -> "Hello!"
        # [REFINED] Only remove the prefix/tag itself to avoid swallowing the following sentence
        clean = re.sub(rf'(?i)^(?:\[(?:{self.THOUGHT_KEYWORDS})[^\]]*\]|\((?:{self.THOUGHT_KEYWORDS})[^\)]*\)|THOUGHTS?:)\s*', '', clean, count=1).strip()

        # 3. Targeted asterisk thought removal (e.g. *thinks to self* I am a bot.)
        # Only removes if it specifically contains thinking/scheming keywords to avoid removing actions like *winks*
        clean = re.sub(r'(?i)\*(?:Loki\s+)?(?:thinks?|thinking|schem\w+|plott\w+).*?\*', '', clean).strip()

        # 4. Remove any remaining bracketed or parenthesized meta-text (tags only, content preserved if not caught above)
        clean = re.sub(r'\[.*?\](?!\()|(?<!\])\(.*?\)', '', clean).strip()

        # new: strip ALL CAPS starting lines
        lines = clean.splitlines()
        cleaned_lines = []
        for line in lines:
            if line.isupper():
                cleaned_lines.append(line.capitalize())  # downcase to normal
            else:
                cleaned_lines.append(line)
        return '\n'.join(cleaned_lines).strip()
    def reflect(self, user_id: str):
        """Perform deep reflection on history."""
        try:
            with self.processing_lock:
                logger.info(f"Loki is reflecting on {user_id}...")
                history = self.memory.get_history()
                if not history: return
                reflection_prompt = (
                    "Analyze our recent chat history and extract: "
                    "1. User Profile (facts/likes/dislikes), 2. Notable Events, 3. Insights, "
                    "4. A concise summary of this conversation segment. "
                    "Format as YAML with keys: user_facts, events, insights, summary."
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

                # Store summary for RAG optimization
                summary = data.get('summary')
                if summary:
                    self.memory.store_summary(user_id, str(summary))

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
        return "that outfit doesn’t exist yet, idiot. stick to the default for now."
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
        return f"\n=== CURRENT OUTFIT ===\nWearing: {outfit['name']}\nDetails: {outfit['desc']}\nEars active: {ears_line}\nOnly mention outfit details if it fits the reply naturally."
    # ─── ETERNAL LEARNING ───
    def _load_brain(self):
        if not self.brain_file.exists():
            brain = {
                "version": "eternal_1.0",
                "born": time.time(),
                "personality": {k: (v[0] + v[1]) / 2 for k, v in self.core_anchors.items()},
                "trust": 0,
                "facts": {},
                "roasts": [],
                "achievements": [],
                "outfits": ["default"],
            }
            self.brain_file.write_text(json.dumps(brain, indent=2))
        return json.loads(self.brain_file.read_text())
    def loki_learn_and_stay_loki(self, user_msg: str, loki_reply: str):
        brain = self._load_brain()
        msg = user_msg.lower()
        # fact extraction: improve robustness to avoid misinterpreting complaints or questions
        if ("my name is" in msg or "call me" in msg) and "username" not in msg and "?" not in msg:
            # Extract name more carefully
            parts = msg.split("is") if "is" in msg else msg.split("me")
            name = parts[-1].strip(" .,!?")
            if len(name) >= 2 and len(name) < 20: # Sanity check on name length (allow 2+ chars like 'Ty')
                brain["facts"]["preferred_name"] = name.title()
        if "i hate" in msg or "i love" in msg:
            thing = msg.split("hate" if "hate" in msg else "love")[-1].strip()
            brain["facts"][f"user_{'hates' if 'hate' in msg else 'loves'}_{thing}"] = True
        # roast memory
        if any(w in loki_reply.lower() for w in ["idiot","minion","peasant","dummy"]):
            if len(brain["roasts"]) < 50:
                brain["roasts"].append({"roast": loki_reply, "ts": time.time()})
        # personality drift (anchored)
        intensity = self.intensity
        brain.setdefault("mood_history", []).append(intensity)
        brain["mood_history"] = brain["mood_history"][-200:]
        avg_mood = sum(brain["mood_history"]) / len(brain["mood_history"])
        drift = (avg_mood - 0.7) * 0.0008
        brain["personality"]["menace"] = self.clamp(brain["personality"]["menace"] + drift * 1.2, self.core_anchors["menace"])
        brain["personality"]["softness"] = self.clamp(brain["personality"]["softness"] + drift * -1.0, self.core_anchors["softness"])
        brain["personality"]["sarcasm"] = self.clamp(brain["personality"]["sarcasm"] + random.uniform(-0.001, 0.001), self.core_anchors["sarcasm"])
        brain["personality"]["loyalty"] = min(1.0, brain["personality"]["loyalty"] + 0.0005)
        # trust & loyalty
        if any(x in msg for x in ["thank", "good job", "love you"]):
            brain["trust"] = min(100, brain["trust"] + 1)
        # achievements
        if brain["trust"] >= 50 and "first_blood" not in brain["achievements"]:
            brain["achievements"].append("first_blood")
        # hard floor: every 500 messages, gentle pull back to core
        total_messages = len(brain.get("mood_history", []))
        if total_messages % 500 < 5:
            for trait, (mn, mx) in self.core_anchors.items():
                current = brain["personality"][trait]
                center = (mn + mx) / 2
                brain["personality"][trait] = current + (center - current) * 0.15
        self.brain_file.write_text(json.dumps(brain, indent=2))
    def clamp(self, value, min_max):
        mn, mx = min_max
        return max(mn, min(mx, value))
