import gradio as gr
import logging
import base64
import json
import time
from pathlib import Path
from typing import Callable, Optional, List, Tuple

logger = logging.getLogger(__name__)

class ShiroGUI:
    """Gradio-based Web GUI for Shiro AI — with Discord Tab."""

    def __init__(
        self,
        process_text_cb: Callable[[str, str], str],
        process_audio_cb: Callable[[str, str], Tuple[str, str]],
        toggle_mic_cb: Callable[[bool], None],
        poll_results_cb: Callable[[], List[Tuple[str, str]]],
        join_chat_cb: Optional[Callable[[str], List[str]]] = None,
        leave_chat_cb: Optional[Callable[[str], None]] = None,
        set_typing_cb: Optional[Callable[[bool], None]] = None,
        # Discord callbacks
        discord_get_status_cb: Optional[Callable[[], dict]] = None,
        discord_start_cb: Optional[Callable[[], bool]] = None,
        discord_stop_cb: Optional[Callable[[], None]] = None,
        discord_join_voice_cb: Optional[Callable[[str], None]] = None,
        discord_leave_voice_cb: Optional[Callable[[], None]] = None,
        discord_send_text_cb: Optional[Callable[[str], None]] = None,
        # Vision callbacks
        vision_toggle_cb: Optional[Callable[[bool], str]] = None,
        vision_status_cb: Optional[Callable[[], dict]] = None,
        toggle_deafen_cb: Optional[Callable[[bool], None]] = None,
        title: str = "Shiro",
        theme: str = "soft"
    ):
        self.process_text_cb = process_text_cb
        self.process_audio_cb = process_audio_cb
        self.toggle_mic_cb = toggle_mic_cb
        self.poll_results_cb = poll_results_cb
        self.join_chat_cb = join_chat_cb
        self.leave_chat_cb = leave_chat_cb
        self.set_typing_cb = set_typing_cb

        # Discord
        self.discord_get_status_cb = discord_get_status_cb
        self.discord_start_cb = discord_start_cb
        self.discord_stop_cb = discord_stop_cb
        self.discord_join_voice_cb = discord_join_voice_cb
        self.discord_leave_voice_cb = discord_leave_voice_cb
        self.discord_send_text_cb = discord_send_text_cb

        # Vision
        self.vision_toggle_cb = vision_toggle_cb
        self.vision_status_cb = vision_status_cb

        # Deafen (suppress local TTS output)
        self.toggle_deafen_cb = toggle_deafen_cb

        # Streaming status (wired from main.py)
        self.stream_set_cb:     Optional[Callable] = None  # set_streaming(live, title, game)
        self.stream_status_cb:  Optional[Callable] = None  # get_streaming_status() -> dict

        # Memory management (wired from main.py)
        self.memory_summary_cb: Optional[Callable] = None  # get_memory_summary() -> dict
        self.memory_add_goal_cb: Optional[Callable] = None  # add_goal_from_gui(text, type, user_id)
        self.memory_delete_goal_cb: Optional[Callable] = None  # delete_goal_by_id(id) -> bool

        # Meditation callbacks — wired from main.py
        self.meditation_status_cb:  Optional[Callable[[], dict]] = None   # get_gui_status() -> dict
        self.meditation_begin_cb:   Optional[Callable[[str], bool]] = None # begin_manual(depth) -> ok
        self.meditation_wake_cb:    Optional[Callable[[], dict]] = None    # wake_manual() -> wake_data
        self.meditation_thoughts_cb: Optional[Callable[[], list]] = None   # get_recent_thoughts() -> list
        self.meditation_stats_cb:   Optional[Callable[[], dict]] = None    # get_session_stats() -> dict

        # Multi-speaker / hub callbacks — wired from main.py
        self.multi_hub_status_cb:   Optional[Callable[[], dict]] = None    # export_status() -> dict
        self.multi_hub_profiles_cb: Optional[Callable[[], list]] = None    # get_profile_summary_for_gui() -> list

        # Book Reader callbacks — wired from main.py
        self.book_list_cb:     Optional[Callable[[], list]] = None   # list available .txt books
        self.book_digested_cb: Optional[Callable[[], list]] = None   # list already-digested titles
        self.book_digest_cb:   Optional[Callable[[str], tuple]] = None  # digest a book file

        # Server toggles — wired from main.py (default: both off)
        self.lan_start_cb:     Optional[Callable[[], dict]] = None
        self.lan_stop_cb:      Optional[Callable[[], None]] = None
        self.public_start_cb:  Optional[Callable[[], dict]] = None
        self.public_stop_cb:   Optional[Callable[[], None]] = None
        self.get_server_status_cb: Optional[Callable[[], dict]] = None

        self.title = title
        self.theme = theme
        self.interface = None
        self.theme_obj = None
        self.custom_css = ""

        self._session_cache: dict = {}

    def _save_avatars(self) -> List[str]:
        """Returns avatar image paths. Uses shiro.png for bot if present, else fallback SVG."""
        assets_dir = Path(__file__).parent / "assets"
        assets_dir.mkdir(exist_ok=True)

        # ── User avatar ──
        user_svg_path = assets_dir / "user.svg"
        user_svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <defs>
    <linearGradient id="ug" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#6ea8d8"/>
      <stop offset="100%" stop-color="#3a6fa8"/>
    </linearGradient>
  </defs>
  <path d="M20 55 Q20 15 50 15 Q80 15 80 55 L80 70 Q80 78 72 78 L28 78 Q20 78 20 70 Z" fill="url(#ug)" stroke="#2a5585" stroke-width="2"/>
  <rect x="28" y="48" width="44" height="10" rx="4" fill="#1a2a3a" stroke="#2a5585" stroke-width="1.5"/>
  <rect x="30" y="50" width="40" height="3" rx="2" fill="#4a90e2" opacity="0.4"/>
  <path d="M20 60 L14 70 Q14 82 24 82 L28 78 L28 60 Z" fill="#4a7ab8" stroke="#2a5585" stroke-width="1.5"/>
  <path d="M80 60 L86 70 Q86 82 76 82 L72 78 L72 60 Z" fill="#4a7ab8" stroke="#2a5585" stroke-width="1.5"/>
  <rect x="30" y="76" width="40" height="10" rx="3" fill="#3a6fa8" stroke="#2a5585" stroke-width="1.5"/>
  <path d="M50 15 L50 55" stroke="#5a9ad0" stroke-width="2" opacity="0.5"/>
  <rect x="46" y="10" width="8" height="8" rx="2" fill="#5a8ab8"/>
