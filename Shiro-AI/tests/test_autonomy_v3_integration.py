import pytest
import sys
import os
import time
import json
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime, timezone, timedelta

# Robust Mocking for missing heavy packages
def mock_package(name):
    m = MagicMock()
    sys.modules[name] = m
    return m

chromadb = mock_package('chromadb')
chromadb.config = mock_package('chromadb.config')
chromadb.utils = mock_package('chromadb.utils')
chromadb.utils.embedding_functions = mock_package('chromadb.utils.embedding_functions')
mock_package('sentence_transformers')
mock_package('googlesearch')
mock_package('ctranslate2')

# Add Shiro_ai to path
shiro_path = Path(__file__).parent.parent
sys.path.insert(0, str(shiro_path))

from autonomy_v3 import TrueAutonomy

def test_autonomy_v3_state_transitions():
    # Test state transitions directly in TrueAutonomy
    brain = {}
    autonomy = TrueAutonomy(name="TestShiro", brain_dict=brain)

    # Reset energy
    autonomy.energy = 50 # Start lower to test gains
    autonomy.mood = "hyper"

    # 1. Test energy drain
    # 120 seconds * 0.00833 = ~1.0
    autonomy.update_state(user_msg="", elapsed_seconds=120)
    assert autonomy.energy < 50
    assert autonomy.energy > 48.9

    # 2. Test user message energy gain
    prev_energy = autonomy.energy
    autonomy.update_state(user_msg="Hello Shiro", elapsed_seconds=1)
    # Gain 15, lose ~0.008
    assert autonomy.energy > prev_energy + 14.9

    # 3. Test mood shift to chaos_mode
    autonomy.energy = 90
    autonomy.update_state(user_msg="Let's do a trick!", elapsed_seconds=1)
    assert autonomy.mood == "chaos_mode"

    # 4. Test mood shift to sleepy
    autonomy.energy = 20
    autonomy.update_state(user_msg="", elapsed_seconds=1)
    assert autonomy.mood == "sleepy"

    # Check sync
    assert brain["autonomy_state"]["mood"] == "sleepy"

@patch("shiro_engine.MemoryStore")
@patch("shiro_engine.LlamaClient")
@patch("shiro_engine.PersonaManager")
def test_shiro_engine_integration(MockPersona, MockLLM, MockMemory):
    # Mock Persona
    mock_persona = MockPersona.return_value
    mock_persona.persona_data = {"persona": {"name": "Shiro"}}

    config = {
        "memory": {"db_path": "./test_mem", "collection_name": "test"},
        "llm": {"model": "test", "api_type": "ollama"},
        "persona": {"sheet_path": "test.yaml"}
    }

    # Patch _load_brain to avoid disk access or use a fake brain
    from shiro_engine import ShiroEngine
    mock_brain = {
        "personality": {"slyness": 0.7, "sass": 0.8, "greed": 0.7, "kindness": 0.3},
        "mood_history": [0.5],
        "facts": {},
        "favors": [],
        "trust": 0,
        "achievements": []
    }
    with patch.object(ShiroEngine, "_load_brain", return_value=mock_brain):
        engine = ShiroEngine(config)

        assert hasattr(engine, "autonomy")
        assert isinstance(engine.autonomy, TrueAutonomy)

        # Test process_text updates autonomy
        engine.last_interaction_time = datetime.now(timezone.utc) - timedelta(minutes=10)

        # Mock LLM and Memory methods used in process_text
        MockLLM.return_value.stream_response.return_value = ["Hello"]
        engine.memory.get_last_interaction_time.return_value = None
        engine.memory.get_full_context.return_value = "context"
        engine.memory.get_history.return_value = []
        engine.memory.add_interaction = MagicMock()

        # Async mocks for things called via _safe_async_run
        engine.memory.search_relevant_memories_async = AsyncMock(return_value=[])
        engine.memory.get_full_context_async = AsyncMock(return_value="context")
        engine.memory.store_insight_async = AsyncMock()

        # We also need to mock _load_profiles and other setup methods
        engine.user_profiles = {}
        engine.brain_file = Path("test_brain_eng.json")

        # Start at 50 energy for clear test
        engine.autonomy.energy = 50

        # Process some text
        # Mock threading.Thread to avoid side effects during test
        with patch("threading.Thread"):
            list(engine.process_text("test message", "user1"))

        # Energy should have increased (approx 50 + 15 - drain)
        assert engine.autonomy.energy > 60

def test_shiro_brain_persistence_compatibility():
    # Test that TrueAutonomy updates the shared brain dictionary
    original_brain = {
        "personality": {"slyness": 0.5},
        "facts": {"likes": "chaos"}
    }
    autonomy = TrueAutonomy(name="Shiro", brain_dict=original_brain)

    # Change state
    autonomy.mood = "chaos_mode"
    autonomy.energy = 88.5

    # Sync
    autonomy.sync_to_brain()

    assert original_brain["personality"]["slyness"] == 0.5
    assert original_brain["facts"]["likes"] == "chaos"
    assert original_brain["autonomy_state"]["mood"] == "chaos_mode"
    assert original_brain["autonomy_state"]["energy"] == 88.5
