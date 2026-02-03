import gradio as gr
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

class AureliaGUI:
    """Gradio-based Web GUI for Aurelia AI."""

    def __init__(
        self,
        process_text_cb: Callable[[str], str],
        process_audio_cb: Callable[[str], str],
        title: str = "⚔️ Aurelia Vale: The Hedge-Knight Squire",
        theme: str = "soft"
    ):
        self.process_text_cb = process_text_cb
        self.process_audio_cb = process_audio_cb
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

        with gr.Blocks(title=self.title, theme=theme_obj) as demo:
            gr.Markdown(f"# {self.title}")

            with gr.Row():
                with gr.Column(scale=4):
                    # We omit type="messages" for cross-version compatibility as per internal guidelines,
                    # but we will provide the dictionary-based data structure.
                    chatbot = gr.Chatbot(label="Chat History", height=500)
                    msg = gr.Textbox(
                        label="Type your message...",
                        placeholder="Say something to Aurelia...",
                        show_label=False,
                    )
                    with gr.Row():
                        submit_btn = gr.Button("Send", variant="primary")
                        clear_btn = gr.Button("Clear Chat")

                with gr.Column(scale=1):
                    gr.Markdown("### 🎙️ Voice Input")
                    audio_input = gr.Audio(
                        label="Record Speech",
                        sources=["microphone"],
                        type="filepath",
                    )
                    stt_btn = gr.Button("Process Speech", variant="secondary")

                    gr.Markdown("### 🛠️ Status")
                    gr.Markdown("System: **Online**")
                    error_box = gr.Textbox(label="Last System Error", interactive=False)

            # Handlers
            def user_message(user_input, history):
                if history is None: history = []
                history.append({"role": "user", "content": user_input})
                return "", history

            def bot_response(history):
                if not history: return [], ""
                # Get the content of the last message (which should be from the user)
                user_input = history[-1]["content"]
                try:
                    response = self.process_text_cb(user_input)
                    history.append({"role": "assistant", "content": response})
                    return history, "" # Clear error box on success
                except Exception as e:
                    err_msg = str(e)
                    history.append({"role": "assistant", "content": f"[System Error]: {err_msg}"})
                    return history, err_msg

            def handle_audio(audio_path, history):
                if history is None: history = []
                if not audio_path:
                    return history, ""

                try:
                    user_txt, bot_txt = self.process_audio_cb(audio_path)
                    history.append({"role": "user", "content": user_txt})
                    history.append({"role": "assistant", "content": bot_txt})
                    return history, ""
                except Exception as e:
                    err_msg = str(e)
                    history.append({"role": "assistant", "content": f"[System Error]: {err_msg}"})
                    return history, err_msg

            msg.submit(user_message, [msg, chatbot], [msg, chatbot], queue=False).then(
                bot_response, chatbot, [chatbot, error_box]
            )
            submit_btn.click(user_message, [msg, chatbot], [msg, chatbot], queue=False).then(
                bot_response, chatbot, [chatbot, error_box]
            )

            stt_btn.click(handle_audio, [audio_input, chatbot], [chatbot, error_box])

            clear_btn.click(lambda: ([], ""), None, [chatbot, error_box], queue=False)

        self.interface = demo

    def launch(self, share=False):
        if self.interface:
            self.interface.launch(share=share)
        else:
            logger.error("UI not built. Call build_ui() first.")
