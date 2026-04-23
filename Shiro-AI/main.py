from __future__ import annotations
import logging
import os
import sys
import re
import subprocess
import yaml
import signal
import threading
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add the current directory to sys.path
sys.path.append(str(Path(__file__).parent))

# [FIX] ctranslate2 ROCm path workaround for Windows
if sys.platform == "win32":
    try:
        import ctranslate2
    except ImportError:
        pass
    except FileNotFoundError as e:
        if "_rocm" in str(e).lower():
            match = re.search(r"'(.*?)'", str(e))
            if match:
                missing_path = match.group(1)
                try:
                    abs_path = os.path.abspath(missing_path)
                    os.makedirs(abs_path, exist_ok=True)
                    print(f"[System] Applied ctranslate2 ROCm path fix: Created {abs_path}")
                    import ctranslate2
                except Exception:
                    pass

from stt.whisper import STTSystem, VoiceMonitor
from ui.web_gui import ShiroGUI
from utils.error_handler import ErrorHandler
from shiro_engine import ShiroEngine
from shiro_sqlite_memory import SystemAwareness
from tts import ShiroTTS, check_tts_server
from queue import Queue

try:
    from voice_room import VoiceRoom
    VOICE_ROOM_AVAILABLE = True
except ImportError:
    VOICE_ROOM_AVAILABLE = False


