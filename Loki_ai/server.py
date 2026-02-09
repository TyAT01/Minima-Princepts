from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
from loki_core import loki_chat

app = FastAPI(title="Loki – Tiny Hoodie Tyrant")

class Msg(BaseModel):
    message: str

@app.post("/")
def chat(msg: Msg):
    reply = loki_chat(msg.message)
    return {"reply": reply}

if __name__ == "__main__":
    print("LOKI UNLOCKED – hoodie up, ears flopping, kingdom secured")
    uvicorn.run(app, host="127.0.0.1", port=8000)
