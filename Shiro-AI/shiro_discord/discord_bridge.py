"""
discord_bridge.py — Shiro's Discord ↔ Engine Bridge v1.0
===========================================================
Connects ShiroDiscordBot to ShiroApp / ShiroEngine.

Handles:
  - Routing Discord text messages through process_text()
  - Routing Discord voice through STT → process_text()
  - Natural-language command detection (join/leave voice)
  - Shiro's awareness of being addressed vs. overheard
  - Multi-user memory management per Discord member
  - Thread-safe callbacks between the async Discord loop and Shiro's sync engine
"""

from __future__ import annotations

import io
import re
import logging
import time
import threading
from typing import Optional, Callable, TYPE_CHECKING

import numpy as np
import soundfile as sf
try:
    import soxr as _soxr
    _SOXR_AVAILABLE = True
except ImportError:
    _SOXR_AVAILABLE = False
    _soxr = None

logger = logging.getLogger("shiro.discord.bridge")

# ── Discord message sanitiser ─────────────────────────────────────────────────
# Custom animated emoji:  <a:name:id>  →  :name:
# Custom static emoji:    <:name:id>   →  :name:
# Bare snowflake IDs:     digits-only messages (e.g. "1481788718313635842")
_RE_ANIMATED_EMOJI = re.compile(r"<a:([a-zA-Z0-9_]+):\d+>")
_RE_STATIC_EMOJI   = re.compile(r"<:([a-zA-Z0-9_]+):\d+>")
_RE_MENTION        = re.compile(r"<@!?(\d+)>")
_RE_CHANNEL_MENTION = re.compile(r"<#\d+>")
_RE_BARE_SNOWFLAKE = re.compile(r"^\d{15,20}$")


def _sanitise_discord_text(text: str) -> str:
    """
    Convert raw Discord message content into something the LLM can reason about.

    <a:gigglewolf:1071941306647068672>  →  :gigglewolf:
    <:heart:123456789>                  →  :heart:
    <@226840618062708736>               →  [mention]
    "1481788718313635842"               →  ""   (bare snowflake, nothing to say)
    """
    text = _RE_ANIMATED_EMOJI.sub(r"::", text)
    text = _RE_STATIC_EMOJI.sub(r"::", text)
    text = _RE_MENTION.sub("[mention]", text)
    text = _RE_CHANNEL_MENTION.sub("[channel]", text)
    if _RE_BARE_SNOWFLAKE.match(text.strip()):
        return ""
    return text.strip()

# Lazy import to avoid hard dependency at module load time
_discord_bot_cls = None


def _get_bot_class():
    global _discord_bot_cls
    if _discord_bot_cls is None:
        from shiro_discord.discord_bot import ShiroDiscordBot
        _discord_bot_cls = ShiroDiscordBot
    return _discord_bot_cls


