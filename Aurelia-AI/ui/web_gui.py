import gradio as gr
import logging
import os
from typing import Callable, Optional

logger = logging.getLogger(__name__)

class AureliaGUI:
    """Gradio-based Web GUI for Aurelia AI."""

    def __init__(
        self,
        process_text_cb: Callable[[str], str],
        process_audio_cb: Callable[[str], str],
        error_reporter: Optional[Callable[[], str]] = None
    ):
        self.process_text_cb = process_text_cb
        self.process_audio_cb = process_audio_cb
        self.error_reporter = error_reporter
        self.interface = None

    def build_ui(self):
        with gr.Blocks(title="Aurelia Vale - AI Companion", theme=gr.themes.Soft()) as demo:
            gr.Markdown("# ⚔️ Aurelia Vale: The Hedge-Knight Squire")

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
                    status_display = gr.Markdown("System: **Online**")
                    error_box = gr.Textbox(label="Last System Error", interactive=False)

            # Handlers
            def user_message(user_input, history):
                return "", history + [[user_input, None]]

            def bot_response(history):
                user_input = history[-1][0]
                try:
                    response = self.process_text_cb(user_input)
                    history[-1][1] = response
                except Exception as e:
                    history[-1][1] = f"[System Error]: {str(e)}"
                return history

            def process_audio(audio_path, history):
                if not audio_path:
                    return history, "No audio recorded."

                try:
                    # Transcribe and get AI response
                    response_text = self.process_audio_cb(audio_path)
                    # We need to know the transcribed text to show in history
                    # For simplicity, we'll assume the callback handles memory but we might want the text here
                    # Let's adjust the callback to return (transcribed_text, response)
                    return response_text # This will be handled by a wrapper in app.py
                except Exception as e:
                    return history + [["[Audio Input]", f"Error processing audio: {str(e)}"]]

            # Simplified Audio Handler for Gradio
            def handle_audio(audio_path, history):
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
