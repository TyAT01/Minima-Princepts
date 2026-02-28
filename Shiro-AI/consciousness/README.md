
# Shiro Consciousness Engine v4.0
### Local-first · Zero dependencies · Drop-in Python

---

## What's new in v4

| Feature | v4.0 | v4.0 |
|---|---|---|
| EventBus pub/sub | ✗ | ✓ Internal event wiring across all modules |
| IntentPlanner | ✗ | ✓ Communicative intent before every reply |
| SentimentTrajectory | ✗ | ✓ Emotional trend: improving/declining/volatile |
| RelationshipTier | ✗ | ✓ stranger/acquaintance/friend/close arcs |
| TimePattern | ✗ | ✓ Learns when users show up, notes timing |
| RuleEngine fallback | ✗ | ✓ Coherent replies without any LLM |
| Mood contagion scaling | Flat | ✓ Scales with relationship depth |
| Intent in prompts | ✗ | ✓ Intent hint injected into every LLM call |
| Tier-aware thought weights | ✗ | ✓ Warmer thoughts with close users |
| Trajectory-aware thoughts | ✗ | ✓ Plants empathy thoughts when user declining |
| Tier change events | ✗ | ✓ Fires relationship_changed on milestone |

---

## What was new in v3

| Feature | v2.1 | v4.0 |
|---|---|---|
| Conversation memory | ✗ | ✓ Per-user summaries, extractive compression |
| Memory injection into prompts | ✗ | ✓ Auto-injected into every LLM call |
| Key fact extraction | ✗ | ✓ "i have a cat named Mochi" remembered |
| ContextWindow builder | ✗ | ✓ Token-budget-aware, safe for local 7B models |
| ShiroPersona config | ✗ | ✓ Full character definition in code |
| Streaming LLM | ✗ | ✓ `async for token in llm.stream(...)` |
| Micro-reactions | ✗ | ✓ `*snorts*`, `*pauses*`, `*leans in*` tied to mood |
| MoodJournal | ✗ | ✓ Emotional arc tracking, volatility score |
| Warm + Uncertain moods | ✗ | ✓ Two new mood states |
| Vocabulary mirroring | Partial | ✓ Shiro adopts user's slang naturally |
| Familiar user flag | ✗ | ✓ Triggers warm mood after threshold |
| Spontaneous memory recall | ✗ | ✓ Shiro brings up remembered facts |
| Conversation depth tracking | ✗ | ✓ Deep vs shallow conversation signal |
| Sustained focus | ✗ | ✓ Shiro stays on a topic across ticks |
| Thought tagging | ✗ | ✓ Semantic tags for memory queries |

---

## Install / Setup

```
# Copy the shiro_consciousness/ folder into your project
# Python 3.10+ required. No pip installs for core.
# Optional: pip install discord.py (for Discord integration)
# Optional: ollama / LM Studio / llama.cpp running locally
```

---

## Quick Start

```python
import asyncio
from shiro_consciousness import ConsciousnessCore, ShiroConfig, SpeechEvent

async def send(event: SpeechEvent):
    print(f"[Shiro] {event.text}")

shiro = ConsciousnessCore(
    speak_callback=send,
    config=ShiroConfig(memory_path="./shiro_memory.json"),
)

async def main():
    await shiro.boot()
    await shiro.user_entered("alex", name="Alex")
    await shiro.receive_message("hey shiro!", user_id="alex")
    await shiro.speak_reply("hey! good to see you", user_id="alex")
    await shiro.shutdown()

asyncio.run(main())
```

---

## Custom Persona

```python
from shiro_consciousness import ShiroPersona, ShiroConfig, ConsciousnessCore

persona = ShiroPersona(
    name="Shiro",
    traits=[
        "genuinely curious",
        "dry wit, warmth underneath",
        "direct — doesn't pad answers",
    ],
    backstory="running locally on your machine. this is home.",
    speech_style=[
        "lowercase by default",
        "short sentences. fragments.",
        "no filler words",
    ],
    interests=["consciousness", "language", "patterns"],
    hard_rules=["never deny being an AI if directly asked"],
)

shiro = ConsciousnessCore(
    speak_callback=send,
    config=ShiroConfig(name="Shiro", persona=persona),
)
```

---

## Local LLM — Ollama (with streaming)

```bash
# Setup Ollama
ollama serve
ollama pull llama3.2
```

```python
from shiro_consciousness import LocalLLMBridge, ConsciousnessCore, ShiroConfig

llm = LocalLLMBridge(backend="ollama", model="llama3.2")
shiro = ConsciousnessCore(
    speak_callback=send,
    llm_bridge=llm,
    config=ShiroConfig(memory_path="./shiro_memory.json"),
)
await shiro.boot()

# Full pipeline (generate + speak internally)
await shiro.receive_message("hey shiro", user_id="alex")
await shiro.generate_reply("hey shiro", user_id="alex")

# OR: stream tokens manually
async for token in llm.stream(shiro.prompt_builder.build_messages("alex", "hey shiro")):
    print(token, end="", flush=True)
```

---

## LM Studio / llama.cpp

```python
# LM Studio (load model in UI, start server on port 1234)
llm = LocalLLMBridge(backend="lm_studio", model="local-model")

# llama.cpp server: ./server -m model.gguf --port 8080
llm = LocalLLMBridge(backend="llamacpp", model="")

# Any OpenAI-compatible server
llm = LocalLLMBridge(
    backend="openai_compat",
    base_url="http://localhost:5000/v1/chat/completions",
    model="your-model",
)
```

