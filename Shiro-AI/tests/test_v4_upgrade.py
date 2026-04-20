
import os
import json
import math
import time
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple

# Use local import
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "persona"))
from inner_mind import ShiroInnerMind, ThoughtType, RelationshipLevel, ResponseStrategy

def test_v4_multi_user_upgrade():
    print("Running test_v4_multi_user_upgrade...")
    mind = ShiroInnerMind(verbose=False)

    # User 1
    mind.switch_user("user1")
    mind.process_input("My name is Alice")
    print(f"User 1 name: {mind.user_profile.name}")
    assert mind.user_profile.name == "Alice"
    mind.reflect_on_response("Nice to meet you Alice", "My name is Alice")

    # User 2
    mind.switch_user("user2")
    mind.process_input("My name is Bob")
    print(f"User 2 name: {mind.user_profile.name}")
    assert mind.user_profile.name == "Bob"
    assert "Alice" not in mind.user_profile.display_name()

    # Verify momentum and journals are independent
    mind.switch_user("user1")
    mind.emotional_momentum.record(0.8)
    assert mind.emotional_momentum.direction() > 0

    mind.switch_user("user2")
    assert mind.emotional_momentum.direction() == 0
    print("Passed test_v4_multi_user_upgrade")

def test_thought_type_suppression():
    print("Running test_thought_type_suppression...")
    mind = ShiroInnerMind(verbose=False)
    mind.switch_user("user1")

    # Persona check should fire if it's the first turn or not on cooldown
    mind.turn_count = 1
    # Mocking _persona_check to always return something
    mind._persona_check = lambda m, s: "Persona thought"

    thoughts = mind._think("test message", ["test"], "test strategy")
    persona_thoughts = [t for t in thoughts if t.thought_type == ThoughtType.PERSONA]
    assert len(persona_thoughts) == 1

    # Update cooldown
    for t in thoughts:
        mind._thought_type_last_turn[t.thought_type] = mind.turn_count

    # Next turn, Persona check should be on cooldown (cooldown=1)
    mind.turn_count = 2
    thoughts = mind._think("test message", ["test"], "test strategy")
    persona_thoughts = [t for t in thoughts if t.thought_type == ThoughtType.PERSONA]
    assert len(persona_thoughts) == 0
    print("Passed test_thought_type_suppression")

def test_persistence_v4():
    print("Running test_persistence_v4...")
    state_file = "test_shiro_state.json"
    if os.path.exists(state_file):
        os.remove(state_file)

    mind = ShiroInnerMind(verbose=False)

    mind.switch_user("user1")
    mind.user_profile.add_curiosity("What is your favorite color?")
    mind.emotional_momentum.record(0.5)
    mind._thought_type_last_turn[ThoughtType.PERSONA] = 10

    mind.save_state(state_file)

    # Reload
    new_mind = ShiroInnerMind(verbose=False)
    assert new_mind.load_state(state_file)

    new_mind.switch_user("user1")
    assert "What is your favorite color?" in new_mind.user_profile.curiosity_journal
    assert len(new_mind.emotional_momentum._valence_history) == 1
    assert new_mind._thought_type_last_turn[ThoughtType.PERSONA] == 10
    assert new_mind.active_user_id == "user1"

    if os.path.exists(state_file):
        os.remove(state_file)
    print("Passed test_persistence_v4")

if __name__ == "__main__":
    try:
        test_v4_multi_user_upgrade()
        test_thought_type_suppression()
        test_persistence_v4()
        print("\nAll tests passed successfully!")
    except AssertionError as e:
        print(f"\nTest failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nAn error occurred: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)