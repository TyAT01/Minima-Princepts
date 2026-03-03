import pytest
import re
from shiro_engine import ShiroEngine

def test_metadata_leak_suppression():
    config = {
        "memory": {"db_path": "./test_memory", "collection_name": "test_collection"},
        "llm": {"model": "test_model"},
        "persona": {"sheet_path": "Shiro-AI/shiro_sheet.yaml"}
    }
    engine = ShiroEngine(config)

    # Case 1: Metadata header followed by more metadata and then dialogue, streamed in chunks
    leaked_log = "[SHIRO INNER MIND v4 -- turn 35 -- 18h 17m 23s] neutral [V:+0. 42 a:+0. 58 d:+0. 28] | momentum: stable accept & counter-attack | rel: PRIMARY USER |\nHello there!"
    chunks = [leaked_log[i:i+10] for i in range(0, len(leaked_log), 10)]

    # Verify extraction
    extracted = list(engine._extract_thought_from_stream(chunks))
    combined_extracted = "".join(extracted).strip()

    assert "SHIRO INNER MIND" not in combined_extracted
    assert "momentum:" not in combined_extracted
    assert "V:+0. 42" not in combined_extracted
    assert "Hello there!" in combined_extracted

def test_clean_response_metadata_fragments():
    config = {
        "memory": {"db_path": "./test_memory", "collection_name": "test_collection"},
        "llm": {"model": "test_model"},
        "persona": {"sheet_path": "Shiro-AI/shiro_sheet.yaml"}
    }
    engine = ShiroEngine(config)

    # Fragmented leak that might pass through extraction if yielded early
    fragment = "[SHIRO INNER MIND v4 -- turn 35 -- 18h 17m 23s] neutral [V:+0. 42 a:+0. 58 d:+0. 28] | momentum: stable accept & counter-attack | rel: PRIMARY USER | Hi!"

    cleaned = engine._clean_response(fragment)

    assert "SHIRO INNER MIND" not in cleaned
    assert "momentum:" not in cleaned
    assert "V:+0. 42" not in cleaned
    assert "Hi!" in cleaned

if __name__ == "__main__":
    # Simple manual run
    pytest.main([__file__])
