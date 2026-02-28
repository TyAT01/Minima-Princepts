"""
Shiro Consciousness Engine v4.0
=================================
Local-first. Zero external dependencies. Drop-in Python package.

Quick start:
    from shiro_consciousness import ConsciousnessCore, ShiroConfig

    async def send(event):
        print(f"[Shiro] {event.text}")

    shiro = ConsciousnessCore(speak_callback=send)
    await shiro.boot()

With local LLM + custom persona:
    from shiro_consciousness import (
        ConsciousnessCore, ShiroConfig, ShiroPersona, LocalLLMBridge
    )
    persona = ShiroPersona(name="Shiro", traits=["curious", "direct", "warm"])
    llm = LocalLLMBridge(backend="ollama", model="llama3.2")
    shiro = ConsciousnessCore(
        speak_callback=send,
        llm_bridge=llm,
        config=ShiroConfig(persona=persona, memory_path="./shiro_memory.json"),
    )

Without any LLM (uses RuleEngine):
    shiro = ConsciousnessCore(speak_callback=send)
    await shiro.boot()
    await shiro.receive_message("hey shiro", user_id="alex")
    await shiro.generate_reply("hey shiro", user_id="alex")  # works without LLM

v4 additions:
    EventBus      — internal pub/sub wiring all modules
    IntentPlanner — communicative intent before every reply
    SentimentTrajectory — emotional trend tracking
    RelationshipTier — named arcs (stranger/acquaintance/friend/close)
    TimePattern   — learns when users show up
    RuleEngine    — coherent LLM-free responses
"""

from .core import (
    ConsciousnessCore,
    ShiroConfig,
    ShiroPersona,
    LocalLLMBridge,
    ShiroPromptBuilder,
)
from .inner_mind import InnerMind, Mood, Thought, MoodJournal
from .self_awareness import SelfAwareness, UserProfile
from .speech_cadence import SpeechCadence, CadenceModel
from .autonomous_voice import AutonomousVoice, SpeechEvent
from .memory import ConversationMemory, ContextWindow, MemorySummary
from .events import EventBus
from .intent import (
    IntentPlanner, Intent,
    SentimentTrajectory,
    RelationshipTier,
    TimePattern,
    RuleEngine,
)

__all__ = [
    # Core
    "ConsciousnessCore",
    "ShiroConfig",
    "ShiroPersona",
    "LocalLLMBridge",
    "ShiroPromptBuilder",
    # Mind
    "InnerMind",
    "Mood",
    "Thought",
    "MoodJournal",
    # Awareness
    "SelfAwareness",
    "UserProfile",
    # Cadence
    "SpeechCadence",
    "CadenceModel",
    # Voice
    "AutonomousVoice",
    "SpeechEvent",
    # Memory
    "ConversationMemory",
    "ContextWindow",
    "MemorySummary",
    # Events
    "EventBus",
    # Intent & planning
    "IntentPlanner",
    "Intent",
    "SentimentTrajectory",
    "RelationshipTier",
    "TimePattern",
    "RuleEngine",
]

__version__ = "4.0.0"
