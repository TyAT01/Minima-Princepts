import gradio as gr
import logging
import base64
import json
import time
from pathlib import Path
from typing import Callable, Optional, List, Tuple

logger = logging.getLogger(__name__)

class ShiroGUI:
    """Gradio-based Web GUI for Shiro AI — Enhanced Edition."""

    def __init__(
        self,
        process_text_cb: Callable[[str, str], str],
        process_audio_cb: Callable[[str, str], Tuple[str, str]],
        toggle_mic_cb: Callable[[bool], None],
        poll_results_cb: Callable[[], List[Tuple[str, str]]],
        join_chat_cb: Optional[Callable[[str], List[str]]] = None,
        leave_chat_cb: Optional[Callable[[str], None]] = None,
        title: str = "Shiro",
        theme: str = "soft"
    ):
        self.process_text_cb = process_text_cb
        self.process_audio_cb = process_audio_cb
        self.toggle_mic_cb = toggle_mic_cb
        self.poll_results_cb = poll_results_cb
        self.join_chat_cb = join_chat_cb
        self.leave_chat_cb = leave_chat_cb
        self.title = title
        self.theme = theme
        self.interface = None
        self.theme_obj = None
        self.custom_css = ""

        # Session persistence — survives page refresh within the same Python process lifetime
        self._session_cache: dict = {}  # user_name -> list of messages

    def _save_avatars(self) -> List[str]:
        """Saves SVG avatars to files."""
        assets_dir = Path(__file__).parent / "assets"
        assets_dir.mkdir(exist_ok=True)

        user_svg_path = assets_dir / "user.svg"
        bot_svg_path = assets_dir / "bot.svg"

        # Knight-helmet user avatar (clean, geometric)
        user_svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <defs>
    <linearGradient id="ug" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#6ea8d8"/>
      <stop offset="100%" stop-color="#3a6fa8"/>
    </linearGradient>
  </defs>
  <!-- Helmet dome -->
  <path d="M20 55 Q20 15 50 15 Q80 15 80 55 L80 70 Q80 78 72 78 L28 78 Q20 78 20 70 Z" fill="url(#ug)" stroke="#2a5585" stroke-width="2"/>
  <!-- Visor slit -->
  <rect x="28" y="48" width="44" height="10" rx="4" fill="#1a2a3a" stroke="#2a5585" stroke-width="1.5"/>
  <!-- Visor highlight -->
  <rect x="30" y="50" width="40" height="3" rx="2" fill="#4a90e2" opacity="0.4"/>
  <!-- Cheek guards -->
  <path d="M20 60 L14 70 Q14 82 24 82 L28 78 L28 60 Z" fill="#4a7ab8" stroke="#2a5585" stroke-width="1.5"/>
  <path d="M80 60 L86 70 Q86 82 76 82 L72 78 L72 60 Z" fill="#4a7ab8" stroke="#2a5585" stroke-width="1.5"/>
  <!-- Neck guard -->
  <rect x="30" y="76" width="40" height="10" rx="3" fill="#3a6fa8" stroke="#2a5585" stroke-width="1.5"/>
  <!-- Center ridge -->
  <path d="M50 15 L50 55" stroke="#5a9ad0" stroke-width="2" opacity="0.5"/>
  <!-- Plume base -->
  <rect x="46" y="10" width="8" height="8" rx="2" fill="#5a8ab8"/>
