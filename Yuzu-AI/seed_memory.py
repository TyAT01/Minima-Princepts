import yaml
import logging
import sys
import os
from pathlib import Path

# Add the current directory to sys.path to ensure local imports work
sys.path.append(str(Path(__file__).parent))

from memory.store import MemoryStore

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def seed_yuzu():
    print("\n" + "="*50)
    print("🌟 YUZU PERSONALITY SEEDER 🌟")
    print("="*50 + "\n")

    config_path = Path(__file__).parent / "config.yaml"
    if not config_path.exists():
        print(f"❌ Error: {config_path} not found.")
        return

    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    mem_cfg = config.get('memory', {})
    # Use paths relative to this script
    db_path = Path(__file__).parent / mem_cfg.get('db_path', './yuzu_memory')
    collection_name = mem_cfg.get('collection_name', 'yuzu_ai_memories')

    print(f"📦 Initializing Memory Store at: {db_path}")
    memory = MemoryStore(db_path=db_path, collection_name=collection_name)

    seed_path = Path(__file__).parent / "yuzu_seed.yaml"
    if not seed_path.exists():
        print(f"❌ Error: {seed_path} not found.")
        return

    with open(seed_path, 'r', encoding='utf-8') as f:
        seed_data = yaml.safe_load(f)

    print(f"🌱 Seeding Yuzu's memory...")

    # Seed Insights
    insights = seed_data.get('insights', [])
    for insight in insights:
        memory.store_insight(insight, source="initial_seed")
        print(f"  ✅ Added Insight: {insight[:60]}...")

    # Seed Episodic Memories
    events = seed_data.get('episodic_memories', [])
    for event_data in events:
        desc = event_data.get('description')
        imp = event_data.get('importance', 5)
        memory.store_episodic_memory(event_description=desc, importance=imp)
        print(f"  ✅ Added Event: {desc[:60]}...")

    # Seed User Profile
    user_profile = seed_data.get('user_profile', {})
    user_id = user_profile.get('user_id', 'Tyler')
    facts = user_profile.get('facts', [])
    for fact in facts:
        memory.update_user_profile(user_id, fact)
        print(f"  ✅ Added Profile Fact ({user_id}): {fact[:60]}...")

    total_items = memory.count()
    print("\n" + "="*50)
    print(f"✨ SUCCESS: Yuzu's mind has been seeded.")
    print(f"📊 Total memories in database: {total_items}")
    print("="*50 + "\n")

if __name__ == "__main__":
    seed_yuzu()
