from __future__ import annotations
import logging
import asyncio
import yaml
import time
import pandas as pd
import plotly.express as px
import gradio as gr
import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pathlib import Path
from config import settings
from hardware.profiler import HardwareProfiler

logger = logging.getLogger(__name__)

app = FastAPI(title="Aurelia Vale Dashboard (Personaplex)")

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
    history = history or []
    if not orchestrator:
        history.append({"role": "assistant", "content": "Error: Orchestrator not initialized."})
        return history, ""
    response = await orchestrator.process_text_input(message, "WebUser", "web")
    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": response})
    return history, ""

async def handle_audio(audio_path, history):
    history = history or []
    if not orchestrator or not audio_path:
        return history, None
    response = await orchestrator.process_audio_input(audio_path, "WebUser", "web")
    history.append({"role": "assistant", "content": response})
    return history, None

def get_system_metrics():
    profiler = HardwareProfiler()
    hw = profiler.detect()
    data = {
        "Metric": ["CPU Cores", "Total RAM (GB)", "VRAM (GB)"],
        "Value": [hw.cpu_count, hw.total_ram_gb, hw.vram_gb or 0]
    }
    df = pd.DataFrame(data)
    fig = px.bar(df, x="Metric", y="Value", title="System Hardware Profile (PP)", color="Metric")
    status_text = f"Status: Online (Personaplex Mode)\nModel: {settings.chroma_model_id}\nGPU: {hw.gpu_name or 'None'}"
    return fig, status_text

def update_settings(max_tokens, local_audio):
    settings.max_new_tokens = max_tokens
    settings.enable_local_audio = local_audio
    return "Settings updated."

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

# Personaplex specific functions
def list_persona_modules():
    modules_dir = Path(settings.persona_modules_dir)
    if not modules_dir.exists():
        return []
    return [f.stem for f in modules_dir.glob("*.yaml")]

def get_module_content(name):
    if not name: return ""
    modules_dir = Path(settings.persona_modules_dir)
    path = modules_dir / f"{name}.yaml"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return ""

def save_module_content(name, content):
    if not name or not content: return "Invalid input."
    # Prevent path traversal
    safe_name = os.path.basename(name)
    modules_dir = Path(settings.persona_modules_dir)
    path = modules_dir / f"{safe_name}.yaml"
    try:
        yaml.safe_load(content) # Validate YAML
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Module {name} saved successfully."
    except Exception as e:
        return f"Error saving module: {e}"

def activate_pp_module(name):
    if orchestrator and orchestrator.personaplex:
        orchestrator.personaplex.activate_module(name)
        # Force prompt regeneration if possible
        if hasattr(orchestrator, "_process_internal_commands"):
             # Mock a command to trigger regeneration
             orchestrator._process_internal_commands(f"[PERSONAPLEX: action=activate, name={name}]")
        return f"Activated {name}. System prompt updated."
    return "Orchestrator or Personaplex not initialized."

def deactivate_pp_module(name):
    if orchestrator and orchestrator.personaplex:
        orchestrator.personaplex.deactivate_module(name)
        if hasattr(orchestrator, "_process_internal_commands"):
             orchestrator._process_internal_commands(f"[PERSONAPLEX: action=deactivate, name={name}]")
        return f"Deactivated {name}."
    return "Orchestrator or Personaplex not initialized."

def build_gradio_ui():
    with gr.Blocks(title="Aurelia Personaplex Control Panel", theme=gr.themes.Soft()) as demo:
        gr.Markdown("# 🌸 Aurelia Vale - Personaplex Dashboard")

        with gr.Tabs():
            with gr.TabItem("💬 Chat"):
                chatbot = gr.Chatbot(label="Conversation")
                with gr.Row():
                    msg = gr.Textbox(placeholder="Type a message...", scale=4)
                    submit = gr.Button("Send", variant="primary")
                with gr.Row():
                    audio_input = gr.Audio(label="Voice Input", type="filepath")
                    audio_submit = gr.Button("Transcribe & Send")
                submit.click(handle_chat, [msg, chatbot], [chatbot, msg])
                msg.submit(handle_chat, [msg, chatbot], [chatbot, msg])
                audio_submit.click(handle_audio, [audio_input, chatbot], [chatbot, audio_input])

            with gr.TabItem("🎭 Personaplex"):
                with gr.Row():
                    with gr.Column(scale=1):
                        module_list = gr.Dropdown(label="Available Modules", choices=list_persona_modules())
                        refresh_list = gr.Button("Refresh List")
                        activate_btn = gr.Button("Activate", variant="primary")
                        deactivate_btn = gr.Button("Deactivate")
                    with gr.Column(scale=2):
                        module_editor = gr.Code(label="Module YAML Editor", language="yaml")
                        save_btn = gr.Button("Save Module Changes")

                pp_status = gr.Textbox(label="Personaplex Status")

                refresh_list.click(lambda: gr.update(choices=list_persona_modules()), outputs=module_list)
                module_list.change(get_module_content, module_list, module_editor)
                save_btn.click(save_module_content, [module_list, module_editor], pp_status)
                activate_btn.click(activate_pp_module, module_list, pp_status)
                deactivate_btn.click(deactivate_pp_module, module_list, pp_status)

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
    gr.mount_gradio_app(app, demo, path="/")
    import uvicorn
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()