class ShiroDiscordBridge:
    """
    The glue layer between ShiroApp and ShiroDiscordBot.

    ShiroApp creates one of these.  It owns the bot instance and wires
    all callbacks so Discord events flow naturally through Shiro's brain.
    """

    def __init__(
        self,
        config: dict,
        shiro_app,           # ShiroApp instance (has .engine, .stt, .tts, etc.)
    ):
        self.config = config
        self.app = shiro_app
        self.engine = shiro_app.engine
        self.stt = shiro_app.stt
        self.tts = shiro_app.tts

        dc_cfg = config.get("discord", {})
        self.token = dc_cfg.get("token", "")
        self.shiro_names: list[str] = dc_cfg.get(
            "shiro_names", ["shiro", "hey shiro"]
        )
        self.address_threshold: float = float(
            dc_cfg.get("address_confidence_threshold", 0.6)
        )
        self.tts_in_voice: bool = dc_cfg.get("tts_in_voice", True)
        self.stt_in_voice: bool = dc_cfg.get("stt_in_voice", True)
        self.prefix: str = dc_cfg.get("prefix", "!")
        self.owner_id: int = int(dc_cfg.get("owner_id", 0))

        self._bot: Optional[object] = None   # ShiroDiscordBot instance
        self._state_callbacks: list[Callable] = []
        self._last_state = None
        self._lock = threading.Lock()
        self._last_tts_finished_at: float = 0.0  # timestamp when TTS last finished speaking

        # Track context: messages we've observed (not addressed to Shiro)
        # so the engine has ambient awareness of the conversation
        self._ambient_buffer: dict[str, list] = {}   # channel_id -> recent msgs

    # ── Boot / shutdown ───────────────────────────────────────────────────────

    def start(self) -> bool:
        """
        Instantiate and start the Discord bot.
        Returns True on success, False if config is missing or discord.py not available.
        """
        if not self.token or self.token.startswith("YOUR_"):
            logger.warning(
                "[Discord Bridge] No valid token configured. "
                "Add discord.token to config.yaml."
            )
            return False

        try:
            BotClass = _get_bot_class()
            self._bot = BotClass(
                token=self.token,
                shiro_names=self.shiro_names,
                on_text_received=self._handle_text,
                on_voice_received=self._handle_voice if self.stt_in_voice else self._handle_voice_noop,
                on_state_change=self._handle_state_change,
                on_speaking_change=self._handle_speaking_change,
                prefix=self.prefix,
                address_threshold=self.address_threshold,
                tts_speak_cb=self._tts_callback if self.tts_in_voice else None,
                local_tts_mute_cb=self._mute_local_tts,
                local_tts_unmute_cb=self._unmute_local_tts,
                auto_reply_text=True,
                owner_id=self.owner_id,
            )
            self._bot.on_reaction_feedback = self._handle_reaction_feedback
            self._bot.on_reaction_seen = self._handle_reaction_seen
            self._bot.on_proactive_react = self._handle_proactive_react
            self._bot.start()
            logger.info("[Discord Bridge] Bot started.")
            return True
        except ImportError as e:
            logger.error(f"[Discord Bridge] Import error: {e}")
            return False
        except Exception as e:
            logger.error(f"[Discord Bridge] Failed to start: {e}")
            return False

    def stop(self):
        """Stop the Discord bot gracefully."""
        if self._bot:
            try:
                self._bot.stop()
                logger.info("[Discord Bridge] Bot stopped.")
            except Exception as e:
                logger.error(f"[Discord Bridge] Stop error: {e}")

    # ── Callbacks from Discord bot ────────────────────────────────────────────

    def _handle_text(
        self,
        user_id: str,
        display_name: str,
        text: str,
        channel_id: int,
        addressed: bool,
    ) -> str:
        """
        Called by the Discord bot when a text message arrives.

        If addressed=True  → generate a real reply from the engine.
        If addressed=False → log context silently; engine might occasionally interject.
        """
        try:
            # Sanitise raw Discord content — resolve custom emoji tags, strip bare IDs
            text = _sanitise_discord_text(text)
            if not text:
                # Nothing meaningful to process (bare snowflake, empty after strip)
                return ""

            # Ensure this Discord user is in Shiro's memory system
            self._ensure_engine_user(user_id, display_name)

            if not addressed:
                # Store in ambient buffer so engine has context if asked later
                self._push_ambient(str(channel_id), display_name, text)
                # Let engine observe silently (no reply expected)
                # We pass a lightweight observation so memory stays current
                try:
                    self.engine.memory.add_user_message(
                        user_id=user_id,
                        content=f"[Discord/observed] {display_name}: {text}",
                    )
                except Exception:
                    pass
                return ""

            # Addressed directly — process through the engine
            logger.info(
                f"[Discord Bridge] Processing text from {display_name} (id={user_id}): {text!r}"
            )

            # Mute local TTS for Discord text replies — user is in Discord,
            # not listening through the web GUI speakers.
            _was_muted = getattr(self.tts, '_muted', False)
            if not _was_muted and self.tts:
                self.tts.set_muted(True)

            # Collect fragments
            reply_parts = []
            try:
                for fragment in self.app.process_text(
                    text,
                    user_name=display_name,
                    user_id=user_id,       # stable Discord member ID — keeps memories separate
                ):
                    reply_parts.append(fragment)
            except Exception as e:
                logger.error(f"[Discord Bridge] Engine text error: {e}")
                return "...sorry, something went sideways in my head. try again?"
            finally:
                # Restore mute state — only unmute if WE muted it
                if not _was_muted and self.tts:
                    self.tts.set_muted(False)

            reply = "".join(reply_parts).strip()
            if not reply:
                # Engine returned nothing (response was fully stripped by sanitiser).
                # Retry once with a plain nudge so Shiro doesn't go silent.
                logger.warning("[Discord Bridge] Engine returned empty reply — retrying once.")
                try:
                    retry_parts = []
                    for fragment in self.app.process_text(
                        f"[respond to: {text}]",
                        user_name=display_name,
                        user_id=user_id,
                    ):
                        retry_parts.append(fragment)
                    reply = "".join(retry_parts).strip()
                except Exception as _re:
                    logger.error(f"[Discord Bridge] Retry error: {_re}")
                if not reply:
                    logger.warning("[Discord Bridge] Retry also empty — staying silent.")
                    return ""
                logger.info(f"[Discord Bridge] Shiro replies (retry): {reply!r}")
            else:
                logger.info(f"[Discord Bridge] Shiro replies: {reply!r}")
            return reply

        except Exception as e:
            logger.error(f"[Discord Bridge] _handle_text error: {e}")
            return ""

    def _handle_voice(
        self,
        user_id: str,
        display_name: str,
        wav_bytes: bytes,
    ) -> str:
        """
        Called when a voice segment finishes.
        Runs STT → AddressDetector → engine (if addressed).
        Returns Shiro's reply (or empty string if she stays quiet).
        """
        try:
            # Guard: if Shiro's TTS is currently active, this audio is probably
            # her own voice echoing back through the voice channel. Skip it.
            if self.tts and hasattr(self.tts, 'is_active') and self.tts.is_active:
                logger.info("[Discord Bridge] Voice segment skipped — Shiro is speaking (TTS active)")
                return ""
            # Also apply a 700ms post-speak silence window — Shiro's voice may
            # still be arriving in the audio buffer even after TTS finishes.
            _POST_SPEAK_SILENCE = 0.7
            if time.time() - self._last_tts_finished_at < _POST_SPEAK_SILENCE:
                logger.info("[Discord Bridge] Voice segment skipped — post-speak silence window")
                return ""
            # Convert WAV bytes → numpy float32 array for Whisper
            wav_buf = io.BytesIO(wav_bytes)
            audio_np, sample_rate = sf.read(wav_buf, dtype="float32")
            # Mix stereo → mono
            if audio_np.ndim == 2:
                audio_np = audio_np.mean(axis=1)
            # Resample to 16kHz (Whisper's native rate) if needed
            if sample_rate != 16000:
                if _SOXR_AVAILABLE:
                    audio_np = _soxr.resample(audio_np, sample_rate, 16000).astype(np.float32)
                else:
                    # crude fallback
                    num_samples = int(len(audio_np) * 16000 / sample_rate)
                    indices = (np.arange(num_samples) * sample_rate / 16000).astype(int)
                    indices = np.clip(indices, 0, len(audio_np) - 1)
                    audio_np = audio_np[indices]

            # RMS energy check — log signal level so we can diagnose audio quality issues.
            # Whisper hallucinates "Thanks for watching" when it gets near-silence.
            rms = float(np.sqrt(np.mean(audio_np ** 2))) if len(audio_np) > 0 else 0.0
            peak = float(np.max(np.abs(audio_np))) if len(audio_np) > 0 else 0.0
            duration_s = len(audio_np) / 16000

            # Normalize audio to prevent clipping — peak > 1.0 causes Whisper hallucinations
            if peak > 1.0:
                audio_np = audio_np / peak * 0.95
                peak = 0.95
            # Also normalize quiet audio up to a reasonable level
            elif peak < 0.1 and peak > 0.0:
                audio_np = audio_np / peak * 0.7
                peak = 0.7

            logger.info(
                f"[Discord Bridge] Audio from {display_name}: "
                f"{duration_s:.2f}s  RMS={rms:.4f}  peak={peak:.4f}"
            )
            # Drop near-silence — RMS < 0.005 means no real speech signal
            if rms < 0.005:
                logger.info(f"[Discord Bridge] Audio too quiet (RMS={rms:.4f}) — skipping Whisper")
                return ""

            # Transcribe with forced English to avoid wrong language detection
            if self.stt.model is None:
                self.stt.load_model()
            segments, info = self.stt.model.transcribe(
                audio_np,
                beam_size=3,
                language="en",
                vad_filter=False,
                condition_on_previous_text=False,
            )
            seg_list = list(segments)
            transcribed = " ".join(s.text.strip() for s in seg_list).strip()

            logger.info(f"[Discord Bridge] STT result from {display_name}: {transcribed!r}")

            if not transcribed or not transcribed.strip():
                logger.info(f"[Discord Bridge] Empty transcription from {display_name} — skipping")
                return ""

            # Filter known Whisper hallucinations
            _HALLUCINATIONS = {
                # "Thanks for watching" family — Whisper's most common hallucination on Discord audio
                "thank you for watching", "thanks for watching",
                "thank you for watching!", "thanks for watching!",
                "thanks for watching.", "thank you for watching.",
                "thanks for watching, and i'll see you next time.",
                "thanks for watching and i'll see you next time",
                "thanks for watching and i'll see you next time.",
                "please subscribe", "like and subscribe",
                "don't forget to subscribe", "see you in the next video",
                "thank you for watching and i'll see you in the next video",
                # Other common hallucinations
                "thank you", "thanks", "thank you very much",
                "you're welcome", "you're welcome.",
                "uh-huh", "u-huh", "uh huh", "mm-hmm", "mmm",
                "uh-huh. uh-huh.", "u-huh. u-huh. u-huh.",
                "uh-huh. uh-huh. uh-huh.",
                "hmm", "hm", "mhm",
                "bye", "goodbye", "bye bye",
                ".", "..", "...", "....", "…",
                "a", "i", "", " ",
            }
            _clean = transcribed.lower().strip(".!?, ")
            if _clean in _HALLUCINATIONS:
                logger.info(f"[Discord Bridge] Hallucination filtered: {transcribed!r}")
                return ""

            # Regex catch for "thanks for watching" family — Whisper generates variations
            import re as _re
            if _re.search(
                r'thanks?\s+for\s+(watch|listen|tun)|'
                r"i'?ll\s+see\s+you\s+(next\s+time|in\s+the\s+next)|"
                r'subscribe\s+and\s+(like|hit)|'
                r'don\'?t\s+forget\s+to\s+(like|subscribe)',
                _clean, _re.IGNORECASE
            ):
                logger.info(f"[Discord Bridge] YouTube artifact filtered: {transcribed!r}")
                return ""

            # Filter repetitive hallucinations
            words = transcribed.lower().split()
            if len(words) >= 3 and len(set(words)) <= 2:
                logger.info(f"[Discord Bridge] Repetitive hallucination filtered: {transcribed!r}")
                return ""

            logger.info(
                f"[Discord Bridge] Voice from {display_name}: {transcribed!r}"
            )

            # Address detection
            from shiro_discord.discord_bot import AddressDetector
            detector = AddressDetector(self.shiro_names)
            score = detector.score(transcribed)
            addressed = score >= self.address_threshold

            if not addressed:
                # Ambient voice — log context, stay quiet
                self._push_ambient(f"voice_{user_id}", display_name, transcribed)
                try:
                    self.engine.memory.add_user_message(
                        user_id=user_id,
                        content=f"[Discord/voice/observed] {display_name}: {transcribed}",
                    )
                except Exception:
                    pass
                return ""

            # Addressed — generate reply
            self._ensure_engine_user(user_id, display_name)
            reply_parts = []
            try:
                for fragment in self.app.process_text(
                    transcribed,
                    user_name=display_name,
                    user_id=user_id,
                ):
                    reply_parts.append(fragment)
            except Exception as e:
                logger.error(f"[Discord Bridge] Engine voice error: {e}")
                return "hm, something went wrong with my thinking."

            return "".join(reply_parts).strip()
        except Exception as e:
            logger.error(f"[Discord Bridge] _handle_voice error: {e}")
            return ""

    def _handle_proactive_react(
        self,
        user_name: str,
        user_id: str,
        message_text: str,
        shiro_reply: str,
    ) -> str:
        """
        Called after Shiro sends a reply. Returns an emoji to react to the user's
        message with, or empty string if Shiro doesn't want to react.
        """
        try:
            return self.engine.get_emoji_reaction(
                reactor_name=user_name,
                incoming_emoji="",
                message_text=message_text,
            )
        except Exception as e:
            logger.debug(f"[Discord Bridge] _handle_proactive_react error: {e}")
            return ""

    def _handle_reaction_seen(
        self,
        reactor_id: str,
        reactor_name: str,
        emoji: str,
        message_text: str,
        was_shiros_message: bool,
        channel_id: int,
    ) -> None:
        """
        Called whenever any reaction is added to any message Shiro can see.
        Feeds it into engine memory so Shiro is aware of the emotional tone.
        Optionally triggers Shiro to react back.
        """
        try:
            context = "her own message" if was_shiros_message else "someone else's message"
            logger.info(
                f"[Discord Bridge] Reaction seen: {emoji} from {reactor_name} on {context}"
            )
            # Store in memory so Shiro is aware
            try:
                self.engine.memory.add_user_message(
                    user_id=reactor_id,
                    content=(
                        f"[Discord/reaction] {reactor_name} reacted with {emoji} "
                        f"to {'Shiro' if was_shiros_message else 'a'}{'s message' if was_shiros_message else 'nother message'}: "
                        f"{message_text[:100]!r}"
                    ),
                )
            except Exception:
                pass

            # Ask the engine if Shiro wants to react back — only on others' reactions
            # to her own messages (don't react-chain on others' messages unprompted)
            if was_shiros_message and self._bot:
                threading.Thread(
                    target=self._consider_emoji_reaction,
                    args=(reactor_name, emoji, message_text, channel_id),
                    daemon=True,
                ).start()
        except Exception as e:
            logger.error(f"[Discord Bridge] _handle_reaction_seen error: {e}")

    def _consider_emoji_reaction(
        self,
        reactor_name: str,
        incoming_emoji: str,
        message_text: str,
        channel_id: int,
    ):
        """
        Background thread: ask the engine if Shiro wants to react with an emoji.
        Lightweight — no full LLM reply, just a single emoji or nothing.
        """
        try:
            emoji = self.engine.get_emoji_reaction(
                reactor_name=reactor_name,
                incoming_emoji=incoming_emoji,
                message_text=message_text,
            )
            if emoji and self._bot:
                # Find the most recent message Shiro sent in this channel to react to
                sent = [
                    (mid, entry) for mid, entry in self._bot._sent_messages.items()
                    if entry.get("channel_id") == channel_id
                ]
                if sent:
                    # Most recent message
                    latest_id = max(sent, key=lambda x: x[0])[0]
                    self._bot.react_to_message_sync(channel_id, latest_id, emoji)
        except Exception as e:
            logger.debug(f"[Discord Bridge] _consider_emoji_reaction error: {e}")

    def react_to_message(self, channel_id: int, message_id: int, emoji: str):
        """Public method — let external callers make Shiro react to a message."""
        if self._bot:
            self._bot.react_to_message_sync(channel_id, message_id, emoji)

    def _handle_reaction_feedback(
        self,
        message_text: str,
        original_user_id: str,
        original_user_name: str,
        reactor_id: str,
        reactor_name: str,
        positive: bool,
    ) -> None:
        """
        Called by the Discord bot when a user reacts 👍 or 👎 to one of Shiro's messages.
        Forwards to the engine's reinforcement feedback system.
        """
        try:
            if self.app and hasattr(self.app, "engine"):
                self.app.engine.process_reaction_feedback(
                    message_text=message_text,
                    original_user_id=original_user_id,
                    original_user_name=original_user_name,
                    reactor_id=reactor_id,
                    reactor_name=reactor_name,
                    positive=positive,
                )
        except Exception as e:
            logger.error(f"[Discord Bridge] reaction feedback error: {e}")

    def _handle_voice_noop(self, user_id, display_name, wav_bytes) -> str:
        """No-op voice handler when voice STT disabled."""
        return ""

    def _handle_speaking_change(self, discord_user, is_speaking: bool):
        """Called when someone starts/stops speaking in Discord voice."""
        try:
            name = discord_user.discord_name
            uid = discord_user.shiro_user_id
            action = "started speaking" if is_speaking else "stopped speaking"
            logger.info(f"[Discord Bridge] {name} {action}")
            # Feed into engine context so Shiro is aware of who is active
            try:
                self.engine.memory.add_user_message(
                    user_id=uid,
                    content=f"[Discord/voice] {name} {action} in voice channel.",
                )
            except Exception:
                pass
            # v4 Consciousness — notify speaking state so attention model updates
            try:
                if self.engine.consciousness is not None:
                    self.engine.consciousness.someone_speaks(name, speaking=is_speaking)
                    # Switch environment to voice when someone starts speaking
                    if is_speaking:
                        from shiro_self_awareness import Medium as ShiroMedium
                        self.engine.consciousness.enter_environment(
                            ShiroMedium.DISCORD_VOICE,
                            channel_name=getattr(self, '_voice_channel_name', 'voice')
                        )
                    # set_ambient_noise — more speakers = higher ambient noise level
                    try:
                        _speaking_count = len([
                            u for u in getattr(self._bot, '_known_users', {}).values()
                            if getattr(u, 'speaking_now', False)
                        ])
                        # 0 speakers = 0.0, 1 = 0.3, 2 = 0.55, 3+ = 0.75
                        _noise_level = min(0.75, _speaking_count * 0.25 + 0.05)
                        if _speaking_count == 0:
                            _noise_level = 0.0
                        self.engine.consciousness.set_ambient_noise(_noise_level)
                    except Exception:
                        pass
            except Exception as _ce:
                logger.debug(f"[Consciousness] someone_speaks error: {_ce}")
        except Exception as e:
            logger.debug(f"speaking_change handler error: {e}")

    def _mute_local_tts(self):
        """Silence local TTS output — Discord voice is the speaker."""
        try:
            if hasattr(self.app, 'tts') and self.app.tts:
                self.app.tts.set_muted(True)
                logger.info("[Discord Bridge] Local TTS muted.")
        except Exception as e:
            logger.debug(f"TTS mute error: {e}")

    def _unmute_local_tts(self):
        """Restore local TTS output after leaving Discord voice."""
        try:
            if hasattr(self.app, 'tts') and self.app.tts:
                self.app.tts.set_muted(False)
                logger.info("[Discord Bridge] Local TTS unmuted.")
        except Exception as e:
            logger.debug(f"TTS unmute error: {e}")

    def _tts_callback(self, text: str) -> Optional[bytes]:
        """
        Get TTS audio bytes for speaking in Discord voice.
        Tries to use GPT-SoVITS TTS if enabled.
        Returns WAV bytes or None.
        """
        try:
            if not self.tts or not hasattr(self.tts, "synthesize_to_bytes"):
                logger.debug("[Discord TTS] TTS has no synthesize_to_bytes method.")
                return None
            result = self.tts.synthesize_to_bytes(text)
            # Stamp the time TTS finished so _handle_voice can apply a silence window
            self._last_tts_finished_at = time.time()
            return result
        except Exception as e:
            logger.debug(f"[Discord TTS] TTS callback error: {e}")
            return None

    def _handle_state_change(self, state):
        """Relay bot state changes to registered GUI callbacks."""
        self._last_state = state
        for cb in self._state_callbacks:
            try:
                cb(state)
            except Exception as e:
                logger.debug(f"State callback error: {e}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _ensure_engine_user(self, user_id: str, display_name: str):
        """Make sure this Discord user is in Shiro's awareness system.
        
        Critically: seeds the inner mind with the stable Discord ID and the display name
        so per-user memory is always keyed to the stable ID, never the display name alone.
        """
        # Always seed the legacy inner mind — idempotent and cheap
        try:
            self.engine.legacy_mind.seed_user(user_id, platform_display_name=display_name)
        except Exception:
            pass

        try:
            if user_id not in self.engine.awareness.users:
                self.engine.awareness.add_user(
                    user_id=user_id,
                    name=display_name,
                    metadata={"source": "discord"},
                )
        except Exception:
            # SelfAwareness may not have add_user — fall back to memory injection
            try:
                summary = self.engine.memory.get_summary(user_id)
                if not summary:
                    self.engine.memory.add_user_message(
                        user_id=user_id,
                        content=f"[Discord user joined: {display_name}]",
                    )
            except Exception:
                pass

    def _push_ambient(self, channel_key: str, name: str, text: str):
        """Keep a small rolling buffer of ambient conversation context."""
        buf = self._ambient_buffer.setdefault(channel_key, [])
        buf.append({"name": name, "text": text, "ts": time.time()})
        # Keep only last 10
        if len(buf) > 10:
            buf.pop(0)

    def get_ambient_context(self, channel_key: str) -> str:
        """Format ambient buffer as a readable context string."""
        buf = self._ambient_buffer.get(channel_key, [])
        if not buf:
            return ""
        lines = [f"{e['name']}: {e['text']}" for e in buf[-6:]]
        return "\n".join(lines)

    # ── Public controls (thread-safe, for GUI) ────────────────────────────────

    def add_state_callback(self, cb: Callable):
        self._state_callbacks.append(cb)

    def remove_state_callback(self, cb: Callable):
        try:
            self._state_callbacks.remove(cb)
        except ValueError:
            pass

    def join_voice(self, channel_name: Optional[str] = None):
        if self._bot:
            self._bot.join_voice_sync(channel_name)

    def leave_voice(self):
        if self._bot:
            self._bot.leave_voice_sync()

    def send_text(self, message: str):
        if self._bot and message and message.strip():
            self._bot.send_message_sync(message)

    def speak_in_voice(self, text: str):
        """Speak text in Discord voice channel only (no text channel)."""
        if self._bot and text and text.strip():
            self._bot.speak_in_voice_sync(text)

    def send_voice_and_text(self, text: str):
        """Send to text channel AND speak in voice — use this for autonomous speech."""
        if self._bot and text and text.strip():
            self._bot.send_voice_and_text_sync(text)

    @property
    def is_connected(self) -> bool:
        return bool(self._bot and self._bot.is_connected)

    @property
    def in_voice(self) -> bool:
        return bool(self._bot and self._bot.in_voice)

    @property
    def state(self):
        return self._bot.state if self._bot else None

    def get_status_dict(self) -> dict:
        """Return a dict of status info for the GUI."""
        if not self._bot:
            return {
                "connected": False,
                "in_voice": False,
                "guild": "",
                "voice_channel": "",
                "text_channel": "",
                "users_in_voice": [],
                "error": "Bot not started",
            }
        s = self._bot.state
        return {
            "connected": s.connected,
            "connecting": s.connecting,
            "in_voice": self.in_voice,
            "guild": s.guild_name,
            "voice_channel": s.voice_channel_name,
            "text_channel": s.text_channel_name,
            "users_in_voice": list(s.users_in_voice.values()),
            "error": s.error,
        }