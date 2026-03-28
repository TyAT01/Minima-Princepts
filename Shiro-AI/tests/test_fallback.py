import asyncio
import aiohttp
import requests
import json
from llm.client import LlamaClient
from unittest.mock import patch, MagicMock, AsyncMock

async def test_fallback_async():
    print("Testing Async Fallback...")
    client = LlamaClient(
        base_url="http://mock:11434/api",
        model="primary-model",
        fallback_model="fallback-model"
    )

    # Mock aiohttp
    with patch("aiohttp.ClientSession.post") as mock_post:
        # First call (primary) fails with 404
        mock_resp_fail = MagicMock()
        mock_resp_fail.status = 404
        mock_resp_fail.raise_for_status.side_effect = aiohttp.ClientResponseError(
            request_info=MagicMock(), history=(), status=404, message="Not Found"
        )

        # Second call (fallback) succeeds
        mock_resp_success = MagicMock()
        mock_resp_success.status = 200
        mock_resp_success.raise_for_status = MagicMock()

        async def mock_iter_lines():
            yield json.dumps({"message": {"content": "Fallback response"}, "done": True}).encode()

        mock_resp_success.content = mock_iter_lines()

        # Side effect to return fail then success
        mock_post.return_value.__aenter__.side_effect = [mock_resp_fail, mock_resp_success]

        response = await client.generate_response_async("system", "user", [])
        print(f"Async Response: {response}")
        assert response == "Fallback response"
        assert mock_post.call_count == 2
        print("Async Fallback Test Passed!")

def test_fallback_sync():
    print("\nTesting Sync Fallback...")
    client = LlamaClient(
        base_url="http://mock:11434/api",
        model="primary-model",
        fallback_model="fallback-model"
    )

    # Mock requests
    with patch("requests.post") as mock_post:
        def create_fail_resp(name, status=404):
            m = MagicMock(name=name)
            m.status_code = status
            m.raise_for_status.side_effect = requests.exceptions.HTTPError(response=m)
            return m

        # primary-model will try:
        # 1. chat (Call 0)
        # 2. generate (Call 1)
        # 3. openai - endpoint 1 (Call 2)
        # 4. openai - endpoint 2 (Call 3)
        # All these should fail.

        # then fallback-model will try:
        # 5. chat (Call 4) - this should succeed.

        m_chat_fail = create_fail_resp("chat_fail", 404)
        m_gen_fail = create_fail_resp("gen_fail", 404)
        m_oa1_fail = create_fail_resp("oa1_fail", 404)
        m_oa2_fail = create_fail_resp("oa2_fail", 404)

        m_success = MagicMock(name="success")
        m_success.status_code = 200
        m_success.iter_lines.return_value = [
            json.dumps({"message": {"content": "Fallback sync success"}, "done": True}).encode()
        ]

        mock_post.side_effect = [m_chat_fail, m_gen_fail, m_oa1_fail, m_oa2_fail, m_success]

        response = client.generate_response("system", "user", [])
        print(f"Sync Response: '{response}'")

        for i, call in enumerate(mock_post.call_args_list):
            print(f"Call {i}: model={call.kwargs['json'].get('model')}, url={call.args[0]}")

        assert response == "Fallback sync success"
        assert mock_post.call_count == 5

        # Check that the 5th call used the fallback model
        args, kwargs = mock_post.call_args_list[4]
        payload = kwargs['json']
        assert payload['model'] == "fallback-model"
        print("Sync Fallback Test Passed!")

if __name__ == "__main__":
    asyncio.run(test_fallback_async())
    test_fallback_sync()
