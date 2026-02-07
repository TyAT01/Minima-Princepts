import gradio as gr
import logging
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
        title: str = "✨ Yuzu: The Calm Observer",
        theme: str = "soft"
    ):
        self.process_text_cb = process_text_cb
        self.process_audio_cb = process_audio_cb
        self.toggle_mic_cb = toggle_mic_cb
        self.poll_results_cb = poll_results_cb
        self.title = title
        self.theme = theme
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

        with gr.Blocks() as demo:
            gr.Markdown(f"# {self.title}")

            with gr.Row():
                with gr.Column(scale=4):
                    # Compatibility for Gradio 4.x (requires type="messages") and Gradio 5.x+ (default)
                    chatbot_kwargs = {"label": "Chat History", "height": 500}
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

                    gr.Markdown("### 🎙️ Hands-Free Mic")
                    mic_toggle = gr.Checkbox(label="Open Mic (Hands-Free)", value=False)

                    gr.Markdown("### 🛠️ Status")
                    gr.Markdown("System: **Online**")
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
                        yield history, ""
                except Exception as e:
                    err_msg = str(e)
                    history.append({"role": "assistant", "content": f"[System Error]: {err_msg}"})
                    yield history, err_msg

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

            mic_toggle.change(on_mic_toggle, mic_toggle, None)

            timer.tick(poll_results, chatbot, chatbot)

            clear_btn.click(lambda: ([], ""), None, [chatbot, error_box], queue=False)

        self.interface = demo

    def launch(self, share=False):
        if self.interface:
            # Theme and Title passed here for Gradio 6.0 compatibility
            theme_obj = gr.themes.Soft() # Fallback or pass from build_ui
            if self.theme == "monochrome": theme_obj = gr.themes.Monochrome()
            elif self.theme == "glass": theme_obj = gr.themes.Glass()
            elif self.theme == "default": theme_obj = gr.themes.Default()

            self.interface.launch(share=share, title=self.title, theme=theme_obj)
        else:
            logger.error("UI not built. Call build_ui() first.")
