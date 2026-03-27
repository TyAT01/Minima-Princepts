import unittest
from unittest.mock import patch, MagicMock
import requests
import json
import sys
import os
from pathlib import Path

# Add parent directory to sys.path to import local modules
sys.path.append(str(Path(__file__).resolve().parent.parent))

from llm.client import LlamaClient

class TestOllamaPayload(unittest.TestCase):
    def setUp(self):
        self.client = LlamaClient(base_url="http://localhost:11434/api", model="loki:latest")

    @patch('requests.post')
    def test_chat_400_fallback_no_tools(self, mock_post):
        # Mock 400 error for the first call (with tools)
        mock_response_400 = MagicMock()
        mock_response_400.status_code = 400
        mock_response_400.text = "Bad Request: tools not supported"
        mock_response_400.raise_for_status.side_effect = requests.exceptions.HTTPError("400 Client Error", response=mock_response_400)

        # Mock success for the second call (without tools)
        mock_response_200 = MagicMock()
        mock_response_200.status_code = 200
        mock_response_200.iter_lines.return_value = [
            json.dumps({"message": {"content": "Hello without tools"}, "done": True}).encode()
        ]

        mock_post.side_effect = [mock_response_400, mock_response_200]

        tools = [{"type": "function", "function": {"name": "test_tool"}}]

        # We need to wrap it in a list because it's a generator
        responses = list(self.client.stream_response("system", "user", [], tools=tools))

        self.assertEqual(responses, ["Hello without tools"])
        self.assertEqual(mock_post.call_count, 2)

        # Verify first call had tools
        first_call_payload = mock_post.call_args_list[0][1]['json']
        self.assertIn('tools', first_call_payload)

        # Verify second call DID NOT have tools
        second_call_payload = mock_post.call_args_list[1][1]['json']
        self.assertNotIn('tools', second_call_payload)

    @patch('requests.post')
    def test_chat_404_fallback_to_generate(self, mock_post):
        # Mock 404 error for the first call (chat)
        mock_response_404 = MagicMock()
        mock_response_404.status_code = 404
        mock_response_404.raise_for_status.side_effect = requests.exceptions.HTTPError("404 Not Found", response=mock_response_404)

        # Mock success for the second call (generate)
        mock_response_200 = MagicMock()
        mock_response_200.status_code = 200
        mock_response_200.iter_lines.return_value = [
            json.dumps({"response": "Hello from generate", "done": True}).encode()
        ]

        mock_post.side_effect = [mock_response_404, mock_response_200]

        responses = list(self.client.stream_response("system", "user", []))

        self.assertEqual(responses, ["Hello from generate"])
        self.assertTrue(any("/generate" in call[0][0] for call in mock_post.call_args_list))

if __name__ == '__main__':
    unittest.main()
