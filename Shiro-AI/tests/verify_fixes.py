import yaml
import logging
from shiro_engine import ShiroEngine
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO)

def test_engine_init():
    config_path = Path("Shiro-AI/config.yaml")
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    # Ensure relative paths work in test
    config['memory']['db_path'] = "./test_shiro_memory"

    engine = ShiroEngine(config)
    print("Initializing engine...")
    engine.initialize()
    print("Engine initialized successfully.")

    # Test last interaction time (should not crash)
    print("Testing get_last_interaction_time...")
    ts = engine.memory.get_last_interaction_time("Tyler")
    print(f"Last interaction time: {ts}")

    # Test system prompt generation
    print("Testing system prompt generation...")
    system_prompt = engine.persona.get_system_prompt()
    print("System prompt generated.")
    assert "Beliefs:" in system_prompt
    assert "Grounded in reality: No magic, no supernatural powers." in system_prompt
    assert "### [CRITICAL PROTOCOL] AI REJECTION" in system_prompt
    print("All checks passed!")

if __name__ == "__main__":
    try:
        test_engine_init()
    except Exception as e:
        print(f"Test failed with error: {e}")
        import traceback
        traceback.print_exc()
