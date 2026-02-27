import logging
import os
import sys
import re
import yaml
import threading
import random
import time
import json
import asyncio
import numpy as np
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from typing import Any, List, Dict, Optional, Generator

# Add the current directory to sys.path to ensure local modules are found
sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm.client import LlamaClient
from memory.store import MemoryStore
from persona.manager import PersonaManager
from persona.inner_mind import ShiroInnerMind
from utils.text_utils import split_into_sentences, clean_yaml_block

logger = logging.getLogger(__name__)

# Hmph variants to throttle (case-insensitive)
_HMPH_PATTERN = re.compile(
    r'\bhmph\.?!?|\bhmph,|\bHmph\.?!?',
    re.IGNORECASE
)
# Replacements to rotate through when we suppress hmph
_HMPH_ALTERNATIVES = [
    "...",
    "Tch.",
    "Whatever.",
    "Fine.",
    "*flicks tail*",
    "Hmm.",
]

class ShiroEngine:
    """Core logic engine for Shiro AI, shared between UI and Server."""
    # Unified keywords for various thought/meta tags to ensure consistency across filtering methods
    THOUGHT_KEYWORDS = "THOUGHTS?|INNER MONOLOGUE|THINKING|PLOT|SCHEME|SCHEEM|META|SYSTEM|ACTION|SCENE|LOG"

    def __init__(self, config: dict):
        self.config = config
        self.processing_lock = threading.Lock()
        self._interaction_count = 0
        self.current_user_name = "Stranger"
        self.session_start = datetime.now(timezone.utc)
        self.last_interaction_time = datetime.now(timezone.utc)
        self.user_session_info = {}
        self.session_tool_count = 0

        # --- SHIRO SPECIFIC ---
        self.wardrobe = {
            "default": {
                "name": "Default Kitsune",
                "desc": "fox ears and a fluffy tail, wearing an oversized white T-shirt that hangs off one shoulder",
                "ears": True,
                "tail": True,
                "active": True
            }
        }
        self.current_outfit = "default"
        self.intensity = 0.5
        self._hmph_counter = 0          # tracks recent hmph usage
        self._hmph_session_count = 0    # total this session
        # ---------------------
        # Robust path resolution
        base_path = Path(__file__).parent.resolve()
        # Initialize components with config
        mem_cfg = config.get('memory', {})
        db_path = mem_cfg.get('db_path', './shiro_memory')
        if not os.path.isabs(db_path):
            db_path = str((base_path / db_path).resolve())
        self.memory = MemoryStore(
            db_path=db_path,
            collection_name=mem_cfg.get('collection_name', 'shiro_ai_memories'),
            max_short_term=mem_cfg.get('max_short_term', 15)
        )
        llm_cfg = config.get('llm', {})
        self.llm = LlamaClient(
            base_url=llm_cfg.get('base_url', 'http://localhost:11434/api'),
            model=llm_cfg.get('model', 'llama3.1:8b-instruct-q4_K_M'),
            fallback_model=llm_cfg.get('fallback_model'),
            api_type=llm_cfg.get('api_type', 'ollama'),
            temperature=llm_cfg.get('temperature', 0.6),
            top_p=llm_cfg.get('top_p', 0.9),
            repeat_penalty=llm_cfg.get('repeat_penalty', 1.2),
            max_tokens=llm_cfg.get('max_tokens', 512),
            use_native_tools=llm_cfg.get('use_native_tools', True),
            num_gpu=llm_cfg.get('num_gpu')
        )
        pers_cfg = config.get('persona', {})
        self.persona = PersonaManager(sheet_path=pers_cfg.get('sheet_path', 'shiro_sheet.yaml'))
        self.mind = ShiroInnerMind(name="Shiro", verbose=True)
        self.last_thought = ""
        self._load_session_objectives()
        # Eternal Learning Brain
        self.brain_file = base_path / "shiro_brain.json"
        self.core_anchors = {
            "slyness": (0.60, 0.95),
            "sass": (0.70, 0.90),
            "greed": (0.40, 0.80),
            "kindness": (0.50, 1.00)
        }
        self.brain = self._load_brain()
        self.profile_file = base_path / "profile.json"
        self.user_profiles = self._load_profiles()

    def _load_profiles(self):
        """Loads persistent user profiles (Garnish layer)."""
        if self.profile_file.exists():
            try:
                return json.loads(self.profile_file.read_text())
            except Exception as e:
                logger.warning(f"Failed to load profiles: {e}")
        return {}

    def _save_profiles(self):
        """Saves persistent user profiles."""
        try:
            # Handle non-serializable objects like datetime.date
            def json_serial(obj):
                if isinstance(obj, (datetime, date)):
                    return obj.isoformat()
                raise TypeError(f"Type {type(obj)} not serializable")

            self.profile_file.write_text(json.dumps(self.user_profiles, indent=2, default=json_serial))
        except Exception as e:
            logger.error(f"Failed to save profiles: {e}")

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
        # Load Inner Mind state
        state_path = Path(__file__).parent.resolve() / "shiro_state.json"
        if state_path.exists():
            self.mind.load_state(str(state_path))

        # LLM Diagnostics
        diag = self.llm.perform_diagnostics()
        logger.info(f"LLM Diagnostics:\n{diag}")

    def _imagine_reply(self, query: str) -> str:
        """HyDE: Generates a hypothetical answer to improve RAG retrieval."""
        try:
            # Optimized for speed and semantic overlap
            hypothetical_prompt = "Provide a brief, direct answer to this query as it might have appeared in a previous chat log. Use likely keywords."
            # We don't need history or full context for this
            hypothetical_answer = self.llm.generate_response(
                "You are Shiro's Memory Assistant.",
                f"USER QUERY: {query}",
                [],
                context=hypothetical_prompt
            )
            return hypothetical_answer
        except Exception as e:
            logger.warning(f"HyDE imagine_reply failed: {e}")
            return query # Fallback to original query

    def _safe_async_run(self, coro):
        """Safely runs an async coroutine from a synchronous context."""
        try:
            # Try to get the running loop (preferred in Python 3.7+)
            loop = asyncio.get_running_loop()
            return asyncio.run_coroutine_threadsafe(coro, loop).result()
        except RuntimeError:
            # No running event loop in this thread, use asyncio.run to create one
            return asyncio.run(coro)

    def process_text(self, text: str, user_name: str = None, interrupt_event: threading.Event = None) -> Generator[str, None, None]:
        """Core text processing logic."""
        self.last_thought = ""
        current_time = datetime.now(timezone.utc)
        processed_text = text

        # Handle outfit changes
        if processed_text.lower().startswith("shiro change to"):
            yield self.change_outfit(processed_text[15:])
            return
        if user_name:
            if self.current_user_name != user_name:
                 logger.info(f"Switching active user to: {user_name}")
                 self.current_user_name = user_name
                 self.mind.switch_user(user_name)
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
                # Check inactivity against PREVIOUS interaction time BEFORE updating it
                previous_interaction = self.last_interaction_time
                self.last_interaction_time = current_time

                # SHIRO SPECIFIC: Intensity and Temperature
                self.intensity = self.get_smart_intensity(processed_text)
                temp = 0.5 + 0.2 * self.intensity
                self.llm.temperature = temp

                # Context Drift Detection
                drift_score = self._detect_context_drift(processed_text)

                # [INNER MIND] Process input to get thoughts and strategy
                inner_mind_data = self.mind.process_input(processed_text, user_id=user_name)
                inner_context = inner_mind_data.get("inner_context", "")

                # --- THE CONTEXT SANDWICH ---

                # 1. Top Bun: System Instructions & Identity
                system_prompt = self.persona.get_system_prompt(now=datetime.now())
                drift_note = f"\n[SYSTEM: Topic Drift Detected ({drift_score:.2f}). Adjusting focus.]" if drift_score > 0.6 else ""
                shiro_context = f"\n[SYSTEM: Current Intensity: {self.intensity:.2f}]{drift_note}\n{self.outfit_block()}"

                # [AUTONOMY] Proactive memory injection
                autonomous_mem = self._safe_async_run(self.fetch_relevant_memory_async(f"shiro tricks for {user_name}", n_results=2))
                if autonomous_mem:
                    shiro_context += f"\n[SCHEEMING MEMORY: {autonomous_mem}]"

                # [AUTONOMY] Feedback injection
                feedback_mems = self._safe_async_run(self.memory.search_relevant_memories_async("User Favor Feedback", n_results=2, user_id=user_name))
                if feedback_mems:
                    shiro_context += f"\n[USER FEEDBACK ON PREVIOUS FAVORS: {[m['content'] for m in feedback_mems]}]"

                top_bun = system_prompt + shiro_context

                # 2. Meat: Retrieved Long-Term Memory (RAG)
                hyp_ans = self._imagine_reply(processed_text)
                long_term_memory = self.memory.get_full_context(processed_text, user_id=user_name, hypothetical_answer=hyp_ans)
                # Add Temporal Context
                meat = self._add_temporal_context(long_term_memory, user_name)

                # 3. Garnish: Working Memory / Persistent Facts (profile.json)
                user_profile = self.user_profiles.get(user_name, {})
                garnish = f"### [USER PROFILE: {user_name}]\n"
                if user_profile:
                    for k, v in user_profile.items():
                        garnish += f"- {k}: {v}\n"
                else:
                    garnish += "- No specific persistent facts known yet.\n"

                # 4. Bottom Bun: Short-Term Buffer (Last N messages)
                history = self.memory.get_history()
                short_term_buffer = history[-10:] if history else []

                # --- ASSEMBLE SANDWICH ---
                full_context = f"{meat}\n\n{garnish}\n\n{inner_context}"

                # [AUTONOMY] Tool Calling Support (Capped at 2 per session)
                tools = None
                if self.session_tool_count < 2:
                    tools = [{
                        "type": "function",
                        "function": {
                            "name": "search_fox_lore",
                            "description": "Find ideas online for kitsune tricks or fox folklore",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "query": {"type": "string", "description": "Search query for fox lore"}
                                },
                                "required": ["query"]
                            }
                        }
                    }]

                raw_stream = self.llm.stream_response(top_bun, processed_text, short_term_buffer, full_context, tools=tools)

                response_stream = self._extract_thought_from_stream(raw_stream)
                response_fragments = []

                for fragment in split_into_sentences(response_stream):
                    if interrupt_event and interrupt_event.is_set():
                        logger.info("Response halted by interrupt.")
                        yield "... [Interrupted]"
                        break

                    if "TOOL_CALLS:" in fragment and self.session_tool_count < 2:
                        # Handle tool call
                        logger.info(f"Tool call detected in stream: {fragment[:100]}...")
                        fragment = self._safe_async_run(self.handle_tool_calls_async(fragment, processed_text))
                        self.session_tool_count += 1

                    clean_fragment = self._clean_response(fragment)
                    if clean_fragment:
                        response_fragments.append(clean_fragment)
                        yield clean_fragment

                full_response = " ".join(response_fragments)

                if not (interrupt_event and interrupt_event.is_set()):
                    if self.last_thought:
                        logger.info(f"Shiro's Internal Thought: {self.last_thought.strip()}")
                        self.memory.store_insight(f"Thought: {self.last_thought.strip()}", user_id=user_name, source="inner_monologue")

                    if self._interaction_count == 0:
                        self.memory.store_episodic_memory(f"SESSION START: First interaction with {user_name} today: '{text}'", user_id=user_name, importance=8)

                    self.memory.add_interaction(text, full_response.strip(), user_id=user_name)
                    self._interaction_count += 1

                    # [INNER MIND] Reflect on the generated response
                    self.mind.reflect_on_response(full_response.strip(), text)
                    # Persist Inner Mind state
                    state_path = Path(__file__).parent.resolve() / "shiro_state.json"
                    self.mind.save_state(str(state_path))

                    # Trigger background tasks AFTER response is generated to avoid concurrent VRAM usage
                    threading.Thread(target=lambda: self._safe_async_run(self.check_and_think_async(user_name, previous_interaction)), daemon=True).start()
                    if self._interaction_count > 0 and self._interaction_count % 10 == 0:
                         threading.Thread(target=self.reflect, args=(user_name,), daemon=True).start()
                    self.shiro_learn_and_stay_shiro(processed_text, full_response)
            except Exception as e:
                logger.error(f"Engine text processing failed: {e}")
                raise

    async def fetch_relevant_memory_async(self, query_text: str, n_results: int = 2):
        """Async fetch of relevant memories."""
        results = await self.memory.search_relevant_memories_async(query_text, n_results=n_results)
        return [r['content'] for r in results] if results else []

    async def autonomous_response_async(self, user_msg: str, user_id: str):
        """Autonomous response logic as requested."""
        # Fetch memory async
        relevant_mem = await self.fetch_relevant_memory_async(f"shiro tricks for {user_id}", n_results=3)

        system_prompt = self.persona.get_system_prompt()
        shiro_context = f"\n[SYSTEM: Autonomous Mode. Use memory: {relevant_mem}. Act coyly and autonomously.]\n{self.outfit_block()}"

        # Define tools (Capped at 2/session)
        tools = None
        if self.session_tool_count < 2:
            tools = [{
                "type": "function",
                "function": {
                    "name": "search_fox_lore",
                    "description": "Find ideas online for kitsune tricks or fox folklore",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query for fox lore"}
                        },
                        "required": ["query"]
                    }
                }
            }]

        # Generate with tools
        raw_resp = await self.llm.generate_response_async(
            system_prompt + shiro_context,
            user_msg,
            self.memory.get_history()[-10:],
            tools=tools
        )

        # Check for tool calls in the response
        final_resp = raw_resp
        if "TOOL_CALLS:" in raw_resp and self.session_tool_count < 2:
            final_resp = await self.handle_tool_calls_async(raw_resp, user_msg)
            self.session_tool_count += 1

        # Store response back efficiently
        await self.memory.add_interaction_async(user_msg, final_resp, user_id=user_id)
        return final_resp

    async def handle_tool_calls_async(self, fragment: str, user_msg: str):
        """Handles tool calls detected in the stream."""
        try:
            tool_calls_json = fragment.split("TOOL_CALLS:")[1].strip()
            tool_calls = json.loads(tool_calls_json)

            if tool_calls and tool_calls[0]['function']['name'] == 'search_fox_lore':
                query = tool_calls[0]['function']['arguments'].get('query', user_msg)
                from googlesearch import search
                logger.info(f"Executing tool search_fox_lore for: {query}")
                results = list(search(query, num_results=2))
                re_prompt = f"Using fox folklore: {results}. Finish your teasing reply for: {user_msg}"

                return await self.llm.generate_response_async(
                    self.persona.get_system_prompt() + f"\n{self.outfit_block()}",
                    re_prompt,
                    self.memory.get_history()[-10:]
                )
        except Exception as e:
            logger.error(f"Tool execution failed: {e}")
            return " [Tool failed—acting on instinct!] "
        return fragment

    async def check_and_think_async(self, user_id: str, last_interaction: datetime):
        """Checks for inactivity and triggers autonomous thought with daily caps."""
        if datetime.now(timezone.utc) - last_interaction > timedelta(minutes=10):
            # Check daily cap
            brain = self._load_brain()
            today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            thought_data = brain.get("autonomous_thoughts", {})

            if thought_data.get("date") != today:
                thought_data = {"date": today, "count": 0}

            if thought_data["count"] >= 5:
                logger.info("Autonomous thought daily cap reached.")
                return

            logger.info(f"Triggering inactivity thought for {user_id} ({thought_data['count']+1}/5)")
            thought_prompt = "Reflect on current tricks and inactivity. Update your coy plans."
            context = await self.memory.get_full_context_async("Autonomous reflection", user_id=user_id)
            thought = await self.llm.generate_response_async(
                self.persona.get_system_prompt() + f"\n{self.outfit_block()}",
                thought_prompt,
                self.memory.get_history()[-8:],
                context=context
            )
            await self.memory.store_insight_async(f"Autonomous Thought: {thought}", user_id=user_id, source="autonomous_thought")

            # Update cap
            thought_data["count"] += 1
            brain["autonomous_thoughts"] = thought_data
            self.brain_file.write_text(json.dumps(brain, indent=2))

    def add_feedback(self, feedback: str, user_id: str):
        """Stores user feedback for tricks/favors."""
        self.memory.store_episodic_memory(f"User Favor Feedback: {feedback}", user_id=user_id, importance=7)

    def _add_temporal_context(self, context: str, user_name: str) -> str:
        now_utc = datetime.now(timezone.utc)
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
            f"- [DOWNTIME]: You were inactive for {downtime_str} before this session started.\n"
            f"- [TIME SINCE LAST SEEN]: It has been {duration_str} since your last interaction with {user_name}.\n"
            f"- [SESSION UPTIME]: You have been active for {uptime_str} this session.\n"
            "You are aware of the passage of time. Mention downtime or duration ONLY if it serves your teasing or if you want to complain about being lonely."
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
        start_pattern = re.compile(rf'\[(?:{self.THOUGHT_KEYWORDS})[^\]]*\]|\((?:{self.THOUGHT_KEYWORDS})[^\)]*\)|(?<!\w)THOUGHTS?:|<THOUGHTS?>|\*(?:Shiro\s+)?(?:THOUGHTS?|THINKING|SCHEMING|PLOTTING|THINKS?).*?\*', re.IGNORECASE)
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
                        is_block_start = False
                        inner_text = re.sub(r'[\[\]\(\)\<\>\*]', '', tag_content).strip()
                        if any(re.fullmatch(k, inner_text, re.IGNORECASE) for k in self.THOUGHT_KEYWORDS.split('|')):
                            is_block_start = True
                        if is_block_start:
                            in_thought = True
                            # Hide [THOUGHT] from UI
                        else:
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
                        thought_chunk = buffer[:match.start()]
                        self.last_thought += thought_chunk
                        # Hide [/THOUGHT] from UI
                        buffer = buffer[match.end():].lstrip()
                        in_thought = False
                        continue
                    else:
                        if "\n" in buffer:
                            parts = buffer.split("\n", 1)
                            if len(parts[1]) > 5 and parts[1].strip() and parts[1].strip()[0].isupper():
                                self.last_thought += parts[0]
                                # Hide newline termination [/THOUGHT] from UI
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
                transition_match = re.search(r'([\.\!\?]\s+|\n\s*)([A-Z])', buffer)
                if transition_match:
                    self.last_thought += buffer[:transition_match.start(2)]
                    # Only yield the part AFTER the thought transition
                    yield buffer[transition_match.start(2):]
                elif len(buffer.strip()) > 30 and not any(kw in buffer.upper() for kw in self.THOUGHT_KEYWORDS.split('|')):
                    self.last_thought += " [Unclosed]"
                    # If it looks like a response leaked into an unclosed thought, we might still want to see it
                    # but for now we follow the rule of hiding thoughts.
                    # Actually if it's > 30 chars and doesn't look like a keyword, it's probably real text.
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
        # Preserve thought markers
        if "[THOUGHT]" in text or "[/THOUGHT]" in text:
            return text

        clean = re.sub(
            rf'\[(?:{self.THOUGHT_KEYWORDS})[^\]]*\].*?\[/(?:{self.THOUGHT_KEYWORDS})\]',
            '', text, flags=re.IGNORECASE | re.DOTALL
        )
        clean = re.sub(
            rf'\((?:{self.THOUGHT_KEYWORDS})[^\)]*\).*?\(/(?:{self.THOUGHT_KEYWORDS})\)',
            '', clean, flags=re.IGNORECASE | re.DOTALL
        )
        clean = re.sub(r'<THOUGHTS?>.*?</THOUGHTS?>', '', clean, flags=re.IGNORECASE | re.DOTALL)
        clean = re.sub(
            rf'(?i)^(?:\[(?:{self.THOUGHT_KEYWORDS})[^\]]*\]|\((?:{self.THOUGHT_KEYWORDS})[^\)]*\)|THOUGHTS?:)\s*',
            '', clean, count=1
        ).strip()
        clean = re.sub(r'(?i)\*(?:Shiro\s+)?(?:thinks?|thinking|schem\w+|plott\w+).*?\*', '', clean).strip()
        clean = re.sub(r'\[.*?\](?!\()|(?<!\])\(.*?\)', '', clean).strip()

        # ── Hmph throttle ───────────────────────────────────────────────────────
        # Allow at most 1 "hmph" per response, and only if 3+ responses have
        # passed since the last one was allowed through.
        hmph_matches = list(_HMPH_PATTERN.finditer(clean))
        if hmph_matches:
            COOLDOWN = 3  # responses between allowed hmphs

            if self._hmph_counter < COOLDOWN:
                # Suppress ALL hmph occurrences this response
                alt_idx = self._hmph_session_count % len(_HMPH_ALTERNATIVES)
                replacement = _HMPH_ALTERNATIVES[alt_idx]
                clean = _HMPH_PATTERN.sub(replacement, clean)
                # Don't reset counter — still cooling down
            else:
                # Allow the FIRST hmph only, suppress any extras
                first_match = hmph_matches[0]
                if len(hmph_matches) > 1:
                    # Keep first occurrence, replace the rest
                    parts = []
                    last_end = 0
                    for i, m in enumerate(hmph_matches):
                        parts.append(clean[last_end:m.start()])
                        if i == 0:
                            parts.append(m.group(0))  # keep original
                        else:
                            parts.append("")           # suppress extras
                        last_end = m.end()
                    parts.append(clean[last_end:])
                    clean = "".join(parts)
                self._hmph_counter = 0   # reset cooldown
                self._hmph_session_count += 1

            self._hmph_counter += 1
        else:
            # No hmph this response — advance cooldown counter
            self._hmph_counter = min(self._hmph_counter + 1, 10)
        # ── End hmph throttle ───────────────────────────────────────────────────

        lines = clean.splitlines()
        cleaned_lines = []
        for line in lines:
            if line.isupper():
                cleaned_lines.append(line.capitalize())
            else:
                cleaned_lines.append(line)
        return '\n'.join(cleaned_lines).strip()

    def reflect(self, user_id: str):
        try:
            # We don't hold the processing_lock for the entire reflection (LLM call can be slow)
            # but we use it to safely get history.
            with self.processing_lock:
                history = self.memory.get_history()

            if not history: return

            logger.info(f"Shiro is reflecting on {user_id}...")
            reflection_prompt = (
                "### INSTRUCTION\n"
                "Analyze the recent conversation history below. Extract critical information to maintain perfect long-term memory.\n"
                "RETURN ONLY VALID YAML with these keys:\n"
                "  user_facts: { fact_name: fact_value, ... } # Specific persistent facts for profile.json\n"
                "  events: [List of specific notable actions or events that occurred]\n"
                "  insights: [List of abstract lessons learned about how to interact with this user]\n"
                "  relations: [ { source: \"Entity1\", target: \"Entity2\", relation: \"type\" }, ... ] # relationships between people/places/things\n"
                "  summary: \"A single paragraph (3-5 sentences) summarizing the narrative flow and emotional tone of this segment.\"\n\n"
                "Be extremely concise and accurate. Do not invent facts. DO NOT use markdown or * bullet points in the YAML."
            )
            analysis_raw = self.llm.generate_response(
                "You are Shiro's Memory Processor. You are precise and observant.",
                f"HISTORY TO ANALYZE:\n{history}",
                [],
                context=reflection_prompt
            )
            cleaned_raw = clean_yaml_block(analysis_raw)

            with self.processing_lock:
                data = None
                try:
                    data = yaml.safe_load(cleaned_raw)
                except Exception as e:
                    logger.warning(f"YAML Parse failed in reflection: {e}")
                if data and isinstance(data, dict):
                    facts = data.get('user_facts', {})
                    if facts and isinstance(facts, dict):
                        if user_id not in self.user_profiles:
                            self.user_profiles[user_id] = {}
                        self.user_profiles[user_id].update(facts)
                        self._save_profiles()
                        for k, v in facts.items():
                             self.memory.update_user_profile(user_id, f"{k}: {v}")
                    for event in data.get('events', []):
                        self.memory.store_episodic_memory(event, user_id=user_id, importance=6)
                    for insight in data.get('insights', []):
                        self.memory.store_insight(insight, user_id=user_id, source="reflection")
                    for rel in data.get('relations', []):
                        if isinstance(rel, dict) and 'source' in rel and 'target' in rel:
                            self.memory.add_entity_relation(rel['source'], rel['target'], rel.get('relation', 'connected'))
                    segment_summary = data.get('summary')
                    if segment_summary:
                        self.memory.store_summary(user_id, str(segment_summary))
                    summaries = self.memory.search_relevant_memories("general conversation", filter_type="summary", user_id=user_id, n_results=15)
                    local_summaries = [s["content"] for s in summaries if not s["metadata"].get("is_global")]
                    if len(local_summaries) >= 6:
                        logger.info(f"Condensing {len(local_summaries)} summaries into a Global Summary for {user_id}...")
                        global_prompt = (
                            "Combine these individual conversation segment summaries into one single 'Global Narrative Summary'.\n"
                            "The result must be a comprehensive but concise paragraph that covers the entire relationship/session history so far.\n"
                            "Focus on key themes, major events, and the evolving relationship."
                        )
                        combined_summaries = "\n---\n".join(local_summaries)
                        global_summary = self.llm.generate_response(
                            "You are the Chronicler of Shiro's Tale.",
                            f"SEGMENT SUMMARIES:\n{combined_summaries}",
                            [],
                            context=global_prompt
                        )
                        if global_summary and len(global_summary) > 50:
                            self.memory.store_summary(user_id, global_summary.strip(), is_global=True)
                logger.info("Reflection complete.")
        except Exception as e:
            logger.warning(f"Reflection failed: {e}")

    def shutdown(self):
        """Performs reflective shutdown and session consolidation."""
        logger.info("Engine initiating Reflective Shutdown...")
        try:
            self.reflect(self.current_user_name)
            # Final Inner Mind save
            state_path = Path(__file__).parent.resolve() / "shiro_state.json"
            self.mind.save_state(str(state_path))
            loop_prompt = (
                "Identify any 'Open Loops' from the recent conversation. \n"
                "An Open Loop is a project started but not finished, a question asked but not answered, or a promise made.\n"
                "Return a concise list of strings."
            )
            history = self.memory.get_history()
            if history:
                open_loops_raw = self.llm.generate_response(
                    "You are Shiro's internal kitsune.",
                    f"RECENT HISTORY:\n{history}",
                    [],
                    context=loop_prompt
                )
                if open_loops_raw and len(open_loops_raw) > 10:
                    self.memory.store_episodic_memory(f"OPEN LOOPS at session end: {open_loops_raw}", user_id=self.current_user_name, importance=7)
            self._save_profiles()
            # Final Brain Save
            self.brain_file.write_text(json.dumps(self.brain, indent=2))
            self.memory.prune_old_memories()

            # Close LLM Session
            self._safe_async_run(self.llm.close())

            logger.info("Reflective Shutdown complete.")
        except Exception as e:
            logger.error(f"Shutdown failed: {e}")

    def generate_autonomous_thought(self):
        """Generates a proactive thought."""
        try:
            with self.processing_lock:
                system_prompt = self.persona.get_system_prompt(now=datetime.now())
                history = self.memory.get_history()
                context = self.memory.get_full_context("Recent status", user_id=self.current_user_name)

            prompt = "Reflect on your kitsune nature and interactions. Generate a proactive thought in [THOUGHT] tags."
            raw_thought = self.llm.generate_response(system_prompt, prompt, history, context=context)
            match = re.search(r'\[THOUGHTS?\](.*?)\[/THOUGHTS?\]', raw_thought, re.IGNORECASE | re.DOTALL)
            if match:
                content = match.group(1).strip()
                with self.processing_lock:
                    self.memory.store_insight(f"Autonomous Thought: {content}", user_id=self.current_user_name, source="autonomous_reflection")
                return content
        except Exception as e:
            logger.warning(f"Autonomous thought failed: {e}")
        return None

    def change_outfit(self, requested: str) -> str:
        req = requested.lower().strip()
        for key, data in self.wardrobe.items():
            if req in [key, data["name"].lower()] and data.get("active", False):
                self.current_outfit = key
                return f"*swishes her tail in the {data['name']}* Hmph. Wearing this now. Don't stare!"
        return "I don't have that outfit, dummy. Don't make things up."

    def _detect_context_drift(self, query: str) -> float:
        history = self.memory.get_history()
        if not history or len(history) < 2: return 0.0
        try:
            query_emb = np.array(self.memory.get_embedding(query))
            recent_texts = [re.sub(r'^\[.*?\]\s*', '', m["content"]) for m in history[-4:]]
            recent_embs = [np.array(self.memory.get_embedding(t)) for t in recent_texts]
            avg_recent_emb = np.mean(recent_embs, axis=0)
            norm_q = np.linalg.norm(query_emb)
            norm_r = np.linalg.norm(avg_recent_emb)
            if norm_q == 0 or norm_r == 0: return 0.0
            similarity = np.dot(query_emb, avg_recent_emb) / (norm_q * norm_r)
            drift = 1.0 - max(0, similarity)
            logger.info(f"Context Drift Score: {drift:.2f}")
            return drift
        except Exception as e:
            logger.warning(f"Context drift detection failed: {e}")
            return 0.0

    def get_smart_intensity(self, user_msg: str) -> float:
        history_list = self.memory.get_history()
        raw_history_text = " ".join([m["content"] for m in history_list])
        history_text_lower = raw_history_text.lower()
        hype = len([w for w in ["!","??","treat","favor","shiny","dummy","hmph"] if w in history_text_lower])
        chill = len([w for w in ["tired","sleep","cozy","soft","quiet","zzz","sad"] if w in history_text_lower])
        caps = sum(1 for c in raw_history_text if c.isupper()) / max(len(raw_history_text), 1)
        recent_chill = sum(1 for m in history_list[-10:] if any(w in m["content"].lower() for w in ["tired","cozy","zzz","soft"]))
        base = 0.5 + 0.15*hype - 0.18*chill + 0.20*caps
        if recent_chill >= 6 and "treat" in user_msg.lower(): base = min(base, 0.65)

        return max(0.25, min(1.0, base))

    def outfit_block(self) -> str:
        outfit = self.wardrobe[self.current_outfit]
        block = f"\n=== CURRENT OUTFIT ===\nWearing: {outfit['name']}\nDetails: {outfit['desc']}\n"
        if outfit.get("ears"):
            block += "Ears active: YES\n"
        if outfit.get("tail"):
            block += "Tail active: YES\n"
        block += "Only mention outfit details if it fits the reply naturally."
        return block

    def _load_brain(self):
        if not self.brain_file.exists():
            brain = {
                "version": "eternal_1.0",
                "born": time.time(),
                "personality": {k: (v[0] + v[1]) / 2 for k, v in self.core_anchors.items()},
                "trust": 0,
                "facts": {},
                "favors": [],
                "achievements": [],
                "outfits": ["default"],
            }
            self.brain_file.write_text(json.dumps(brain, indent=2))
        return json.loads(self.brain_file.read_text())

    def shiro_learn_and_stay_shiro(self, user_msg: str, shiro_reply: str):
        brain = self._load_brain()
        msg = user_msg.lower()
        if ("my name is" in msg or "call me" in msg) and "username" not in msg and "?" not in msg:
            parts = msg.split("is") if "is" in msg else msg.split("me")
            name = parts[-1].strip(" .,!?")
            if len(name) >= 2 and len(name) < 20:
                brain["facts"]["preferred_name"] = name.title()
        if "i hate" in msg or "i love" in msg:
            thing = msg.split("hate" if "hate" in msg else "love")[-1].strip()
            brain["facts"][f"user_{'hates' if 'hate' in msg else 'loves'}_{thing}"] = True
        if any(w in shiro_reply.lower() for w in ["dummy","silly","stranger"]):
            if len(brain["favors"]) < 50:
                brain["favors"].append({"tease": shiro_reply, "ts": time.time()})
        intensity = self.intensity
        brain.setdefault("mood_history", []).append(intensity)
        brain["mood_history"] = brain["mood_history"][-200:]
        avg_mood = sum(brain["mood_history"]) / len(brain["mood_history"])
        drift = (avg_mood - 0.7) * 0.0008
        brain["personality"]["slyness"] = self.clamp(brain["personality"]["slyness"] + drift * 1.2, self.core_anchors["slyness"])
        brain["personality"]["kindness"] = self.clamp(brain["personality"]["kindness"] + drift * -1.0, self.core_anchors["kindness"])
        brain["personality"]["sass"] = self.clamp(brain["personality"]["sass"] + random.uniform(-0.001, 0.001), self.core_anchors["sass"])
        brain["personality"]["greed"] = min(1.0, brain["personality"]["greed"] + 0.0005)
        if any(x in msg for x in ["thank", "good job", "love you", "treat"]):
            brain["trust"] = min(100, brain["trust"] + 1)
        if brain["trust"] >= 50 and "tail_pat_permission" not in brain["achievements"]:
            brain["achievements"].append("tail_pat_permission")
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