</svg>"""

        # Fox-girl Shiro avatar — stylized kitsune face with ears and fluffy tail hint
        bot_svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <defs>
    <linearGradient id="fg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#e8c49a"/>
      <stop offset="100%" stop-color="#d4a070"/>
    </linearGradient>
    <linearGradient id="ear_g" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#f5e0c8"/>
      <stop offset="100%" stop-color="#e0aa78"/>
    </linearGradient>
    <radialGradient id="cheek" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#f5a0a0" stop-opacity="0.7"/>
      <stop offset="100%" stop-color="#f5a0a0" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <!-- Left fox ear -->
  <path d="M22 45 L16 14 L40 36 Z" fill="url(#ear_g)" stroke="#c08050" stroke-width="1.5"/>
  <path d="M24 42 L20 20 L37 37 Z" fill="#e8a0a0" opacity="0.6"/>
  <!-- Right fox ear -->
  <path d="M78 45 L84 14 L60 36 Z" fill="url(#ear_g)" stroke="#c08050" stroke-width="1.5"/>
  <path d="M76 42 L80 20 L63 37 Z" fill="#e8a0a0" opacity="0.6"/>
  <!-- Face -->
  <ellipse cx="50" cy="57" rx="28" ry="26" fill="url(#fg)" stroke="#c08050" stroke-width="1.5"/>
  <!-- Cheek blush -->
  <ellipse cx="33" cy="62" rx="8" ry="5" fill="url(#cheek)"/>
  <ellipse cx="67" cy="62" rx="8" ry="5" fill="url(#cheek)"/>
  <!-- Eyes — slightly lidded, cunning -->
  <ellipse cx="38" cy="53" rx="6" ry="5" fill="#1a0a00"/>
  <ellipse cx="62" cy="53" rx="6" ry="5" fill="#1a0a00"/>
  <!-- Eye shine -->
  <circle cx="36" cy="51" r="2" fill="white" opacity="0.9"/>
  <circle cx="60" cy="51" r="2" fill="white" opacity="0.9"/>
  <!-- Amber iris -->
  <ellipse cx="38" cy="53" rx="4" ry="3.5" fill="#c07020" opacity="0.7"/>
  <ellipse cx="62" cy="53" rx="4" ry="3.5" fill="#c07020" opacity="0.7"/>
  <!-- Smug/sly eyebrow left -->
  <path d="M32 47 Q38 44 44 46" stroke="#7a4020" stroke-width="2" fill="none" stroke-linecap="round"/>
  <!-- Smug/sly eyebrow right (slightly raised = smug) -->
  <path d="M56 45 Q62 42 68 46" stroke="#7a4020" stroke-width="2" fill="none" stroke-linecap="round"/>
  <!-- Small foxy nose -->
  <ellipse cx="50" cy="64" rx="3.5" ry="2.5" fill="#c07070"/>
  <!-- Smug smirk -->
  <path d="M42 70 Q50 75 62 69" stroke="#9a5040" stroke-width="2" fill="none" stroke-linecap="round"/>
  <!-- Short white hair fringe suggestion -->
  <path d="M28 40 Q35 30 50 28 Q65 26 72 38" stroke="#f8f0e8" stroke-width="4" fill="none" stroke-linecap="round" opacity="0.8"/>
  <!-- Collar of oversized tee peeking in -->
  <path d="M30 83 Q50 78 70 83 L75 90 L25 90 Z" fill="#e8e0d8" stroke="#c8bfb0" stroke-width="1"/>
</svg>"""

        with open(user_svg_path, "w", encoding="utf-8") as f:
            f.write(user_svg)
        with open(bot_svg_path, "w", encoding="utf-8") as f:
            f.write(bot_svg)

        return [str(user_svg_path), str(bot_svg_path)]

    def build_ui(self):
        self.custom_css = """
        /* ─── Base & Font ─────────────────────────────── */
        @import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@400;600&family=Nunito:wght@300;400;600&display=swap');

        * { box-sizing: border-box; }

        .gradio-container {
            background: #0a0a0f !important;
            color: #e8e0d0 !important;
            font-family: 'Nunito', sans-serif !important;
            min-height: 100vh;
        }

        /* ─── Animated background ────────────────────── */
        .gradio-container::before {
            content: '';
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background:
                radial-gradient(ellipse 60% 40% at 20% 30%, rgba(180, 80, 40, 0.07) 0%, transparent 70%),
                radial-gradient(ellipse 50% 60% at 80% 70%, rgba(60, 40, 120, 0.08) 0%, transparent 70%),
                repeating-linear-gradient(
                    0deg,
                    transparent,
                    transparent 2px,
                    rgba(255,255,255,0.008) 2px,
                    rgba(255,255,255,0.008) 4px
                );
            pointer-events: none;
            z-index: 0;
        }

        /* ─── Header ──────────────────────────────────── */
        .shiro-header {
            text-align: center;
            padding: 24px 0 8px;
            position: relative;
        }

        .shiro-header h1 {
            font-family: 'Cinzel', serif !important;
            font-size: 2.4rem !important;
            font-weight: 600 !important;
            letter-spacing: 0.15em !important;
            background: linear-gradient(135deg, #f0c070 0%, #e88040 40%, #c05020 100%) !important;
            -webkit-background-clip: text !important;
            -webkit-text-fill-color: transparent !important;
            background-clip: text !important;
            margin: 0 !important;
            text-shadow: none !important;
        }

        .shiro-subtitle {
            font-size: 0.78rem;
            color: #6a5a4a;
            letter-spacing: 0.3em;
            text-transform: uppercase;
            margin-top: 4px;
        }

        /* ─── Chatbot ──────────────────────────────────── */
        #chatbot {
            background: #0f0f18 !important;
            border: 1px solid #2a1f18 !important;
            border-radius: 16px !important;
            box-shadow:
                0 0 0 1px rgba(200, 100, 40, 0.08),
                0 8px 32px rgba(0,0,0,0.6),
                inset 0 1px 0 rgba(255,255,255,0.03) !important;
        }

        /* Message bubbles */
        .message-bubble-border {
            border-radius: 14px !important;
        }

        /* User messages */
        .message.user .message-bubble-border,
        div[data-testid="user"] .message-bubble-border {
            background: linear-gradient(135deg, #1e3a5a, #2a4a7a) !important;
            border: 1px solid #3a6aaa !important;
            color: #d8e8f8 !important;
        }

        /* Bot messages */
        .message.bot .message-bubble-border,
        div[data-testid="bot"] .message-bubble-border {
            background: linear-gradient(135deg, #1a1208, #221810) !important;
            border: 1px solid #4a2a18 !important;
            color: #f0e8d8 !important;
        }

        /* ─── Input area ───────────────────────────────── */
        .message-input, textarea, input[type="text"] {
            background: #13111a !important;
            border: 1px solid #2a2030 !important;
            color: #e8e0d0 !important;
            border-radius: 12px !important;
            font-family: 'Nunito', sans-serif !important;
            transition: border-color 0.2s ease !important;
        }

        .message-input:focus, textarea:focus, input[type="text"]:focus {
            border-color: #c07030 !important;
            box-shadow: 0 0 0 2px rgba(192, 112, 48, 0.15) !important;
            outline: none !important;
        }

        /* ─── Buttons ──────────────────────────────────── */
        button.primary {
            background: linear-gradient(135deg, #c07030, #a05020) !important;
            border: none !important;
            color: #fff8f0 !important;
            font-family: 'Nunito', sans-serif !important;
            font-weight: 600 !important;
            border-radius: 10px !important;
            letter-spacing: 0.05em !important;
            transition: all 0.2s ease !important;
            box-shadow: 0 2px 8px rgba(160, 80, 32, 0.4) !important;
        }

        button.primary:hover {
            background: linear-gradient(135deg, #d08040, #b06030) !important;
            box-shadow: 0 4px 16px rgba(160, 80, 32, 0.5) !important;
            transform: translateY(-1px) !important;
        }

        button.secondary {
            background: #1a1820 !important;
            border: 1px solid #3a3050 !important;
            color: #b8a8c8 !important;
            border-radius: 10px !important;
            font-family: 'Nunito', sans-serif !important;
            transition: all 0.2s ease !important;
        }

        button.secondary:hover {
            border-color: #6a50a0 !important;
            color: #d8c8e8 !important;
        }

        button.stop {
            background: #1a0f0f !important;
            border: 1px solid #5a2020 !important;
            color: #c08080 !important;
            border-radius: 10px !important;
            font-family: 'Nunito', sans-serif !important;
            transition: all 0.2s ease !important;
        }

        button.stop:hover {
            border-color: #a04040 !important;
            color: #e0a0a0 !important;
        }

        /* ─── Sidebar panel ────────────────────────────── */
        .sidebar-panel {
            background: #0f0d18 !important;
            border: 1px solid #1e1828 !important;
            border-radius: 14px !important;
            padding: 16px !important;
        }

        /* Labels */
        label span {
            font-size: 0.78rem !important;
            letter-spacing: 0.08em !important;
            text-transform: uppercase !important;
            color: #7a6a5a !important;
            font-weight: 600 !important;
        }

        /* Markdown headers in sidebar */
        .gradio-markdown h3 {
            font-family: 'Cinzel', serif !important;
            font-size: 0.85rem !important;
            color: #c07030 !important;
            letter-spacing: 0.12em !important;
            margin: 16px 0 8px !important;
            text-transform: uppercase !important;
        }

        /* ─── Status indicator ─────────────────────────── */
        .status-text {
            font-size: 0.78rem !important;
            color: #5a7a5a !important;
            font-family: 'Nunito', sans-serif !important;
        }

        /* Checkbox */
        input[type="checkbox"] {
            accent-color: #c07030 !important;
        }

        /* Error box */
        .error-box textarea {
            background: #120808 !important;
            border-color: #4a1818 !important;
            color: #d07070 !important;
            font-size: 0.8rem !important;
        }

        /* ─── Dividers ─────────────────────────────────── */
        hr { border-color: #1e1828 !important; }

        /* ─── Scrollbar ────────────────────────────────── */
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: #0a0a0f; }
        ::-webkit-scrollbar-thumb { background: #3a2820; border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: #6a4830; }

        /* ─── Session badge ────────────────────────────── */
        .session-badge {
            display: inline-flex;
            align-items: center;
            gap: 5px;
            font-size: 0.72rem;
            color: #5a9a5a;
            background: rgba(40, 80, 40, 0.12);
            border: 1px solid rgba(80, 160, 80, 0.2);
            border-radius: 20px;
            padding: 2px 10px;
            margin-top: 6px;
        }

        /* ─── Typing indicator (reduces flicker) ──────── */
        .streaming-indicator {
            display: inline-block;
            width: 6px; height: 6px;
            background: #c07030;
            border-radius: 50%;
            animation: blink 0.8s ease-in-out infinite;
            margin-left: 4px;
            vertical-align: middle;
        }

        @keyframes blink {
            0%, 100% { opacity: 0.2; }
            50% { opacity: 1; }
        }

        /* Avatar sizing */
        .avatar-image img { border-radius: 50% !important; }

        footer { display: none !important; }
        """

        if self.theme == "soft":
            self.theme_obj = gr.themes.Soft(
                primary_hue="orange",
                secondary_hue="violet",
                neutral_hue="stone",
            )
        elif self.theme == "monochrome":
            self.theme_obj = gr.themes.Monochrome()
        elif self.theme == "glass":
            self.theme_obj = gr.themes.Glass()
        else:
            self.theme_obj = gr.themes.Default()

        avatar_paths = self._save_avatars()

        with gr.Blocks(
            title=self.title,
            theme=self.theme_obj,
            css=self.custom_css,
        ) as demo:

            # ── Header ──────────────────────────────────────
            gr.HTML("""
            <div class="shiro-header">
                <h1>Shiro</h1>
                <div class="shiro-subtitle">The Sly Kitsune Yaoguai</div>
            </div>
            """)

            # ── State: session cache for page-refresh persistence ──
            # Stores chat history as JSON string so it survives a page refresh
            # (persists as long as the Python process is alive)
            session_key = gr.State(value=None)

            with gr.Row(equal_height=True):

                # ── LEFT: Chat column ────────────────────────
                with gr.Column(scale=4):
                    chatbot_kwargs = {
                        "label": "",
                        "height": 520,
                        "elem_id": "chatbot",
                        "avatar_images": avatar_paths,
                        "show_label": False,
                        "render_markdown": True,
                    }
                    try:
                        gr.Chatbot(type="messages", render=False)
                        chatbot_kwargs["type"] = "messages"
                    except TypeError:
                        pass
                    try:
                        gr.Chatbot(bubble_full_width=False, render=False)
                        chatbot_kwargs["bubble_full_width"] = False
                    except TypeError:
                        pass

                    chatbot = gr.Chatbot(**chatbot_kwargs)

                    with gr.Row(elem_classes=["input-row"]):
                        msg = gr.Textbox(
                            label="",
                            placeholder="Say something to Shiro, stranger...",
                            show_label=False,
                            scale=6,
                            container=False,
                            autofocus=True,
                        )
                        submit_btn = gr.Button("↑ Send", variant="primary", scale=1, min_width=90)

                    with gr.Row():
                        clear_btn = gr.Button("✕ Clear Chat", variant="secondary", size="sm")
                        gr.HTML('<div style="flex:1"></div>')  # spacer

                # ── RIGHT: Sidebar ───────────────────────────
                with gr.Column(scale=1, min_width=200):
                    gr.Markdown("### 👤 Profile")
                    user_name = gr.Textbox(
                        label="Your Name",
                        value="Stranger",
                        placeholder="Enter your name...",
                        interactive=True,
                    )

                    with gr.Row():
                        join_btn = gr.Button("Join", variant="secondary", size="sm")
                        leave_btn = gr.Button("Leave", variant="stop", size="sm")

                    gr.Markdown("### 🎙️ Mic")
                    mic_toggle = gr.Checkbox(label="Hands-Free Mode", value=False)

                    gr.Markdown("### ⚡ Status")
                    status_md = gr.Markdown(
                        value="<span class='status-text'>🟢 Online · Room: **Empty**</span>"
                    )

                    gr.Markdown("### 🛠️ Last Error")
                    error_box = gr.Textbox(
                        label="",
                        show_label=False,
                        interactive=False,
                        placeholder="No errors",
                        lines=3,
                        elem_classes=["error-box"],
                        max_lines=6,
                    )

            # ── Hidden timer for background audio polling ──
            timer = gr.Timer(value=1.0, active=True)

            # ════════════════════════════════════════════════
            # Event handlers
            # ════════════════════════════════════════════════

            def _normalize_input(user_input):
                """Normalize various input types to string."""
                if isinstance(user_input, list) and user_input:
                    first = user_input[0]
                    return first.get("text", str(first)) if isinstance(first, dict) else str(first)
                if isinstance(user_input, dict):
                    return user_input.get("text", str(user_input))
                return str(user_input) if user_input else ""

            def _get_cache_key(name: str) -> str:
                return f"session_{name}"

            def _load_session(name: str):
                """Load persisted chat history for this user."""
                key = _get_cache_key(name)
                return self._session_cache.get(key, [])

            def _save_session(name: str, history: list):
                """Persist chat history so page refresh doesn't wipe it."""
                key = _get_cache_key(name)
                self._session_cache[key] = history

            def restore_session(name, history):
                """On page load / name change: restore previous session if history is empty."""
                if not history:
                    restored = _load_session(name)
                    if restored:
                        return restored
                return history or []

            def user_message(user_input, history, name):
                if history is None:
                    history = _load_session(name)
                text = _normalize_input(user_input)
                if not text.strip():
                    return "", history
                history.append({"role": "user", "content": text})
                _save_session(name, history)
                return "", history

            def bot_response(history, name):
                """Stream Shiro's reply fragment by fragment, with flicker reduction."""
                if history is None:
                    history = []
                if not history:
                    yield history, gr.update()
                    return

                user_input = _normalize_input(history[-1]["content"])

                # Placeholder so the bubble appears immediately
                history = history + [{"role": "assistant", "content": "▌"}]
                yield history, gr.update()

                try:
                    full_response = ""
                    # Buffer small fragments to reduce DOM thrashing
                    BUFFER_THRESHOLD = 6   # chars before flushing to UI

                    pending = ""
                    for fragment in self.process_text_cb(user_input, name):
                        pending += fragment + " "
                        if len(pending) >= BUFFER_THRESHOLD:
                            full_response += pending
                            history[-1]["content"] = full_response.strip() + " ▌"
                            yield history, gr.update()
                            pending = ""

                    # Flush remainder
                    if pending:
                        full_response += pending

                    history[-1]["content"] = full_response.strip()
                    _save_session(name, history)
                    yield history, gr.update(value="")

                except Exception as e:
                    err_msg = str(e)
                    logger.error(f"bot_response error: {err_msg}")
                    if history and history[-1]["role"] == "assistant":
                        history[-1]["content"] = f"⚠️ [System Error]"
                    else:
                        history.append({"role": "assistant", "content": "⚠️ [System Error]"})
                    _save_session(name, history)
                    yield history, gr.update(value=err_msg)

            def on_name_change(name, history):
                """When the user changes their name, try to restore their session."""
                restored = _load_session(name)
                if restored and not history:
                    return restored
                return history or []

            def on_join(name, history):
                if history is None:
                    history = _load_session(name)
                if self.join_chat_cb:
                    resp_fragments = self.join_chat_cb(name)
                    response = " ".join(resp_fragments)
                    history.append({"role": "assistant", "content": response})
                    _save_session(name, history)
                status = f"<span class='status-text'>🟢 Online · Room: **{name} is here**</span>"
                return history, status

            def on_leave(name, history):
                if self.leave_chat_cb:
                    self.leave_chat_cb(name)
                status = "<span class='status-text'>🟡 Online · Room: **Empty**</span>"
                return history, status

            def on_mic_toggle(value):
                self.toggle_mic_cb(value)

            def poll_results(history):
                if history is None:
                    history = []
                new_results = self.poll_results_cb()
                if new_results:
                    for u, b in new_results:
                        history.append({"role": "user", "content": u})
                        history.append({"role": "assistant", "content": b})
                    return history
                return gr.update()

            def on_clear(name):
                # Clear both UI and session cache
                key = _get_cache_key(name)
                self._session_cache.pop(key, None)
                return [], gr.update(value="")

            # ── On load: restore session ─────────────────────
            demo.load(
                fn=restore_session,
                inputs=[user_name, chatbot],
                outputs=[chatbot],
            )

            # ── Wiring ───────────────────────────────────────
            user_name.change(
                fn=on_name_change,
                inputs=[user_name, chatbot],
                outputs=[chatbot],
            )

            msg.submit(
                user_message, [msg, chatbot, user_name], [msg, chatbot], queue=False
            ).then(
                bot_response, [chatbot, user_name], [chatbot, error_box]
            )

            submit_btn.click(
                user_message, [msg, chatbot, user_name], [msg, chatbot], queue=False
            ).then(
                bot_response, [chatbot, user_name], [chatbot, error_box]
            )

            join_btn.click(on_join, [user_name, chatbot], [chatbot, status_md])
            leave_btn.click(on_leave, [user_name, chatbot], [chatbot, status_md])
            mic_toggle.change(on_mic_toggle, mic_toggle, None)

            timer.tick(poll_results, chatbot, chatbot)

            clear_btn.click(
                on_clear,
                inputs=[user_name],
                outputs=[chatbot, error_box],
                queue=False,
            )

        self.interface = demo

    def launch(self, share=False):
        if self.interface:
            self.interface.launch(share=share)
        else:
            logger.error("UI not built. Call build_ui() first.")