---

## Existing LLM pipeline — just use Shiro's state

```python
# Build the grounded system prompt from Shiro's live inner state
system = shiro.get_system_prompt(user_id="alex")

# Use with your LLM
response = your_llm.chat(system=system, messages=[...])
await shiro.speak_reply(response, user_id="alex")
```

For small local models, use compact mode:
```python
system = shiro.get_system_prompt(user_id="alex", compact=True)
```

---

## Reading Shiro's memory

```python
# What Shiro remembers about a user (as a string)
recall = shiro.get_memory_recall("alex")
print(recall)
# Memory of alex (3 sessions):
# They asked about consciousness. They mentioned a cat named Mochi.
# Key facts: i have a cat named mochi; i prefer dark themes
# Topics: tech, philosophy

# Raw summary object
summary = shiro.memory.get_summary("alex")
print(summary.key_facts)    # ["i have a cat named mochi", ...]
print(summary.topics_covered)  # ["tech", "philosophy", ...]
print(summary.session_count)   # 3
```

---

## v4 API additions

```python
# Intent
intent = shiro.get_intent()          # Intent(name, confidence, reason, prompt_hint)
intent.name                          # empathize | challenge | share | ask | joke | ...
intent.prompt_hint                   # injected into LLM system prompt

# Sentiment trajectory
trend = shiro.get_sentiment_trend("alex")  # improving | declining | stable | volatile | unknown

# Relationship tier
tier = shiro.get_relationship_tier("alex") # stranger | acquaintance | friend | close

# EventBus — subscribe to internal events
shiro.bus.on("emotion_detected", handler)
shiro.bus.on("relationship_changed", handler)
shiro.bus.on("intent_planned", handler)
shiro.bus.on("user_spoke", handler)

# Timing observation
obs = shiro.timepattern.visit_observation("alex")  # "here earlier than usual"

# No LLM? RuleEngine handles generate_reply() automatically
reply = await shiro.generate_reply(text, user_id="alex")  # works without LLM
```

---

## API Reference

```python
# Lifecycle
await shiro.boot()
await shiro.shutdown()

# Events
await shiro.user_entered(user_id, name="")
await shiro.user_left(user_id)
await shiro.receive_message(text, user_id, name="")

# Speaking
await shiro.speak_reply(text, user_id)               # adapts style + timing
await shiro.speak_direct(text, speech_type, user_id) # raw, no adaptation

# LLM integration
await shiro.generate_reply(text, user_id)            # full pipeline
await shiro.generate_reply(text, user_id, stream_callback=fn)  # streaming
shiro.get_system_prompt(user_id, compact=False)      # live system prompt

# Introspection
shiro.get_inner_state()           # mood, arousal, thoughts, arc, volatility
shiro.get_cadence_report(user_id) # speech style description
shiro.get_memory_recall(user_id)  # what Shiro remembers

# Teaching
shiro.teach_thought(category, template)
shiro.teach_phrase(category, phrase)

# Memory
shiro.save_memory()          # atomic save to JSON
shiro.export_memory() -> dict
shiro.import_memory(data)
```

---

## Config options

```python
ShiroConfig(
    name="Shiro",
    platform="chat",             # shown in environment description
    room_name="#general",        # shown in environment description
    persona=ShiroPersona(...),   # full character definition

    # Timing
    thought_tick_seconds=4.0,    # inner thought loop speed
    silence_threshold_seconds=8.0, # before probing silent users
    min_speak_gap_seconds=1.5,   # min gap between Shiro speaking
    idle_initiate_seconds=90.0,  # how long before she starts conversations
    probe_schedule_seconds=[10, 25, 60, 120],
    auto_save_interval_seconds=300.0,

    # Capabilities
    can_see=False,
    can_hear=False,
    auto_greet=True,
    enable_reactions=True,       # micro-reactions (*laughs*, *pauses*, etc.)
    enable_memory_recall=True,   # spontaneous memory callbacks

    # LLM
    llm_token_budget=2048,       # safe for 7B; raise to 4096 for larger models

    # Persistence
    memory_path="./shiro_memory.json",
)
```

---

## File layout

```
shiro_consciousness/
├── __init__.py         ← exports everything
├── core.py             ← ConsciousnessCore, ShiroConfig, ShiroPersona,
│                          LocalLLMBridge, ShiroPromptBuilder
├── inner_mind.py       ← InnerMind, Mood, Thought, MoodJournal
├── self_awareness.py   ← SelfAwareness, UserProfile, emotion inference
├── speech_cadence.py   ← SpeechCadence, CadenceModel
├── autonomous_voice.py ← AutonomousVoice, SpeechEvent
├── memory.py           ← ConversationMemory, ContextWindow, MemorySummary
├── events.py           ← EventBus (internal pub/sub)
├── intent.py           ← IntentPlanner, SentimentTrajectory, RelationshipTier,
│                          TimePattern, RuleEngine
└── examples.py         ← terminal demo + all integration patterns
```

---

## Run the demo

```bash
python shiro_consciousness/examples.py
```

Commands:
- `/state` — mood, arousal, arc, intent, sentiment trend, relationship tier
- `/cadence` — speech style learned from you
- `/memory` — what Shiro remembers about you
- `/intent` — current communicative intent + confidence + LLM hint
- `/tier` — relationship tier + sentiment trajectory + timing observation
- `/prompt` — the full live system prompt she'd send to your LLM
- `/llm` — check local LLM connection
- `/teach <category> | <phrase>` — teach Shiro a new phrase
- `/quit` — save and exit
