import unittest
import shutil
import os
from pathlib import Path
from memory.store import ChromaMemoryStore

class TestLearning(unittest.TestCase):
    def setUp(self):
        import time
        # Use a unique collection name to avoid crosstalk between tests
        unique_name = f"test_col_{int(time.time() * 1000)}"
        self.store = ChromaMemoryStore(db_path=None, collection_name=unique_name)

    def tearDown(self):
        pass

    def test_store_and_search_interaction(self):
        self.store.store_memory("Hello Aurelia", "Greetings traveler!")

        # Search all
        results = self.store.search("Hello")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["type"], "interaction")

        # Search with filter
        results = self.store.search("Hello", filter_type="interaction")
        self.assertEqual(len(results), 1)

        results = self.store.search("Hello", filter_type="insight")
        self.assertEqual(len(results), 0)

    def test_store_and_search_insight(self):
        self.store.store_insight("I should be more polite to knights.", "test_source")

        # Search all
        results = self.store.search("knights")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["type"], "insight")

        # Search with filter
        results = self.store.search("knights", filter_type="insight")
        self.assertEqual(len(results), 1)

        results = self.store.search("knights", filter_type="interaction")
        self.assertEqual(len(results), 0)

    def test_mixed_search(self):
        self.store.store_memory("What is a knight?", "A brave soul.")
        self.store.store_insight("Knights are brave.", "reflection")

        # Search for 'brave'
        results = self.store.search("brave", n_results=10)
        self.assertEqual(len(results), 2)

        # Filter interactions
        results = self.store.search("brave", filter_type="interaction")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["type"], "interaction")

        # Filter insights
        results = self.store.search("brave", filter_type="insight")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["type"], "insight")

    def test_backward_compatibility(self):
        # Manually add a document without 'type' metadata (simulating old data)
        now = "2023-01-01T00:00:00Z"
        self.store._collection.add(
            ids=["old-memory"],
            documents=["old interaction"],
            metadatas=[{"user_text": "old", "bot_text": "data", "created_at": now}]
        )

        # Searching for 'old' with filter_type="interaction" should still return it
        results = self.store.search("old", filter_type="interaction")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["user_text"], "old")
        # Check that it doesn't have a 'type' (or it has whatever it had)
        self.assertIsNone(results[0].get("type"))

if __name__ == "__main__":
    unittest.main()
