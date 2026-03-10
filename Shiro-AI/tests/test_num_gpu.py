import unittest
from unittest.mock import patch, MagicMock
import json
import sys
from pathlib import Path

# Add parent directory to sys.path to import local modules
sys.path.append(str(Path(__file__).resolve().parent.parent))

from llm.client import LlamaClient

class TestOllamaNumGpu(unittest.TestCase):
    def setUp(self):
        self.client = LlamaClient(
            base_url="http://localhost:11434/api",
            model="shiro-v2:latest",
            num_gpu=20,
            use_native_tools=False
        )

    @patch('requests.post')
    def test_num_gpu_and_no_native_tools_payload(self, mock_post):
        # Mock success for the call
        mock_response_200 = MagicMock()
        mock_response_200.status_code = 200
        mock_response_200.iter_lines.return_value = [
            json.dumps({"message": {"content": "Hello"}, "done": True}).encode()
        ]

        mock_post.return_value = mock_response_200

        tools = [{"type": "function", "function": {"name": "test_tool"}}]

        # We need to wrap it in a list because it's a generator
        responses = list(self.client.stream_response("system", "user", [], tools=tools))

        self.assertEqual(responses, ["Hello"])

        # Verify payload
        call_payload = mock_post.call_args[1]['json']

        # Verify num_gpu is in options
        self.assertEqual(call_payload['options']['num_gpu'], 20)

        # Verify tools are NOT in payload (since use_native_tools=False)
        self.assertNotIn('tools', call_payload)

        # Verify tool instructions were injected into system prompt
        self.assertIn("### [SYSTEM] TOOL USE", call_payload['messages'][0]['content'])

if __name__ == '__main__':
    unittest.main()