class ShiroApp:
    def __init__(self, config_path: str = "config.yaml"):
        self.config = self._load_config(config_path)
        self.engine = ShiroEngine(self.config, on_autonomous_speak=self.handle_autonomous_speak)
        self.error_handler = ErrorHandler(ai_comment_callback=self.ai_comment_on_error)

        stt_cfg = self.config.get('stt', {})
        self.stt = STTSystem(
            model_size=stt_cfg.get('model_size', 'base'),
            device=stt_cfg.get('device'),
            compute_type=stt_cfg.get('compute_type', 'float16')
        )

        self.results_queue = Queue()
        self.interrupt_event = threading.Event()
        self.is_responding = False
        self.active_users = set()
        # Queue for messages that arrive while engine is busy.
        # Stores (text, user_name, user_id) tuples — processed after current response finishes.
        self._pending_queue: list = []
        self._pending_lock = threading.Lock()

        self.voice_monitor = VoiceMonitor(
            callback=self.process_background_audio,
            interrupt_callback=self.handle_interrupt
        )

        # ── TTS ───────────────────────────────────────────────────────────────
        tts_cfg = self.config.get('tts', {}).copy()

        # Override enabled state if SHIRO_VOICE_DISABLED environment variable is set
        if os.environ.get("SHIRO_VOICE_DISABLED") == "1":
            logging.info("[System] Voice disabled via environment variable (model missing).")
            tts_cfg['enabled'] = False

        self.tts = ShiroTTS(tts_cfg)
        if tts_cfg.get('enabled', False):
            if not check_tts_server(tts_cfg.get('model_path', 'kokoro/kokoro-v0_19.onnx')):
                logging.warning(
                    "⚠️  Kokoro TTS model not found — Shiro will run text-only.\n"
                    "    Check your kokoro/ directory or config.yaml tts.model_path."
                )
                self.tts.enabled = False
            else:
                self.tts.start()
        self.engine._tts_ref = self.tts

        # ── Discord ───────────────────────────────────────────────────────────
        self._discord_bridge = None
        dc_cfg = self.config.get('discord', {})
        if dc_cfg.get('enabled', False):
            self._init_discord(dc_cfg)
        else:
            logging.info("[Discord] Disabled in config (discord.enabled = false). "
                         "Enable and add token to use.")

        # ── Meditation bridge ─────────────────────────────────────────────────
        try:
            from meditation.shiro_meditation_bridge import create_meditation_bridge
            med_cfg = self.config.get('meditation', {})
            self._meditation_bridge = create_meditation_bridge(
                shiro_root=str(Path(__file__).parent.resolve()),
                primary_user=self.config.get('primary_user', 'Tyler'),
                idle_trigger_seconds=med_cfg.get('idle_trigger_seconds', 600),
            )
            self._meditation_bridge.start()
            logging.info("[Meditation] Bridge started and GUI callbacks wired.")
        except Exception as _med_err:
            logging.warning(f"[Meditation] Bridge unavailable: {_med_err}")
            self._meditation_bridge = None

        # ── Server state (LAN voice room + public cloudflared tunnel) ─────────
        # Both servers are OFF by default — started on-demand from the GUI.
        self._voice_room = None           # VoiceRoom instance when running
        self._cf_proc    = None           # cloudflared subprocess when running
        self._lan_url    = ""             # LAN URL when running
        self._pub_url    = ""             # Public URL when tunnel is up
        self._pub_starting = False        # True while tunnel is negotiating URL
        self._server_lock = threading.Lock()

        # ── GUI ───────────────────────────────────────────────────────────────
        ui_cfg = self.config.get('ui', {})
        self.gui = ShiroGUI(
            process_text_cb=self.process_text,
            process_audio_cb=self.process_audio,
            toggle_mic_cb=self.toggle_mic,
            poll_results_cb=self.poll_results,
            join_chat_cb=self.handle_user_join,
            leave_chat_cb=self.handle_user_leave,
            set_typing_cb=self.set_user_typing,
            # Discord hooks
            discord_get_status_cb=self._discord_get_status,
            discord_start_cb=self._discord_start,
            discord_stop_cb=self._discord_stop,
            discord_join_voice_cb=self._discord_join_voice,
            discord_leave_voice_cb=self._discord_leave_voice,
            discord_send_text_cb=self._discord_send_text,
            # Vision hooks
            vision_toggle_cb=self._vision_toggle,
            vision_status_cb=self._vision_status,
            toggle_deafen_cb=self.toggle_deafen,
            title=ui_cfg.get('title', "🦊 Shiro: The Sly Kitsune Yaoguai"),
            theme=ui_cfg.get('theme', "soft")
        )
        # Wire server callbacks into GUI after construction
        self.gui.lan_start_cb         = self._server_start_lan
        self.gui.lan_stop_cb          = self._server_stop_lan
        self.gui.public_start_cb      = self._server_start_public
        self.gui.public_stop_cb       = self._server_stop_public
        self.gui.get_server_status_cb = self._server_get_status

        # Wire streaming callbacks into GUI
        self.gui.stream_set_cb    = self.engine.set_streaming
        self.gui.stream_status_cb = self.engine.get_streaming_status

        # Wire LLM model selection callbacks into GUI
        self.gui.llm_list_models_cb = self.engine.llm.list_models
        self.gui.llm_set_model_cb    = self.engine.llm.set_model

        # Wire memory management callbacks into GUI
        self.gui.memory_summary_cb     = self.engine.get_memory_summary
        self.gui.memory_add_goal_cb    = self.engine.add_goal_from_gui
        self.gui.memory_delete_goal_cb = self.engine.delete_goal_by_id

        # Wire meditation callbacks into GUI
        if self._meditation_bridge:
            self.gui.meditation_status_cb   = self._meditation_bridge.get_gui_status
            self.gui.meditation_begin_cb    = self._meditation_bridge.begin_manual
            self.gui.meditation_wake_cb     = self._meditation_bridge.wake_manual
            self.gui.meditation_thoughts_cb = self._meditation_bridge.get_recent_thoughts
            self.gui.meditation_stats_cb    = self._meditation_bridge.get_session_stats

        # ── Book Reader ───────────────────────────────────────────────────────
        try:
            from book_reader import BookReader  # book_reader/__init__.py exports this
            self._book_reader = BookReader(
                books_dir    = str(Path(__file__).parent / "book_reader" / "books"),
                memories_dir = str(Path(__file__).parent / "book_reader" / "book_memories"),
            )
            self.gui.book_list_cb     = self._book_list
            self.gui.book_digest_cb   = self._book_digest
            self.gui.book_digested_cb = self._book_list_digested
            logging.info("[Books] BookReader ready.")
        except Exception as _br_err:
            logging.warning(f"[Books] BookReader unavailable: {_br_err}")
            self._book_reader = None

    # ── Config ────────────────────────────────────────────────────────────────

    def _load_config(self, path: str) -> dict:
        try:
            if not os.path.exists(path):
                alt_path = os.path.join("Shiro-AI", path)
                if os.path.exists(alt_path):
                    path = alt_path
                else:
                    print(f"Warning: Config file {path} not found. Using defaults.")
                    return {}
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except Exception as e:
            print(f"Warning: Error loading config from {path}: {e}")
            return {}

    # ── Discord init ──────────────────────────────────────────────────────────

    def _init_discord(self, dc_cfg: dict):
        """Initialize Discord bridge (safe — never crashes the app)."""
        try:
            from shiro_discord.discord_bridge import ShiroDiscordBridge
            self._discord_bridge = ShiroDiscordBridge(
                config=self.config,
                shiro_app=self,
            )
            logging.info("[Discord] Bridge initialized. Bot not started yet — use GUI or config auto_start.")
            if dc_cfg.get("auto_start", False):
                self._discord_start()
        except ImportError as e:
            logging.warning(f"[Discord] discord_bridge.py not found: {e}")
        except Exception as e:
            logging.error(f"[Discord] Init error: {e}")

    # ── Discord GUI callbacks ─────────────────────────────────────────────────

    def _discord_get_status(self) -> dict:
        try:
            if self._discord_bridge:
                return self._discord_bridge.get_status_dict()
        except Exception as e:
            logging.error(f"[Discord] get_status error: {e}")
        return {"connected": False, "error": "Bridge not available"}

    def _discord_start(self) -> bool:
        try:
            if not self._discord_bridge:
                # Try lazy init
                dc_cfg = self.config.get('discord', {})
                self._init_discord(dc_cfg)
            if self._discord_bridge:
                return self._discord_bridge.start()
        except Exception as e:
            logging.error(f"[Discord] Start error: {e}")
        return False

    def _discord_stop(self):
        try:
            if self._discord_bridge:
                self._discord_bridge.stop()
        except Exception as e:
            logging.error(f"[Discord] Stop error: {e}")

    def _discord_join_voice(self, channel_name: str = None):
        try:
            if self._discord_bridge:
                self._discord_bridge.join_voice(channel_name)
        except Exception as e:
            logging.error(f"[Discord] Join voice error: {e}")

    def _discord_leave_voice(self):
        try:
            if self._discord_bridge:
                self._discord_bridge.leave_voice()
        except Exception as e:
            logging.error(f"[Discord] Leave voice error: {e}")

    def _discord_send_text(self, message: str):
        try:
            if self._discord_bridge:
                self._discord_bridge.send_text(message)
        except Exception as e:
            logging.error(f"[Discord] Send text error: {e}")

    # ── Natural language Discord command detection ────────────────────────────

    def _check_discord_nlp(self, text: str, user_name: str) -> bool:
        """
        Detects natural language requests to join/leave Discord voice.
        Returns True if a Discord command was handled (so engine skips normal reply).
        Only fires for the configured owner — other users cannot trigger this.
        """
        if not self._discord_bridge:
            return False

        # Owner-only guard — check against configured owner name
        # (web GUI uses display name, not Discord ID)
        _owner_names = {"Tyler", "TyAT"}  # add aliases if needed
        if user_name and user_name not in _owner_names:
            return False

        text_l = text.lower().strip()

        # Tight patterns — must be a clear imperative directed at Shiro or VC
        # NOT: "i joined a discord", "he left the call", casual mentions
        join_patterns = [
            r"\bshiro[,\s].{0,25}(join|connect|come|hop|get on|jump in)\b",
            r"(?:^|[,;]\s*)(join|hop on|get on|come to|get into)\s+(?:the\s+)?(vc|voice|call|voice\s*chat|voice\s*channel)(?:\s|$|[,!?])",
        ]
        leave_patterns = [
            r"\bshiro[,\s].{0,25}(leave|disconnect|drop out|exit|get off)\b",
            r"(?:^|[,;]\s*)(leave|disconnect|drop out|exit)\s+(?:the\s+)?(vc|voice|call|voice\s*chat|voice\s*channel)(?:\s|$|[,!?])",
        ]
        for p in join_patterns:
            if re.search(p, text_l):
                logging.info(f"[Discord NLP] Join command from {user_name}: {text!r}")
                self._discord_join_voice(None)
                return True
        for p in leave_patterns:
            if re.search(p, text_l):
                logging.info(f"[Discord NLP] Leave command from {user_name}: {text!r}")
                self._discord_leave_voice()
                return True
        return False

    # ── Core app methods (unchanged from original) ────────────────────────────

    def initialize(self):
        try:
            logging.info("Initializing Shiro AI...")
            self.engine.initialize()
            logging.info("Initialization complete.")
        except Exception as e:
            self.error_handler.handle_error(e, "Initialization")

    def handle_interrupt(self):
        if self.is_responding:
            logging.info("!!! Interrupt received !!!")
            self.interrupt_event.set()
        self.tts.interrupt()
        self.engine.last_interaction_time = datetime.now(timezone.utc)

    def handle_autonomous_speak(self, text: str, speech_type: str):
        if not text or not text.strip():
            return  # never send blank messages to UI or TTS

        # CRITICAL: Never interrupt an active response stream.
        # Autonomous messages (idle loop, continuations, etc.) fired while the
        # engine is mid-stream cause interleaved text in the UI and concurrent
        # TTS synthesis which produces garbled/overlapping audio.
        if self.is_responding or self.engine.processing_lock.locked():
            logging.debug(f"[Autonomous] Suppressed mid-response ({speech_type}): {text[:60]}")
            return
        # Also suppress if TTS is still speaking the previous response.
        # The stream may have finished but playback lags by several seconds.
        if hasattr(self, 'tts') and self.tts and getattr(self.tts, 'is_speaking', False):
            logging.debug(f"[Autonomous] Suppressed while TTS speaking ({speech_type}): {text[:60]}")
            return

        # Push to web GUI results queue
        self.results_queue.put((None, text))

        # Route to Discord if bridge is active — this is how continuations,
        # autonomous interjections, and any on_autonomous_speak output reach
        # Discord users. Without this, Shiro goes silent after a cut-off reply.
        if self._discord_bridge and self._discord_bridge.is_connected:
            try:
                if self._discord_bridge.in_voice:
                    # In voice channel — send to text AND speak
                    self._discord_bridge.send_voice_and_text(text)
                else:
                    # Text-only Discord session
                    self._discord_bridge.send_text(text)
            except Exception as _de:
                logging.warning(f"[Autonomous→Discord] Send failed: {_de}")
        else:
            # No Discord — speak locally via TTS
            self.tts.speak(text)

    def handle_user_join(self, user_name: str):
        if user_name in self.active_users:
            logging.info(f"User {user_name} already joined. Skipping greeting.")
            return []
        self.active_users.add(user_name)
        logging.info(f"User {user_name} joined.")
        greeting_gen = self.engine.on_user_join(user_name)
        if greeting_gen is None:
            return []
        try:
            resp = list(greeting_gen)
            if not resp or not any(r.strip() for r in resp):
                last_seen = self.engine.memory.get_last_interaction_time(user_name)
                mode = "new"
                if last_seen:
                    delta = datetime.now(timezone.utc) - last_seen
                    mode = "returning_soon" if delta.total_seconds() < 7200 else "returning_long"
                fallback = self.engine.get_fallback_greeting(user_name, mode)
                self.tts.speak(fallback)
                return [fallback]
            for line in resp:
                if line and line.strip():
                    self.tts.speak(line)
            return resp
        except Exception as e:
            logging.warning(f"Error yielding greeting: {e}")
            return []

    def handle_user_leave(self, user_name: str):
        if user_name in self.active_users:
            self.active_users.remove(user_name)
        logging.info(f"User {user_name} left.")
        self.engine.on_user_leave(user_name)
        if not self.active_users:
            logging.info("Chat room is now empty.")
            self.engine.memory.add_interaction(
                "[System]", f"{user_name} left. The room is now empty.", user_id="System"
            )

    def process_text(self, text: Any, user_name: str = None, user_id: str = None):
        """Generator that yields sentence fragments from the LLM and streams them to TTS."""
        processed_text = ""
        if isinstance(text, list) and len(text) > 0:
            first_item = text[0]
            if isinstance(first_item, dict):
                processed_text = first_item.get("text", str(first_item))
            else:
                processed_text = str(first_item)
        elif isinstance(text, dict):
            processed_text = text.get("text", str(text))
        else:
            processed_text = str(text)

        # ── Discord NLP check ─────────────────────────────
        if self._check_discord_nlp(processed_text, user_name or ""):
            # Shiro responds in character to the Discord command
            yield "sure, give me a second..."
            return

        # ── SystemAwareness intercept ─────────────────────
        # If the user asks about hardware/VRAM/status, answer directly.
        # No LLM call needed — just report the live stats in character.
        _hw_triggers = re.compile(
            r"\b(vram|gpu|cpu|ram|memory usage|hardware|system status|"
            r"how much vram|how much memory|how much gpu|"
            r"what.?s your (?:vram|gpu|cpu|ram|status|hardware)|"
            r"how are you (?:running|doing) (?:on|with) (?:memory|vram|hardware))\b",
            re.IGNORECASE,
        )
        if _hw_triggers.search(processed_text):
            try:
                _hw = SystemAwareness.get_status()
                _hw_reply = (
                    f"right now — VRAM: {_hw['vram']} | "
                    f"RAM: {_hw['ram']} | "
                    f"CPU: {_hw['cpu']} | "
                    f"Disk: {_hw['disk']}"
                )
                yield _hw_reply
                return
            except Exception as _hwe:
                logging.debug(f"[SystemAwareness] intercept error: {_hwe}")

        if self.engine.processing_lock.locked():
            # Engine is busy — queue this message and process it after current response
            with self._pending_lock:
                # Only keep the most recent pending message per user — discard older ones
                self._pending_queue = [
                    p for p in self._pending_queue
                    if p[1] != user_name  # drop older pending from same user
                ]
                self._pending_queue.append((processed_text, user_name, user_id))
            logging.info(f"[Queue] Message from {user_name!r} queued — engine busy. Will process after current response.")
            # Don't yield "one moment..." — it pollutes Discord chat and the web GUI.
            # The per-channel queue in discord_bot.py ensures ordering; just wait silently.
            # Wait for the lock to release then process our queued message
            _wait_deadline = time.time() + 30.0
            while self.engine.processing_lock.locked() and time.time() < _wait_deadline:
                time.sleep(0.1)
            # Check if our message is still in the queue (wasn't superseded)
            with self._pending_lock:
                pending = [(t, u, uid) for t, u, uid in self._pending_queue if u == user_name]
                if pending:
                    self._pending_queue = [p for p in self._pending_queue if p[1] != user_name]
                else:
                    return  # superseded by a newer message
            # Re-enter process_text with the queued message (not recursive — direct engine call)
            pending_text, pending_user, pending_uid = pending[-1]
            yield from self._process_text_inner(pending_text, pending_user, pending_uid)
            return

        # ── Notify meditation bridge of activity — resets idle timer ─────────
        # This must happen on EVERY message so Shiro never meditates mid-chat.
        if self._meditation_bridge:
            try:
                self._meditation_bridge.notify_activity()
            except Exception:
                pass

        yield from self._process_text_inner(processed_text, user_name, user_id)

    def _process_text_inner(self, processed_text: str, user_name: str, user_id: str):
        """Core processing — called by process_text and the pending queue handler."""
        self.is_responding = True
        self.interrupt_event.clear()
        _was_interrupted = False

        # Interrupt any currently-playing TTS before starting the new response.
        if hasattr(self, 'tts') and self.tts and self.tts.is_speaking:
            self.tts.interrupt()

        try:
            for fragment in self.engine.process_text(processed_text, user_name, user_id=user_id, interrupt_event=self.interrupt_event):
                self.tts.feed(fragment)
                yield fragment
            self.tts.flush()
        except Exception as e:
            self.error_handler.handle_error(e, "Text Processing")
            yield self.error_handler.get_ai_fallback_response()
        finally:
            _was_interrupted = self.interrupt_event.is_set()
            self.is_responding = False
            self.interrupt_event.clear()
            _uname = user_name or self.engine.current_user_name
            try:
                self.engine.cadence.record_response(_uname, processed_text, was_interrupted=_was_interrupted)
            except Exception:
                pass

    def process_background_audio(self, audio_data: Any):
        # Guard: only process one segment at a time
        if self.is_responding or self.engine.processing_lock.locked():
            logger.debug("[VoiceMonitor] Skipping segment — engine busy")
            return
        try:
            if hasattr(self, 'tts') and self.tts and self.tts.is_speaking:
                return

            transcribed_text = self.stt.transcribe(audio_data)
            if not transcribed_text or not transcribed_text.strip():
                return

            # Ignore very short transcriptions — likely noise/false positives
            # (e.g. "uh", "mm", single-char artifacts from Whisper)
            if len(transcribed_text.strip()) < 4:
                return

            _voice_user = self.engine.current_user_name
            if not _voice_user or _voice_user in ('System', 'Stranger', '', 'unknown'):
                _voice_user = self.config.get('primary_user', 'Tyler')

            fragments = []
            for fragment in self.process_text(transcribed_text, _voice_user):
                fragments.append(fragment)
            full_response = " ".join(fragments).strip()
            if full_response:
                self.results_queue.put((transcribed_text, full_response))
        except Exception as e:
            logging.error(f"Background audio processing failed: {e}")

    def toggle_mic(self, state: bool):
        if state:
            self.voice_monitor.start()
        else:
            self.voice_monitor.stop()

    def toggle_deafen(self, deafened: bool):
        """Mute or unmute Shiro's local TTS output from the web GUI.
        When deafened=True: Shiro still speaks (Discord voice etc.) but
        nothing plays through the local/web speakers.
        """
        if hasattr(self, 'tts') and self.tts:
            self.tts.set_muted(deafened)
            logging.info(f"[GUI] Local TTS {'muted (deafened)' if deafened else 'unmuted'}.")

    def set_user_typing(self, is_typing: bool, user_id: str = None):
        """Called by UI/voice room when user starts or stops typing/speaking."""
        self.engine.set_user_typing(is_typing, user_id=user_id or self.engine.current_user_name)

    # ── Vision callbacks ──────────────────────────────────────────────────────

    def _vision_toggle(self, enable: bool) -> str:
        """Toggle screen vision on/off from GUI button."""
        try:
            return self.engine.toggle_vision(enable)
        except Exception as e:
            logging.error(f"[Vision] Toggle error: {e}")
            return f"Vision toggle error: {e}"

    def _vision_status(self) -> dict:
        """Return vision status for GUI display."""
        try:
            return self.engine.get_vision_status()
        except Exception:
            return {"available": False, "enabled": False}

    # ── Book Reader callbacks ─────────────────────────────────────────────────

    def _book_list(self) -> list[str]:
        """Return list of undigested .txt books available."""
        if not self._book_reader:
            return []
        try:
            return self._book_reader.list_books()
        except Exception as e:
            logging.error(f"[Books] list error: {e}")
            return []

    def _book_list_digested(self) -> list[str]:
        """Return list of already-digested book titles."""
        if not self._book_reader:
            return []
        try:
            return self._book_reader.list_digested()
        except Exception as e:
            logging.error(f"[Books] list_digested error: {e}")
            return []

    def _book_digest(self, filename: str) -> tuple[bool, str]:
        """
        Digest a book file and inject it into Shiro's memory.
        Returns (success: bool, message: str).
        """
        if not self._book_reader:
            return False, "BookReader is not available."
        if not filename or not filename.strip():
            return False, "No file selected."
        filename = filename.strip()
        try:
            memory  = self._book_reader.digest_book(filename)
            path    = self._book_reader.save_to_memories(memory)
            # Inject into Shiro's live memory store
            injected = self._book_reader.inject_into_shiro_memory(memory, self.engine.memory)
            # Build a short confirmation to tell Shiro about
            snippet  = self._book_reader.get_shiro_prompt_snippet(memory)
            title    = memory["title"]
            author   = memory.get("author", "Unknown")
            fic      = "fiction" if memory["is_fiction"] else "non-fiction"
            themes   = ", ".join(memory["key_themes"][:5])
            entities = len(memory["key_entities"])
            inject_line = "✅ Injected into Shiro's live memory." if injected else "⚠️ Memory store injection failed — JSON saved only."
            msg = (
                f"✅ '{title}' by {author} digested ({fic}).\n"
                f"Themes: {themes}\n"
                f"Entities extracted: {entities}\n"
                f"Memory saved → {path.name}\n"
                f"{inject_line}"
            )
            logging.info(f"[Books] '{title}' digested and {'injected' if injected else 'saved only'}.")
            return True, msg
        except FileNotFoundError as e:
            return False, f"❌ File not found: {e}"
        except ValueError as e:
            return False, f"❌ Bad file format: {e}"
        except Exception as e:
            logging.error(f"[Books] digest error: {e}", exc_info=True)
            return False, f"❌ Error: {e}"

    def poll_results(self) -> list[tuple[str, str]]:
        results = []
        while not self.results_queue.empty():
            results.append(self.results_queue.get())
        return results

    def process_audio(self, audio_source: Any, user_name: str = None):
        try:
            transcribed_text = self.stt.transcribe(audio_source)
            if not transcribed_text or not transcribed_text.strip():
                yield "[Inaudible]", "I'm sorry, I couldn't quite hear you. Could you repeat that?"
                return
            yield transcribed_text, ""
            response_fragments = []
            for fragment in self.process_text(transcribed_text, user_name):
                response_fragments.append(fragment)
                yield transcribed_text, " ".join(response_fragments)
        except Exception as e:
            self.error_handler.handle_error(e, "Audio Processing")
            yield "[Audio Error]", self.error_handler.get_ai_fallback_response()

    def ai_comment_on_error(self, error_details: str):
        try:
            system_prompt = self.engine.persona.get_system_prompt()
            comment_prompt = (
                f"The system just encountered a technical error: {error_details}. "
                "Make a very short (1 sentence), in-character comment about it, like the coy kitsune you are."
            )
            comment = self.engine.llm.generate_response(system_prompt, comment_prompt, [])
            logging.info(f"AI Error Commentary: {comment}")
        except Exception:
            pass

    # ── Server management (LAN + Public) ─────────────────────────────────────

    def _server_start_lan(self) -> dict:
        """Start the LAN voice room on demand."""
        with self._server_lock:
            if self._voice_room is not None:
                return {"running": True, "url": self._lan_url}
            if not VOICE_ROOM_AVAILABLE:
                return {"running": False, "error": "VoiceRoom module not installed. Run: pip install websockets"}
            try:
                self._voice_room = VoiceRoom(app=self, port=8765)
                self._voice_room.start()
                self._lan_url = self._voice_room.get_invite_url()
                logging.info(f"[VoiceRoom] LAN started: {self._lan_url}")
                print(f"\n🎙️  Voice Room (LAN):    {self._lan_url}")
                return {"running": True, "url": self._lan_url}
            except Exception as e:
                logging.error(f"[VoiceRoom] Failed to start: {e}")
                self._voice_room = None
                self._lan_url = ""
                return {"running": False, "error": str(e)}

    def _server_stop_lan(self):
        """Stop the LAN voice room."""
        with self._server_lock:
            if self._voice_room:
                try:
                    self._voice_room.stop()
                except Exception as e:
                    logging.warning(f"[VoiceRoom] Stop error: {e}")
                finally:
                    self._voice_room = None
                    self._lan_url = ""
                    logging.info("[VoiceRoom] LAN stopped.")

    def _server_start_public(self) -> dict:
        """Start the cloudflared public tunnel on demand."""
        with self._server_lock:
            if self._cf_proc and self._cf_proc.poll() is None:
                return {"running": True, "url": self._pub_url, "starting": self._pub_starting}

            # LAN room must be running for the tunnel to have something to expose
            if self._voice_room is None:
                lan_result = self._server_start_lan()
                if not lan_result.get("running"):
                    return {"running": False, "error": "Could not start LAN room first: " + lan_result.get("error", "")}

            try:
                self._cf_proc = subprocess.Popen(
                    ["cloudflared", "tunnel", "--url", "http://localhost:8765"],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1
                )
                self._pub_url = ""
                self._pub_starting = True

                def _watch_cf(proc):
                    for line in proc.stdout:
                        m = re.search(r'https://[a-z0-9\-]+\.trycloudflare\.com', line)
                        if m:
                            url = m.group(0) + "/voice-room"
                            with self._server_lock:
                                self._pub_url = url
                                self._pub_starting = False
                            logging.info(f"[VoiceRoom] Public URL: {url}")
                            print(f"🌐  Voice Room (Public): {url}")
                            break
                    with self._server_lock:
                        self._pub_starting = False  # done either way

                t = threading.Thread(target=_watch_cf, args=(self._cf_proc,), daemon=True)
                t.start()
                logging.info("[VoiceRoom] cloudflared tunnel starting...")
                return {"running": True, "url": "", "starting": True}

            except FileNotFoundError:
                self._cf_proc = None
                msg = "cloudflared not found. Install: winget install --id Cloudflare.cloudflared"
                logging.warning(f"[VoiceRoom] {msg}")
                return {"running": False, "error": msg}
            except Exception as e:
                self._cf_proc = None
                logging.error(f"[VoiceRoom] cloudflared error: {e}")
                return {"running": False, "error": str(e)}

    def _server_stop_public(self):
        """Stop the cloudflared tunnel."""
        with self._server_lock:
            if self._cf_proc:
                try:
                    self._cf_proc.terminate()
                    self._cf_proc.wait(timeout=3)
                except Exception:
                    try:
                        self._cf_proc.kill()
                    except Exception:
                        pass
                finally:
                    self._cf_proc = None
                    self._pub_url = ""
                    self._pub_starting = False
                    logging.info("[VoiceRoom] Public tunnel stopped.")

    def _server_get_status(self) -> dict:
        """Return current state of both servers for the GUI status poll."""
        with self._server_lock:
            lan_running = self._voice_room is not None
            pub_running = self._cf_proc is not None and self._cf_proc.poll() is None
            if not pub_running:
                self._cf_proc = None
                self._pub_url = ""
                self._pub_starting = False
            return {
                "lan_running":   lan_running,
                "lan_url":       self._lan_url if lan_running else "",
                "pub_running":   pub_running,
                "pub_url":       self._pub_url,
                "pub_starting":  self._pub_starting,
            }

    def run(self):
        self.initialize()
        self.gui.build_ui()
        ui_cfg = self.config.get('ui', {})

        if threading.current_thread() is threading.main_thread():
            def handle_exit(sig, frame):
                logging.info("Graceful shutdown signal received...")
                if self.gui and self.gui.interface:
                    try:
                        self.gui.interface.close()
                    except Exception:
                        pass

            try:
                signal.signal(signal.SIGINT, handle_exit)
                signal.signal(signal.SIGTERM, handle_exit)
            except ValueError:
                pass

        # Voice Room and public tunnel are now started on-demand from the
        # Servers tab in the GUI — not auto-started here.
        # This reduces startup time and resource usage.
        if not VOICE_ROOM_AVAILABLE:
            logging.info("[VoiceRoom] voice_room module not available — install deps to enable.")

        try:
            self.gui.launch(share=ui_cfg.get('share', False))
        finally:
            logging.info("Performing Reflective Shutdown...")
            # Stop servers cleanly
            try:
                self._server_stop_public()
            except Exception as e:
                logging.error(f"Public tunnel shutdown error: {e}")
            try:
                self._server_stop_lan()
            except Exception as e:
                logging.error(f"LAN server shutdown error: {e}")
            # Stop Discord first
            try:
                if self._discord_bridge:
                    self._discord_bridge.stop()
            except Exception as e:
                logging.error(f"Discord shutdown error: {e}")
            try:
                if self._meditation_bridge:
                    self._meditation_bridge.stop()
            except Exception as e:
                logging.error(f"Meditation shutdown error: {e}")
            try:
                self.tts.stop()
            except Exception as e:
                logging.error(f"TTS shutdown error: {e}")
            try:
                self.engine.shutdown()
            except Exception as e:
                logging.error(f"Error during engine shutdown: {e}")


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except (AttributeError, IOError):
            pass

    # Use RotatingFileHandler so shiro_ai.log doesn't grow unbounded.
    # Max 5 MB per file, keeps 3 backups.
    from logging.handlers import RotatingFileHandler
    _file_handler = RotatingFileHandler(
        "shiro_ai.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    _file_handler.setFormatter(
        logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    )
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            _file_handler,
        ]
    )

    app = ShiroApp()
    app.run()