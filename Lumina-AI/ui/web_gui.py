import gradio as gr
import logging
from typing import Callable, Optional, List, Tuple

logger = logging.getLogger(__name__)

class LuminaGUI:
    """Gradio-based Web GUI for Lumina AI."""

    def __init__(
        self,
        process_text_cb: Callable[[str, str], str],
        process_audio_cb: Callable[[str, str], Tuple[str, str]],
        toggle_mic_cb: Callable[[bool], None],
        poll_results_cb: Callable[[], List[Tuple[str, str]]],
        on_join_cb: Callable[[str], str],
        on_leave_cb: Callable[[str], str],
        title: str = "✨ Lumina: The Digital Spark",
        theme: str = "soft"
    ):
        self.process_text_cb = process_text_cb
        self.process_audio_cb = process_audio_cb
        self.toggle_mic_cb = toggle_mic_cb
        self.poll_results_cb = poll_results_cb
        self.on_join_cb = on_join_cb
        self.on_leave_cb = on_leave_cb
        self.title = title
        self.theme = theme
        self.theme_obj = None
        self.custom_css = ""
        self.interface = None

    def build_ui(self):
        # Determine theme object
        if self.theme == "soft":
            theme_obj = gr.themes.Soft()
        elif self.theme == "monochrome":
            theme_obj = gr.themes.Monochrome()
        elif self.theme == "glass":
            theme_obj = gr.themes.Glass()
        else:
            theme_obj = gr.themes.Default()

        self.custom_css = """
        body {
            margin: 0;
            background: #0d0d0d !important;
            font-family: system-ui, -apple-system, sans-serif;
            -webkit-font-smoothing: antialiased;
        }
        .gradio-container {
            background: linear-gradient(#111 0%, #000 100%) !important;
            border: none !important;
            max-width: 100% !important;
        }
        #chatbot {
            background: transparent !important;
            border: none !important;
        }
        /* User Message Styles */
        .user {
            background-color: #4a90e2 !important;
            color: white !important;
            border-radius: 20px 20px 4px 20px !important;
        }
        .user p { color: white !important; }
        /* Assistant Message Styles */
        .bot {
            background-color: #222 !important;
            color: white !important;
            border: 1px solid #333 !important;
            border-radius: 20px 20px 20px 4px !important;
        }
        .bot p { color: white !important; }
        /* Avatar Styles */
        .avatar-container {
            width: 36px !important;
            height: 36px !important;
            border-radius: 50% !important;
            border: 2px solid #444 !important;
            background-color: #111 !important;
        }
        #error-box {
            background-color: #111 !important;
            border: 1px solid #333 !important;
            color: #ff6b6b !important;
            border-radius: 12px;
        }
        #input-bar {
            background: #111 !important;
            border-top: 1px solid #333 !important;
            padding: 10px 15px !important;
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            z-index: 100;
        }
        #input-text textarea {
            background: #1c1c1c !important;
            border: none !important;
            border-radius: 24px !important;
            color: #fff !important;
            padding: 10px 18px !important;
        }
        #send-btn {
            background: #4a90e2 !important;
            border-radius: 50% !important;
            min-width: 48px !important;
            height: 48px !important;
            border: none !important;
            color: white !important;
            font-size: 20px !important;
        }
        /* Hide sidebar header for cleaner look */
        .sidebar h3 {
            color: #4a90e2;
        }
        """

        self.theme_obj = theme_obj

        # AI Avatar SVG & User Avatar (just a blue circle)
        ai_avatar = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0naHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmcnIHZpZXdCb3g9JzAgMCAxMDAgMTAwJz48Y2lyY2xlIGN4PSc1MCcgY3k9JzUwJyByPSc0NScgZmlsbD0nJTIzMDAwJy8+PHBhdGggZD0nTTMwIDQ1YTUgNSAwIDEgMSAwLTEwIDUgNSAwIDAgMSAwIDEwem00MCAwYTUgNSAwIDEgMSAwLTEwIDUgNSAwIDAgMSAwIDEweicgZmlsbD0nJTIzZmZmJy8+PHBhdGggZD0nTTI1IDY1YzUgMTAgMjAgMTUgNTAgMCcgc3Ryb2tlPSclMjNmZmYnIHN0cm9rZS13aWR0aD0nNicgZmlsbD0nbm9uZScgc3Ryb2tlLWxpbmVjYXA9J3JvdW5kJy8+PC9zdmc+"
        user_avatar = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0naHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmcnIHZpZXdCb3g9JzAgMCAxMDAgMTAwJz48Y2lyY2xlIGN4PSc1MCcgY3k9JzUwJyByPSc0NScgZmlsbD0nJTIzNGE5MGUyJy8+PC9zdmc+"

        self.custom_css += """
        .gradio-container { background: #0d0d0d !important; }
        footer { display: none !important; }
        #send-btn {
            max-width: 60px !important;
            border-radius: 50% !important;
            margin: auto !important;
        }
        #input-bar {
            align-items: center !important;
        }
        #chatbot {
            min-height: 400px !important;
        }
        """

        # Check Gradio version for parameter move
        blocks_kwargs = {"title": self.title}
        try:
            from gradio.utils import get_package_version
            version = get_package_version("gradio")
            if version and int(version.split(".")[0]) < 6:
                blocks_kwargs["theme"] = theme_obj
                blocks_kwargs["css"] = self.custom_css
        except:
            # Fallback to old behavior if version check fails
            blocks_kwargs["theme"] = theme_obj
            blocks_kwargs["css"] = self.custom_css

        with gr.Blocks(**blocks_kwargs) as demo:
            gr.Markdown(f"# {self.title}")

            with gr.Row():
                with gr.Column(scale=4):
                    # Compatibility for Gradio 4.x (requires type="messages") and Gradio 5.x+ (default)
                    chatbot_kwargs = {
                        "label": "Chat History",
                        "height": 500,
                        "avatar_images": (user_avatar, ai_avatar),
                        "show_label": False
                    }
                    try:
                        gr.Chatbot(type="messages", render=False)
                        chatbot_kwargs["type"] = "messages"
                    except TypeError:
                        pass
                    chatbot = gr.Chatbot(elem_id="chatbot", **chatbot_kwargs)
                    with gr.Row(elem_id="input-bar"):
                        msg = gr.Textbox(
                            label="Type your message...",
                            placeholder="talk to me bestie...",
                            show_label=False,
                            scale=4,
                            elem_id="input-text"
                        )
                        submit_btn = gr.Button("➢", variant="primary", elem_id="send-btn", scale=0)
                        clear_btn = gr.Button("Clear", variant="secondary", scale=0)

                with gr.Column(scale=1):
                    gr.Markdown("### 👤 User Profile")
                    user_name = gr.Textbox(label="Your Name", value="Tyler", placeholder="Enter your name...")
                    with gr.Row():
                        join_btn = gr.Button("Join Chat", variant="primary")
                        leave_btn = gr.Button("Leave Chat", variant="stop")

                    gr.Markdown("### 🎙️ Hands-Free Mic")
                    mic_toggle = gr.Checkbox(label="Open Mic (Hands-Free)", value=False)

                    gr.Markdown("### 🛠️ Status")
                    gr.Markdown("System: **Online**")
                    error_box = gr.Textbox(label="Last System Error", interactive=False, elem_id="error-box")

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
                if not history: yield [], ""; return
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
                    yield history, err_msg

            def on_join(name, history):
                if history is None: history = []
                response = self.on_join_cb(name)
                history.append({"role": "assistant", "content": response})
                return history

            def on_leave(name, history):
                if history is None: history = []
                response = self.on_leave_cb(name)
                history.append({"role": "assistant", "content": response})
                return history

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

            join_btn.click(on_join, [user_name, chatbot], chatbot)
            leave_btn.click(on_leave, [user_name, chatbot], chatbot)

            mic_toggle.change(on_mic_toggle, mic_toggle, None)

            timer.tick(poll_results, chatbot, chatbot, show_progress="hidden")

            clear_btn.click(lambda: ([], ""), None, [chatbot, error_box], queue=False)

        self.interface = demo

    def launch(self, share=False):
        if self.interface:
            launch_kwargs = {"share": share}
            try:
                from gradio.utils import get_package_version
                version = get_package_version("gradio")
                if version and int(version.split(".")[0]) >= 6:
                    launch_kwargs["theme"] = self.theme_obj
                    launch_kwargs["css"] = self.custom_css
            except:
                pass
            self.interface.launch(**launch_kwargs)
        else:
            logger.error("UI not built. Call build_ui() first.")
