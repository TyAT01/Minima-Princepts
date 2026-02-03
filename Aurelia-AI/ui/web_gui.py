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
                return "", history + [[user_input, None]]

            def bot_response(history):
                if not history: return []
                user_input = history[-1][0]
                try:
                    response = self.process_text_cb(user_input)
                    history[-1][1] = response
                except Exception as e:
                    history[-1][1] = f"[System Error]: {str(e)}"
                return history

            def handle_audio(audio_path, history):
                if history is None: history = []
                if not audio_path:
                    return history

                user_txt, bot_txt = self.process_audio_cb(audio_path)
                history.append([user_txt, bot_txt])
                return history

            msg.submit(user_message, [msg, chatbot], [msg, chatbot], queue=False).then(
                bot_response, chatbot, chatbot
            )
            submit_btn.click(user_message, [msg, chatbot], [msg, chatbot], queue=False).then(
                bot_response, chatbot, chatbot
            )

            stt_btn.click(handle_audio, [audio_input, chatbot], chatbot)

            clear_btn.click(lambda: None, None, chatbot, queue=False)

        self.interface = demo

    def launch(self, share=False):
        if self.interface:
            self.interface.launch(share=share)
        else:
            logger.error("UI not built. Call build_ui() first.")
