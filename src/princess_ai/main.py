"""Entry point for the Aurelia Vale runtime."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from princess_ai.config import ProfileLoader
from princess_ai.config.runtime import RuntimeConfig
from princess_ai.context.builder import ContextBuilder, Persona
from princess_ai.emotion.engine import EmotionEngine
from princess_ai.hardware.profiler import AdaptiveResourceManager, HardwareProfiler
from princess_ai.input_adapters.discord_voice import DiscordVoiceAdapter, DiscordTranscriptAdapter
from princess_ai.input_adapters.multi import MultiInputAdapter
from princess_ai.input_adapters.text_adapter import TextInputAdapter
from princess_ai.input_adapters.twitch import TwitchChatAdapter, TwitchLogAdapter
from princess_ai.input_adapters.youtube import YouTubeChatAdapter, YouTubeLogAdapter
from princess_ai.learning.controller import LearningController
from princess_ai.logging.telemetry import InMemoryLogStore
from princess_ai.llm.engine import (
    HeuristicEngine,
    LlamaCppServerConfig,
    LlamaCppServerEngine,
    OllamaConfig,
    OllamaEngine,
)
from princess_ai.memory.policy import MemoryPolicy
from princess_ai.memory.retrieval import MemoryRetriever
from princess_ai.memory.store import MemoryStore
from princess_ai.personality.layer import PersonaPolicy, PersonalityLayer
from princess_ai.personality.loader import PersonalityLoader, PersonalityProfile
from princess_ai.runtime.control import ControlHub
from princess_ai.runtime.event_router import EventRouter
from princess_ai.runtime.orchestrator import RuntimeDependencies, RuntimeOrchestrator
from princess_ai.runtime.session import SessionManager
from princess_ai.safety.filter import SafetyFilter
from princess_ai.thought.inner import InnerThought
from princess_ai.tools.router import ToolRouter


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    runtime_config = RuntimeConfig.from_env()
    profiler = HardwareProfiler()
    resource_manager = AdaptiveResourceManager(profiler)
    profiles = ProfileLoader(Path(__file__).parent / "config" / "profiles.json").load()
    active_profile = profiles.get(resource_manager.choose_profile(), profiles["default"])
    print(f"Selected profile: {active_profile.name}")
    llm_engine = _build_llm_engine(runtime_config)
    personality_profile = _load_personality_profile()

    memory_store = MemoryStore(Path("memory.sqlite"))
    control_hub = ControlHub()
    session_manager = SessionManager()
    log_store = InMemoryLogStore()
    adapter = _build_adapter()
    deps = RuntimeDependencies(
        adapter=adapter,
        llm=llm_engine,
        memory_store=memory_store,
        memory_retriever=MemoryRetriever(),
        memory_policy=MemoryPolicy(memory_store),
        context_builder=ContextBuilder(),
        safety_filter=SafetyFilter(),
        personality_layer=PersonalityLayer(
            personality_profile.policy, persona_name=personality_profile.persona.name
        ),
        emotion_engine=EmotionEngine(),
        inner_thought=InnerThought(response_marker=personality_profile.persona.response_marker),
        tool_router=ToolRouter(),
        learning_controller=LearningController(),
        event_router=EventRouter(),
        session_manager=session_manager,
        control_hub=control_hub,
        log_store=log_store,
        use_streaming=runtime_config.use_streaming,
        persona=personality_profile.persona,
    )
    orchestrator = RuntimeOrchestrator(deps)
    await orchestrator.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Shutting down Aurelia Vale AI.")


def _build_llm_engine(config: RuntimeConfig):
    engine = config.engine.lower().strip()
    if engine == "ollama":
        return OllamaEngine(
            OllamaConfig(base_url=config.ollama_url, model=config.ollama_model)
        )
    if engine in {"dummy", "heuristic"}:
        return HeuristicEngine()
    return LlamaCppServerEngine(
        LlamaCppServerConfig(base_url=config.llama_cpp_url, model=config.llama_cpp_model)
    )


def _load_personality_profile() -> PersonalityProfile:
    logger = logging.getLogger(__name__)
    sheet_path = Path(__file__).parent / "aurelia_sheet.yaml"
    loader = PersonalityLoader(sheet_path)
    try:
        return loader.load()
    except Exception as exc:  # noqa: BLE001 - fallback to defaults
        logger.exception("Failed to load personality sheet: %s", exc)
        return PersonalityProfile(persona=Persona(), policy=PersonaPolicy())


def _build_adapter() -> MultiInputAdapter:
    adapters = [
        TextInputAdapter(),
        DiscordVoiceAdapter(),
        DiscordTranscriptAdapter(),
        TwitchChatAdapter(),
        TwitchLogAdapter(),
        YouTubeChatAdapter(),
        YouTubeLogAdapter(),
    ]
    return MultiInputAdapter(adapters)
