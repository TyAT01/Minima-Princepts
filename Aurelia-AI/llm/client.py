from __future__ import annotations
import logging
import requests
import json
from typing import Any, Optional, Dict, List

logger = logging.getLogger(__name__)

class LlamaClient:
    """A client for interacting with a local Llama 3.1 8B instance (Ollama or llama.cpp)."""

    def __init__(self, base_url: str = "http://localhost:11434/api", model: str = "llama3.1:8b-instruct-q4_K_M", api_type: str = "ollama"):
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
        """Generates a response using the Llama model with automatic endpoint discovery and model checks."""
        logger.info(f"Generating response for input: {user_input[:50]}...")

        if self.api_type == "ollama":
            return self._generate_ollama_with_fallback(system_prompt, user_input, history, context)
        else:
            return self._generate_openai(system_prompt, user_input, history, context)

    def _generate_ollama_with_fallback(self, system_prompt, user_input, history, context):
        # Candidates for Ollama endpoints
        endpoints = ["chat", "generate", "openai"]
        if self._endpoint_type != "chat":
            # Prioritize last successful type
            if self._endpoint_type in endpoints:
                endpoints.remove(self._endpoint_type)
                endpoints.insert(0, self._endpoint_type)

        last_error = None
        for etype in endpoints:
            try:
                if etype == "chat":
                    res = self._generate_ollama_chat(system_prompt, user_input, history, context)
                elif etype == "generate":
                    res = self._generate_ollama_generate(system_prompt, user_input, history, context)
                else: # openai style
                    res = self._generate_openai(system_prompt, user_input, history, context)

                self._endpoint_type = etype
                return res
            except requests.exceptions.HTTPError as e:
                last_error = e
                status = e.response.status_code
                body = e.response.text

                logger.warning(f"Ollama /{etype} failed with status {status}. Body: {body}")

                if status == 404:
                    # Specific check for "model not found" in body
                    if "model" in body.lower() and "not found" in body.lower():
                        raise Exception(f"Model '{self.model}' not found in Ollama. Please run: ollama pull {self.model}")

                    logger.warning(f"Endpoint /{etype} not found (404). Trying next...")
                    continue

                raise
            except Exception as e:
                last_error = e
                logger.warning(f"Error with {etype}: {e}")
                continue

        # If all failed
        if last_error:
            # Re-raise with more context
            err_msg = f"All Ollama endpoints failed. Last error: {last_error}"
            if isinstance(last_error, requests.exceptions.HTTPError):
                err_msg += f" (Status {last_error.response.status_code}: {last_error.response.text})"
            raise Exception(err_msg)

        raise Exception("All Ollama endpoints failed. Please check if Ollama is running and the model is pulled.")

    def _generate_ollama_chat(self, system_prompt: str, user_input: str, history: List[Dict[str, str]], context: str) -> str:
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

        # Determine base from self.base_url
        base = self.base_url
        if base.endswith("/api"):
             base = base[:-4]

        # Try /v1 first, then fallback
        endpoints = [f"{base}/v1/chat/completions", f"{self.base_url}/chat/completions"]

        for ep in endpoints:
            try:
                response = requests.post(ep, json=payload, timeout=60)
                response.raise_for_status()
                data = response.json()
                return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            except Exception as e:
                if isinstance(e, requests.exceptions.HTTPError):
                     logger.debug(f"OpenAI fallback endpoint {ep} failed with {e.response.status_code}: {e.response.text}")
                continue

        raise Exception(f"Failed to connect to OpenAI-compatible API at {base}")

    def perform_diagnostics(self) -> str:
        """Tests the connection to Ollama and reports available models."""
        base = self.base_url
        if base.endswith("/api"): base = base[:-4]

        report = f"--- OLLAMA DIAGNOSTIC REPORT ---\nTarget Server: {base}\nTarget Model: {self.model}\n\n"

        # 1. Test basic connectivity
        try:
            r = requests.get(base, timeout=5)
            report += f"1. Root Server Check: SUCCESS (Status {r.status_code})\n   Body: {r.text[:50]}...\n"
        except Exception as e:
            report += f"1. Root Server Check: FAILED ({e})\n"

        # 2. List Models
        try:
            r = requests.get(f"{base}/api/tags", timeout=5)
            if r.status_code == 200:
                models = [m.get("name") for m in r.json().get("models", [])]
                report += f"2. Models Found: {models}\n"
                if self.model in models or (self.model + ":latest") in models:
                    report += f"   - Target model '{self.model}' is AVAILABLE.\n"
                else:
                    report += f"   - WARNING: Target model '{self.model}' IS NOT PULLED.\n"
            else:
                report += f"2. Models Found: FAILED (Status {r.status_code}: {r.text})\n"
        except Exception as e:
            report += f"2. Models Found: ERROR ({e})\n"

        # 3. Check Version
        try:
            r = requests.get(f"{base}/api/version", timeout=5)
            if r.status_code == 200:
                report += f"3. Ollama Version: {r.json().get('version')}\n"
        except:
             pass

        report += "---------------------------------"
        return report
