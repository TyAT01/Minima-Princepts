"""
voice_room.py — Shiro's Web Voice + Text Room
Multi-user browser chat (voice + text) via WebSockets.
Audio: browser WebM/Opus → ffmpeg → WAV → Whisper STT
Text: direct message routing → Shiro engine → TTS reply
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import subprocess
import tempfile
import threading
import time
from typing import Callable, Dict, Optional
import requests

logger = logging.getLogger("shiro.voice_room")

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

try:
    import numpy as np
    import soundfile as sf
    AUDIO_AVAILABLE = True
except ImportError:
    AUDIO_AVAILABLE = False


ROOM_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Shiro - Voice Room</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: #1a1a2e; color: #e0e0e0;
  font-family: 'Segoe UI', sans-serif;
  display: flex; flex-direction: column; align-items: center;
  min-height: 100vh; padding: 20px;
}
h1 { color: #ff9d72; margin: 20px 0 4px; font-size: 2em; }
.subtitle { color: #888; margin-bottom: 24px; font-size: 0.9em; }
#setup {
  background: #16213e; border-radius: 16px; padding: 28px;
  width: 100%; max-width: 420px; text-align: center;
}
#setup input {
  width: 100%; padding: 12px 16px; border-radius: 10px;
  border: 1px solid #333; background: #0f3460; color: #fff;
  font-size: 1em; margin-bottom: 14px; outline: none;
}
#setup input:focus { border-color: #ff9d72; }
.btn-join {
  padding: 12px 28px; border-radius: 10px; border: none;
  font-size: 1em; cursor: pointer; font-weight: 600;
  background: #ff9d72; color: #1a1a2e; width: 100%;
}
.btn-join:hover { background: #ffb899; }
#room { display: none; width: 100%; max-width: 700px; flex-direction: column; gap: 14px; }
#room.active { display: flex; }
#users-bar {
  background: #16213e; border-radius: 12px; padding: 12px 16px;
  display: flex; gap: 10px; flex-wrap: wrap; align-items: center; min-height: 52px;
}
.user-chip {
  background: #0f3460; border-radius: 20px; padding: 4px 14px;
  font-size: 0.85em; display: flex; align-items: center; gap: 6px; transition: background 0.3s;
}
.user-chip.speaking { background: #2d6a2d; animation: pulse 0.8s infinite; }
.user-chip.shiro { background: #6b2d8a; }
@keyframes pulse { 0%,100% { opacity:1 } 50% { opacity:0.6 } }
#chat {
  background: #16213e; border-radius: 12px; padding: 16px;
  height: 360px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px;
}
.msg { display: flex; gap: 10px; align-items: flex-start; }
.msg.shiro { flex-direction: row-reverse; }
.avatar {
  width: 36px; height: 36px; border-radius: 50%; background: #0f3460;
  display: flex; align-items: center; justify-content: center; font-size: 1.2em; flex-shrink: 0;
}
.msg.shiro .avatar { background: #6b2d8a; }
.bubble {
  background: #0f3460; border-radius: 12px; padding: 10px 14px;
  max-width: 80%; font-size: 0.95em; line-height: 1.5;
}
.msg.shiro .bubble { background: #3d1a5e; color: #f0c0ff; }
.speaker { font-size: 0.75em; color: #888; margin-bottom: 3px; }
.sys-msg { text-align:center; color:#555; font-size:0.8em; padding:4px; }
#text-input-row { display: flex; gap: 10px; }
#text-input {
  flex: 1; padding: 12px 16px; border-radius: 10px;
  border: 1px solid #333; background: #0f3460; color: #fff; font-size: 0.95em; outline: none;
}
#text-input:focus { border-color: #ff9d72; }
#send-btn {
  padding: 12px 20px; border-radius: 10px; border: none;
  background: #ff9d72; color: #1a1a2e; font-size: 1em; font-weight: 700; cursor: pointer;
}
#send-btn:hover { background: #ffb899; }
#controls { display: flex; justify-content: center; }
#mic-btn {
  width: 68px; height: 68px; border-radius: 50%; border: none; font-size: 1.8em;
  cursor: pointer; background: #0f3460; color: white; transition: all 0.2s;
  box-shadow: 0 4px 15px rgba(0,0,0,0.3);
}
#mic-btn.recording { background: #c0392b; animation: pulse 1s infinite; box-shadow: 0 0 20px rgba(192,57,43,0.5); }
#mic-btn:hover { transform: scale(1.05); }
#status {
  background: #16213e; border-radius: 10px; padding: 10px 16px;
  font-size: 0.85em; color: #888; text-align: center;
}
#status.connected { color: #4caf50; }
#status.error { color: #f44336; }
.typing-indicator { display:flex; gap:4px; align-items:center; padding:8px 12px; }
.dot { width:8px; height:8px; border-radius:50%; background:#888; animation:bounce 1.2s infinite; }
.dot:nth-child(2) { animation-delay:0.2s; }
.dot:nth-child(3) { animation-delay:0.4s; }
@keyframes bounce { 0%,60%,100% { transform:translateY(0) } 30% { transform:translateY(-6px) } }
</style>
</head>
<body>
<h1>&#x1F98A; Shiro</h1>
<p class="subtitle">Voice + Text Room</p>

<div id="setup">
  <input id="name-input" placeholder="Your name" maxlength="32" />
  <button class="btn-join" onclick="joinRoom()">Join Room</button>
</div>

<div id="room">
  <div id="users-bar">
    <span style="color:#888;font-size:0.8em">In room:</span>
    <div class="user-chip shiro">&#x1F98A; Shiro</div>
  </div>
  <div id="chat"></div>
  <div id="text-input-row">
    <input id="text-input" placeholder="Type a message to Shiro..." />
    <button id="send-btn" onclick="sendText()">Send</button>
  </div>
  <div id="controls">
    <button id="mic-btn" onclick="toggleMic()" title="Click to speak">&#x1F3A4;</button>
  </div>
  <div id="status">Connecting...</div>
</div>

<script>
let ws = null, userName = '', mediaRecorder = null, audioChunks = [], isRecording = false;

function joinRoom() {
  userName = document.getElementById('name-input').value.trim() || 'Guest';
  document.getElementById('setup').style.display = 'none';
  document.getElementById('room').classList.add('active');
  connectWS();
}

function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(proto + '://' + location.host + '/voice-room/ws/' + encodeURIComponent(userName));
  setStatus('Connecting...', '');
  ws.onopen = () => setStatus('Connected -- type below or click mic to speak', 'connected');
  ws.onmessage = e => handleMessage(JSON.parse(e.data));
  ws.onclose = () => { setStatus('Disconnected -- reconnecting...', 'error'); setTimeout(connectWS, 3000); };
  ws.onerror = () => setStatus('Connection error', 'error');
}

function handleMessage(msg) {
  if (msg.type === 'chat') { removeTyping(); addMessage(msg.speaker, msg.text, msg.speaker === 'Shiro'); }
  else if (msg.type === 'audio') { playAudio(msg.data); }
  else if (msg.type === 'users') { updateUsers(msg.users); }
  else if (msg.type === 'speaking') { setSpeaking(msg.user, msg.speaking); }
  else if (msg.type === 'typing') { msg.typing ? showTyping() : removeTyping(); }
  else if (msg.type === 'system') { addSysMsg(msg.text); }
}

function sendText() {
  const input = document.getElementById('text-input');
  const text = input.value.trim();
  if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: 'text', text: text }));
  input.value = '';
}

async function toggleMic() {
  if (isRecording) { stopRecording(); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus'
               : MediaRecorder.isTypeSupported('audio/ogg;codecs=opus') ? 'audio/ogg;codecs=opus'
               : 'audio/webm';
    mediaRecorder = new MediaRecorder(stream, { mimeType: mime });
    audioChunks = [];
    mediaRecorder.ondataavailable = e => { if (e.data.size > 0) audioChunks.push(e.data); };
    mediaRecorder.onstop = () => {
      const blob = new Blob(audioChunks, { type: mime });
      const reader = new FileReader();
      reader.onloadend = () => {
        if (ws && ws.readyState === WebSocket.OPEN)
          ws.send(JSON.stringify({ type: 'audio', data: reader.result.split(',')[1], mime: mime }));
      };
      reader.readAsDataURL(blob);
      setSpeaking(userName, false);
    };
    mediaRecorder.start(100);
    isRecording = true;
    document.getElementById('mic-btn').classList.add('recording');
    document.getElementById('mic-btn').textContent = String.fromCodePoint(0x1F534);
    setStatus('Recording -- click again to send', 'connected');
    setSpeaking(userName, true);
    setTimeout(() => { if (isRecording) stopRecording(); }, 30000);
  } catch(e) { setStatus('Mic error: ' + e.message, 'error'); }
}

function stopRecording() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop();
    mediaRecorder.stream.getTracks().forEach(t => t.stop());
  }
  isRecording = false;
  document.getElementById('mic-btn').classList.remove('recording');
  document.getElementById('mic-btn').textContent = String.fromCodePoint(0x1F3A4);
  setStatus('Processing...', 'connected');
}

function playAudio(b64) {
  try {
    const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: 'audio/wav' }));
    const a = new Audio(url);
    a.onended = () => URL.revokeObjectURL(url);
    a.play().catch(e => console.warn('Playback:', e));
  } catch(e) { console.error(e); }
}

function addMessage(speaker, text, isShiro) {
  const chat = document.getElementById('chat');
  const div = document.createElement('div');
  div.className = 'msg' + (isShiro ? ' shiro' : '');
  div.innerHTML = '<div class="avatar">' + (isShiro ? '&#x1F98A;' : '&#x1F464;') + '</div><div><div class="speaker">' + esc(speaker) + '</div><div class="bubble">' + esc(text) + '</div></div>';
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

function addSysMsg(text) {
  const div = document.createElement('div');
  div.className = 'sys-msg'; div.textContent = text;
  const chat = document.getElementById('chat');
  chat.appendChild(div); chat.scrollTop = chat.scrollHeight;
}

function showTyping() {
  removeTyping();
  const div = document.createElement('div');
  div.className = 'msg shiro'; div.id = 'typing-indicator';
  div.innerHTML = '<div class="avatar">&#x1F98A;</div><div class="bubble typing-indicator"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>';
  const chat = document.getElementById('chat');
  chat.appendChild(div); chat.scrollTop = chat.scrollHeight;
}

function removeTyping() { const el = document.getElementById('typing-indicator'); if (el) el.remove(); }

function updateUsers(users) {
  const bar = document.getElementById('users-bar');
  bar.innerHTML = '<span style="color:#888;font-size:0.8em">In room:</span><div class="user-chip shiro">&#x1F98A; Shiro</div>';
  users.forEach(u => {
    const chip = document.createElement('div');
    chip.className = 'user-chip'; chip.id = 'chip-' + u; chip.textContent = '👤 ' + u;
    bar.appendChild(chip);
  });
}

function setSpeaking(user, speaking) {
  const chip = document.getElementById('chip-' + user);
  if (chip) { chip.classList.toggle('speaking', speaking); chip.textContent = (speaking ? '🎙 ' : '👤 ') + user; }
}

function setStatus(text, cls) {
  const el = document.getElementById('status');
  el.textContent = text; el.className = cls;
}

function esc(t) { return String(t).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('name-input').addEventListener('keydown', e => { if (e.key === 'Enter') joinRoom(); });
  document.getElementById('text-input').addEventListener('keydown', e => { if (e.key === 'Enter') sendText(); });
});
</script>
</body>
</html>"""


