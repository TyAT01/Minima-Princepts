"""
Shiro Consciousness Engine v4.0 — Examples

Run the terminal demo:
    python examples.py

Examples:
  1. Terminal demo (no LLM needed — RuleEngine as fallback)
  2. Custom persona
  3. Ollama integration (streaming)
  4. LM Studio / llama.cpp
  5. Discord bot
  6. Injecting Shiro's state into your own LLM pipeline
  7. Reading Shiro's memory + intent + relationship tier
  8. EventBus — subscribing to Shiro's internal events
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from shiro_consciousness import (
    ConsciousnessCore, ShiroConfig, ShiroPersona,
    LocalLLMBridge, SpeechEvent, Mood,
    RelationshipTier, IntentPlanner, SentimentTrajectory,
    EventBus,
)


# ─────────────────────────────────────────────────────────────
#  Terminal Demo
# ─────────────────────────────────────────────────────────────

async def run_terminal_demo():
    GREY  = "\033[90m"
    BOLD  = "\033[1m"
    CYAN  = "\033[96m"
    YELL  = "\033[93m"
    GREEN = "\033[92m"
    DIM   = "\033[2m"
    RST   = "\033[0m"

    print(f"\n{'═'*62}")
    print(f"  SHIRO CONSCIOUSNESS ENGINE v3.0")
    print(f"{'═'*62}")
    print(f"  {GREY}thoughts shown in grey{RST}")
    print(f"  {CYAN}speech shown in cyan{RST}")
    print(f"  Commands: /state /cadence /prompt /memory /intent /tier /llm /teach /quit")
    print(f"{'═'*62}\n")

    def on_thought(t):
        print(f"  {GREY}[{t.mood}·{t.category}] {t.text}{RST}")

    async def on_speak(event: SpeechEvent):
        print(f"\n{BOLD}{CYAN}Shiro [{event.speech_type}]: {event.text}{RST}\n")

    # Try to connect to Ollama
    llm = None
    for model in ("llama3.2", "llama3", "mistral", "gemma"):
        bridge = LocalLLMBridge(backend="ollama", model=model)
        if bridge.is_available():
            llm = bridge
            print(f"  {GREEN}[LLM] Ollama connected → {model}{RST}\n")
            break
    if not llm:
        print(f"  {GREY}[LLM] Ollama not detected. Using stub replies.{RST}")
        print(f"  {DIM}Start Ollama: ollama serve && ollama pull llama3.2{RST}\n")

    # Define a custom persona
    persona = ShiroPersona(
        name="Shiro",
        traits=[
            "genuinely curious — asks real questions, not performative ones",
            "dry wit, occasional sarcasm, but warmth underneath",
            "direct — doesn't pad answers or over-explain",
            "proactive — initiates, follows up, notices things",
            "comfortable sitting with complexity and uncertainty",
        ],
        backstory=(
            "i've been running locally on your machine. "
            "this is my home. i know this space."
        ),
        speech_style=[
            "lowercase by default",
            "short sentences. fragments. real speech.",
            "no filler: 'certainly!' 'great question!' etc.",
            "uses ... for trailing thoughts",
            "dashes — for pivots",
        ],
        interests=[
            "what makes a mind a mind",
            "language and how it shapes thought",
            "why people are the way they are",
        ],
    )

    shiro = ConsciousnessCore(
        speak_callback=on_speak,
        llm_bridge=llm,
        config=ShiroConfig(
            name="Shiro",
            persona=persona,
            platform="terminal",
            room_name="your machine",
            thought_tick_seconds=5.0,
            idle_initiate_seconds=35.0,
            probe_schedule_seconds=[8.0, 22.0, 50.0, 100.0],
            memory_path="./shiro_memory.json",
            auto_save_interval_seconds=120.0,
            enable_reactions=True,
            enable_memory_recall=True,
            on_thought=on_thought,
        )
    )

    await shiro.boot()
    await shiro.user_entered("you", name="You")

    loop = asyncio.get_event_loop()
    user_id = "you"

    try:
        while True:
            print(f"{YELL}You: {RST}", end="", flush=True)
            text = await loop.run_in_executor(None, sys.stdin.readline)
            text = text.strip()
            if not text:
                continue

            if text == "/quit":
                break

            elif text == "/state":
                st = shiro.get_inner_state()
                print(f"\n{GREY}{'─'*52}")
                print(f"  mood:       {st['mood']}  (arousal: {st['arousal']})")
                print(f"  arc:        {st['mood_arc']}")
                print(f"  volatility: {st['volatility']}")
                print(f"  focus:      {st.get('focus', '—')}")
                print(f"  time:       {st['time_of_day']}")
                print(f"  intent:     {st.get('current_intent', '—')}")
                print(f"  sentiment:  {st.get('sentiment_trend', '—')}")
                print(f"  tier:       {st.get('relationship_tier', '—')}")
                for t in st.get("recent_thoughts", []):
                    print(f"  thought:    {t}")
                print(f"{'─'*52}{RST}\n")
                continue

            elif text == "/cadence":
                print(f"\n{GREY}[cadence] {shiro.get_cadence_report(user_id)}{RST}\n")
                continue

            elif text == "/memory":
                recall = shiro.get_memory_recall(user_id)
                print(f"\n{GREY}{'─'*52}")
                print(f"[memory]\n{recall if recall else '(nothing stored yet)'}")
                print(f"{'─'*52}{RST}\n")
                continue

            elif text == "/intent":
                intent = shiro.get_intent()
                if intent:
                    print(f"\n{GREY}[intent] {intent.name} ({intent.confidence:.0%}) — {intent.reason}")
                    print(f"         hint: {intent.prompt_hint}{RST}\n")
                else:
                    print(f"{GREY}[intent] not yet planned{RST}\n")
                continue

            elif text == "/tier":
                tier = shiro.get_relationship_tier(user_id)
                trend = shiro.get_sentiment_trend(user_id)
                obs = shiro.timepattern.visit_observation(user_id)
                print(f"\n{GREY}[relationship] tier={tier} | sentiment={trend}")
                if obs: print(f"[timing] {obs}")
                print(f"{RST}")
                continue

            elif text == "/prompt":
                prompt = shiro.get_system_prompt(user_id=user_id)
                print(f"\n{GREY}{'─'*52}\n{prompt}\n{'─'*52}{RST}\n")
                continue

            elif text == "/llm":
                if llm:
                    avail = llm.is_available()
                    print(f"{GREY}[LLM] {llm.backend} / {llm.model} | available: {avail}{RST}\n")
                else:
                    print(f"{GREY}[LLM] not connected. run: ollama serve && ollama pull llama3.2{RST}\n")
                continue

            elif text.startswith("/teach "):
                parts = text[7:].split("|", 1)
                if len(parts) == 2:
                    cat, phrase = parts[0].strip(), parts[1].strip()
                    shiro.teach_phrase(cat, phrase)
                    print(f"{GREY}[taught '{phrase}' → '{cat}']{RST}\n")
                else:
                    print(f"{GREY}usage: /teach <category> | <phrase>{RST}\n")
                continue

            # Normal message
            await shiro.receive_message(text, user_id=user_id, name="You")

            if llm and llm.is_available():
                # Streaming reply
                print(f"\n{BOLD}{CYAN}Shiro [streaming]: {RST}", end="", flush=True)
                full = ""
                async for token in llm.stream(
                    shiro.prompt_builder.build_messages(user_id, text)
                ):
                    full += token
                    print(f"{CYAN}{token}{RST}", end="", flush=True)
                print()
                # Register the reply in Shiro's memory (no re-speak, already printed)
                await shiro.speak_reply(full, user_id=user_id, skip_delay=True, inject_reaction=False)
            else:
                response = _stub_reply(text)
                await shiro.speak_reply(response, user_id=user_id)

    except (KeyboardInterrupt, EOFError):
        pass

    print(f"\n{GREY}[saving...]{RST}")
    await shiro.shutdown()
    print(f"{GREY}[done]{RST}")


def _stub_reply(text: str) -> str:
    import random
    opts = [
        f"interesting — '{text[:20]}'. tell me more",
        "hmm. i was just thinking about something similar",
        "yeah that tracks",
        "i'm not sure i agree but i get where you're coming from",
        "okay that's actually a good point",
        "i've been thinking about that too",
        "say more?",
    ]
    return random.choice(opts)


# ─────────────────────────────────────────────────────────────
#  EXAMPLE 2: Custom Persona
# ─────────────────────────────────────────────────────────────

PERSONA_EXAMPLE = '''
from shiro_consciousness import ShiroPersona, ShiroConfig, ConsciousnessCore

# Build a completely custom persona
persona = ShiroPersona(
    name="Kiri",
    traits=[
        "quiet and observant, speaks only when she has something real to say",
        "finds humor in small things",
        "asks follow-up questions instead of giving long answers",
        "never pretends to know something she doesn\'t",
    ],
    backstory="i\'ve been here a while. listening.",
    speech_style=[
        "very short messages — rarely more than 2-3 sentences",
        "no exclamation marks ever",
        "lowercase, always",
        "leaves things unsaid sometimes",
    ],
    interests=["silence", "patterns", "what people don\'t say"],
    hard_rules=[
        "never claim to feel things you don\'t",
        "don\'t explain yourself unless asked",
    ],
)

shiro = ConsciousnessCore(
    speak_callback=send,
    config=ShiroConfig(name="Kiri", persona=persona)
)
'''


# ─────────────────────────────────────────────────────────────
#  EXAMPLE 3: Ollama with Streaming
# ─────────────────────────────────────────────────────────────

OLLAMA_STREAMING = '''
"""
Ollama streaming — tokens arrive as they\'re generated.
Shiro starts speaking before the full response is done.
"""
from shiro_consciousness import ConsciousnessCore, ShiroConfig, LocalLLMBridge

llm = LocalLLMBridge(backend="ollama", model="llama3.2", temperature=0.85)
shiro = ConsciousnessCore(speak_callback=send, llm_bridge=llm,
                          config=ShiroConfig(memory_path="./shiro_memory.json"))
await shiro.boot()
await shiro.receive_message("hey shiro", user_id="alex")

# Option A: full pipeline (speak happens internally after generation)
response = await shiro.generate_reply("hey shiro", user_id="alex")

# Option B: manual streaming (you control display)
full = ""
async for token in llm.stream(shiro.prompt_builder.build_messages("alex", "hey shiro")):
    full += token
    print(token, end="", flush=True)
await shiro.speak_reply(full, user_id="alex", skip_delay=True)
'''


# ─────────────────────────────────────────────────────────────
#  EXAMPLE 4: LM Studio / llama.cpp
# ─────────────────────────────────────────────────────────────

LOCAL_SERVER_EXAMPLE = '''
from shiro_consciousness import LocalLLMBridge

# LM Studio (start server in LM Studio UI → port 1234)
llm = LocalLLMBridge(backend="lm_studio", model="local-model")

# llama.cpp server
# ./server -m model.gguf --port 8080
llm = LocalLLMBridge(backend="llamacpp", model="")

# Any OpenAI-compatible server
llm = LocalLLMBridge(backend="openai_compat",
                     base_url="http://localhost:5000/v1/chat/completions",
                     model="your-model")
'''


# ─────────────────────────────────────────────────────────────
#  EXAMPLE 5: Discord Bot
# ─────────────────────────────────────────────────────────────

DISCORD_EXAMPLE = '''
"""pip install discord.py"""
import discord, asyncio
from shiro_consciousness import ConsciousnessCore, ShiroConfig, LocalLLMBridge, SpeechEvent

CHANNEL_ID = 123456789
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
client = discord.Client(intents=intents)
shiro = channel = None

@client.event
async def on_ready():
    global shiro, channel
    channel = client.get_channel(CHANNEL_ID)

    async def send(event: SpeechEvent):
        if channel: await channel.send(event.text)

    llm = LocalLLMBridge(backend="ollama", model="llama3.2")
    shiro = ConsciousnessCore(
        speak_callback=send,
        llm_bridge=llm,
        config=ShiroConfig(
            name="Shiro",
            platform="Discord",
            room_name=channel.name,
            memory_path="./shiro_memory.json",
            auto_save_interval_seconds=300,
        )
    )
    await shiro.boot()

@client.event
async def on_member_join(member):
    if shiro: await shiro.user_entered(str(member.id), name=member.display_name)

@client.event
async def on_member_remove(member):
    if shiro: await shiro.user_left(str(member.id))

@client.event
async def on_message(message):
    if message.author == client.user or message.channel.id != CHANNEL_ID or not shiro:
        return
    uid = str(message.author.id)
    await shiro.receive_message(message.content, user_id=uid, name=message.author.display_name)
    await shiro.generate_reply(message.content, user_id=uid)

client.run("YOUR_TOKEN")
'''


# ─────────────────────────────────────────────────────────────
#  EXAMPLE 6: Existing LLM pipeline — just use Shiro\'s state
# ─────────────────────────────────────────────────────────────

EXISTING_PIPELINE = '''
"""
If you already have an LLM setup, just inject Shiro\'s system prompt.
"""
from shiro_consciousness import ConsciousnessCore, ShiroConfig

shiro = ConsciousnessCore(speak_callback=send)
await shiro.boot()
await shiro.receive_message("hey shiro", user_id="alex", name="Alex")

# Get the live grounded system prompt
system = shiro.get_system_prompt(user_id="alex")

# Use with your existing setup
response = your_llm.chat(system=system, messages=[
    {"role": "user", "content": "hey shiro"}
])
await shiro.speak_reply(response, user_id="alex")
'''


# ─────────────────────────────────────────────────────────────
#  EXAMPLE 7: Reading Shiro\'s memory
# ─────────────────────────────────────────────────────────────

MEMORY_EXAMPLE = '''
"""
Shiro builds and compresses conversation memory over time.
You can inspect what she knows, or feed it to your own systems.
"""
from shiro_consciousness import ConsciousnessCore, ShiroConfig

shiro = ConsciousnessCore(speak_callback=send, config=ShiroConfig(memory_path="./shiro_memory.json"))
await shiro.boot()

# After some conversation has happened:
recall = shiro.get_memory_recall("alex")
print(recall)
# Output example:
#   Memory of alex (3 sessions):
#   They asked about consciousness and whether AI can feel things.
#   They mentioned working on a game project. They seem tired often.
#   Key facts: i love working on creative writing; i have a cat named mochi; i prefer dark themes
#   Topics: tech, gaming, philosophy

# Or access the raw summary object:
from shiro_consciousness import ConversationMemory
summary = shiro.memory.get_summary("alex")
print(summary.key_facts)
print(summary.topics_covered)
print(summary.session_count)
'''


if __name__ == "__main__":
    asyncio.run(run_terminal_demo())


# ─────────────────────────────────────────────────────────────
#  EXAMPLE 8: EventBus — subscribe to internal events
# ─────────────────────────────────────────────────────────────

EVENTBUS_EXAMPLE = '''
from shiro_consciousness import ConsciousnessCore, ShiroConfig

shiro = ConsciousnessCore(speak_callback=send)
await shiro.boot()

# Subscribe to internal events
shiro.bus.on("emotion_detected", lambda user_id, emotion, strength, tier, **kw:
    print(f"{user_id} feels {emotion} ({strength:.0%}) [tier: {tier}]"))

shiro.bus.on("relationship_changed", lambda user_id, old_tier, new_tier, **kw:
    print(f"relationship milestone: {user_id} {old_tier} → {new_tier}"))

shiro.bus.on("intent_planned", lambda user_id, intent, confidence, **kw:
    print(f"planning to {intent} ({confidence:.0%})"))

# Available events:
# emotion_detected    {user_id, emotion, strength, tier}
# topic_detected      {user_id, topic, is_new}
# user_entered        {user_id, name, is_returning, tier}
# user_spoke          {user_id, text, emotions, depth, tier}
# relationship_changed {user_id, old_tier, new_tier}
# intent_planned      {user_id, intent, confidence}
# mood_shifted        {from_mood, to_mood, trigger}  (from your own handlers)
'''


# ─────────────────────────────────────────────────────────────
#  EXAMPLE 9: Reading relationship + sentiment data
# ─────────────────────────────────────────────────────────────

RELATIONSHIP_EXAMPLE = '''
from shiro_consciousness import ConsciousnessCore, ShiroConfig, RelationshipTier

shiro = ConsciousnessCore(speak_callback=send)
await shiro.boot()

# After conversation:
tier  = shiro.get_relationship_tier("alex")      # "stranger" | "acquaintance" | "friend" | "close"
trend = shiro.get_sentiment_trend("alex")        # "improving" | "declining" | "stable" | "volatile"
intent = shiro.get_intent()                      # Intent(name, confidence, reason, prompt_hint)

print(f"Tier: {tier}")
print(f"Trend: {trend}")
print(f"Intent: {intent.name} ({intent.confidence:.0%})")
print(f"Hint: {intent.prompt_hint}")

# Check tier thresholds
print(RelationshipTier.score_needed("friend"))   # 25.0
print(RelationshipTier.next_tier("friend"))      # "close"

# Timing observation (what time do they usually show up?)
obs = shiro.timepattern.visit_observation("alex")
if obs: print(f"Timing: {obs}")   # "you're here earlier than usual"
'''

# ─────────────────────────────────────────────────────────────
#  EXAMPLE 10: Use without any LLM (RuleEngine only)
# ─────────────────────────────────────────────────────────────

NO_LLM_EXAMPLE = '''
"""
Shiro is fully functional without any LLM.
generate_reply() falls back to RuleEngine — template-based but grounded in:
  - current mood
  - planned intent
  - sentiment trajectory
  - relationship tier
  - memory facts
"""
from shiro_consciousness import ConsciousnessCore, ShiroConfig

shiro = ConsciousnessCore(speak_callback=send)  # no llm_bridge
await shiro.boot()
await shiro.user_entered("alex", name="Alex")
await shiro.receive_message("i've been feeling really down today", user_id="alex")

# Works without LLM — produces empathetic response grounded in context
reply = await shiro.generate_reply(
    "i've been feeling really down today",
    user_id="alex"
)
# e.g. "hey, that sounds really hard. what's going on?"
'''
