import gradio as gr
import logging
import base64
from pathlib import Path
from typing import Callable, Optional, List, Tuple

logger = logging.getLogger(__name__)

class YuzuGUI:
    """Gradio-based Web GUI for Yuzu AI."""

    def __init__(
        self,
        process_text_cb: Callable[[str, str], str],
        process_audio_cb: Callable[[str, str], Tuple[str, str]],
        toggle_mic_cb: Callable[[bool], None],
        poll_results_cb: Callable[[], List[Tuple[str, str]]],
        join_chat_cb: Optional[Callable[[str], List[str]]] = None,
        leave_chat_cb: Optional[Callable[[str], None]] = None,
        title: str = "✨ Yuzu: The Calm Observer",
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

    def _save_avatars(self) -> List[str]:
        """Saves SVG data URIs to files to avoid Gradio 6.0 OSError."""
        assets_dir = Path(__file__).parent / "assets"
        assets_dir.mkdir(exist_ok=True)

        user_svg_path = assets_dir / "user.svg"
        bot_svg_path = assets_dir / "bot.svg"

        user_avatar_b64 = "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0iIzRhOTBlMiI+PHBhdGggZD0iTTEyIDJDNi40OCAyIDIgNi40OCAyIDEyczQuNDggMTAgMTAgMTAgMTAtNC40OCAxMC0xMFMxNy41MiAyIDEyIDJ6bTAgM2MyLjIxIDAgNCAxLjc5IDQgNHMtMS43OSA0LTQgNC00LTEuNzktNC00IDEuNzktNCA0LTR6bTAgMTMuOGMtMi42NyAwLTUuMjYtMS4zMi02LjUtMy41OC4wMi0yLjE0IDQuMjctMy4yNyA2LjUtMy4yNyBzNi40OCAxLjEzIDYuNSAzLjI3Yy0xLjI0IDIuMjYtMy44MyAzLjU4LTYuNSAzLjU4eiIvPjwvc3ZnPg=="
        bot_avatar_b64 = "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0iI2ZmZjdkNyI+PHBhdGggZD0iTTEyIDJDNi40OCAyIDIgNi40OCAyIDEyczQuNDggMTAgMTAgMTAgMTAtNC40OCAxMC0xMFMxNy41MiAyIDEyIDJ6bTAgNGMuNjYgMCAxLjE4LjUxIDEuMjUgMS4xN2wuMTUgMi41M2MuMDEuMTgtLjEyLjMzLS4zLjMzaC0yLjJjLS4xOCAwLS4zMS0uMTUtLjMtLjMzbC4xNS0yLjUzYy4wNy0uNjYuNTktMS4xNyAxLjI1LTEuMTd6bTAgMTAuNWMtLjg0IDAtMS41LS42Ni0xLjUtMS41cy42Ni0xLjUgMS41LTEuNSAxLjUuNjYgMS41IDEuNS0uNjYgMS41LTEuNSAxLjV6Ii8+PC9zdmc+"

        if not user_svg_path.exists():
            with open(user_svg_path, "wb") as f:
                f.write(base64.b64decode(user_avatar_b64))

        if not bot_svg_path.exists():
            with open(bot_svg_path, "wb") as f:
                f.write(base64.b64decode(bot_avatar_b64))

        return [str(user_svg_path), str(bot_svg_path)]

    def build_ui(self):
        # Princess AI Theme CSS
        self.custom_css = """
        .gradio-container { background-color: #0b0f19 !important; color: #e0e0e0 !important; }
        .message.user { background-color: #4a90e2 !important; color: white !important; border-radius: 15px 15px 0 15px !important; }
        .message.bot { background-color: #222222 !important; color: white !important; border-radius: 15px 15px 15px 0 !important; }
        #chatbot { border: 1px solid #333 !important; }
        footer { display: none !important; }
        """

        # Determine theme object
        if self.theme == "soft":
            self.theme_obj = gr.themes.Soft()
        elif self.theme == "monochrome":
            self.theme_obj = gr.themes.Monochrome()
        elif self.theme == "glass":
            self.theme_obj = gr.themes.Glass()
        else:
            self.theme_obj = gr.themes.Default()

        # Save and get avatar file paths
        avatar_paths = self._save_avatars()

        with gr.Blocks(title=self.title) as demo:
            gr.Markdown(f"# {self.title}")

            with gr.Row():
                with gr.Column(scale=4):
                    # Compatibility for Gradio 4.x (requires type="messages") and Gradio 5.x+ (default)
                    chatbot_kwargs = {
                        "label": "Chat History",
                        "height": 500,
                        "elem_id": "chatbot",
                        "avatar_images": avatar_paths
                    }
                    try:
                        gr.Chatbot(type="messages", render=False)
                        chatbot_kwargs["type"] = "messages"
                    except TypeError:
                        pass
                    chatbot = gr.Chatbot(**chatbot_kwargs)
                    msg = gr.Textbox(
                        label="Type your message...",
                        placeholder="Say something to Yuzu...",
                        show_label=False,
                    )
                    with gr.Row():
                        submit_btn = gr.Button("Send", variant="primary")
                        clear_btn = gr.Button("Clear Chat")

                with gr.Column(scale=1):
                    gr.Markdown("### 👤 User Profile")
                    user_name = gr.Textbox(label="Your Name", value="Tyler", placeholder="Enter your name...")

                    with gr.Row():
                        join_btn = gr.Button("Join Chat", variant="secondary", size="sm")
                        leave_btn = gr.Button("Leave Chat", variant="stop", size="sm")

                    gr.Markdown("### 🎙️ Hands-Free Mic")
                    mic_toggle = gr.Checkbox(label="Open Mic (Hands-Free)", value=False)

                    gr.Markdown("### 🛠️ Status")
                    status_md = gr.Markdown("System: **Online**\nRoom: **Empty**")
                    error_box = gr.Textbox(label="Last System Error", interactive=False)

            # Timer for polling background STT results
            timer = gr.Timer(value=1.0, active=True)

            # Handlers
            def user_message(user_input, history):
                if history is None: history = []

                processed_input = user_input
                if isinstance(user_input, list) and len(user_input) > 0:
                    first_item = user_input[0]
                    if isinstance(first_item, dict):
                        processed_input = first_item.get("text", str(first_item))
                    else:
                        processed_input = str(first_item)
                elif isinstance(user_input, dict):
                    processed_input = user_input.get("text", str(user_input))

                history.append({"role": "user", "content": processed_input})
                return "", history

            def bot_response(history, name):
                if not history: yield [], gr.update(); return
                user_input = history[-1]["content"]

                if isinstance(user_input, list) and len(user_input) > 0:
                    first_item = user_input[0]
                    if isinstance(first_item, dict):
                        user_input = first_item.get("text", str(first_item))
                    else:
                        user_input = str(first_item)
                elif isinstance(user_input, dict):
                    user_input = user_input.get("text", str(user_input))

                try:
                    history.append({"role": "assistant", "content": ""})
                    full_response = ""
                    for fragment in self.process_text_cb(user_input, name):
                        full_response += fragment + " "
                        history[-1]["content"] = full_response.strip()
                        yield history, gr.update()
                except Exception as e:
                    err_msg = str(e)
                    history.append({"role": "assistant", "content": f"[System Error]: {err_msg}"})
                    yield history, gr.update(value=err_msg)

            def on_join(name, history):
                if history is None: history = []
                if self.join_chat_cb:
                    resp_fragments = self.join_chat_cb(name)
                    history.append({"role": "assistant", "content": " ".join(resp_fragments)})
                return history, f"System: **Online**\\nRoom: **{name} is here**"

            def on_leave(name, history):
                if self.leave_chat_cb:
                    self.leave_chat_cb(name)
                return history, "System: **Online**\\nRoom: **Empty**"

            def on_mic_toggle(value):
                self.toggle_mic_cb(value)

            def poll_results(history):
                if history is None: history = []
                new_results = self.poll_results_cb()
                if new_results:
                    for u, b in new_results:
                        history.append({"role": "user", "content": u})
                        history.append({"role": "assistant", "content": b})
                    return history
                return history

            msg.submit(user_message, [msg, chatbot], [msg, chatbot], queue=False).then(
                bot_response, [chatbot, user_name], [chatbot, error_box]
            )
            submit_btn.click(user_message, [msg, chatbot], [msg, chatbot], queue=False).then(
                bot_response, [chatbot, user_name], [chatbot, error_box]
            )

            join_btn.click(on_join, [user_name, chatbot], [chatbot, status_md])
            leave_btn.click(on_leave, [user_name, chatbot], [chatbot, status_md])

            mic_toggle.change(on_mic_toggle, mic_toggle, None)

            timer.tick(poll_results, chatbot, chatbot)

            clear_btn.click(lambda: ([], gr.update(value="")), None, [chatbot, error_box], queue=False)

        self.interface = demo

    def launch(self, share=False):
        if self.interface:
            # Gradio 6.0 compatibility: theme and css moved to launch()
            self.interface.launch(share=share, theme=self.theme_obj, css=self.custom_css)
        else:
            logger.error("UI not built. Call build_ui() first.")
