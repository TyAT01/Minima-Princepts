import os
import sys
import yaml
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

# Add the current directory to sys.path
sys.path.append(str(Path(__file__).parent))
from pydantic import BaseModel
import uvicorn
from shiro_engine import ShiroEngine

# Load config to initialize engine
def load_config():
    path = "config.yaml"
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

config = load_config()
engine = ShiroEngine(config)
engine.initialize()

app = FastAPI(title="Shiro – Sly Kitsune Yaoguai")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Msg(BaseModel):
    message: str
    user_name: str = "Stranger"

@app.get("/", response_class=HTMLResponse)
def get_ui():
    ui_path = Path(__file__).parent / "ui" / "index.html"
    if ui_path.exists():
        return ui_path.read_text(encoding="utf-8")
    return "<h1>Shiro UI not found</h1>"

@app.post("/")
def chat(msg: Msg):
    try:
        # Join all fragments into one reply for the simple API
        reply_fragments = list(engine.process_text(msg.message, user_name=msg.user_name))
        reply = " ".join(reply_fragments).strip()
        if not reply:
            reply = "I'm thinking... but I've got nothing to say to you, stranger."
        return {"reply": reply}
    except Exception as e:
        print(f"Server Error: {e}")
        return {"reply": "Hmph, the universe is being annoying. Try again, dummy!"}

if __name__ == "__main__":
    print("SHIRO UNLOCKED – tail swishing, ears twitching, kingdom secured")
    uvicorn.run(app, host="127.0.0.1", port=8000)
