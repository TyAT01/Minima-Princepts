"""
discord_bot.py — Shiro's Discord Integration v1.2
===================================================
Changes in v1.2:
  - Voice listening via discord-ext-voice-recv (works with standard discord.py)
  - Real-time speaking detection: Shiro knows WHO is talking right now
  - Local TTS mute: when Shiro joins Discord voice, local speakers go silent
  - Graceful fallback if voice-recv not installed (text + speaking still work)

Install for full voice support:
    pip install discord-ext-voice-recv
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import time
import threading
import wave
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger("shiro.discord")

# ── Opus loader — must happen before any voice operations ─────────────────────
def _load_opus():
    """
    Load libopus. discord.py ships libopus-0.x64.dll in its own bin/ folder.
    We let discord.py find and load it using its own internal mechanism.
    """
    import os, sys

    if not DISCORD_AVAILABLE:
        return

    try:
        from discord.opus import is_loaded
        if is_loaded():
            logger.info("[Discord] Opus already loaded.")
            return
    except Exception:
        pass

    import discord

    # discord.py's bin/ folder — ships with libopus-0.x64.dll on Windows
    discord_bin = os.path.join(os.path.dirname(discord.__file__), "bin")

    candidates = [
        # discord.py's own bundled DLL (correct version, correct bitness)
        os.path.join(discord_bin, "libopus-0.x64.dll"),
        os.path.join(discord_bin, "libopus-0.x86.dll"),
        os.path.join(discord_bin, "libopus-0.dll"),
        # User-placed fallback
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "opus.dll"),
        os.path.join(os.getcwd(), "opus.dll"),
    ]

    for path in candidates:
        path = os.path.normpath(path)
        if os.path.exists(path):
            try:
                discord.opus.load_opus(path)
                logger.info(f"[Discord] Opus loaded from: {path}")
                return
            except Exception as e:
                logger.debug(f"Opus load failed ({path}): {e}")

    logger.warning(
        "[Discord] libopus not loaded — voice decoding will fail.\n"
        "  The DLL should be at: " + os.path.join(discord_bin, "libopus-0.x64.dll")
    )

# ── discord.py ────────────────────────────────────────────────────────────────
try:
    import discord
    from discord.ext import commands
    DISCORD_AVAILABLE = True
except ImportError:
    DISCORD_AVAILABLE = False
    logger.warning("discord.py not installed. Run: pip install discord.py[voice] PyNaCl")

_load_opus()  # Load libopus before any voice operations

# Suppress noisy voice_recv internal logs
import logging as _logging
_logging.getLogger("discord.ext.voice_recv.router").setLevel(_logging.CRITICAL)
_logging.getLogger("discord.ext.voice_recv.reader").setLevel(_logging.WARNING)

# ── discord-ext-voice-recv ────────────────────────────────────────────────────
try:
    from discord.ext import voice_recv
    VOICE_RECV_AVAILABLE = True
    logger.info("[Discord] discord-ext-voice-recv found — voice listening enabled.")
except ImportError:
    VOICE_RECV_AVAILABLE = False
    logger.info(
        "[Discord] discord-ext-voice-recv not installed — Shiro can speak but not listen.\n"
        "    Fix: pip install discord-ext-voice-recv"
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DiscordUser:
    discord_id: int
    discord_name: str
    shiro_user_id: str
    last_spoke_at: float = 0.0
    last_typed_at: float = 0.0
    speaking_now: bool = False


@dataclass
class SpeechSegment:
    user: DiscordUser
    pcm_data: bytes
    timestamp: float = field(default_factory=time.time)


@dataclass
class DiscordState:
    connected: bool = False
    guild_id: Optional[int] = None
    guild_name: str = ""
    voice_channel_id: Optional[int] = None
    voice_channel_name: str = ""
    text_channel_id: Optional[int] = None
    text_channel_name: str = ""
    users_in_voice: dict = field(default_factory=dict)
    currently_speaking: dict = field(default_factory=dict)
    error: str = ""
    connecting: bool = False
    voice_listening: bool = False


# ─────────────────────────────────────────────────────────────────────────────
#  Address detector
# ─────────────────────────────────────────────────────────────────────────────

class AddressDetector:
    def __init__(self, shiro_names: list):
        self.names = [n.lower().strip() for n in shiro_names]
        self._patterns = [re.compile(r"\b" + re.escape(n) + r"\b", re.I) for n in self.names]
        self._question_words = {
            "what","who","why","how","when","where",
            "do","did","can","could","would","will","is","are","have"
        }

    def score(self, text: str) -> float:
        if not text:
            return 0.0
        text_l = text.lower().strip()
        score = 0.0
        for pat in self._patterns:
            if pat.search(text_l):
                score += 0.7
                break
        words = text_l.split()
        if words:
            if words[0] in self.names:
                score += 0.2
            if words[-1].rstrip("!?,.:"  ) in self.names:
                score += 0.15
        if "?" in text and words and words[0] in self._question_words and score == 0.0:
            score += 0.1
        return min(score, 1.0)

    def is_addressed(self, text: str, threshold: float = 0.5) -> bool:
        return self.score(text) >= threshold


# ─────────────────────────────────────────────────────────────────────────────
#  Voice receiver
# ─────────────────────────────────────────────────────────────────────────────

class ShiroVoiceReceiver:
    """Per-user audio capture using discord-ext-voice-recv."""

    SILENCE_MS    = 1200  # wait 1.2s of silence before ending utterance
    MIN_SPEECH_MS = 3000  # min 3.0s of speech before sending to STT
                          # 2s clips reliably hallucinate "Thanks for watching" regardless of model
                          # 3s gives Whisper enough context to decode real speech accurately
    SAMPLE_RATE   = 48000
    CHANNELS      = 2
    SAMPLE_WIDTH  = 2

    def __init__(self, on_speech, on_speaking_change):
        self._on_speech = on_speech
        self._on_speaking_change = on_speaking_change
        self._buffers = defaultdict(bytearray)
        self._last_audio = {}
        self._user_map = {}

    def set_user_map(self, user_map):
        self._user_map = user_map

    def build_sink(self):
        """Build a voice_recv sink. Returns None if not available."""
        if not VOICE_RECV_AVAILABLE:
            return None

        receiver = self

        # BasicSink(event, ...) — confirmed API from inspect
        # event callback signature: (user: VoiceData) -> None
        # speaking is tracked via a separate VoiceClient event hook below

        _packet_count = [0]

        def _on_audio(user, data):
            """Called by voice_recv for every decoded PCM packet per user."""
            if user is None:
                return
            uid = user.id
            raw = getattr(data, "pcm", None)
            if raw is None:
                return

            # Check if packet is silent (all zeros = our DAVE patch placeholder)
            # If so, skip it — don't buffer silence
            if raw and len(raw) > 0:
                # Sample a few bytes to check for real audio vs silence
                sample = raw[:min(100, len(raw))]
                non_zero = sum(1 for b in sample if b != 0)
                if non_zero < 3:
                    # Pure silence — skip
                    return

            receiver._buffers[uid].extend(raw)
            receiver._last_audio[uid] = time.time()
            _packet_count[0] += 1
            if _packet_count[0] <= 5:
                logger.info(f"[VoiceReceiver] Real audio #{_packet_count[0]} from {getattr(user, 'display_name', uid)} ({len(raw)} bytes)")

        # Subclass BasicSink so we can also override on_speaking for green-light detection
        try:
            _recv = receiver

            class _ShiroSink(voice_recv.BasicSink):
                def __init__(self):
                    super().__init__(_on_audio, decode=True)

                def on_speaking(self, user, speaking_state):
                    """Fired by voice_recv when Discord's speaking flag changes."""
                    if user is None:
                        return
                    uid = user.id
                    discord_user = _recv._user_map.get(uid)
                    if discord_user:
                        was = discord_user.speaking_now
                        discord_user.speaking_now = bool(speaking_state)
                        if was != discord_user.speaking_now:
                            _recv._on_speaking_change(discord_user, discord_user.speaking_now)

            sink = _ShiroSink()
            logger.info("[VoiceReceiver] BasicSink attached — voice listening + speaking detection active.")
            return sink
        except Exception as e:
            logger.warning(f"[VoiceReceiver] Sink init failed: {e}")
            return None

    async def flush_loop(self):
        silence_s = self.SILENCE_MS / 1000.0
        min_bytes = int(
            self.SAMPLE_RATE * self.CHANNELS * self.SAMPLE_WIDTH * self.MIN_SPEECH_MS / 1000
        )
        while True:
            try:
                await asyncio.sleep(0.2)
                now = time.time()
                finished = [
                    uid for uid, last in list(self._last_audio.items())
                    if now - last >= silence_s and len(self._buffers.get(uid, b"")) > 0
                ]
                for uid in finished:
                    data = bytes(self._buffers.pop(uid, b""))
                    self._last_audio.pop(uid, None)
                    duration_ms = len(data) / (self.SAMPLE_RATE * self.CHANNELS * self.SAMPLE_WIDTH) * 1000
                    if len(data) < min_bytes:
                        discord_user = self._user_map.get(uid)
                        name = discord_user.discord_name if discord_user else str(uid)
                        logger.debug(
                            f"[VoiceReceiver] Dropped short segment from {name}: "
                            f"{duration_ms:.0f}ms < {self.MIN_SPEECH_MS}ms minimum"
                        )
                        continue
                    discord_user = self._user_map.get(uid)
                    if discord_user:
                        logger.debug(f"[VoiceReceiver] Sending {duration_ms:.0f}ms segment from {discord_user.discord_name} to STT")
                        self._on_speech(SpeechSegment(user=discord_user, pcm_data=data))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"VoiceReceiver flush error: {e}")

    def pcm_to_wav(self, pcm: bytes) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self.CHANNELS)
            wf.setsampwidth(self.SAMPLE_WIDTH)
            wf.setframerate(self.SAMPLE_RATE)
            wf.writeframes(pcm)
        return buf.getvalue()

    def cleanup(self):
        self._buffers.clear()
        self._last_audio.clear()


