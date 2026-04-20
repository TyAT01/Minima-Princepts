import yaml
import logging
import sys
from pathlib import Path

# Add Shiro-AI to path
sys.path.insert(0, str(Path("Shiro-AI").resolve()))

from persona.manager import PersonaManager

# Setup logging
logging.basicConfig(level=logging.INFO)

def test_persona_manager():
    print("Testing PersonaManager...")
    manager = PersonaManager(sheet_path="Shiro-AI/shiro_sheet.yaml")
    manager.load_persona()

    system_prompt = manager.get_system_prompt()
    print("System prompt generated.")
    # print(system_prompt)

    assert "Beliefs:" in system_prompt
    assert "Grounded in reality: No magic, no supernatural powers." in system_prompt
    assert "### [CRITICAL PROTOCOL] AI REJECTION" in system_prompt
    assert "Condition: User mentions AI, Bot, or LLM." in system_prompt

    print("PersonaManager checks passed!")

if __name__ == "__main__":
    try:
        test_persona_manager()
    except Exception as e:
        print(f"Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)