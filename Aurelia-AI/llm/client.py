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
        self._endpoint_type = "chat" # Default to chat
        logger.info(f"Initialized LlamaClient ({self.api_type}) at {self.base_url} with model {self.model}")

    def generate_response(self, system_prompt: str, user_input: str, history: List[Dict[str, str]], context: str = "") -> str:
        """Generates a response using the Llama model with automatic endpoint discovery."""
        logger.info(f"Generating response for input: {user_input[:50]}...")

        if self.api_type == "ollama":
            try:
                return self._generate_ollama(system_prompt, user_input, history, context)
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 404 and self._endpoint_type == "chat":
                    logger.warning("Ollama /api/chat not found (404). Falling back to /api/generate.")
                    self._endpoint_type = "generate"
                    return self._generate_ollama(system_prompt, user_input, history, context)
                raise
        else:
            return self._generate_openai(system_prompt, user_input, history, context)

    def _generate_ollama(self, system_prompt: str, user_input: str, history: List[Dict[str, str]], context: str) -> str:
        if self._endpoint_type == "chat":
            return self._generate_ollama_chat(system_prompt, user_input, history, context)
        else:
            return self._generate_ollama_generate(system_prompt, user_input, history, context)

    def _generate_ollama_chat(self, system_prompt: str, user_input: str, history: List[Dict[str, str]], context: str) -> str:
        logger.debug("Using Ollama /api/chat endpoint")
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
        response = requests.post(endpoint, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        return data.get("message", {}).get("content", "").strip()

    def _generate_ollama_generate(self, system_prompt: str, user_input: str, history: List[Dict[str, str]], context: str) -> str:
        logger.debug("Using Ollama /api/generate endpoint (fallback)")
        # Fallback for old Ollama versions without /api/chat
        full_prompt = f"{system_prompt}\n\n"
        if context:
            full_prompt += f"Relevant Context:\n{context}\n\n"

        for msg in history:
            role = "User" if msg["role"] == "user" else "Aurelia"
            full_prompt += f"{role}: {msg['content']}\n"

        full_prompt += f"User: {user_input}\nAurelia:"

        payload = {
            "model": self.model,
            "prompt": full_prompt,
            "stream": False,
            "options": {
                "temperature": 0.7,
                "num_predict": 512,
            }
        }

        endpoint = f"{self.base_url}/generate"
        response = requests.post(endpoint, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        return data.get("response", "").strip()

    def _generate_openai(self, system_prompt: str, user_input: str, history: List[Dict[str, str]], context: str) -> str:
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

        # Recent Ollama versions support OpenAI style at /v1/chat/completions
        # But for general OpenAI compatible servers, we use /chat/completions
        # If the base_url is .../api, we might need to strip /api
        base = self.base_url
        if base.endswith("/api"):
             base = base[:-4]

        endpoint = f"{base}/v1/chat/completions"
        try:
            response = requests.post(endpoint, json=payload, timeout=60)
            response.raise_for_status()
            data = response.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        except:
            # Fallback to the provided base if /v1 fails
            endpoint = f"{self.base_url}/chat/completions"
            response = requests.post(endpoint, json=payload, timeout=60)
            response.raise_for_status()
            data = response.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
