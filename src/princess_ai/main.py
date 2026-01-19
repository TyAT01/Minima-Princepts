"""Entry point for the Princess AI runtime."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from princess_ai.config import ProfileLoader
from princess_ai.config.runtime import RuntimeConfig
from princess_ai.context.builder import ContextBuilder
from princess_ai.emotion.engine import EmotionEngine
from princess_ai.hardware.profiler import AdaptiveResourceManager, HardwareProfiler
from princess_ai.input_adapters.text_adapter import TextInputAdapter
from princess_ai.learning.controller import LearningController
from princess_ai.llm.engine import (
    DummyEngine,
    LlamaCppServerConfig,
    LlamaCppServerEngine,
    OllamaConfig,
    OllamaEngine,
)
from princess_ai.memory.retrieval import MemoryRetriever
from princess_ai.memory.store import MemoryStore
from princess_ai.personality.layer import PersonalityLayer
from princess_ai.runtime.orchestrator import RuntimeDependencies, RuntimeOrchestrator
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

    memory_store = MemoryStore(Path("memory.sqlite"))
    deps = RuntimeDependencies(
        adapter=TextInputAdapter(),
        llm=llm_engine,
        memory_store=memory_store,
        memory_retriever=MemoryRetriever(),
        context_builder=ContextBuilder(),
        safety_filter=SafetyFilter(),
        personality_layer=PersonalityLayer(),
        emotion_engine=EmotionEngine(),
        inner_thought=InnerThought(),
        tool_router=ToolRouter(),
        learning_controller=LearningController(),
        use_streaming=runtime_config.use_streaming,
    )
    orchestrator = RuntimeOrchestrator(deps)
    await orchestrator.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Shutting down Princess AI.")


def _build_llm_engine(config: RuntimeConfig):
    engine = config.engine.lower().strip()
    if engine == "ollama":
        return OllamaEngine(
            OllamaConfig(base_url=config.ollama_url, model=config.ollama_model)
        )
    if engine == "dummy":
        return DummyEngine()
    return LlamaCppServerEngine(
        LlamaCppServerConfig(base_url=config.llama_cpp_url, model=config.llama_cpp_model)
    )
