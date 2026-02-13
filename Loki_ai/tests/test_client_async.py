import asyncio
import aiohttp
from llm.client import LlamaClient
from unittest.mock import patch, MagicMock, AsyncMock
import json

async def test_client_async():
    client = LlamaClient(base_url="http://mock:11434/api", model="loki")

    # Mock aiohttp
    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.raise_for_status = MagicMock()

        # Mock streaming response
        async def mock_iter_lines():
            yield json.dumps({"message": {"content": "Hello"}, "done": False}).encode()
            yield json.dumps({"message": {"content": " world"}, "done": True}).encode()

        mock_resp.content = mock_iter_lines()
        mock_post.return_value.__aenter__.return_value = mock_resp

        response = await client.generate_response_async("system", "user", [])
        print(f"Async Response: {response}")
        assert response == "Hello world"

        # Test tool call yielding
        async def mock_iter_lines_tools():
            yield json.dumps({"message": {"tool_calls": [{"function": {"name": "test"}}]}, "done": True}).encode()

        mock_resp.content = mock_iter_lines_tools()

        chunks = []
        async for chunk in client.stream_response_async("system", "user", []):
            chunks.append(chunk)

        print(f"Chunks: {chunks}")
        assert any("TOOL_CALLS:" in c for c in chunks)

if __name__ == "__main__":
    asyncio.run(test_client_async())
