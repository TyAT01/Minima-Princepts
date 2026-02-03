from __future__ import annotations
import logging
import asyncio
import yaml
import time
import pandas as pd
import plotly.express as px
import gradio as gr
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pathlib import Path
from config import settings
from hardware.profiler import HardwareProfiler

logger = logging.getLogger(__name__)

app = FastAPI(title="Aurelia Vale Dashboard")

subscribers: set[asyncio.Queue] = set()
main_loop: asyncio.AbstractEventLoop | None = None
orchestrator = None # Global reference

# Global log buffer for Gradio UI
log_buffer = []
MAX_LOG_BUFFER = 500

class QueueHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            # Add to global buffer
            log_buffer.append(msg)
            if len(log_buffer) > MAX_LOG_BUFFER:
                log_buffer.pop(0)

            if main_loop and main_loop.is_running():
                main_loop.call_soon_threadsafe(self.broadcast, msg)
        except Exception:
            self.handleError(record)

    def broadcast(self, msg: str):
        for q in subscribers:
            q.put_nowait(msg)

# Configure logging to use our QueueHandler
queue_handler = QueueHandler()
queue_handler.setFormatter(logging.Formatter('[%(levelname)s] %(message)s'))
logging.getLogger().addHandler(queue_handler)

def get_logs():
    return "\n".join(log_buffer)

async def handle_chat(message, history):
    if not orchestrator:
        return history + [["Error", "Orchestrator not initialized."]]

    # Gradio history is list of [user_msg, bot_msg]
    # process_text_input returns the response string
    response = await orchestrator.process_text_input(message, "WebUser", "web")
    return history + [[message, response]]

async def handle_audio(audio_path, history):
    if not orchestrator or not audio_path:
        return history

    response = await orchestrator.process_audio_input(audio_path, "WebUser", "web")
    return history + [[None, response]] # Gradio audio input doesn't have a text message from user usually

def get_system_metrics():
    profiler = HardwareProfiler()
    hw = profiler.detect()

    # Mock some activity data for the chart if we don't have real historical data
    # In a real scenario, we'd track this over time.
    data = {
        "Metric": ["CPU Cores", "Total RAM (GB)", "VRAM (GB)"],
        "Value": [hw.cpu_count, hw.total_ram_gb, hw.vram_gb or 0]
    }
    df = pd.DataFrame(data)
    fig = px.bar(df, x="Metric", y="Value", title="System Hardware Profile", color="Metric")

    status_text = f"Status: Online\nModel: {settings.chroma_model_id}\nGPU: {hw.gpu_name or 'None'}"
    return fig, status_text

def update_settings(max_tokens, local_audio):
    settings.max_new_tokens = max_tokens
    settings.enable_local_audio = local_audio
    return "Settings updated successfully."

async def join_discord(channel_id):
    if orchestrator and orchestrator.discord_bot:
        try:
            await orchestrator.discord_bot.join_voice(int(channel_id))
            return f"Joined channel {channel_id}"
        except Exception as e:
            return f"Error: {e}"
    return "Discord bot not available."

async def leave_discord():
    if orchestrator and orchestrator.discord_bot:
        try:
            await orchestrator.discord_bot.leave_voice()
            return "Left voice channel."
        except Exception as e:
            return f"Error: {e}"
    return "Discord bot not available."

def build_gradio_ui():
    with gr.Blocks() as demo:
        gr.Markdown("# 🌸 Aurelia Vale Control Panel")

        with gr.Tabs():
            with gr.TabItem("💬 Chat"):
                chatbot = gr.Chatbot(label="Conversation")
                with gr.Row():
                    msg = gr.Textbox(placeholder="Type a message...", scale=4)
                    submit = gr.Button("Send", variant="primary")

                with gr.Row():
                    audio_input = gr.Audio(label="Voice Input", type="filepath")
                    audio_submit = gr.Button("Transcribe & Send")

                submit.click(handle_chat, [msg, chatbot], [chatbot])
                msg.submit(handle_chat, [msg, chatbot], [chatbot])
                audio_submit.click(handle_audio, [audio_input, chatbot], [chatbot])

            with gr.TabItem("📋 Logs"):
                log_output = gr.Textbox(label="System Logs", value=get_logs, lines=20, interactive=False, every=2)
                gr.Button("Refresh").click(get_logs, outputs=log_output)

            with gr.TabItem("📊 Dashboard"):
                with gr.Row():
                    metrics_plot = gr.Plot(label="Hardware Stats")
                    status_info = gr.Textbox(label="System Status", interactive=False)
                refresh_metrics = gr.Button("Refresh Metrics")
                refresh_metrics.click(get_system_metrics, outputs=[metrics_plot, status_info])
                demo.load(get_system_metrics, outputs=[metrics_plot, status_info])

            with gr.TabItem("⚙️ Settings"):
                max_tokens = gr.Slider(10, 500, value=settings.max_new_tokens, label="Max New Tokens")
                local_audio = gr.Checkbox(value=settings.enable_local_audio, label="Enable Local Audio Playback")
                update_btn = gr.Button("Apply Settings")
                settings_status = gr.Textbox(label="Status")
                update_btn.click(update_settings, [max_tokens, local_audio], settings_status)

            with gr.TabItem("🎧 Discord"):
                discord_channel = gr.Textbox(label="Voice Channel ID", value=str(settings.discord_voice_channel_id or ""))
                with gr.Row():
                    join_btn = gr.Button("Join Voice", variant="primary")
                    leave_btn = gr.Button("Leave Voice")
                discord_status = gr.Textbox(label="Discord Status")
                join_btn.click(join_discord, discord_channel, discord_status)
                leave_btn.click(leave_discord, outputs=discord_status)

    return demo

@app.get("/old-dashboard", response_class=HTMLResponse)
async def index(request: Request):
    from jinja2 import Template
    # Keep old dashboard as an alternative
    templates_html = """
    <!DOCTYPE html>
    <html>
    <head><title>Aurelia Vale - Old Dashboard</title></head>
    <body style="background:#1a1a1a; color:#eee; font-family:sans-serif; padding:20px;">
        <h1>Old Dashboard (FastAPI)</h1>
        <p>Gradio dashboard is now at <a href="/" style="color:#bb86fc;">root</a>.</p>
        <p>Status: Online</p>
    </body>
    </html>
    """
    return HTMLResponse(content=templates_html)

@app.get("/logs-stream")
async def logs_stream(request: Request):
    q = asyncio.Queue()
    subscribers.add(q)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    log_msg = await asyncio.wait_for(q.get(), timeout=1.0)
                    lines = log_msg.splitlines()
                    sse_msg = "".join([f"data: {line}\n" for line in lines])
                    yield f"{sse_msg}\n"
                except asyncio.TimeoutError:
                    continue
        finally:
            subscribers.remove(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

async def run_dashboard(orch=None, host: str = "0.0.0.0", port: int = 8000):
    global orchestrator
    orchestrator = orch

    demo = build_gradio_ui()
    # In Gradio 6.0+, title and theme moved from Blocks constructor to launch/mount
    gr.mount_gradio_app(app, demo, path="/", title="Aurelia Vale Control Panel", theme=gr.themes.Soft())

    import uvicorn
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()