class VoiceRoom:
    def __init__(self, app, port: int = 8765, host: str = "0.0.0.0"):
        self.shiro_app = app
        self.port = port
        self.host = host
        self._connections: Dict[str, WebSocket] = {}
        self._fastapi = None
        self._thread = None
        self._loop = None

    def start(self):
        if not FASTAPI_AVAILABLE:
            logger.error("FastAPI not installed.")
            return None
        self._fastapi = FastAPI(title="Shiro Voice Room")
        self._fastapi.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
        self._register_routes()
        self._thread = threading.Thread(target=self._run_server, name="ShiroVoiceRoom", daemon=True)
        self._thread.start()
        logger.info(f"[VoiceRoom] Started at http://0.0.0.0:{self.port}/voice-room")
        return self

    def _run_server(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            config = uvicorn.Config(self._fastapi, host=self.host, port=self.port, log_level="info", loop="asyncio", proxy_headers=True, forwarded_allow_ips="*")
            server = uvicorn.Server(config)
            loop.run_until_complete(server.serve())
        except Exception as e:
            logger.error(f"[VoiceRoom] Server crashed: {e}", exc_info=True)

    def _register_routes(self):
        app = self._fastapi
        room = self

        @app.get("/voice-room", response_class=HTMLResponse)
        async def get_room():
            return HTMLResponse(content=ROOM_HTML, headers={"Access-Control-Allow-Origin": "*"})

        @app.get("/voice-room/invite")
        async def get_invite():
            return {"url": room.get_invite_url(), "users": list(room._connections.keys())}

        @app.websocket("/voice-room/ws/{user_name}")
        async def voice_ws(websocket: WebSocket, user_name: str):
            await room._handle_connection(websocket, user_name)

    async def _handle_connection(self, ws: WebSocket, user_name: str):
        await ws.accept()
        self._connections[user_name] = ws
        logger.info(f"[VoiceRoom] {user_name} joined")
        await self._broadcast({"type": "system", "text": f"{user_name} joined."})
        await self._broadcast_users()
        try:
            while True:
                raw = await ws.receive_text()
                msg = json.loads(raw)
                if msg.get("type") == "audio":
                    await self._handle_audio(ws, user_name, msg["data"], msg.get("mime", "audio/webm"))
                elif msg.get("type") == "text":
                    await self._handle_text(ws, user_name, msg["text"])
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"[VoiceRoom] WS error for {user_name}: {e}")
        finally:
            self._connections.pop(user_name, None)
            logger.info(f"[VoiceRoom] {user_name} left")
            await self._broadcast({"type": "system", "text": f"{user_name} left."})
            await self._broadcast_users()

    async def _handle_text(self, ws: WebSocket, user_name: str, text: str):
        if not text or not text.strip():
            return
        loop = asyncio.get_running_loop()  # FIX: get_event_loop() deprecated in 3.10+
        # Notify engine: user just submitted (no longer typing, but activity recorded)
        self._notify_typing(user_name, False)
        await self._broadcast({"type": "chat", "speaker": user_name, "text": text})
        await self._broadcast({"type": "typing", "typing": True})
        reply = await loop.run_in_executor(None, lambda: self._get_reply(text, user_name))
        await self._broadcast({"type": "typing", "typing": False})
        if not reply:
            return
        await self._broadcast({"type": "chat", "speaker": "Shiro", "text": reply})
        tts_audio = await loop.run_in_executor(None, lambda: self._synthesize(reply))
        if tts_audio:
            await self._broadcast({"type": "audio", "data": base64.b64encode(tts_audio).decode()})

    async def _handle_audio(self, ws: WebSocket, user_name: str, b64_audio: str, mime: str):
        loop = asyncio.get_running_loop()  # FIX: get_event_loop() deprecated in 3.10+
        # Notify engine: user is speaking (counts as activity, hold autonomous)
        self._notify_typing(user_name, True)
        await self._broadcast({"type": "speaking", "user": user_name, "speaking": True})
        try:
            audio_bytes = base64.b64decode(b64_audio)
            transcribed = await loop.run_in_executor(None, lambda: self._transcribe(audio_bytes, mime))
            self._notify_typing(user_name, False)  # done speaking
            await self._broadcast({"type": "speaking", "user": user_name, "speaking": False})
            if not transcribed or not transcribed.strip():
                return
            await self._broadcast({"type": "chat", "speaker": user_name, "text": transcribed})
            await self._broadcast({"type": "typing", "typing": True})
            reply = await loop.run_in_executor(None, lambda: self._get_reply(transcribed, user_name))
            await self._broadcast({"type": "typing", "typing": False})
            if not reply:
                return
            await self._broadcast({"type": "chat", "speaker": "Shiro", "text": reply})
            tts_audio = await loop.run_in_executor(None, lambda: self._synthesize(reply))
            if tts_audio:
                await self._broadcast({"type": "audio", "data": base64.b64encode(tts_audio).decode()})
        except Exception as e:
            logger.error(f"[VoiceRoom] Audio handling error: {e}")
            await self._broadcast({"type": "speaking", "user": user_name, "speaking": False})
            await self._broadcast({"type": "typing", "typing": False})

    def _transcribe(self, audio_bytes: bytes, mime: str = "audio/webm") -> str:
        try:
            suffix = ".ogg" if "ogg" in mime else ".webm"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_in:
                tmp_in.write(audio_bytes)
                tmp_in_path = tmp_in.name
            tmp_out_path = tmp_in_path.replace(suffix, ".wav")
            try:
                result = subprocess.run(
                    ["ffmpeg", "-y", "-i", tmp_in_path, "-ar", "16000", "-ac", "1", "-f", "wav", tmp_out_path],
                    capture_output=True, timeout=30,
                )
                if result.returncode != 0:
                    logger.error(f"[VoiceRoom] ffmpeg error: {result.stderr.decode()}")
                    return ""
                audio_np, sr = sf.read(tmp_out_path, dtype="float32")
                if audio_np.ndim == 2:
                    audio_np = audio_np.mean(axis=1)
            finally:
                for p in [tmp_in_path, tmp_out_path]:
                    try: os.unlink(p)
                    except OSError: pass

            stt = self.shiro_app.stt
            if stt.model is None:
                stt.load_model()
            segments, _ = stt.model.transcribe(audio_np, beam_size=5, language="en", vad_filter=False)
            text = " ".join(s.text.strip() for s in segments).strip()

            HALLUCINATIONS = {"thank you for watching", "thanks for watching", "please subscribe", ".", "..", "..."}
            if text.lower().strip(".! ") in HALLUCINATIONS:
                return ""
            words = text.lower().split()
            if len(words) >= 4 and len(set(words)) <= 2:
                return ""
            logger.info(f"[VoiceRoom] Transcribed: {text!r}")
            return text

        except FileNotFoundError:
            logger.error("[VoiceRoom] ffmpeg not found — install: winget install ffmpeg")
            return ""
        except Exception as e:
            logger.error(f"[VoiceRoom] Transcribe error: {e}")
            return ""

    def _get_reply(self, text: str, user_name: str) -> str:
        """Get Shiro's reply. Suppresses local TTS speaker — voice room handles audio itself.

        FIX: Use set_muted(True) instead of tts.enabled = False.
        The old approach left TTS permanently disabled if process_text() raised.
        set_muted() only silences local playback — the TTS pipeline stays warm.
        """
        try:
            tts = getattr(self.shiro_app, 'tts', None)
            _was_muted = False
            if tts and tts.enabled and not tts._muted:
                tts.set_muted(True)
                _was_muted = True
            try:
                reply = "".join(self.shiro_app.process_text(text, user_name=user_name)).strip()
            finally:
                if _was_muted and tts:
                    tts.set_muted(False)
            return reply
        except Exception as e:
            logger.error(f"[VoiceRoom] Engine error: {e}")
            # Safety: always unmute on error so local TTS recovers
            try:
                tts = getattr(self.shiro_app, 'tts', None)
                if tts:
                    tts.set_muted(False)
            except Exception:
                pass
            return ""

    def _synthesize(self, text: str) -> Optional[bytes]:
        """Synthesize speech using ShiroTTS (Kokoro)."""
        try:
            tts = getattr(self.shiro_app, 'tts', None)
            if not tts or not tts.enabled:
                return None

            # ShiroTTS.synthesize_to_bytes returns raw PCM16
            pcm_bytes = tts.synthesize_to_bytes(text)
            if not pcm_bytes:
                return None

            # Convert raw PCM16 to WAV bytes for browser playback
            # Assuming sr=24000 from ShiroTTS implementation
            with io.BytesIO() as wav_buf:
                audio_np = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32767.0
                sf.write(wav_buf, audio_np, 24000, format='WAV')
                return wav_buf.getvalue()
        except Exception as e:
            logger.debug(f"[VoiceRoom] TTS error: {e}")
        return None

    def _notify_typing(self, user_name: str, is_typing: bool):
        """Notify Shiro engine about user typing/speaking state for pace tracking."""
        try:
            app = self.shiro_app
            if hasattr(app, 'set_user_typing'):
                app.set_user_typing(is_typing, user_id=user_name)
            elif hasattr(app, 'engine') and hasattr(app.engine, 'set_user_typing'):
                app.engine.set_user_typing(is_typing, user_id=user_name)
        except Exception:
            pass

    async def _broadcast(self, msg: dict):
        dead = []
        for name, ws in list(self._connections.items()):
            try: await ws.send_text(json.dumps(msg))
            except Exception: dead.append(name)
        for name in dead:
            self._connections.pop(name, None)

    async def _broadcast_users(self):
        await self._broadcast({"type": "users", "users": list(self._connections.keys())})

    def get_invite_url(self) -> str:
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
        except Exception: ip = "localhost"
        return f"http://{ip}:{self.port}/voice-room"