</svg>"""
        user_svg_path.write_text(user_svg, encoding="utf-8")

        # ── Bot avatar: shiro.png if present, else fallback SVG ──
        shiro_png = assets_dir / "shiro.png"
        if shiro_png.exists():
            return [str(user_svg_path), str(shiro_png)]

        bot_svg_path = assets_dir / "bot.svg"
        bot_svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <defs>
    <linearGradient id="fg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#e8c49a"/>
      <stop offset="100%" stop-color="#d4a070"/>
    </linearGradient>
    <linearGradient id="eg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#ff9a5c"/>
      <stop offset="100%" stop-color="#e07030"/>
    </linearGradient>
  </defs>
  <ellipse cx="50" cy="58" rx="26" ry="28" fill="url(#fg)" stroke="#c08050" stroke-width="1.5"/>
  <path d="M24 42 Q20 10 38 18 Q44 30 50 32 Q56 30 62 18 Q80 10 76 42" fill="url(#fg)" stroke="#c08050" stroke-width="1.5"/>
  <ellipse cx="50" cy="56" rx="18" ry="20" fill="#f8e8d0"/>
  <ellipse cx="40" cy="50" rx="5" ry="6" fill="white" stroke="#d08050" stroke-width="1"/>
  <ellipse cx="60" cy="50" rx="5" ry="6" fill="white" stroke="#d08050" stroke-width="1"/>
  <circle cx="41" cy="51" r="3" fill="url(#eg)"/>
  <circle cx="61" cy="51" r="3" fill="url(#eg)"/>
  <circle cx="42" cy="50" r="1.2" fill="#1a0a00"/>
  <circle cx="62" cy="50" r="1.2" fill="#1a0a00"/>
  <path d="M44 62 Q50 67 56 62" stroke="#c08050" stroke-width="1.5" fill="none" stroke-linecap="round"/>
</svg>"""
        bot_svg_path.write_text(bot_svg, encoding="utf-8")
        return [str(user_svg_path), str(bot_svg_path)]

    def build_ui(self):
        self.custom_css = """
        @import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@400;600&family=Nunito:wght@300;400;600&display=swap');

        body, .gradio-container { background: #0a0a0f !important; color: #e8e0d0 !important; font-family: 'Nunito', sans-serif !important; }

        .shiro-header { text-align: center; padding: 18px 0 8px; }
        .shiro-header h1 { font-family: 'Cinzel', serif !important; font-size: 2rem !important; background: linear-gradient(135deg, #e8b870, #c07030, #a05020); -webkit-background-clip: text; -webkit-text-fill-color: transparent; letter-spacing: 0.3em !important; margin: 0 !important; }
        .shiro-subtitle { font-size: 0.7rem; color: #6a5a4a; letter-spacing: 0.25em; text-transform: uppercase; margin-top: 2px; }

        .gradio-chatbot, #chatbot { background: #0f0f18 !important; border: 1px solid #2a1f18 !important; border-radius: 16px !important; box-shadow: 0 0 0 1px rgba(200,100,40,0.08), 0 8px 32px rgba(0,0,0,0.6), inset 0 1px 0 rgba(255,255,255,0.03) !important; }
        .message-bubble-border { border-radius: 14px !important; }
        .message.user .message-bubble-border, div[data-testid="user"] .message-bubble-border { background: linear-gradient(135deg, #1e3a5a, #2a4a7a) !important; border: 1px solid #3a6aaa !important; color: #d8e8f8 !important; }
        .message.bot .message-bubble-border, div[data-testid="bot"] .message-bubble-border { background: linear-gradient(135deg, #1a1208, #221810) !important; border: 1px solid #4a2a18 !important; color: #f0e8d8 !important; }
        .message-input, textarea, input[type="text"] { background: #13111a !important; border: 1px solid #2a2030 !important; color: #e8e0d0 !important; border-radius: 12px !important; font-family: 'Nunito', sans-serif !important; transition: border-color 0.2s ease !important; }
        .message-input:focus, textarea:focus, input[type="text"]:focus { border-color: #c07030 !important; box-shadow: 0 0 0 2px rgba(192,112,48,0.15) !important; outline: none !important; }
        button.primary { background: linear-gradient(135deg, #c07030, #a05020) !important; border: none !important; color: #fff8f0 !important; font-family: 'Nunito', sans-serif !important; font-weight: 600 !important; border-radius: 10px !important; letter-spacing: 0.05em !important; transition: all 0.2s ease !important; box-shadow: 0 2px 8px rgba(160,80,32,0.4) !important; }
        button.primary:hover { background: linear-gradient(135deg, #d08040, #b06030) !important; box-shadow: 0 4px 16px rgba(160,80,32,0.5) !important; transform: translateY(-1px) !important; }
        button.secondary { background: #1a1820 !important; border: 1px solid #3a3050 !important; color: #b8a8c8 !important; border-radius: 10px !important; font-family: 'Nunito', sans-serif !important; transition: all 0.2s ease !important; }
        button.secondary:hover { border-color: #6a50a0 !important; color: #d8c8e8 !important; }
        button.stop { background: #1a0f0f !important; border: 1px solid #5a2020 !important; color: #c08080 !important; border-radius: 10px !important; font-family: 'Nunito', sans-serif !important; transition: all 0.2s ease !important; }
        button.stop:hover { border-color: #a04040 !important; color: #e0a0a0 !important; }
        .sidebar-panel { background: #0f0d18 !important; border: 1px solid #1e1828 !important; border-radius: 14px !important; padding: 16px !important; }
        label span { font-size: 0.78rem !important; letter-spacing: 0.08em !important; text-transform: uppercase !important; color: #7a6a5a !important; font-weight: 600 !important; }
        .gradio-markdown h3 { font-family: 'Cinzel', serif !important; font-size: 0.85rem !important; color: #c07030 !important; letter-spacing: 0.12em !important; margin: 16px 0 8px !important; text-transform: uppercase !important; }
        .status-text { font-size: 0.78rem !important; color: #5a7a5a !important; font-family: 'Nunito', sans-serif !important; }
        input[type="checkbox"] { accent-color: #c07030 !important; }
        .error-box textarea { background: #120808 !important; border-color: #4a1818 !important; color: #d07070 !important; font-size: 0.8rem !important; }
        hr { border-color: #1e1828 !important; }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: #0a0a0f; }
        ::-webkit-scrollbar-thumb { background: #3a2820; border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: #6a4830; }
        .session-badge { display: inline-flex; align-items: center; gap: 5px; font-size: 0.72rem; color: #5a9a5a; background: rgba(40,80,40,0.12); border: 1px solid rgba(80,160,80,0.2); border-radius: 20px; padding: 2px 10px; margin-top: 6px; }
        .streaming-indicator { display: inline-block; width: 6px; height: 6px; background: #c07030; border-radius: 50%; animation: blink 0.8s ease-in-out infinite; margin-left: 4px; vertical-align: middle; }
        @keyframes blink { 0%, 100% { opacity: 0.2; } 50% { opacity: 1; } }
        .avatar-image img { border-radius: 50% !important; }
        footer { display: none !important; }

        /* ── Discord Tab Styles ── */
        .discord-panel { background: #0d0f1a; border: 1px solid #23272a; border-radius: 14px; padding: 20px; }
        .dc-status-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }
        .dc-dot-on  { background: #57f287; box-shadow: 0 0 6px #57f287; }
        .dc-dot-off { background: #ed4245; }
        .dc-dot-mid { background: #fee75c; }
        .dc-info-row { display: flex; gap: 16px; flex-wrap: wrap; margin: 8px 0; }
        .dc-info-chip { background: #1e2030; border: 1px solid #3a3d5c; border-radius: 8px; padding: 4px 12px; font-size: 0.8rem; color: #b0b8d8; }
        .dc-info-chip b { color: #e0e8ff; }

        /* ── Servers Tab Styles ── */
        .srv-card { background: #0f0d18; border: 1px solid #2a2040; border-radius: 14px; padding: 20px; margin-bottom: 12px; }
        .srv-url-box { background: #0a0810; border: 1px solid #3a2a50; border-radius: 8px; padding: 10px 14px; font-family: 'Courier New', monospace; font-size: 0.82rem; color: #b8a0e0; word-break: break-all; margin: 8px 0; min-height: 36px; }
        .srv-badge-on  { display: inline-block; background: rgba(60,180,60,0.12); border: 1px solid rgba(80,200,80,0.3); color: #6adf6a; border-radius: 20px; padding: 2px 12px; font-size: 0.75rem; font-weight: 600; letter-spacing: 0.08em; }
        .srv-badge-off { display: inline-block; background: rgba(180,60,60,0.10); border: 1px solid rgba(200,80,80,0.25); color: #df8080; border-radius: 20px; padding: 2px 12px; font-size: 0.75rem; font-weight: 600; letter-spacing: 0.08em; }
        .srv-badge-mid { display: inline-block; background: rgba(200,160,40,0.12); border: 1px solid rgba(220,180,60,0.3); color: #e0c060; border-radius: 20px; padding: 2px 12px; font-size: 0.75rem; font-weight: 600; letter-spacing: 0.08em; }
        """

        if self.theme == "soft":
            self.theme_obj = gr.themes.Soft(primary_hue="orange", secondary_hue="violet", neutral_hue="stone")
        elif self.theme == "monochrome":
            self.theme_obj = gr.themes.Monochrome()
        elif self.theme == "glass":
            self.theme_obj = gr.themes.Glass()
        else:
            self.theme_obj = gr.themes.Default()

        avatar_paths = self._save_avatars()

        with gr.Blocks(title=self.title) as demo:

            gr.HTML("""
            <div class="shiro-header">
                <h1>Shiro</h1>
                <div class="shiro-subtitle">The Sly Kitsune Yaoguai</div>
            </div>
            """)

            session_key = gr.State(value=None)

            with gr.Tabs():

                # ════════════════════════════════════════════
                # TAB 1: Chat
                # ════════════════════════════════════════════
                with gr.Tab("💬 Chat"):
                    with gr.Row(equal_height=True):

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
                                    label="", placeholder="Say something to Shiro, stranger...",
                                    show_label=False, scale=6, container=False, autofocus=True,
                                )
                                submit_btn = gr.Button("↑ Send", variant="primary", scale=1, min_width=90)

                            with gr.Row():
                                clear_btn = gr.Button("✕ Clear Chat", variant="secondary", size="sm")
                                gr.HTML('<div style="flex:1"></div>')

                        with gr.Column(scale=1, min_width=200):
                            gr.Markdown("### 👤 Profile")
                            user_name = gr.Textbox(
                                label="Your Name", value="Stranger",
                                placeholder="Enter your name...", interactive=True,
                            )
                            with gr.Row():
                                join_btn = gr.Button("Join", variant="secondary", size="sm")
                                leave_btn = gr.Button("Leave", variant="stop", size="sm")
                            gr.Markdown("### 🎙️ Mic")
                            mic_toggle = gr.Checkbox(label="Hands-Free Mode", value=False)
                            deafen_toggle = gr.Checkbox(
                                label="🔇 Deafen (mute local TTS)", value=False,
                                info="Silence Shiro's voice locally — useful when in Discord voice"
                            )
                            gr.Markdown("### 👁️ Vision")
                            vision_toggle = gr.Checkbox(label="Screen Vision", value=False,
                                info="Let Shiro read text on your screen (EasyOCR — no VRAM required)")
                            vision_status_md = gr.Markdown(
                                value="<span style='color:#888;font-size:0.85em'>Vision: off</span>"
                            )
                            gr.Markdown("### ⚡ Status")
                            status_md = gr.Markdown(value="<span class='status-text'>🟢 Online · Room: **Empty**</span>")
                            gr.Markdown("### 🛠️ Last Error")
                            error_box = gr.Textbox(
                                label="", show_label=False, interactive=False,
                                placeholder="No errors", lines=3,
                                elem_classes=["error-box"], max_lines=6,
                            )

                    timer = gr.Timer(value=1.0, active=True)

                # ════════════════════════════════════════════
                # TAB 2: Discord
                # ════════════════════════════════════════════
                with gr.Tab("🎮 Discord"):
                    gr.HTML("<div style='height:10px'></div>")

                    with gr.Row():
                        # ── LEFT: Connection panel ──────────────
                        with gr.Column(scale=2):
                            gr.Markdown("### 🔌 Connection")

                            dc_status_html = gr.HTML(
                                value=self._dc_status_html(None),
                                label="",
                            )

                            with gr.Row():
                                dc_connect_btn = gr.Button("Connect to Discord", variant="primary")
                                dc_disconnect_btn = gr.Button("Disconnect", variant="stop")

                            gr.HTML("<hr style='border-color:#23272a;margin:12px 0;'>")
                            gr.Markdown("### 🔊 Voice Channel")

                            dc_voice_channel_input = gr.Textbox(
                                label="Channel Name (optional)",
                                placeholder="Leave blank to auto-join",
                                interactive=True,
                            )
                            with gr.Row():
                                dc_join_voice_btn = gr.Button("Join Voice", variant="secondary")
                                dc_leave_voice_btn = gr.Button("Leave Voice", variant="stop")

                            gr.HTML("<hr style='border-color:#23272a;margin:12px 0;'>")
                            gr.Markdown("### 💬 Send to Text Channel")

                            dc_send_input = gr.Textbox(
                                label="Message",
                                placeholder="Type a message to send as Shiro...",
                                interactive=True,
                                lines=2,
                            )
                            dc_send_btn = gr.Button("Send Message", variant="secondary")

                        # ── RIGHT: Status panel ─────────────────
                        with gr.Column(scale=1, min_width=220):
                            gr.Markdown("### 📊 Status")

                            dc_detail_box = gr.Textbox(
                                label="",
                                show_label=False,
                                interactive=False,
                                lines=10,
                                max_lines=14,
                                value="Not connected.",
                                elem_classes=["error-box"],
                            )

                            dc_refresh_btn = gr.Button("↻ Refresh Status", variant="secondary", size="sm")

                    gr.HTML("""
                    <div style='background:#0a0c14;border:1px solid #1e2030;border-radius:10px;padding:14px;margin-top:8px;font-size:0.8rem;color:#6a7090;line-height:1.7;'>
                        <b style='color:#8090c0;'>Setup quick guide:</b><br>
                        1. Add your bot token to <code>config.yaml</code> under <code>discord.token</code><br>
                        2. Invite your bot to your server with the Bot + Voice permissions<br>
                        3. Hit <b>Connect</b> — Shiro will come online<br>
                        4. In Discord, type <b>!join</b> or just say <i>"shiro, join the call"</i> in chat<br>
                        5. Shiro will listen in voice and reply to anyone who addresses her directly
                    </div>
                    """)

                    dc_timer = gr.Timer(value=3.0, active=True)

                # ════════════════════════════════════════════
                # TAB 3: Servers
                # ════════════════════════════════════════════
                with gr.Tab("🌐 Servers"):
                    gr.HTML("<div style='height:10px'></div>")
                    gr.HTML("""
                    <div style='background:#0a0810;border:1px solid #2a1a40;border-radius:10px;
                                padding:12px 16px;margin-bottom:14px;font-size:0.82rem;color:#8070a0;line-height:1.6;'>
                        <b style='color:#a888d8;'>ℹ️ Both servers are OFF by default</b> — start them here when needed.
                        The LAN voice room lets people on your network join by browser.
                        The public tunnel (via cloudflared) gives a temporary URL anyone can use.
                    </div>
                    """)

                    with gr.Row(equal_height=False):

                        # ── LEFT: LAN Voice Room ──────────────
                        with gr.Column(scale=1):
                            gr.Markdown("### 🏠 LAN Voice Room")
                            srv_lan_status = gr.HTML(value=self._srv_card_html("lan", None))
                            with gr.Row():
                                srv_lan_start_btn = gr.Button("▶ Start LAN", variant="primary", size="sm")
                                srv_lan_stop_btn  = gr.Button("■ Stop LAN",  variant="stop",    size="sm")
                            srv_lan_log = gr.Textbox(
                                label="", show_label=False, interactive=False,
                                lines=3, max_lines=5,
                                placeholder="LAN server log...",
                                elem_classes=["error-box"],
                            )

                        # ── RIGHT: Public Tunnel ──────────────
                        with gr.Column(scale=1):
                            gr.Markdown("### 🌍 Public Tunnel (cloudflared)")
                            srv_pub_status = gr.HTML(value=self._srv_card_html("public", None))
                            with gr.Row():
                                srv_pub_start_btn = gr.Button("▶ Start Public", variant="primary", size="sm")
                                srv_pub_stop_btn  = gr.Button("■ Stop Public",  variant="stop",    size="sm")
                            srv_pub_log = gr.Textbox(
                                label="", show_label=False, interactive=False,
                                lines=3, max_lines=5,
                                placeholder="Public tunnel log...",
                                elem_classes=["error-box"],
                            )

                    gr.HTML("""
                    <div style='background:#0a0c10;border:1px solid #1e2820;border-radius:10px;
                                padding:12px 16px;margin-top:6px;font-size:0.78rem;color:#5a7060;line-height:1.7;'>
                        <b style='color:#7a9a70;'>cloudflared quick setup:</b><br>
                        Windows: <code>winget install --id Cloudflare.cloudflared</code><br>
                        Linux/Mac: <code>brew install cloudflare/cloudflare/cloudflared</code><br>
                        No account needed — free temporary URL, refreshes on restart.
                    </div>
                    """)

                    srv_timer = gr.Timer(value=5.0, active=True)

                # ════════════════════════════════════════════
                # TAB 4: Streaming
                # ════════════════════════════════════════════
                with gr.Tab("📡 Streaming"):
                    gr.HTML("<div style='height:10px'></div>")
                    gr.HTML("""
                    <div style='background:#0a0810;border:1px solid #2a1a40;border-radius:10px;
                                padding:12px 16px;margin-bottom:14px;font-size:0.82rem;color:#8070a0;line-height:1.6;'>
                        <b style='color:#c07030;'>📡 Stream Awareness</b> — when live, Shiro knows she's on stream.
                        She'll treat chat as her audience, be more welcoming to new people, and can reference the stream naturally.
                    </div>
                    """)
                    with gr.Row(equal_height=False):
                        with gr.Column(scale=1):
                            gr.Markdown("### 🔴 Stream Status")
                            stream_status_html = gr.HTML(value=self._stream_status_html(None))
                            stream_live_toggle = gr.Checkbox(
                                label="🔴 I'm Live (Shiro knows she's on stream)",
                                value=False,
                                info="Toggle when you go live — Shiro adjusts her behavior accordingly"
                            )
                        with gr.Column(scale=1):
                            gr.Markdown("### 🎮 Stream Info (optional)")
                            stream_title_input = gr.Textbox(
                                label="Stream Title",
                                placeholder="e.g. late night coding session...",
                                interactive=True,
                            )
                            stream_game_input = gr.Textbox(
                                label="Game / Activity",
                                placeholder="e.g. Minecraft, just chatting, coding...",
                                interactive=True,
                            )
                            stream_update_btn = gr.Button("Update Stream Info", variant="secondary", size="sm")
                    stream_timer = gr.Timer(value=10.0, active=True)

                # ════════════════════════════════════════════
                # TAB 5: Memory
                # ════════════════════════════════════════════
                with gr.Tab("🧠 Memory"):
                    gr.HTML("<div style='height:10px'></div>")
                    gr.HTML("""
                    <div style='background:#0a0810;border:1px solid #2a1a40;border-radius:10px;
                                padding:12px 16px;margin-bottom:14px;font-size:0.82rem;color:#8070a0;line-height:1.6;'>
                        <b style='color:#c07030;'>🧠 Memory Management</b> — view, add, and remove Shiro's goals and memories.
                        Changes take effect immediately without a restart.
                    </div>
                    """)

                    with gr.Row():
                        with gr.Column(scale=1):
                            gr.Markdown("### 🎯 Goals")
                            memory_refresh_btn = gr.Button("↻ Refresh", variant="secondary", size="sm")
                            goals_display = gr.Dataframe(
                                headers=["ID", "Type", "Owner", "Status", "Text", "Progress"],
                                datatype=["str", "str", "str", "str", "str", "str"],
                                label="",
                                interactive=False,
                                wrap=True,
                            )
                            gr.Markdown("#### ➕ Add Goal")
                            with gr.Row():
                                new_goal_text = gr.Textbox(
                                    label="Goal text",
                                    placeholder="What should Shiro work toward?",
                                    scale=3, container=False,
                                )
                                new_goal_type = gr.Dropdown(
                                    choices=["short", "long"],
                                    value="short",
                                    label="Type",
                                    scale=1,
                                )
                                new_goal_user = gr.Textbox(
                                    label="For user (optional)",
                                    placeholder="e.g. Tyler",
                                    scale=1, container=False,
                                )
                            add_goal_btn = gr.Button("Add Goal", variant="primary", size="sm")
                            gr.Markdown("#### 🗑️ Delete Goal")
                            with gr.Row():
                                delete_goal_id = gr.Textbox(
                                    label="Goal ID to delete",
                                    placeholder="e.g. g123456",
                                    scale=2, container=False,
                                )
                                delete_goal_btn = gr.Button("Delete", variant="stop", size="sm", scale=1)
                            memory_status_box = gr.Textbox(
                                label="", show_label=False, interactive=False,
                                placeholder="Actions will appear here...",
                                lines=2, max_lines=4,
                                elem_classes=["error-box"],
                            )

                        with gr.Column(scale=1):
                            gr.Markdown("### 📊 Memory Stats")
                            memory_stats_html = gr.HTML(value="<span style='color:#555;'>Click Refresh to load stats.</span>")

                with gr.Tab("🧘 Meditation"):
                    gr.HTML("<div style='height:10px'></div>")
                    gr.HTML("""
                    <div style='background:#030408;border:1px solid #1a2040;border-radius:10px;
                                padding:12px 16px;margin-bottom:14px;font-size:0.82rem;color:#6070a0;line-height:1.6;'>
                        <b style='color:#6b8ecf;'>🧘 Deep Reflection & Meditation</b> — Shiro's autonomous inner space.
                        She enters meditation during idle periods to reflect, learn, and grow. You can also trigger it manually.
                        When meditating she <i>peeks</i> periodically — if nothing needs her, she goes back in.
                    </div>
                    """)

                    with gr.Row():
                        with gr.Column(scale=2):
                            med_status_html = gr.HTML(
                                value="<span style='color:#334;font-size:0.85rem;'>Loading meditation status…</span>"
                            )
                            gr.Markdown("#### 🕹️ Controls")
                            with gr.Row():
                                med_depth = gr.Dropdown(
                                    choices=["quick", "standard", "deep"],
                                    value="standard",
                                    label="Depth",
                                    scale=1,
                                )
                                med_begin_btn = gr.Button("▶ Begin Meditation", variant="primary", scale=2)
                                med_wake_btn  = gr.Button("⬆ Gentle Wake", variant="secondary", scale=2)
                            med_action_status = gr.Textbox(
                                label="", show_label=False, interactive=False,
                                placeholder="Meditation actions appear here…",
                                lines=2, max_lines=3,
                            )
                            gr.Markdown("#### 📊 Session Stats")
                            with gr.Row():
                                med_stats_btn = gr.Button("↻ Refresh Stats", variant="secondary", size="sm")
                            med_stats_html = gr.HTML(
                                value="<span style='color:#334;'>Click Refresh Stats to load.</span>"
                            )

                        with gr.Column(scale=1):
                            gr.Markdown("#### 💭 Live Thought Stream")
                            med_thoughts_box = gr.Textbox(
                                label="",
                                show_label=False,
                                interactive=False,
                                placeholder="Shiro's thoughts appear here during meditation…",
                                lines=16,
                                max_lines=20,
                            )
                            with gr.Row():
                                med_thoughts_btn = gr.Button("↻ Refresh Thoughts", variant="secondary", size="sm")
                                med_thoughts_clear_btn = gr.Button("✕ Clear", variant="secondary", size="sm")

                    med_auto_timer = gr.Timer(value=3)

                with gr.Tab("👥 Speakers"):
                    gr.HTML("<div style='height:10px'></div>")
                    gr.HTML("""
                    <div style='background:#030408;border:1px solid #1a2a1a;border-radius:10px;
                                padding:12px 16px;margin-bottom:14px;font-size:0.82rem;color:#607060;line-height:1.6;'>
                        <b style='color:#5a9e6f;'>👥 Multi-Speaker Hub</b> — Shiro tracks every person in a group chat individually.
                        Each user gets their own profile, fact store, and history. Shiro knows who said what
                        and will never mix up facts between speakers.
                    </div>
                    """)

                    with gr.Row():
                        with gr.Column(scale=2):
                            gr.Markdown("#### 👤 User Profiles")
                            speakers_refresh_btn = gr.Button("↻ Refresh", variant="secondary", size="sm")
                            speakers_profiles_df = gr.Dataframe(
                                headers=["Username", "Display Name", "Primary?", "Messages", "First Seen", "Last Seen"],
                                datatype=["str", "str", "str", "str", "str", "str"],
                                label="",
                                interactive=False,
                                wrap=True,
                            )
                            gr.Markdown("#### 🔍 Inspect User")
                            with gr.Row():
                                speakers_inspect_input = gr.Textbox(
                                    label="",
                                    placeholder="Enter username to inspect…",
                                    scale=3, container=False,
                                )
                                speakers_inspect_btn = gr.Button("Inspect", variant="secondary", scale=1)
                            speakers_detail_box = gr.Textbox(
                                label="",
                                show_label=False,
                                interactive=False,
                                placeholder="User profile details will appear here…",
                                lines=10,
                                max_lines=14,
                            )

                        with gr.Column(scale=1):
                            gr.Markdown("#### 📋 Queue Status")
                            speakers_queue_html = gr.HTML(
                                value="<span style='color:#334;'>Click Refresh to load queue status.</span>"
                            )

                    speakers_auto_timer = gr.Timer(value=10)

                with gr.Tab("📚 Books"):
                    gr.HTML("<div style='height:10px'></div>")
                    gr.HTML("""
                    <div style='background:#030408;border:1px solid #1a2820;border-radius:10px;
                                padding:12px 16px;margin-bottom:14px;font-size:0.82rem;color:#607860;line-height:1.6;'>
                        <b style='color:#7abe8a;'>📚 Book Reader</b> — Feed Shiro books so she remembers them in conversation.
                        Drop <code>.txt</code> files into the <code>books/</code> folder next to Shiro, then digest them here.
                        Shiro's memory will update live — she'll reference the book naturally when topics match.
                    </div>
                    """)
                    with gr.Row():
                        with gr.Column(scale=2):
                            gr.Markdown("#### 📂 Available Books")
                            gr.Markdown(
                                "<span style='font-size:0.8rem;color:#607060;'>"
                                "Place <code>.txt</code> files in the <code>books/</code> folder beside Shiro, "
                                "then click Refresh.</span>"
                            )
                            with gr.Row():
                                book_refresh_btn  = gr.Button("↻ Refresh List", variant="secondary", size="sm")
                            book_selector = gr.Dropdown(
                                choices=[],
                                value=None,
                                label="Select a book to digest",
                                interactive=True,
                            )
                            book_digest_btn = gr.Button("📖 Digest & Load into Memory", variant="primary")
                            book_status_box = gr.Textbox(
                                label="",
                                show_label=False,
                                interactive=False,
                                placeholder="Digest results will appear here…",
                                lines=8,
                                max_lines=12,
                            )

                        with gr.Column(scale=1):
                            gr.Markdown("#### ✅ Already Digested")
                            gr.Markdown(
                                "<span style='font-size:0.8rem;color:#607060;'>"
                                "Books Shiro already knows.</span>"
                            )
                            book_digested_list = gr.Textbox(
                                label="",
                                show_label=False,
                                interactive=False,
                                placeholder="None yet…",
                                lines=14,
                                max_lines=20,
                            )
                            book_digested_refresh_btn = gr.Button("↻ Refresh Digested", variant="secondary", size="sm")

            def _normalize_input(user_input):
                if isinstance(user_input, list) and user_input:
                    first = user_input[0]
                    return first.get("text", str(first)) if isinstance(first, dict) else str(first)
                if isinstance(user_input, dict):
                    return user_input.get("text", str(user_input))
                return str(user_input) if user_input else ""

            def _get_cache_key(name: str) -> str:
                return f"session_{name}"

            def _load_session(name: str):
                key = _get_cache_key(name)
                return self._session_cache.get(key, [])

            def _save_session(name: str, history: list):
                key = _get_cache_key(name)
                self._session_cache[key] = history

            def restore_session(name, history):
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
                if history is None:
                    history = []
                if not history:
                    yield history, gr.update()
                    return
                user_input = _normalize_input(history[-1]["content"])
                history = history + [{"role": "assistant", "content": "▌"}]
                yield history, gr.update()
                try:
                    full_response = ""
                    BUFFER_THRESHOLD = 15
                    pending = ""
                    for fragment in self.process_text_cb(user_input, name):
                        pending += fragment
                        if len(pending) >= BUFFER_THRESHOLD:
                            full_response += pending
                            history[-1]["content"] = full_response.strip() + " ▌"
                            yield history, gr.update()
                            pending = ""
                    if pending:
                        full_response += pending
                    final_content = full_response.strip()
                    if not final_content:
                        # Engine yielded nothing (sanitized away) — remove the empty bubble
                        # so it doesn't sit as a permanent blinking '...' in the chat.
                        history = [m for m in history if not (
                            m.get("role") == "assistant" and m.get("content") in ("▌", "", None)
                        )]
                    else:
                        history[-1]["content"] = final_content
                    _save_session(name, history)
                    yield history, gr.update(value="")
                except Exception as e:
                    err_msg = str(e)
                    logger.error(f"bot_response error: {err_msg}")
                    if history and history[-1]["role"] == "assistant":
                        history[-1]["content"] = "Hmph. Something broke on my end. Don't ask me what, it's embarrassing."
                    else:
                        history.append({"role": "assistant", "content": "Hmph. Something broke on my end. Don't ask me what, it's embarrassing."})
                    _save_session(name, history)
                    yield history, gr.update(value=err_msg)

            def on_name_change(name, history):
                restored = _load_session(name)
                if restored and not history:
                    return restored
                return history or []

            def on_join(name, history):
                if history is None:
                    history = _load_session(name)
                if self.join_chat_cb:
                    greeting_gen = self.join_chat_cb(name)
                    if greeting_gen is not None:
                        try:
                            fragments = list(greeting_gen)
                            response = " ".join(f for f in fragments if f).strip()
                            if response:
                                history.append({"role": "assistant", "content": response})
                                _save_session(name, history)
                        except Exception as e:
                            logger.warning(f"Greeting generation error: {e}")
                status = f"<span class='status-text'>🟢 Online · Room: **{name} is here**</span>"
                return history, status

            def on_leave(name, history):
                if self.leave_chat_cb:
                    self.leave_chat_cb(name)
                status = "<span class='status-text'>🟡 Online · Room: **Empty**</span>"
                return history, status

            def on_mic_toggle(value):
                self.toggle_mic_cb(value)

            def on_deafen_toggle(value):
                if self.toggle_deafen_cb:
                    self.toggle_deafen_cb(value)

            def on_vision_toggle(value):
                if self.vision_toggle_cb:
                    self.vision_toggle_cb(value)
                if value:
                    return "<span style='color:#c060e0;font-size:0.85em'>👁️ Vision: <b>on</b> (EasyOCR — reading screen text)</span>"
                return "<span style='color:#888;font-size:0.85em'>Vision: off</span>"

            def poll_results(history):
                if history is None:
                    history = []
                new_results = self.poll_results_cb()
                if new_results:
                    for u, b in new_results:
                        if u and u not in ("[Shiro]", "__shiro__", None):
                            history.append({"role": "user", "content": u})
                        history.append({"role": "assistant", "content": b})
                    return history
                return gr.update()

            def on_clear(name):
                key = _get_cache_key(name)
                self._session_cache.pop(key, None)
                return [], gr.update(value="")

            def on_typing(text):
                if self.set_typing_cb:
                    self.set_typing_cb(bool(text and text.strip()))
                return None

            def on_submit_clear_typing(text, history, name):
                if self.set_typing_cb:
                    self.set_typing_cb(False)
                return user_message(text, history, name)

            demo.load(fn=restore_session, inputs=[user_name, chatbot], outputs=[chatbot])
            user_name.change(fn=on_name_change, inputs=[user_name, chatbot], outputs=[chatbot])
            msg.change(on_typing, inputs=[msg], outputs=[])
            msg.submit(on_submit_clear_typing, [msg, chatbot, user_name], [msg, chatbot], queue=False).then(
                bot_response, [chatbot, user_name], [chatbot, error_box]
            )
            submit_btn.click(on_submit_clear_typing, [msg, chatbot, user_name], [msg, chatbot], queue=False).then(
                bot_response, [chatbot, user_name], [chatbot, error_box]
            )
            join_btn.click(on_join, [user_name, chatbot], [chatbot, status_md])
            leave_btn.click(on_leave, [user_name, chatbot], [chatbot, status_md])
            mic_toggle.change(on_mic_toggle, mic_toggle, None)
            deafen_toggle.change(on_deafen_toggle, deafen_toggle, None)
            vision_toggle.change(on_vision_toggle, vision_toggle, vision_status_md)
            timer.tick(poll_results, chatbot, chatbot)
            clear_btn.click(fn=on_clear, inputs=[user_name], outputs=[chatbot, error_box], queue=False)

            # ════════════════════════════════════════════════
            # Event Handlers — Discord Tab
            # ════════════════════════════════════════════════

            def _dc_refresh():
                """Refresh Discord status display."""
                status_dict = None
                if self.discord_get_status_cb:
                    try:
                        status_dict = self.discord_get_status_cb()
                    except Exception as e:
                        logger.error(f"Discord status error: {e}")

                html = self._dc_status_html(status_dict)
                detail = self._dc_detail_text(status_dict)
                return html, detail

            def _dc_connect():
                if self.discord_start_cb:
                    try:
                        ok = self.discord_start_cb()
                        if not ok:
                            return (
                                self._dc_status_html(None),
                                "❌ Could not start bot. Check your token in config.yaml and ensure discord.py is installed.\n\nRun: pip install discord.py[voice] PyNaCl"
                            )
                    except Exception as e:
                        return self._dc_status_html(None), f"❌ Error: {e}"
                time.sleep(2)  # brief wait for bot to connect
                return _dc_refresh()

            def _dc_disconnect():
                if self.discord_stop_cb:
                    try:
                        self.discord_stop_cb()
                    except Exception as e:
                        logger.error(f"Discord stop error: {e}")
                time.sleep(0.5)
                return _dc_refresh()

            def _dc_join_voice(channel_name):
                if self.discord_join_voice_cb:
                    try:
                        self.discord_join_voice_cb(channel_name.strip() if channel_name else None)
                    except Exception as e:
                        logger.error(f"Discord join voice error: {e}")
                time.sleep(1)
                return _dc_refresh()

            def _dc_leave_voice():
                if self.discord_leave_voice_cb:
                    try:
                        self.discord_leave_voice_cb()
                    except Exception as e:
                        logger.error(f"Discord leave voice error: {e}")
                time.sleep(0.5)
                return _dc_refresh()

            def _dc_send(text):
                if text and text.strip() and self.discord_send_text_cb:
                    try:
                        self.discord_send_text_cb(text.strip())
                    except Exception as e:
                        logger.error(f"Discord send error: {e}")
                return gr.update(value=""), *_dc_refresh()

            def _dc_auto_refresh():
                return _dc_refresh()

            dc_connect_btn.click(fn=_dc_connect, outputs=[dc_status_html, dc_detail_box])
            dc_disconnect_btn.click(fn=_dc_disconnect, outputs=[dc_status_html, dc_detail_box])
            dc_join_voice_btn.click(fn=_dc_join_voice, inputs=[dc_voice_channel_input], outputs=[dc_status_html, dc_detail_box])
            dc_leave_voice_btn.click(fn=_dc_leave_voice, outputs=[dc_status_html, dc_detail_box])
            dc_send_btn.click(fn=_dc_send, inputs=[dc_send_input], outputs=[dc_send_input, dc_status_html, dc_detail_box])
            dc_refresh_btn.click(fn=_dc_refresh, outputs=[dc_status_html, dc_detail_box])
            dc_timer.tick(fn=_dc_auto_refresh, outputs=[dc_status_html, dc_detail_box])

            # ════════════════════════════════════════════════
            # Event Handlers — Servers Tab
            # ════════════════════════════════════════════════

            def _srv_start_lan():
                if self.lan_start_cb:
                    try:
                        result = self.lan_start_cb()
                        url  = result.get("url", "")
                        err  = result.get("error", "")
                        log  = f"✅ LAN started: {url}" if url else f"❌ {err or 'Failed to start'}"
                        return self._srv_card_html("lan", result), log
                    except Exception as e:
                        return self._srv_card_html("lan", None), f"❌ Error: {e}"
                return self._srv_card_html("lan", None), "⚠️ LAN server not available"

            def _srv_stop_lan():
                if self.lan_stop_cb:
                    try:
                        self.lan_stop_cb()
                        return self._srv_card_html("lan", {"running": False}), "⏹ LAN stopped."
                    except Exception as e:
                        return self._srv_card_html("lan", None), f"❌ Error: {e}"
                return self._srv_card_html("lan", None), "⚠️ Not running"

            def _srv_start_public():
                if self.public_start_cb:
                    try:
                        result = self.public_start_cb()
                        url  = result.get("url", "")
                        err  = result.get("error", "")
                        if url:
                            log = f"✅ Tunnel active: {url}"
                        elif result.get("starting"):
                            log = "⏳ Starting tunnel — URL will appear shortly..."
                        else:
                            log = f"❌ {err or 'Failed to start'}"
                        return self._srv_card_html("public", result), log
                    except Exception as e:
                        return self._srv_card_html("public", None), f"❌ Error: {e}"
                return self._srv_card_html("public", None), "⚠️ cloudflared not available"

            def _srv_stop_public():
                if self.public_stop_cb:
                    try:
                        self.public_stop_cb()
                        return self._srv_card_html("public", {"running": False}), "⏹ Tunnel stopped."
                    except Exception as e:
                        return self._srv_card_html("public", None), f"❌ Error: {e}"
                return self._srv_card_html("public", None), "⚠️ Not running"

            def _srv_auto_refresh():
                if self.get_server_status_cb:
                    try:
                        st = self.get_server_status_cb()
                        lan_st  = {"running": st.get("lan_running", False),  "url": st.get("lan_url", "")}
                        pub_st  = {"running": st.get("pub_running", False),  "url": st.get("pub_url", ""), "starting": st.get("pub_starting", False)}
                        return self._srv_card_html("lan", lan_st), self._srv_card_html("public", pub_st)
                    except Exception:
                        pass
                return self._srv_card_html("lan", None), self._srv_card_html("public", None)

            srv_lan_start_btn.click(fn=_srv_start_lan,   outputs=[srv_lan_status, srv_lan_log])
            srv_lan_stop_btn.click( fn=_srv_stop_lan,    outputs=[srv_lan_status, srv_lan_log])
            srv_pub_start_btn.click(fn=_srv_start_public, outputs=[srv_pub_status, srv_pub_log])
            srv_pub_stop_btn.click( fn=_srv_stop_public,  outputs=[srv_pub_status, srv_pub_log])
            srv_timer.tick(fn=_srv_auto_refresh, outputs=[srv_lan_status, srv_pub_status])

            # ════════════════════════════════════════════════
            # Event Handlers — Streaming Tab
            # ════════════════════════════════════════════════

            def _stream_update(live, title, game):
                if self.stream_set_cb:
                    try:
                        self.stream_set_cb(live, title.strip(), game.strip())
                    except Exception as e:
                        logger.error(f"[Stream] Set error: {e}")
                status = _get_stream_status()
                return self._stream_status_html(status)

            def _stream_info_update(title, game, live):
                return _stream_update(live, title, game)

            def _stream_auto_refresh():
                return self._stream_status_html(_get_stream_status())

            def _get_stream_status():
                if self.stream_status_cb:
                    try:
                        return self.stream_status_cb()
                    except Exception:
                        pass
                return None

            stream_live_toggle.change(
                fn=_stream_update,
                inputs=[stream_live_toggle, stream_title_input, stream_game_input],
                outputs=[stream_status_html]
            )
            stream_update_btn.click(
                fn=_stream_info_update,
                inputs=[stream_title_input, stream_game_input, stream_live_toggle],
                outputs=[stream_status_html]
            )
            stream_timer.tick(fn=_stream_auto_refresh, outputs=[stream_status_html])

            # ════════════════════════════════════════════════
            # Event Handlers — Memory Tab
            # ════════════════════════════════════════════════

            def _memory_refresh():
                goals_rows = []
                stats_html = "<span style='color:#555;'>Memory system unavailable.</span>"
                if self.memory_summary_cb:
                    try:
                        summary = self.memory_summary_cb()
                        goals = summary.get("goals", [])
                        for g in goals:
                            owner = g.get("user_id") or "Shiro"
                            goals_rows.append([
                                g.get("id", ""),
                                g.get("type", ""),
                                owner,
                                g.get("status", ""),
                                g.get("text", ""),
                                g.get("progress", "") or "",
                            ])
                        st = summary.get("short_term_count", "?")
                        lt = summary.get("longterm_count", "?")
                        ep = len(summary.get("episodic", []))
                        stats_html = self._memory_stats_html(st, lt, ep, len(goals))
                    except Exception as e:
                        stats_html = f"<span style='color:#d07070;'>Error loading memory: {e}</span>"
                return goals_rows, stats_html

            def _memory_add_goal(text, goal_type, user_id):
                if not text or not text.strip():
                    return "⚠️ Goal text cannot be empty.", *_memory_refresh()
                if self.memory_add_goal_cb:
                    try:
                        uid = user_id.strip() if user_id and user_id.strip() else None
                        new_id = self.memory_add_goal_cb(text.strip(), goal_type, uid)
                        msg = f"✅ Added {'user' if uid else 'Shiro'} goal (id: {new_id}): {text[:60]}"
                    except Exception as e:
                        msg = f"❌ Error: {e}"
                else:
                    msg = "⚠️ Goal system not available."
                rows, stats = _memory_refresh()
                return msg, rows, stats

            def _memory_delete_goal(goal_id):
                if not goal_id or not goal_id.strip():
                    return "⚠️ Enter a goal ID to delete.", *_memory_refresh()
                if self.memory_delete_goal_cb:
                    try:
                        ok = self.memory_delete_goal_cb(goal_id.strip())
                        msg = f"✅ Deleted goal {goal_id}" if ok else f"⚠️ Goal {goal_id} not found."
                    except Exception as e:
                        msg = f"❌ Error: {e}"
                else:
                    msg = "⚠️ Goal system not available."
                rows, stats = _memory_refresh()
                return msg, rows, stats

            memory_refresh_btn.click(fn=_memory_refresh, outputs=[goals_display, memory_stats_html])
            add_goal_btn.click(
                fn=_memory_add_goal,
                inputs=[new_goal_text, new_goal_type, new_goal_user],
                outputs=[memory_status_box, goals_display, memory_stats_html]
            )
            delete_goal_btn.click(
                fn=_memory_delete_goal,
                inputs=[delete_goal_id],
                outputs=[memory_status_box, goals_display, memory_stats_html]
            )

            # ── Meditation handlers ──────────────────────────────────────────

            def _med_refresh_status():
                if not self.meditation_status_cb:
                    return self._med_status_html(None)
                try:
                    return self._med_status_html(self.meditation_status_cb())
                except Exception as e:
                    return f"<span style='color:#c45a5a;'>Error: {e}</span>"

            def _med_begin(depth):
                if not self.meditation_begin_cb:
                    return "⚠️ Meditation system not connected."
                try:
                    ok = self.meditation_begin_cb(depth)
                    return f"✅ Meditation started ({depth} depth)." if ok else "⚠️ Already meditating or unavailable."
                except Exception as e:
                    return f"❌ Error: {e}"

            def _med_wake():
                if not self.meditation_wake_cb:
                    return "⚠️ Meditation system not connected."
                try:
                    data = self.meditation_wake_cb()
                    if data:
                        phase = data.get("phase", "unknown")
                        return f"⬆ Gently waking Shiro from {phase}…"
                    return "⚠️ Shiro is not meditating."
                except Exception as e:
                    return f"❌ Error: {e}"

            def _med_refresh_thoughts():
                if not self.meditation_thoughts_cb:
                    return "No thought stream available."
                try:
                    thoughts = self.meditation_thoughts_cb()
                    if not thoughts:
                        return "(No thoughts yet — session not active)"
                    return "\n".join(f"[{t.get('phase','?')}] {t.get('text','')}" for t in thoughts[-30:])
                except Exception as e:
                    return f"Error: {e}"

            def _med_refresh_stats():
                if not self.meditation_stats_cb:
                    return "<span style='color:#334;'>Stats system not connected.</span>"
                try:
                    return self._med_stats_html(self.meditation_stats_cb())
                except Exception as e:
                    return f"<span style='color:#c45a5a;'>Error: {e}</span>"

            def _med_auto_refresh():
                status = _med_refresh_status()
                return status

            med_begin_btn.click(fn=_med_begin, inputs=[med_depth], outputs=[med_action_status])
            med_wake_btn.click(fn=_med_wake, outputs=[med_action_status])
            med_stats_btn.click(fn=_med_refresh_stats, outputs=[med_stats_html])
            med_thoughts_btn.click(fn=_med_refresh_thoughts, outputs=[med_thoughts_box])
            med_thoughts_clear_btn.click(fn=lambda: "", outputs=[med_thoughts_box])
            med_auto_timer.tick(fn=_med_auto_refresh, outputs=[med_status_html])

            # ── Speakers handlers ────────────────────────────────────────────

            def _speakers_refresh():
                profiles_rows = []
                queue_html = "<span style='color:#334;'>No queue data.</span>"
                detail = ""
                if self.multi_hub_profiles_cb:
                    try:
                        profiles = self.multi_hub_profiles_cb()
                        for p in profiles:
                            profiles_rows.append([
                                p.get("username", ""),
                                p.get("display_name", ""),
                                "✅ Yes" if p.get("is_primary") else "—",
                                str(p.get("message_count", 0)),
                                p.get("first_seen", ""),
                                p.get("last_seen", ""),
                            ])
                    except Exception as e:
                        profiles_rows = [[f"Error: {e}", "", "", "", "", ""]]
                if self.multi_hub_status_cb:
                    try:
                        status = self.multi_hub_status_cb()
                        queue_html = self._speakers_queue_html(status)
                    except Exception as e:
                        queue_html = f"<span style='color:#c45a5a;'>Error: {e}</span>"
                return profiles_rows, queue_html

            def _speakers_inspect(username):
                if not username or not username.strip():
                    return "Enter a username above."
                if not self.multi_hub_status_cb:
                    return "Hub not connected."
                try:
                    status = self.multi_hub_status_cb()
                    profiles = status.get("profiles", {})
                    p = profiles.get(username.strip().lower())
                    if not p:
                        return f"No profile found for '{username}'."
                    lines = [
                        f"Username:      {p.get('username', username)}",
                        f"Display name:  {p.get('display_name', username)}",
                        f"Primary user:  {'Yes' if p.get('is_primary') else 'No'}",
                        f"Messages:      {p.get('message_count', 0)}",
                        f"First seen:    {p.get('first_seen', '—')}",
                        f"Last seen:     {p.get('last_seen', '—')}",
                        "",
                        f"Known facts ({len(p.get('known_facts', []))}):",
                    ]
                    for fact in p.get("known_facts", [])[:10]:
                        lines.append(f"  • {fact}")
                    topics = p.get("topics_discussed", [])
                    if topics:
                        lines.append(f"\nTopics discussed: {', '.join(topics[:12])}")
                    notes = p.get("shiro_notes", "")
                    if notes:
                        lines.append(f"\nShiro's notes: {notes[:200]}")
                    return "\n".join(lines)
                except Exception as e:
                    return f"Error: {e}"

            def _speakers_auto_refresh():
                rows, queue = _speakers_refresh()
                return rows, queue

            speakers_refresh_btn.click(fn=_speakers_refresh, outputs=[speakers_profiles_df, speakers_queue_html])
            speakers_inspect_btn.click(fn=_speakers_inspect, inputs=[speakers_inspect_input], outputs=[speakers_detail_box])
            speakers_auto_timer.tick(fn=_speakers_auto_refresh, outputs=[speakers_profiles_df, speakers_queue_html])

            # ── Books handlers ────────────────────────────────────────────────

            def _book_refresh():
                choices = []
                if self.book_list_cb:
                    try:
                        choices = self.book_list_cb() or []
                    except Exception as e:
                        choices = []
                return gr.update(choices=choices, value=choices[0] if choices else None)

            def _book_refresh_digested():
                if self.book_digested_cb:
                    try:
                        titles = self.book_digested_cb() or []
                        return "\n".join(f"• {t}" for t in titles) if titles else "(none yet)"
                    except Exception as e:
                        return f"Error: {e}"
                return "(BookReader not connected)"

            def _book_do_digest(filename):
                if not filename:
                    return "⚠️ Select a book from the dropdown first.", gr.update()
                if self.book_digest_cb:
                    try:
                        ok, msg = self.book_digest_cb(filename)
                        # After digesting, refresh the dropdown
                        new_choices = []
                        if self.book_list_cb:
                            try:
                                new_choices = self.book_list_cb() or []
                            except Exception:
                                pass
                        return msg, gr.update(choices=new_choices, value=new_choices[0] if new_choices else None)
                    except Exception as e:
                        return f"❌ Error: {e}", gr.update()
                return "⚠️ BookReader not connected.", gr.update()

            book_refresh_btn.click(fn=_book_refresh, outputs=[book_selector])
            book_digested_refresh_btn.click(fn=_book_refresh_digested, outputs=[book_digested_list])
            book_digest_btn.click(
                fn=_book_do_digest,
                inputs=[book_selector],
                outputs=[book_status_box, book_selector],
            )

        self.interface = demo

    # ── Discord HTML helpers ──────────────────────────────────────────────────

    def _srv_card_html(self, kind: str, status: Optional[dict]) -> str:
        """Render a status card for LAN or Public server."""
        label   = "LAN Voice Room" if kind == "lan" else "Public Tunnel"
        icon    = "🏠" if kind == "lan" else "🌍"

        if not status:
            running  = False
            url      = ""
            starting = False
        else:
            running  = status.get("running", False)
            url      = status.get("url", "")
            starting = status.get("starting", False)

        if starting:
            badge = "<span class='srv-badge-mid'>⏳ STARTING</span>"
            dot   = "#fee75c"
            desc  = "Tunnel is connecting... URL will appear shortly."
        elif running and url:
            badge = "<span class='srv-badge-on'>● ONLINE</span>"
            dot   = "#57f287"
            desc  = f"<div class='srv-url-box'>🔗 {url}</div>"
        elif running:
            badge = "<span class='srv-badge-on'>● RUNNING</span>"
            dot   = "#57f287"
            desc  = "<span style='color:#7a9a7a;font-size:0.82rem;'>Server is up — getting URL...</span>"
        else:
            badge = "<span class='srv-badge-off'>○ OFFLINE</span>"
            dot   = "#ed4245"
            desc  = "<span style='color:#7a5a5a;font-size:0.82rem;'>Not running. Click Start to launch.</span>"

        return f"""
        <div class='srv-card'>
            <div style='display:flex;align-items:center;gap:10px;margin-bottom:10px;'>
                <span style='font-size:1.2rem;'>{icon}</span>
                <span style='font-family:Cinzel,serif;font-size:0.85rem;color:#c0a070;letter-spacing:0.1em;'>{label}</span>
                <span style='flex:1'></span>
                {badge}
            </div>
            {desc}
        </div>"""

    def _dc_status_html(self, status: Optional[dict]) -> str:
        if not status:
            return """
            <div style='display:flex;align-items:center;gap:8px;padding:10px 0;'>
                <span style='display:inline-block;width:12px;height:12px;border-radius:50%;background:#ed4245;'></span>
                <span style='color:#b0b8d8;font-size:0.95rem;'>Disconnected</span>
            </div>"""

        connected = status.get("connected", False)
        connecting = status.get("connecting", False)
        in_voice   = status.get("in_voice", False)

        if connecting:
            dot_color = "#fee75c"
            label = "Connecting..."
        elif connected:
            dot_color = "#57f287"
            label = "Connected to Discord"
        else:
            dot_color = "#ed4245"
            label = "Disconnected"

        guild   = status.get("guild", "")
        voice   = status.get("voice_channel", "")
        text_ch = status.get("text_channel", "")
        users   = status.get("users_in_voice", [])
        error   = status.get("error", "")

        chips = ""
        if guild:
            chips += f"<span class='dc-info-chip'>🏠 <b>{guild}</b></span>"
        if voice:
            chips += f"<span class='dc-info-chip'>🔊 <b>{voice}</b></span>"
        if text_ch:
            chips += f"<span class='dc-info-chip'># <b>{text_ch}</b></span>"
        if in_voice and users:
            chips += f"<span class='dc-info-chip'>👥 {', '.join(users)}</span>"
        if error:
            chips += f"<span class='dc-info-chip' style='color:#ed4245;border-color:#5a2020;'>⚠️ {error[:60]}</span>"

        return f"""
        <div style='padding:8px 0;'>
            <div style='display:flex;align-items:center;gap:8px;margin-bottom:8px;'>
                <span style='display:inline-block;width:12px;height:12px;border-radius:50%;background:{dot_color};box-shadow:0 0 6px {dot_color};'></span>
                <span style='color:#e0e8ff;font-size:0.95rem;font-weight:600;'>{label}</span>
            </div>
            <div style='display:flex;flex-wrap:wrap;gap:6px;'>{chips}</div>
        </div>"""

    def _stream_status_html(self, status: Optional[dict]) -> str:
        """Render streaming status card."""
        if not status or not status.get("live"):
            return """
            <div style='display:flex;align-items:center;gap:8px;padding:10px 14px;
                        background:#0a0810;border:1px solid #2a1a30;border-radius:10px;'>
                <span style='display:inline-block;width:10px;height:10px;border-radius:50%;background:#444;'></span>
                <span style='color:#665566;font-size:0.9rem;'>Offline — not streaming</span>
            </div>"""
        title = status.get("title", "")
        game  = status.get("game", "")
        started = status.get("started_at")
        duration_str = ""
        if started:
            import time as _t
            elapsed = _t.time() - started
            mins = int(elapsed // 60)
            duration_str = f" · {mins}m live" if mins < 60 else f" · {int(mins//60)}h {mins%60}m live"
        detail_lines = ""
        if title:
            detail_lines += f"<div style='color:#e0c080;font-size:0.82rem;margin-top:4px;'>📝 {title}</div>"
        if game:
            detail_lines += f"<div style='color:#a0c0e0;font-size:0.82rem;'>🎮 {game}</div>"
        return f"""
        <div style='padding:10px 14px;background:#0a0810;border:1px solid #502010;border-radius:10px;'>
            <div style='display:flex;align-items:center;gap:8px;'>
                <span style='display:inline-block;width:10px;height:10px;border-radius:50%;
                             background:#ff4040;box-shadow:0 0 8px #ff4040;'></span>
                <span style='color:#ff8060;font-weight:600;font-size:0.9rem;'>LIVE{duration_str}</span>
            </div>
            {detail_lines}
        </div>"""

    def _memory_stats_html(self, short_term: int, long_term, episodic: int, goals: int) -> str:
        """Render memory stats card."""
        return f"""
        <div style='background:#0a0810;border:1px solid #2a1a40;border-radius:10px;padding:16px 20px;font-size:0.85rem;'>
            <div style='color:#c07030;font-family:Cinzel,serif;font-size:0.8rem;letter-spacing:0.1em;
                        margin-bottom:12px;text-transform:uppercase;'>Memory Overview</div>
            <div style='display:grid;grid-template-columns:1fr 1fr;gap:10px;'>
                <div style='background:#13111a;border:1px solid #2a2030;border-radius:8px;padding:10px 14px;'>
                    <div style='color:#7a6a8a;font-size:0.72rem;text-transform:uppercase;'>Short-term</div>
                    <div style='color:#e0d0f0;font-size:1.4rem;font-weight:600;'>{short_term}</div>
                    <div style='color:#5a4a6a;font-size:0.7rem;'>turns in buffer</div>
                </div>
                <div style='background:#13111a;border:1px solid #2a2030;border-radius:8px;padding:10px 14px;'>
                    <div style='color:#7a6a8a;font-size:0.72rem;text-transform:uppercase;'>Long-term</div>
                    <div style='color:#e0d0f0;font-size:1.4rem;font-weight:600;'>{long_term}</div>
                    <div style='color:#5a4a6a;font-size:0.7rem;'>semantic memories</div>
                </div>
                <div style='background:#13111a;border:1px solid #2a2030;border-radius:8px;padding:10px 14px;'>
                    <div style='color:#7a6a8a;font-size:0.72rem;text-transform:uppercase;'>Episodic</div>
                    <div style='color:#e0d0f0;font-size:1.4rem;font-weight:600;'>{episodic}</div>
                    <div style='color:#5a4a6a;font-size:0.7rem;'>recent episodes</div>
                </div>
                <div style='background:#13111a;border:1px solid #2a2030;border-radius:8px;padding:10px 14px;'>
                    <div style='color:#7a6a8a;font-size:0.72rem;text-transform:uppercase;'>Goals</div>
                    <div style='color:#e0d0f0;font-size:1.4rem;font-weight:600;'>{goals}</div>
                    <div style='color:#5a4a6a;font-size:0.7rem;'>total (all statuses)</div>
                </div>
            </div>
        </div>"""

    def _dc_detail_text(self, status: Optional[dict]) -> str:
        lines = []
        lines.append(f"Status:      {'✅ Connected' if status.get('connected') else '⚪ Connecting...' if status.get('connecting') else '❌ Offline'}")
        lines.append(f"Voice:       {'🔊 In voice' if status.get('in_voice') else '— Not in voice'}")
        if status.get("guild"):
            lines.append(f"Server:      {status['guild']}")
        if status.get("voice_channel"):
            lines.append(f"Voice Ch:    #{status['voice_channel']}")
        if status.get("text_channel"):
            lines.append(f"Text Ch:     #{status['text_channel']}")
        users = status.get("users_in_voice", [])
        if users:
            lines.append(f"In Voice:    {', '.join(users)}")
        else:
            lines.append("In Voice:    (empty)")
        if status.get("error"):
            lines.append(f"\n⚠️  Error: {status['error']}")
        return "\n".join(lines)

    def _med_status_html(self, status) -> str:
        """Render meditation status card for the GUI tab."""
        if not status:
            return """
            <div style='padding:12px 16px;background:#030408;border:1px solid #1a2040;
                        border-radius:10px;display:flex;align-items:center;gap:10px;'>
                <span style='display:inline-block;width:10px;height:10px;border-radius:50%;background:#334;'></span>
                <span style='color:#445;font-size:0.9rem;'>Meditation system not connected.</span>
            </div>"""

        active  = status.get("active", False)
        phase   = status.get("phase", "IDLE")
        thoughts = status.get("thoughts_count", 0)
        insights = status.get("insights_count", 0)
        duration = status.get("duration_seconds", 0)
        streak   = status.get("session_streak", 0)
        alignment = status.get("alignment_score", 0.5)
        idle_pct  = status.get("idle_pct", 0)
        depth     = status.get("depth", "—")

        if active:
            dot_color = "#6b8ecf"
            dot_glow  = "box-shadow:0 0 8px #6b8ecf;"
            state_label = f"Meditating · {phase}"
            state_color = "#8ab0e8"
        else:
            dot_color = "#334455"
            dot_glow  = ""
            state_label = "At rest"
            state_color = "#445566"

        mins = int(duration // 60)
        secs = int(duration % 60)
        dur_str = f"{mins}m {secs:02d}s" if mins else f"{secs}s"

        idle_bar = f"""
            <div style='margin:8px 0 4px;display:flex;align-items:center;gap:8px;'>
                <span style='font-family:monospace;font-size:0.65rem;color:#445;text-transform:uppercase;'>Idle</span>
                <div style='flex:1;height:3px;background:#0a0c14;border-radius:3px;overflow:hidden;'>
                    <div style='height:100%;width:{min(100,int(idle_pct))}%;background:rgba(107,142,207,0.5);
                                border-radius:3px;transition:width 1s;'></div>
                </div>
                <span style='font-family:monospace;font-size:0.6rem;color:#445;'>{int(idle_pct)}%</span>
            </div>"""

        align_color = "#5a9e6f" if alignment > 0.65 else "#6b8ecf" if alignment > 0.45 else "#c45a5a"
        chips = "".join(f"""<span style='font-family:monospace;font-size:0.65rem;padding:3px 8px;
                border-radius:12px;border:1px solid #1a2040;background:rgba(107,142,207,0.06);
                color:#6070a0;'>{c}</span> """
            for c in [
                f"💭 {thoughts} thoughts",
                f"✦ {insights} insights",
                f"⏱ {dur_str}",
                f"🔥 streak {streak}",
                f"⚖ {int(alignment*100)}% aligned",
                f"depth: {depth}",
            ])

        return f"""
        <div style='padding:14px 16px;background:#030408;border:1px solid #1a2040;border-radius:10px;'>
            <div style='display:flex;align-items:center;gap:10px;margin-bottom:10px;'>
                <span style='display:inline-block;width:12px;height:12px;border-radius:50%;
                             background:{dot_color};{dot_glow}'></span>
                <span style='color:{state_color};font-size:0.95rem;font-weight:600;'>{state_label}</span>
            </div>
            {idle_bar}
            <div style='display:flex;flex-wrap:wrap;gap:5px;margin-top:8px;'>{chips}</div>
        </div>"""

    def _med_stats_html(self, stats) -> str:
        """Render meditation session stats card."""
        if not stats:
            return "<span style='color:#334;'>No stats available.</span>"
        sessions   = stats.get("total_sessions", 0)
        total_time = stats.get("total_seconds", 0)
        insights   = stats.get("total_insights", 0)
        questions  = stats.get("total_questions", 0)
        streak     = stats.get("current_streak", 0)
        avg_align  = stats.get("avg_alignment", 0.5)
        hours = int(total_time // 3600)
        mins  = int((total_time % 3600) // 60)
        time_str = f"{hours}h {mins}m" if hours else f"{mins}m"
        def cell(val, label):
            return f"""<div style='background:#0a0c14;border:1px solid #1a2040;border-radius:8px;padding:10px 14px;'>
                <div style='color:#445;font-size:0.68rem;text-transform:uppercase;'>{label}</div>
                <div style='color:#8ab0e8;font-size:1.3rem;font-weight:600;'>{val}</div>
            </div>"""
        return f"""
        <div style='background:#030408;border:1px solid #1a2040;border-radius:10px;padding:14px;'>
            <div style='color:#6b8ecf;font-size:0.72rem;letter-spacing:0.12em;text-transform:uppercase;margin-bottom:10px;'>
                Lifetime Meditation Stats
            </div>
            <div style='display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;'>
                {cell(sessions,'Sessions')}{cell(time_str,'Total Time')}{cell(f"🔥 {streak}",'Streak')}
                {cell(insights,'Insights')}{cell(questions,'Questions')}{cell(f"{int(avg_align*100)}%",'Avg Align')}
            </div>
        </div>"""

    def _speakers_queue_html(self, status) -> str:
        """Render speaker queue status card."""
        if not status:
            return "<span style='color:#334;'>Queue data unavailable.</span>"
        queued    = status.get("queued_messages", 0)
        active    = status.get("active_speakers", 0)
        total     = status.get("total_profiles", 0)
        strategy  = status.get("last_strategy", "—")
        last_user = status.get("last_speaker", "—")
        strat_color = {
            "SINGLE": "#5a9e6f", "MULTI_TURN": "#6b8ecf",
            "SELECTIVE": "#b8934a", "ACKNOWLEDGE": "#8e5ac8",
        }.get(strategy, "#445")
        return f"""
        <div style='padding:12px 14px;background:#030408;border:1px solid #1a2a1a;border-radius:10px;'>
            <div style='color:#5a9e6f;font-size:0.7rem;letter-spacing:0.12em;text-transform:uppercase;margin-bottom:10px;'>
                Queue Status
            </div>
            <div style='display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-bottom:10px;'>
                <div style='background:#0a0c0a;border:1px solid #1a2a1a;border-radius:8px;padding:8px 12px;'>
                    <div style='color:#345;font-size:0.65rem;text-transform:uppercase;'>Queued</div>
                    <div style='color:#8ab8a8;font-size:1.2rem;'>{queued}</div>
                </div>
                <div style='background:#0a0c0a;border:1px solid #1a2a1a;border-radius:8px;padding:8px 12px;'>
                    <div style='color:#345;font-size:0.65rem;text-transform:uppercase;'>Active Speakers</div>
                    <div style='color:#8ab8a8;font-size:1.2rem;'>{active}</div>
                </div>
            </div>
            <div style='font-family:monospace;font-size:0.72rem;color:#445;line-height:1.8;'>
                <span>Profiles: <b style='color:#667'>{total}</b></span>&nbsp;&nbsp;
                <span>Strategy: <b style='color:{strat_color};'>{strategy}</b></span>&nbsp;&nbsp;
                <span>Last speaker: <b style='color:#667'>{last_user}</b></span>
            </div>
        </div>"""

    def launch(self, share=False):
        if self.interface:
            self.interface.launch(
                share=share,
                theme=self.theme_obj,
                css=self.custom_css
            )
        else:
            logger.error("UI not built. Call build_ui() first.")