# ─────────────────────────────────────────────────────────────────────────────
#  Main Discord Bot
# ─────────────────────────────────────────────────────────────────────────────

class ShiroDiscordBot:

    NATURAL_CONNECT_PATTERNS = [
        # Must explicitly name Shiro + action: "shiro join", "shiro come to voice", etc.
        re.compile(r"\bshiro[,\s].{0,25}(join|connect|come|hop|get on|jump in)\b", re.I),
        # Direct imperative with no other subject: "join the vc", "hop on the call"
        # but NOT "i joined", "he joined", "they joined" — require sentence start or comma
        re.compile(r"(?:^|[,;]\s*)(join|hop on|get on|come to|get into)\s+(the\s+)?(vc|voice|call|voice\s*chat|voice\s*channel)(?:\s|$|[,!?])", re.I),
    ]
    NATURAL_DISCONNECT_PATTERNS = [
        # Must explicitly name Shiro + action
        re.compile(r"\bshiro[,\s].{0,25}(leave|disconnect|drop out|exit|get off)\b", re.I),
        # Direct imperative: "leave the vc", "drop out of voice"
        re.compile(r"(?:^|[,;]\s*)(leave|disconnect|drop out|exit)\s+(the\s+)?(vc|voice|call|voice\s*chat|voice\s*channel)(?:\s|$|[,!?])", re.I),
    ]

    def __init__(
        self,
        token,
        shiro_names,
        on_text_received,
        on_voice_received,
        on_state_change=None,
        on_speaking_change=None,
        prefix="!",
        address_threshold=0.6,
        tts_speak_cb=None,
        local_tts_mute_cb=None,
        local_tts_unmute_cb=None,
        auto_reply_text=True,
        owner_id: int = 0,
    ):
        if not DISCORD_AVAILABLE:
            raise RuntimeError("discord.py not installed.")

        self.token = token
        self.owner_id = owner_id  # Only this Discord user ID can issue commands
        self.on_text_received = on_text_received
        self.on_voice_received = on_voice_received
        self.on_state_change = on_state_change
        self.on_speaking_change = on_speaking_change
        self.tts_speak_cb = tts_speak_cb
        self.local_tts_mute_cb = local_tts_mute_cb
        self.local_tts_unmute_cb = local_tts_unmute_cb
        self.auto_reply_text = auto_reply_text
        self.address_threshold = address_threshold

        self.detector = AddressDetector(shiro_names)
        self.state = DiscordState()
        self.state.voice_listening = VOICE_RECV_AVAILABLE

        self._known_users = {}
        self._voice_client = None
        # Tracks Shiro's sent messages for reaction-based feedback
        # {message_id -> {"text": str, "user_id": str, "user_name": str, "channel_id": int}}
        self._sent_messages: dict = {}
        self._sent_messages_max = 100   # rolling window
        self.on_reaction_feedback = None  # set by bridge after construction
        self.on_reaction_seen = None       # set by bridge — fires for ANY reaction Shiro sees
        # Conversation context: {channel_id -> {user_id -> last_replied_at}}
        # When Shiro replies to someone, they stay "in conversation" for a window
        self._active_conversations: dict = {}  # channel_id → last active time
        self._channel_queues: dict[int, asyncio.Queue] = {}
        self._channel_workers: dict[int, bool] = {}  # channel_id -> timestamp (channel-wide warmth)
        self.CONVERSATION_WINDOW_S = 90   # 90 seconds — keeps Shiro from hijacking side-chats
        self._receiver = None
        self._flush_task = None
        self._speech_queue = asyncio.Queue()
        self._voice_audio_queue: asyncio.Queue = asyncio.Queue()  # sequential Discord voice
        self._voice_drain_running: bool = False
        self._voice_synth_lock: asyncio.Lock = asyncio.Lock()  # serialize synthesis → arrival order preserved
        self._bot_loop = None
        self._thread = None
        self._ready = threading.Event()

        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        intents.members = True
        intents.reactions = True  # needed for thumbs up/down feedback
        self.bot = commands.Bot(command_prefix=prefix, intents=intents)
        self._register_events()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        if self._thread and self._thread.is_alive():
            logger.warning("Discord bot already running.")
            return
        self.state.connecting = True
        self._notify_state()
        self._thread = threading.Thread(target=self._run_bot, name="ShiroDiscord", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=30)

    def stop(self):
        if self._bot_loop and not self._bot_loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._bot_loop)

    def _run_bot(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._bot_loop = loop
        try:
            loop.run_until_complete(self.bot.start(self.token))
        except Exception as e:
            logger.error(f"Discord bot error: {e}")
            self.state.error = str(e)
            self.state.connected = False
            self.state.connecting = False
            self._notify_state()
        finally:
            loop.close()
            self._ready.set()

    async def _shutdown(self):
        await self._leave_voice()
        await self.bot.close()

    # ── Events ────────────────────────────────────────────────────────────────

    def _register_events(self):
        bot = self.bot

        @bot.event
        async def on_ready():
            logger.info(f"[Discord] Shiro online as {bot.user} (id={bot.user.id})")
            self.state.connected = True
            self.state.connecting = False
            self.state.error = ""
            asyncio.create_task(self._process_speech_queue())
            self._ready.set()
            self._notify_state()

        @bot.event
        async def on_disconnect():
            self.state.connected = False
            self._notify_state()

        @bot.event
        async def on_message(message):
            await self._handle_text_message(message)
            await bot.process_commands(message)

        @bot.event
        async def on_voice_state_update(member, before, after):
            await self._handle_voice_state(member, before, after)

        @bot.event
        async def on_raw_reaction_add(payload):
            await self._handle_reaction(payload)

        # ── Owner-only commands ───────────────────────────────────────────────
        # These commands are COMPLETELY DISABLED for all non-owner users.
        # No error message, no acknowledgement — Shiro simply ignores them.
        # Admin actions belong in the web GUI, not Discord.

        def _is_owner(ctx_or_payload) -> bool:
            """Returns True only if the caller is the configured owner."""
            uid = getattr(ctx_or_payload, 'author', None)
            uid = getattr(uid, 'id', None) if uid else None
            if uid is None:
                uid = getattr(ctx_or_payload, 'user_id', None)
            return self.owner_id != 0 and uid == self.owner_id

        @bot.command(name="join")
        async def cmd_join(ctx, *, channel_name=None):
            if not _is_owner(ctx):
                return  # silently ignore
            await self._cmd_join(ctx, channel_name)

        @bot.command(name="leave")
        async def cmd_leave(ctx):
            if not _is_owner(ctx):
                return
            await self._leave_voice()
            if ctx.channel:
                await ctx.channel.send("ok, leaving the call.")

        @bot.command(name="status")
        async def cmd_status(ctx):
            if not _is_owner(ctx):
                return
            await ctx.channel.send(self._build_status_message())

        @bot.command(name="mute")
        async def cmd_mute(ctx):
            if not _is_owner(ctx):
                return
            if self.local_tts_mute_cb:
                self.local_tts_mute_cb()
            await ctx.channel.send("local speakers muted.")

        @bot.command(name="unmute")
        async def cmd_unmute(ctx):
            if not _is_owner(ctx):
                return
            if self.local_tts_unmute_cb:
                self.local_tts_unmute_cb()
            await ctx.channel.send("local speakers back on.")

    # ── Text ──────────────────────────────────────────────────────────────────

    async def _handle_text_message(self, message):
        if message.author == self.bot.user:
            return
        if message.type != discord.MessageType.default:
            return

        user = self._ensure_user(message.author)
        user.last_typed_at = time.time()
        text = message.content.strip()
        if not text:
            return

        self.state.text_channel_id = message.channel.id
        self.state.text_channel_name = getattr(message.channel, "name", str(message.channel.id))

        # Natural language voice commands — owner only
        if self.owner_id != 0 and message.author.id == self.owner_id:
            for pat in self.NATURAL_CONNECT_PATTERNS:
                if pat.search(text):
                    await self._cmd_join(message, None, reply_channel=message.channel)
                    return
            for pat in self.NATURAL_DISCONNECT_PATTERNS:
                if pat.search(text):
                    await self._leave_voice()
                    await message.channel.send("sure, dropping out of voice.")
                    return

        # ── Smart address detection ──────────────────────────────────────────
        # 1. Direct mention (name in message)
        addressed = self.detector.is_addressed(text, self.address_threshold)

        # 1b. If the message starts with someone else's name (human-to-human),
        #     Shiro should stay quiet even if in active conversation window.
        #     e.g. "Dra, how is your family?" — Shiro should observe, not reply.
        _human_directed = False
        if not addressed:
            _known_humans = [u.discord_name for u in self._known_users.values()
                             if hasattr(u, 'discord_name')]
            _first_word = text.split()[0].rstrip(',.:!?').lower() if text.split() else ""
            if any(_first_word == h.lower() for h in _known_humans
                   if h.lower() not in self.detector.names):
                _human_directed = True

        # 2. Active conversation context — if Shiro recently replied to ANYONE
        #    in this channel, treat ALL users as addressed for the warmth window.
        #    Skipped if message is clearly directed at another human.
        if not addressed and not _human_directed:
            conv_time = self._active_conversations.get(message.channel.id)
            if conv_time and time.time() - conv_time < self.CONVERSATION_WINDOW_S:
                addressed = True
                logger.info(
                    f"[Discord/text] {message.author.display_name}: '{text}' "
                    f"| addressed=True (active conversation context)"
                )

        if not addressed:
            logger.info(f"[Discord/text] {message.author.display_name}: '{text}' | addressed=False")
        else:
            logger.info(f"[Discord/text] {message.author.display_name}: '{text}' | addressed=True")

        if not addressed:
            # Observe silently — ambient context
            try:
                if callable(self.on_text_received):
                    await asyncio.get_running_loop().run_in_executor(
                        None,
                        lambda: self.on_text_received(
                            user.shiro_user_id, user.discord_name, text, message.channel.id, False
                        )
                    )
            except Exception as e:
                logger.debug(f"Ambient observe error: {e}")
            return

        if not self.auto_reply_text:
            return

        try:
            # Serialize messages per-channel — enqueue this message and let the
            # channel worker drain them one at a time in arrival order.
            # This prevents concurrent LLM calls when a user types fast.
            ch_id = message.channel.id
            if ch_id not in self._channel_queues:
                self._channel_queues[ch_id] = asyncio.Queue()
                self._channel_workers[ch_id] = False

            await self._channel_queues[ch_id].put((message, user, text))

            if not self._channel_workers.get(ch_id, False):
                self._channel_workers[ch_id] = True
                asyncio.create_task(self._drain_text_queue(ch_id))
        except Exception as e:
            logger.error(f"[Discord] Text reply error: {e}")

    # ── Voice ─────────────────────────────────────────────────────────────────

    async def _cmd_join(self, ctx_or_msg, channel_name=None, reply_channel=None):
        guild = getattr(ctx_or_msg, "guild", None)
        if not guild:
            return
        channel = None
        if channel_name:
            channel = discord.utils.get(guild.voice_channels, name=channel_name)
        if not channel and hasattr(ctx_or_msg, "author"):
            author = ctx_or_msg.author
            if hasattr(author, "voice") and author.voice and author.voice.channel:
                channel = author.voice.channel
        if not channel and guild.voice_channels:
            channel = guild.voice_channels[0]
        if not channel:
            ch = reply_channel or getattr(ctx_or_msg, "channel", None)
            if ch:
                await ch.send("i don't see a voice channel to join.")
            return
        await self._join_voice(channel)
        ch = reply_channel or getattr(ctx_or_msg, "channel", None)
        if ch:
            await ch.send(f"joining **{channel.name}**.")

    async def _join_voice(self, channel):
        try:
            await self._leave_voice(silent=True)

            if VOICE_RECV_AVAILABLE:
                self._voice_client = await channel.connect(cls=voice_recv.VoiceRecvClient)
            else:
                self._voice_client = await channel.connect(timeout=10.0, reconnect=True)

            self.state.voice_channel_id = channel.id
            self.state.voice_channel_name = channel.name
            self.state.guild_id = channel.guild.id
            self.state.guild_name = channel.guild.name

            for member in channel.members:
                if member != self.bot.user:
                    u = self._ensure_user(member)
                    self.state.users_in_voice[member.id] = u.discord_name

            self._receiver = ShiroVoiceReceiver(
                on_speech=self._on_speech_segment,
                on_speaking_change=self._on_speaking_change,
            )
            self._receiver.set_user_map(self._known_users)

            if VOICE_RECV_AVAILABLE:
                sink = self._receiver.build_sink()
                if sink:
                    self._voice_client.listen(sink)
                    self.state.voice_listening = True
                    logger.info("[Discord] Voice listening active.")

            self._flush_task = asyncio.create_task(self._receiver.flush_loop())

            # Mute local TTS — Discord voice is the output now
            if self.local_tts_mute_cb:
                self.local_tts_mute_cb()
                logger.info("[Discord] Local TTS muted.")

            logger.info(f"[Discord] Joined voice: {channel.name}")
            self._notify_state()

        except Exception as e:
            logger.error(f"[Discord] Failed to join voice: {e}")
            self.state.error = f"Voice join failed: {e}"
            self._notify_state()

    async def _leave_voice(self, silent=False):
        try:
            if self._flush_task:
                self._flush_task.cancel()
                self._flush_task = None
            if self._receiver:
                self._receiver.cleanup()
                self._receiver = None
            if self._voice_client and self._voice_client.is_connected():
                await self._voice_client.disconnect(force=True)
            self._voice_client = None
            self.state.voice_channel_id = None
            self.state.voice_channel_name = ""
            self.state.users_in_voice.clear()
            self.state.currently_speaking.clear()
            self.state.voice_listening = False
            # Restore local TTS
            if self.local_tts_unmute_cb:
                self.local_tts_unmute_cb()
                logger.info("[Discord] Local TTS restored.")
            if not silent:
                self._notify_state()
        except Exception as e:
            logger.error(f"[Discord] Voice disconnect error: {e}")

    async def _handle_voice_state(self, member, before, after):
        if member == self.bot.user:
            return
        user = self._ensure_user(member)
        if after.channel and self._voice_client and after.channel == self._voice_client.channel:
            self.state.users_in_voice[member.id] = user.discord_name
            logger.info(f"[Discord/voice] {member.display_name} joined voice")
        if before.channel and self._voice_client and before.channel == self._voice_client.channel:
            self.state.users_in_voice.pop(member.id, None)
            self.state.currently_speaking.pop(member.id, None)
            user.speaking_now = False
            logger.info(f"[Discord/voice] {member.display_name} left voice")
        self._notify_state()

    def _on_speaking_change(self, user: DiscordUser, is_speaking: bool):
        if is_speaking:
            self.state.currently_speaking[user.discord_id] = user.discord_name
            user.last_spoke_at = time.time()
            logger.info(f"[Discord/voice] 🎙️  {user.discord_name} is speaking")
        else:
            self.state.currently_speaking.pop(user.discord_id, None)
            logger.info(f"[Discord/voice] 🔇 {user.discord_name} stopped speaking")
        self._notify_state()
        if callable(self.on_speaking_change):
            try:
                self.on_speaking_change(user, is_speaking)
            except Exception as e:
                logger.debug(f"on_speaking_change error: {e}")

    # ── Speech STT pipeline ───────────────────────────────────────────────────

    def _on_speech_segment(self, segment: SpeechSegment):
        if self._bot_loop and not self._bot_loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._speech_queue.put(segment), self._bot_loop
            )

    async def _process_speech_queue(self):
        while True:
            try:
                segment = await asyncio.wait_for(self._speech_queue.get(), timeout=1.0)
                await self._handle_voice_segment(segment)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Discord] Speech queue error: {e}")

    async def _handle_voice_segment(self, segment: SpeechSegment):
        try:
            wav_bytes = self._receiver.pcm_to_wav(segment.pcm_data)
            user = segment.user

            # Guard: don't process audio if Shiro is currently speaking —
            # this prevents her from hearing her own TTS output and responding
            # to herself (feedback loop). Check both the voice client source
            # and the TTS is_playing flag via the speak callback reference.
            if self._voice_client and self._voice_client.is_playing():
                logger.debug(f"[Discord] Skipping voice segment from {user.discord_name} — Shiro is speaking")
                return

            reply = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.on_voice_received(user.shiro_user_id, user.discord_name, wav_bytes)
            )
            if reply and reply.strip():
                await self._speak_in_voice(reply)
                await self._send_to_text(f"*[to {user.discord_name}]* {reply}")
        except Exception as e:
            logger.error(f"[Discord] Voice segment error: {e}")

    async def _speak_in_voice(self, text: str):
        """
        Synthesise text and play in Discord voice, queuing if already speaking.

        The synth lock serialises synthesis so that if two messages are
        dispatched close together (e.g. a reply + a continuation), they are
        synthesised in arrival order and played in that order.
        Without the lock, the shorter/faster message wins the executor race
        and gets pushed into the audio queue first, playing out of order.
        """
        if not self._voice_client or not self._voice_client.is_connected():
            return
        if not self.tts_speak_cb:
            return
        try:
            async with self._voice_synth_lock:
                audio_bytes = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: self.tts_speak_cb(text)
                )
                if not audio_bytes:
                    return
                # Queue into the voice audio queue — drain task plays in order.
                await self._voice_audio_queue.put(audio_bytes)
                # Start drain task if not already running
                if not getattr(self, '_voice_drain_running', False):
                    asyncio.create_task(self._drain_voice_queue())
        except Exception as e:
            logger.error(f"[Discord] TTS speak error: {e}")

    async def _drain_voice_queue(self):
        """Play queued voice audio segments sequentially — no overlap, no glitch."""
        self._voice_drain_running = True
        try:
            while not self._voice_audio_queue.empty():
                try:
                    audio_bytes = self._voice_audio_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if not self._voice_client or not self._voice_client.is_connected():
                    break
                # Wait for any current playback to finish
                while self._voice_client.is_playing():
                    await asyncio.sleep(0.1)
                src = discord.PCMAudio(io.BytesIO(audio_bytes))
                done_event = asyncio.Event()
                # FIX: capture loop explicitly — get_event_loop() as default arg is deprecated
                _play_loop = asyncio.get_running_loop()
                def _after(err, ev=done_event, loop=_play_loop):
                    loop.call_soon_threadsafe(ev.set)
                    if err:
                        logger.error(f'[Discord voice] Playback error: {err}')
                self._voice_client.play(src, after=_after)
                await done_event.wait()
        except Exception as e:
            logger.error(f'[Discord] Drain voice queue error: {e}')
        finally:
            self._voice_drain_running = False

    def _track_sent_message(self, message_id: int, text: str,
                            user_id: str, user_name: str, channel_id: int) -> None:
        """Record a message Shiro sent so we can match reactions back to it."""
        self._sent_messages[message_id] = {
            "text":       text,
            "user_id":    user_id,
            "user_name":  user_name,
            "channel_id": channel_id,
        }
        # Rolling window — drop oldest if over limit
        if len(self._sent_messages) > self._sent_messages_max:
            oldest = next(iter(self._sent_messages))
            del self._sent_messages[oldest]

    def _try_proactive_reaction(self, message, incoming_text: str, shiro_reply: str):
        """
        Background thread: ask the engine if Shiro wants to react to the user's message.
        Uses the on_proactive_react callback if set by the bridge.
        """
        try:
            if not callable(getattr(self, 'on_proactive_react', None)):
                return
            emoji = self.on_proactive_react(
                user_name=message.author.display_name,
                user_id=f"discord_{message.author.id}",
                message_text=incoming_text,
                shiro_reply=shiro_reply,
            )
            if emoji and self._bot_loop and not self._bot_loop.is_closed():
                asyncio.run_coroutine_threadsafe(
                    message.add_reaction(emoji),
                    self._bot_loop
                )
                logger.info(f"[Discord] Shiro reacted to {message.author.display_name}'s message with {emoji}")
        except Exception as e:
            logger.debug(f"[Discord] _try_proactive_reaction error: {e}")

    async def _handle_reaction(self, payload):
        """
        Called when any reaction is added to any message.
        - 👍/👎 on Shiro's messages → feedback callback
        - Any emoji on any message → on_reaction_seen callback (Shiro is aware)
        """
        THUMBS_UP   = "👍"
        THUMBS_DOWN = "👎"

        emoji_str = str(payload.emoji)

        # Don't process Shiro's own reactions
        if payload.user_id == self.bot.user.id:
            return

        # Get reactor display name
        reactor_name = str(payload.user_id)
        guild = self.bot.get_guild(payload.guild_id)
        if guild:
            member = guild.get_member(payload.user_id)
            if member:
                reactor_name = member.display_name

        # Feedback path — 👍/👎 on Shiro's messages
        msg_id = payload.message_id
        if emoji_str in (THUMBS_UP, THUMBS_DOWN) and msg_id in self._sent_messages:
            entry    = self._sent_messages[msg_id]
            positive = (emoji_str == THUMBS_UP)
            logger.info(
                f"[Discord] Reaction feedback: {'👍' if positive else '👎'} "
                f"from user_id={payload.user_id} on message: {entry['text'][:60]!r}"
            )
            if callable(self.on_reaction_feedback):
                try:
                    self.on_reaction_feedback(
                        message_text=entry["text"],
                        original_user_id=entry["user_id"],
                        original_user_name=entry["user_name"],
                        reactor_id=f"discord_{payload.user_id}",
                        reactor_name=reactor_name,
                        positive=positive,
                    )
                except Exception as e:
                    logger.error(f"[Discord] on_reaction_feedback error: {e}")

        # Awareness path — Shiro sees ALL reactions, not just feedback ones
        # Fire on_reaction_seen so the bridge can tell the engine
        if callable(self.on_reaction_seen):
            try:
                # Try to get the message text for context
                message_text = ""
                if msg_id in self._sent_messages:
                    message_text = self._sent_messages[msg_id]["text"]
                    was_shiros_message = True
                else:
                    # Reaction on someone else's message — fetch it for context
                    was_shiros_message = False
                    try:
                        ch = self.bot.get_channel(payload.channel_id)
                        if ch:
                            msg = await ch.fetch_message(msg_id)
                            message_text = msg.content[:200] if msg else ""
                    except Exception:
                        pass

                self.on_reaction_seen(
                    reactor_id=f"discord_{payload.user_id}",
                    reactor_name=reactor_name,
                    emoji=emoji_str,
                    message_text=message_text,
                    was_shiros_message=was_shiros_message,
                    channel_id=payload.channel_id,
                )
            except Exception as e:
                logger.error(f"[Discord] on_reaction_seen error: {e}")

    async def _drain_text_queue(self, channel_id: int):
        """
        Drain the per-channel message queue one message at a time.
        Ensures messages from the same channel are always processed in arrival
        order, even when a user sends multiple messages faster than Shiro responds.
        The worker exits when the queue is empty and resets the worker flag so
        the next incoming message will start a fresh worker.
        """
        queue = self._channel_queues.get(channel_id)
        if not queue:
            return
        try:
            while not queue.empty():
                try:
                    message, user, text = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                try:
                    async with message.channel.typing():
                        reply = await asyncio.get_running_loop().run_in_executor(
                            None,
                            lambda: self.on_text_received(
                                user.shiro_user_id, user.discord_name, text, message.channel.id, True
                            )
                        )
                    if reply and reply.strip():
                        self._active_conversations[message.channel.id] = time.time()
                        for chunk in self._chunk_message(reply, 1900):
                            sent_msg = await message.channel.send(chunk)
                            self._track_sent_message(
                                sent_msg.id, chunk,
                                user_id=f"discord_{message.author.id}",
                                user_name=message.author.display_name,
                                channel_id=message.channel.id,
                            )
                        if self._voice_client and self._voice_client.is_connected() and self.tts_speak_cb:
                            await self._speak_in_voice(reply)
                        if callable(self.on_reaction_seen):
                            try:
                                await asyncio.get_running_loop().run_in_executor(
                                    None,
                                    lambda: self._try_proactive_reaction(message, text, reply)
                                )
                            except Exception:
                                pass
                except Exception as e:
                    logger.error(f"[Discord] _drain_text_queue error processing message: {e}")
        finally:
            self._channel_workers[channel_id] = False

    async def react_to_message(self, channel_id: int, message_id: int, emoji: str):
        """Add an emoji reaction to a specific message."""
        try:
            ch = self.bot.get_channel(channel_id)
            if not ch:
                return
            msg = await ch.fetch_message(message_id)
            if msg:
                await msg.add_reaction(emoji)
                logger.info(f"[Discord] Shiro reacted with {emoji} to message {message_id}")
        except Exception as e:
            logger.debug(f"[Discord] react_to_message error: {e}")

    def react_to_message_sync(self, channel_id: int, message_id: int, emoji: str):
        """Thread-safe version of react_to_message."""
        if self._bot_loop and not self._bot_loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self.react_to_message(channel_id, message_id, emoji),
                self._bot_loop
            )

    async def _send_to_text(self, text: str):
        if not self.state.text_channel_id:
            return
        try:
            ch = self.bot.get_channel(self.state.text_channel_id)
            if ch:
                for chunk in self._chunk_message(text, 1900):
                    await ch.send(chunk)
        except Exception as e:
            logger.debug(f"send_to_text error: {e}")

    # ── Thread-safe public API ────────────────────────────────────────────────

    def send_message_sync(self, text: str):
        if self._bot_loop and not self._bot_loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._send_to_text(text), self._bot_loop)

    def speak_in_voice_sync(self, text: str):
        """Thread-safe: synthesise text and play in Discord voice channel."""
        if self._bot_loop and not self._bot_loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._speak_in_voice(text), self._bot_loop)

    def send_voice_and_text_sync(self, text: str):
        """Thread-safe: send to text channel AND speak in voice channel."""
        if self._bot_loop and not self._bot_loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._send_to_text(text), self._bot_loop)
            asyncio.run_coroutine_threadsafe(self._speak_in_voice(text), self._bot_loop)

    def join_voice_sync(self, channel_name=None):
        """Thread-safe voice join. Works even before guild_id is cached."""
        if not self._bot_loop or self._bot_loop.is_closed():
            logger.warning('[Discord] join_voice_sync: bot loop not running')
            return
        if not self.state.connected:
            logger.warning('[Discord] join_voice_sync: bot not connected yet')
            return

        # Resolve guild — use cached id or fall back to first guild in bot
        guild = None
        if self.state.guild_id:
            guild = self.bot.get_guild(self.state.guild_id)
        if not guild and self.bot.guilds:
            guild = self.bot.guilds[0]
            self.state.guild_id = guild.id
            self.state.guild_name = guild.name
            logger.info(f'[Discord] join_voice_sync: resolved guild from bot.guilds[0]: {guild.name}')

        if not guild:
            logger.warning('[Discord] join_voice_sync: no guild found — is Shiro in a server?')
            return

        ch = None
        if channel_name:
            ch = discord.utils.get(guild.voice_channels, name=channel_name)
        if not ch and guild.voice_channels:
            ch = guild.voice_channels[0]
        if ch:
            logger.info(f'[Discord] join_voice_sync: joining {ch.name}')
            asyncio.run_coroutine_threadsafe(self._join_voice(ch), self._bot_loop)
        else:
            logger.warning('[Discord] join_voice_sync: no voice channels found in guild')

    def leave_voice_sync(self):
        if self._bot_loop and not self._bot_loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._leave_voice(), self._bot_loop)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _ensure_user(self, member) -> DiscordUser:
        uid = member.id
        if uid not in self._known_users:
            display = getattr(member, "display_name", str(member))
            self._known_users[uid] = DiscordUser(
                discord_id=uid,
                discord_name=display,
                shiro_user_id=f"discord_{uid}",
            )
        return self._known_users[uid]

    @staticmethod
    def _chunk_message(text: str, size: int = 1900) -> list:
        """Split on sentence boundary where possible, falling back to word boundary."""
        import re as _re
        chunks = []
        while len(text) > size:
            sentence_end = -1
            for m in _re.finditer(r'[.!?]\s+', text[:size]):
                sentence_end = m.end()
            if sentence_end > 100:
                cut = sentence_end
            else:
                cut = text.rfind(" ", 0, size)
                if cut == -1:
                    cut = size
            chunks.append(text[:cut].rstrip())
            text = text[cut:].lstrip()
        if text:
            chunks.append(text)
        return chunks

    def _build_status_message(self) -> str:
        s = self.state
        lines = ["**Shiro Discord Status**"]
        lines.append(f"Connected: {'yes' if s.connected else 'no'}")
        lines.append(f"Voice listening: {'yes' if s.voice_listening else 'no — pip install discord-ext-voice-recv'}")
        if s.guild_name:
            lines.append(f"Server: {s.guild_name}")
        if s.voice_channel_name:
            lines.append(f"Voice: {s.voice_channel_name}")
        if s.users_in_voice:
            lines.append(f"In voice: {', '.join(s.users_in_voice.values())}")
        if s.currently_speaking:
            lines.append(f"Speaking now: {', '.join(s.currently_speaking.values())}")
        if s.text_channel_name:
            lines.append(f"Text ch: #{s.text_channel_name}")
        if s.error:
            lines.append(f"Error: {s.error}")
        return "\n".join(lines)

    def _notify_state(self):
        if callable(self.on_state_change):
            try:
                self.on_state_change(self.state)
            except Exception as e:
                logger.debug(f"State notify error: {e}")

    @property
    def is_connected(self) -> bool:
        return self.state.connected

    @property
    def in_voice(self) -> bool:
        return self._voice_client is not None and self._voice_client.is_connected()