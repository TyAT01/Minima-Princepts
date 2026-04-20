import asyncio
from memory.store import MemoryStore
from datetime import datetime, timezone, timedelta
import os
import shutil
import sys
from pathlib import Path

# Add parent directory to sys.path
sys.path.append(str(Path(__file__).resolve().parent.parent))

def test_reproduce_pruning_error():
    db_path = "./test_prune_db"
    if os.path.exists(db_path):
        shutil.rmtree(db_path)

    store = MemoryStore(db_path=db_path)

    # Add an interaction
    store.add_interaction("hello", "hi", user_id="test_user")

    print("Attempting to prune...")
    try:
        store.prune_old_memories(days=30)
        print("Pruning succeeded (unexpectedly?)")
    except Exception as e:
        print(f"Pruning failed as expected: {e}")

    if os.path.exists(db_path):
        shutil.rmtree(db_path)

if __name__ == "__main__":
    test_reproduce_pruning_error()