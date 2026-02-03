from __future__ import annotations
import logging
import requests
import json
from typing import Any, Optional, Dict, List

logger = logging.getLogger(__name__)

class LlamaClient:
    """A client for interacting with a local Llama 3.1 8B instance (Ollama or llama.cpp)."""

    def __init__(self, base_url: str = "http://localhost:11434/api", model: str = "llama3.1:8b", api_type: str = "ollama"):
        """
        Args:
            base_url: The base URL of the local model server.
            model: The model name to use.
            api_type: 'ollama' or 'openai' (for llama.cpp or other OpenAI compatible servers).
        """
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_type = api_type.lower()
        logger.info(f"Initialized LlamaClient ({self.api_type}) at {self.base_url} with model {self.model}")

    def generate_response(self, system_prompt: str, user_input: str, history: List[Dict[str, str]], context: str = "") -> str:
        """Generates a response using the Llama model."""
        logger.info(f"Generating response for input: {user_input[:50]}...")

        if self.api_type == "ollama":
            return self._generate_ollama(system_prompt, user_input, history, context)
        else:
            return self._generate_openai(system_prompt, user_input, history, context)

    def _generate_ollama(self, system_prompt: str, user_input: str, history: List[Dict[str, str]], context: str) -> str:
        messages = [{"role": "system", "content": system_prompt}]
        if context:
            messages[0]["content"] += f"\n\nRelevant Context from Memory:\n{context}"

        messages.extend(history)
        messages.append({"role": "user", "content": user_input})

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0.7,
                "num_predict": 512,
            }
        }

        endpoint = f"{self.base_url}/chat"
        try:
            logger.debug(f"Sending request to Ollama: {endpoint}")
            response = requests.post(endpoint, json=payload, timeout=60)
            response.raise_for_status()
            data = response.json()
            content = data.get("message", {}).get("content", "").strip()
            if not content:
                logger.warning("Ollama returned an empty response.")
            return content
        except requests.exceptions.ConnectionError:
            logger.error(f"Failed to connect to Ollama at {endpoint}. Is Ollama running?")
            raise Exception(f"Could not connect to local LLM server at {self.base_url}. Please ensure Ollama is running.")
        except Exception as e:
            logger.error(f"Ollama Error: {e}")
            raise

    def _generate_openai(self, system_prompt: str, user_input: str, history: List[Dict[str, str]], context: str) -> str:
        # For llama.cpp server or other OpenAI-compatible APIs
        messages = [{"role": "system", "content": system_prompt}]
        if context:
            messages[0]["content"] += f"\n\nRelevant Context from Memory:\n{context}"

        messages.extend(history)
        messages.append({"role": "user", "content": user_input})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 512,
        }

        endpoint = f"{self.base_url}/chat/completions"
        try:
            logger.debug(f"Sending request to OpenAI-compatible API: {endpoint}")
            response = requests.post(endpoint, json=payload, timeout=60)
            response.raise_for_status()
            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            if not content:
                logger.warning("LLM returned an empty response.")
            return content
        except requests.exceptions.ConnectionError:
            logger.error(f"Failed to connect to LLM server at {endpoint}.")
            raise Exception(f"Could not connect to local LLM server at {self.base_url}.")
        except Exception as e:
            logger.error(f"OpenAI-Compatible Error: {e}")
            raise
