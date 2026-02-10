import os
import sys
import yaml
from pathlib import Path
from fastapi import FastAPI

# Add the current directory to sys.path
sys.path.append(str(Path(__file__).parent))
from pydantic import BaseModel
import uvicorn
from loki_engine import LokiEngine

# Load config to initialize engine
def load_config():
    path = "config.yaml"
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

config = load_config()
engine = LokiEngine(config)
engine.initialize()

app = FastAPI(title="Loki – Tiny Hoodie Tyrant")

class Msg(BaseModel):
    message: str
    user_name: str = "Tyler"

@app.post("/")
def chat(msg: Msg):
    try:
        # Join all fragments into one reply for the simple API
        reply_fragments = list(engine.process_text(msg.message, user_name=msg.user_name))
        reply = " ".join(reply_fragments).strip()
        if not reply:
            reply = "I'm scheming... but I've got nothing to say to you, minion."
        return {"reply": reply}
    except Exception as e:
        print(f"Server Error: {e}")
        return {"reply": "LOKI DOES NOT FAIL. The universe is just being annoying. Try again, minion!"}

if __name__ == "__main__":
    print("LOKI UNLOCKED – hoodie up, ears flopping, kingdom secured")
    uvicorn.run(app, host="127.0.0.1", port=8000)
