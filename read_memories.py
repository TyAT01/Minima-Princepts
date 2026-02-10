import chromadb
from pathlib import Path

db_path = Path("Loki_ai/loki_memory").resolve()
client = chromadb.PersistentClient(path=str(db_path))
collection = client.get_collection("loki_ai_memories")

results = collection.get(
    where={"source": "inner_monologue"},
    limit=10
)

for i in range(len(results['ids'])):
    print(f"ID: {results['ids'][i]}")
    print(f"Metadata: {results['metadatas'][i]}")
    print(f"Content: {results['documents'][i]}")
    print("-" * 20)
