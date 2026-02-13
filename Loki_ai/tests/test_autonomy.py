import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from loki_engine import LokiEngine
from datetime import datetime, timezone, timedelta
import json

@pytest.fixture
def config():
    return {
        "memory": {"db_path": "./test_loki_memory", "collection_name": "test_collection"},
        "llm": {"model": "loki:latest", "api_type": "ollama"},
        "persona": {"sheet_path": "loki_sheet.yaml"}
    }

@pytest.fixture
def mock_brain_data():
    return {
        "personality": {
            "menace": 0.5,
            "sarcasm": 0.5,
            "loyalty": 0.5,
            "softness": 0.5
        },
        "facts": {},
        "roasts": [],
        "mood_history": [],
        "autonomous_thoughts": {"date": "2024-01-01", "count": 0},
        "trust": 0,
        "achievements": []
    }

@pytest.mark.asyncio
async def test_fetch_relevant_memory_async(config):
    with patch("loki_engine.MemoryStore") as MockMemory:
        mock_mem = MockMemory.return_value
        mock_mem.search_relevant_memories_async = AsyncMock(return_value=[{"content": "past scheme"}])

        engine = LokiEngine(config)
        memories = await engine.fetch_relevant_memory_async("test query")

        assert memories == ["past scheme"]
        mock_mem.search_relevant_memories_async.assert_called_once()

@pytest.mark.asyncio
async def test_handle_tool_calls_async(config):
    with patch("loki_engine.LlamaClient") as MockLLM:
        mock_llm = MockLLM.return_value
        mock_llm.generate_response_async = AsyncMock(return_value="Final scheme with results")

        engine = LokiEngine(config)

        # Mock googlesearch
        with patch("googlesearch.search", return_value=["result1", "result2"]):
            tool_fragment = 'TOOL_CALLS: [{"function": {"name": "search_pranks", "arguments": {"query": "test prank"}}}]'
            result = await engine.handle_tool_calls_async(tool_fragment, "user msg")

            assert result == "Final scheme with results"
            mock_llm.generate_response_async.assert_called_once()

@pytest.mark.asyncio
async def test_check_and_think_async_triggered(config, mock_brain_data):
    with patch("loki_engine.MemoryStore") as MockMemory, \
         patch("loki_engine.LlamaClient") as MockLLM:

        mock_mem = MockMemory.return_value
        mock_mem.get_full_context_async = AsyncMock(return_value="context")
        mock_mem.store_insight_async = AsyncMock()

        mock_llm = MockLLM.return_value
        mock_llm.generate_response_async = AsyncMock(return_value="New thought")

        engine = LokiEngine(config)
        engine._load_brain = MagicMock(return_value=mock_brain_data)

        # Inactivity triggered
        last_interaction = datetime.now(timezone.utc) - timedelta(minutes=11)
        await engine.check_and_think_async("user1", last_interaction)

        mock_mem.store_insight_async.assert_called_once()
        assert "New thought" in mock_mem.store_insight_async.call_args[0][0]

@pytest.mark.asyncio
async def test_check_and_think_async_capped(config, mock_brain_data):
    with patch("loki_engine.MemoryStore") as MockMemory, \
         patch("loki_engine.LlamaClient") as MockLLM:

        mock_mem = MockMemory.return_value
        mock_mem.store_insight_async = AsyncMock()

        engine = LokiEngine(config)
        mock_brain_data["autonomous_thoughts"] = {"date": datetime.now(timezone.utc).strftime('%Y-%m-%d'), "count": 5}
        engine._load_brain = MagicMock(return_value=mock_brain_data)

        last_interaction = datetime.now(timezone.utc) - timedelta(minutes=11)
        await engine.check_and_think_async("user1", last_interaction)

        mock_mem.store_insight_async.assert_not_called()

@pytest.mark.asyncio
async def test_autonomous_response_async(config):
    with patch("loki_engine.MemoryStore") as MockMemory, \
         patch("loki_engine.LlamaClient") as MockLLM:

        mock_mem = MockMemory.return_value
        mock_mem.search_relevant_memories_async = AsyncMock(return_value=[{"content": "mem1"}])
        mock_mem.add_interaction_async = AsyncMock()
        mock_mem.get_history = MagicMock(return_value=[])

        mock_llm = MockLLM.return_value
        mock_llm.generate_response_async = AsyncMock(return_value="Autonomous reply")

        engine = LokiEngine(config)
        response = await engine.autonomous_response_async("Hello", "user1")

        assert response == "Autonomous reply"
        mock_mem.add_interaction_async.assert_called_once()

def test_process_text_updates_interaction_time(config, mock_brain_data):
    with patch("loki_engine.MemoryStore") as MockMemory, \
         patch("loki_engine.LlamaClient") as MockLLM:

        mock_mem = MockMemory.return_value
        mock_mem.get_last_interaction_time.return_value = None
        mock_mem.search_relevant_memories_async = AsyncMock(return_value=[])
        mock_mem.get_full_context.return_value = "context"
        mock_mem.get_history.return_value = []
        mock_mem.add_interaction.return_value = None

        mock_llm = MockLLM.return_value
        mock_llm.stream_response = MagicMock(return_value=["Hi"])

        engine = LokiEngine(config)
        engine._load_brain = MagicMock(return_value=mock_brain_data)
        old_time = engine.last_interaction_time

        import time
        time.sleep(0.01)

        with patch("threading.Thread"):
             list(engine.process_text("Hello", "user1"))

        assert engine.last_interaction_time > old_time

def test_tool_usage_cap(config, mock_brain_data):
    with patch("loki_engine.MemoryStore") as MockMemory, \
         patch("loki_engine.LlamaClient") as MockLLM:

        mock_mem = MockMemory.return_value
        mock_mem.get_last_interaction_time.return_value = None
        mock_mem.search_relevant_memories_async = AsyncMock(return_value=[])
        mock_mem.get_full_context.return_value = "context"
        mock_mem.get_history.return_value = []

        mock_llm = MockLLM.return_value
        mock_llm.stream_response = MagicMock(return_value=['TOOL_CALLS: [{"function": {"name": "search_pranks", "arguments": {"query": "prank"}}}]'])

        engine = LokiEngine(config)
        engine._load_brain = MagicMock(return_value=mock_brain_data)
        engine.session_tool_count = 2 # Cap reached

        engine.handle_tool_calls_async = AsyncMock()

        list(engine.process_text("Hello", "user1"))

        engine.handle_tool_calls_async.assert_not_called()
