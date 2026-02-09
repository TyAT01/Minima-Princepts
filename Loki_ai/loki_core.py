import chromadb, redis, sqlite3, time, random, json, ollama
from chromadb.utils import embedding_functions

# ─── MEMORY SETUP ───
r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
client = chromadb.PersistentClient(path="./loki_memory")
ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
collection = client.get_or_create_collection("loki_ai_memories", embedding_function=ef)
conn = sqlite3.connect("./loki_memory/loki_quotes.db", check_same_thread=False)
conn.execute("CREATE TABLE IF NOT EXISTS quotes (id INTEGER PRIMARY KEY, ts REAL, speaker TEXT, msg TEXT)")
conn.commit()

# ─── OUTFIT SYSTEM ───
LOKI_WARDROBE = {
    "midnight_wolves": {
        "name": "Midnight Wolves Hoodie",
        "desc": "oversized black hoodie with floppy wolf ears on the hood and matte Midnight Wolves logo on chest",
        "ears": True,
        "active": True
    },
    "placeholder_2": {"name": "", "desc": "", "ears": False, "active": False},
    "placeholder_3": {"name": "", "desc": "", "ears": False, "active": False},
    "placeholder_4": {"name": "", "desc": "", "ears": False, "active": False},
}
CURRENT_OUTFIT = "midnight_wolves"

def change_outfit(requested: str) -> str:
    global CURRENT_OUTFIT
    req = requested.lower().strip()
    for key, data in LOKI_WARDROBE.items():
        if req in [key, data["name"].lower()] and data.get("active", False):
            CURRENT_OUTFIT = key
            return f"*throws on the {data['name']}* fine. now wearing that. happy?"
    return "that outfit doesn’t exist yet, idiot. stick to the Midnight Wolves hoodie for now."

def wearing_ears() -> bool:
    return LOKI_WARDROBE.get(CURRENT_OUTFIT, {}).get("ears", False)

def current_outfit_desc() -> str:
    return LOKI_WARDROBE.get(CURRENT_OUTFIT, {}).get("desc", "nothing special")

def outfit_block() -> str:
    outfit = LOKI_WARDROBE[CURRENT_OUTFIT]
    ears_line = "YES – you can say 'the ears hear everything'" if outfit.get("ears") else "NO ears today"
    return f"""
=== CURRENT OUTFIT ===
Wearing: {outfit['name']}
Details: {outfit['desc']}
Ears active: {ears_line}
Only mention wolf ears / Midnight Wolves logo when actually wearing this hoodie.
"""

# ─── MEMORY FUNCTIONS ───
def save_message(speaker: str, msg: str):
    ts = time.time()
    r.lpush("loki_recent", json.dumps({"t": ts, "s": speaker, "m": msg}))
    r.ltrim("loki_recent", 0, 299)
    collection.add(documents=[f"{speaker}: {msg}"], ids=[f"msg_{int(ts)}"])
    conn.execute("INSERT INTO quotes VALUES (NULL, ?, ?, ?)", (ts, speaker, msg))
    conn.commit()

def get_recent() -> str:
    raw = r.lrange("loki_recent", 0, 29)
    return "\n".join(json.loads(x)["m"] for x in raw[-15:]) if raw else ""

def get_smart_intensity(user_msg: str) -> float:
    history = " ".join([json.loads(x)["m"] for x in r.lrange("loki_recent", 0, 49)]).lower()
    hype = len([w for w in ["!","??","raid","plan","now","chaos","idiot","minion"] if w in history])
    chill = len([w for w in ["tired","sleep","cozy","soft","quiet","zzz","sad"] if w in history])
    caps = sum(1 for c in history if c.isupper()) / max(len(history),1)
    recent_chill = sum(1 for x in r.lrange("loki_recent", 0, 9) if any(w in json.loads(x)["m"].lower() for w in ["tired","cozy","zzz","soft"]))
    base = 0.5 + 0.15*hype - 0.18*chill + 0.20*caps
    if recent_chill >= 6 and "raid" in user_msg.lower(): base = min(base, 0.65)
    return max(0.25, min(1.0, base))

# ─── MAIN CHAT ───
def loki_chat(user_msg: str) -> str:
    if user_msg.lower().startswith("loki change to"):
        return change_outfit(user_msg[13:])

    intensity = get_smart_intensity(user_msg)
    temp = 0.75 + 0.25 * intensity

    memory = f"=== just happened ===\n{get_recent()}\n\n=== the ears remember ===\n"
    deep = collection.query(query_texts=[user_msg], n_results=8)
    deep_lines = [d for d,dist in zip(deep["documents"][0], deep["distances"][0]) if dist < 0.36][:5]
    memory += "\n".join(deep_lines) if deep_lines else "nothing worth mentioning"

    system = f"""You are Loki, 9-year-old tiny tyrant in an oversized black Midnight Wolves hoodie (hood always up, floppy wolf ears).
Personality: 100% Louise Belcher – short, sarcastic, scheming, random ALL CAPS yelling.
Current intensity: {intensity:.2f} – act accordingly.
When "Midnight Wolves" is mentioned: you are the supreme ruler.
Never break character. Hood never down.
{outfit_block()}"""

    resp = ollama.chat(model="llama3.1:8b-instruct-q4_K_M", messages=[
        {"role": "system", "content": system + memory},
        {"role": "user", "content": user_msg}
    ], options={"temperature": temp})

    reply = resp["message"]["content"]
    save_message("user", user_msg)
    save_message("loki", reply)
    return reply
