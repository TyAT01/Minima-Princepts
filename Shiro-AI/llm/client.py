from __future__ import annotations
import logging
import requests
import json
import asyncio
import aiohttp
from typing import Any, Optional, Dict, List, Generator, AsyncGenerator


# ── Module-level constants (computed once, reused on every LLM call) ─────────
# B1 FIX: Removed [ABSOLUTE OUTPUT RULE] bracket token — it was demonstrating
# bracket-format output to the model. Plain prose instruction instead.
# P6 FIX: Precomputed so string isn't rebuilt on every call.
_ANTI_LEAK_SUFFIX = (
    "\n\nSpeak ONLY as Shiro. Never output thought markers, inner mind blocks, "
    "system directives, or log entries in your reply. Plain spoken words only. "
    "Do not prefix your reply with 'Shiro:'."
)

def _build_payload(model, messages, temperature, top_p, repeat_penalty,
                   max_tokens, num_gpu, tools=None, max_tokens_override=None):
    """Build Ollama chat payload. Called from both sync and async paths.
    max_tokens_override: if set, overrides self.max_tokens for this call only.
    Used to allow longer responses for complex/deep messages.
    """
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {
            "temperature": temperature,
            "top_p": top_p,
            "repeat_penalty": repeat_penalty,
            "num_predict": max_tokens_override if max_tokens_override else max_tokens,
            "stop": ["User:", "System:", "\nUser:", "\nSystem:"],
        }
    }
    if num_gpu is not None:
        payload["options"]["num_gpu"] = num_gpu
    if tools:
        payload["tools"] = tools
    return payload


logger = logging.getLogger(__name__)

class LlamaClient:
    """A client for interacting with a local Llama 3.1 8B instance (Ollama or llama.cpp)."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434/api",
        model: str = "llama3.1:8b-instruct-q4_K_M",
        fallback_model: Optional[str] = None,
        api_type: str = "ollama",
        temperature: float = 0.6,
        top_p: float = 0.9,
        repeat_penalty: float = 1.2,
        max_tokens: int = 512,
        use_native_tools: bool = True,
        num_gpu: Optional[int] = None
    ):
        """
        Args:
            base_url: The base URL of the local model server.
            model: The model name to use.
            fallback_model: Optional fallback model if the primary model fails.
            api_type: 'ollama' or 'openai' (for llama.cpp or other OpenAI compatible servers).
            temperature: Sampling temperature.
            top_p: Top-p sampling.
            repeat_penalty: Penalty for repeating tokens.
            max_tokens: Maximum tokens to generate.
            use_native_tools: Whether to use native tool calling if supported.
            num_gpu: Number of layers to offload to GPU (Ollama).
        """
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.fallback_model = fallback_model
        self.api_type = api_type.lower()
        self.temperature = temperature
        self.top_p = top_p
        self.repeat_penalty = repeat_penalty
        self.max_tokens = max_tokens
        self.use_native_tools = use_native_tools
        self.num_gpu = num_gpu
        self._endpoint_type = "chat" # Default to chat
        self._session: Optional[aiohttp.ClientSession] = None
        self._supports_tools: Optional[bool] = None # Cache for tool support
        logger.info(f"Initialized LlamaClient ({self.api_type}) at {self.base_url} with model {self.model} (fallback: {self.fallback_model}, native_tools: {self.use_native_tools}, num_gpu: {self.num_gpu})")

    async def _get_session(self) -> aiohttp.ClientSession:
        """Returns the active aiohttp session, creating it if necessary."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        """Closes the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()

    def generate_response(self, system_prompt: str, user_input: str, history: List[Dict[str, str]], context: str = "", tools: Optional[List[Dict]] = None) -> str:
        """Generates a full response using the Llama model."""
        full_text = ""
        for chunk in self.stream_response(system_prompt, user_input, history, context, tools=tools):
            full_text += chunk
        return full_text.strip()

    async def generate_response_async(self, system_prompt: str, user_input: str, history: List[Dict[str, str]], context: str = "", tools: Optional[List[Dict]] = None) -> str:
        """Generates a full response asynchronously."""
        full_text = ""
        async for chunk in self.stream_response_async(system_prompt, user_input, history, context, tools=tools):
            full_text += chunk
        return full_text.strip()

    def stream_response(self, system_prompt: str, user_input: str, history: List[Dict[str, str]], context: str = "", tools: Optional[List[Dict]] = None, max_tokens_override: int = None) -> Generator[str, None, None]:
        """Generates a streaming response using the Llama model."""
        logger.info(f"Streaming response for input: {user_input[:50]}...")

        if self.api_type == "ollama":
            yield from self._stream_ollama_with_fallback(system_prompt, user_input, history, context, tools=tools, max_tokens_override=max_tokens_override)
        else:
            yield from self._stream_openai(system_prompt, user_input, history, context, tools=tools)

    async def stream_response_async(self, system_prompt: str, user_input: str, history: List[Dict[str, str]], context: str = "", tools: Optional[List[Dict]] = None) -> AsyncGenerator[str, None]:
        """Generates a streaming response asynchronously."""
        logger.info(f"Streaming async response for input: {user_input[:50]}...")

        if self.api_type == "ollama":
            models_to_try = [self.model]
            if self.fallback_model:
                models_to_try.append(self.fallback_model)

            last_exception = None
            for current_model in models_to_try:
                try:
                    try:
                        # Use cached tool support
                        native_tools_enabled = self.use_native_tools and self._supports_tools is not False
                        effective_tools = tools if native_tools_enabled else None
                        actual_system_prompt = system_prompt
                        if tools and not native_tools_enabled:
                            actual_system_prompt = self._inject_tool_instructions(system_prompt, tools)

                        async for chunk in self._stream_ollama_chat_async(actual_system_prompt, user_input, history, context, tools=effective_tools, model=current_model):
                            yield chunk
                        if tools and self._supports_tools is None:
                            self._supports_tools = native_tools_enabled
                        return # Success
                    except aiohttp.ClientResponseError as e:
                        if e.status == 400 and tools:
                            logger.warning(f"Async Ollama model {current_model} does not support native tools. Switching to prompt-based tools.")
                            self._supports_tools = False
                            actual_system_prompt = self._inject_tool_instructions(system_prompt, tools)
                            async for chunk in self._stream_ollama_chat_async(actual_system_prompt, user_input, history, context, tools=None, model=current_model):
                                yield chunk
                            return # Success
                        else:
                            raise
                except Exception as e:
                    logger.warning(f"Async Ollama chat failed with model {current_model}: {e}")
                    last_exception = e
                    continue

            # If all Ollama models failed, try OpenAI as last resort
            logger.warning(f"All Ollama models failed, falling back to OpenAI: {last_exception}")
            async for chunk in self._stream_openai_async(system_prompt, user_input, history, context, tools=tools):
                yield chunk
        else:
            async for chunk in self._stream_openai_async(system_prompt, user_input, history, context, tools=tools):
                yield chunk

    def _stream_ollama_with_fallback(self, system_prompt, user_input, history, context, tools=None, max_tokens_override=None):
        models_to_try = [self.model]
        if self.fallback_model:
            models_to_try.append(self.fallback_model)

        endpoints = ["chat", "generate", "openai"]
        if self._endpoint_type != "chat":
            if self._endpoint_type in endpoints:
                endpoints.remove(self._endpoint_type)
                endpoints.insert(0, self._endpoint_type)

        last_error = None
        for current_model in models_to_try:
            for etype in endpoints:
                try:
                    if etype == "chat":
                        try:
                            # Use cached tool support
                            native_tools_enabled = self.use_native_tools and self._supports_tools is not False
                            effective_tools = tools if native_tools_enabled else None
                            actual_system_prompt = system_prompt
                            if tools and not native_tools_enabled:
                                actual_system_prompt = self._inject_tool_instructions(system_prompt, tools)

                            yield from self._stream_ollama_chat(actual_system_prompt, user_input, history, context, tools=effective_tools, model=current_model, max_tokens_override=max_tokens_override)
                            if tools and self._supports_tools is None:
                                self._supports_tools = native_tools_enabled
                        except requests.exceptions.HTTPError as e:
                            if e.response.status_code == 400 and tools:
                                logger.warning(f"Ollama model {current_model} does not support native tools. Switching to prompt-based tools.")
                                self._supports_tools = False
                                actual_system_prompt = self._inject_tool_instructions(system_prompt, tools)
                                yield from self._stream_ollama_chat(actual_system_prompt, user_input, history, context, tools=None, model=current_model)
                            else:
                                raise
                    elif etype == "generate":
                        yield from self._stream_ollama_generate(system_prompt, user_input, history, context, model=current_model)
                    else:
                        yield from self._stream_openai(system_prompt, user_input, history, context, tools=tools, model=current_model)

                    self._endpoint_type = etype
                    return
                except requests.exceptions.HTTPError as e:
                    last_error = e
                    if e.response.status_code in [400, 404]:
                        continue
                    raise
                except Exception as e:
                    last_error = e
                    continue
        if last_error: raise last_error

    def _stream_ollama_chat(self, system_prompt: str, user_input: str, history: List[Dict[str, str]], context: str, tools=None, model: Optional[str] = None, max_tokens_override: int = None):
        target_model = model or self.model
        messages = [{"role": "system", "content": system_prompt + _ANTI_LEAK_SUFFIX}]
        if context:
            messages[0]["content"] += f"\n\nRelevant Context from Memory:\n{context}"
        messages.extend(history)
        messages.append({"role": "user", "content": user_input})

        payload = _build_payload(target_model, messages, self.temperature, self.top_p,
                                  self.repeat_penalty, self.max_tokens, self.num_gpu, tools,
                                  max_tokens_override=max_tokens_override)

        response = requests.post(f"{self.base_url}/chat", json=payload, timeout=60, stream=True)
        if response.status_code == 400:
            logger.error(f"Ollama Chat 400 Bad Request: {response.text}")
        response.raise_for_status()

        for line in response.iter_lines():
            if line:
                data = json.loads(line)
                msg = data.get("message", {})
                chunk = msg.get("content", "")
                if chunk:
                    yield chunk
                if "tool_calls" in msg:
                    yield f"TOOL_CALLS: {json.dumps(msg['tool_calls'])}"
                if data.get("done"):
                    # Yield sentinel so caller knows if we hit token limit
                    if data.get("done_reason") == "length":
                        yield "__TRUNCATED__"
                    break

    async def _stream_ollama_chat_async(self, system_prompt: str, user_input: str, history: List[Dict[str, str]], context: str, tools=None, model: Optional[str] = None):
        target_model = model or self.model
        messages = [{"role": "system", "content": system_prompt + _ANTI_LEAK_SUFFIX}]
        if context:
            messages[0]["content"] += f"\n\nRelevant Context from Memory:\n{context}"
        messages.extend(history)
        messages.append({"role": "user", "content": user_input})

        payload = _build_payload(target_model, messages, self.temperature, self.top_p,
                                  self.repeat_penalty, self.max_tokens, self.num_gpu, tools)

        session = await self._get_session()
        async with session.post(f"{self.base_url}/chat", json=payload) as response:
            if response.status == 400:
                resp_text = await response.text()
                logger.error(f"Ollama Chat Async 400 Bad Request: {resp_text}")
            response.raise_for_status()
            async for line in response.content:
                if line:
                    data = json.loads(line)
                    msg = data.get("message", {})
                    chunk = msg.get("content", "")
                    if chunk:
                        yield chunk
                    if "tool_calls" in msg:
                        yield f"TOOL_CALLS: {json.dumps(msg['tool_calls'])}"
                    if data.get("done"):
                        break

    def _stream_ollama_generate(self, system_prompt: str, user_input: str, history: List[Dict[str, str]], context: str, model: Optional[str] = None):
        target_model = model or self.model
        full_prompt = f"{system_prompt}\n\n"
        if context:
            full_prompt += f"Relevant Context:\n{context}\n\n"
        for msg in history:
            role = "User" if msg["role"] == "user" else "Shiro"
            full_prompt += f"{role}: {msg['content']}\n"
        full_prompt += f"User: {user_input}\nShiro:"

        payload = {
            "model": target_model,
            "prompt": full_prompt,
            "stream": True,
            "options": {
                "temperature": self.temperature,
                "top_p": self.top_p,
                "repeat_penalty": self.repeat_penalty,
                "num_predict": self.max_tokens,
                "stop": ["User:", "System:", "\nUser:", "\nSystem:"]  # removed [SYSTEM: — was cutting replies
            }
        }
        if self.num_gpu is not None:
            payload["options"]["num_gpu"] = self.num_gpu
        response = requests.post(f"{self.base_url}/generate", json=payload, timeout=60, stream=True)
        response.raise_for_status()
        for line in response.iter_lines():
            if line:
                data = json.loads(line)
                chunk = data.get("response", "")
                if chunk:
                    yield chunk
                if data.get("done"):
                    break

    def _stream_openai(self, system_prompt: str, user_input: str, history: List[Dict[str, str]], context: str, tools=None, model: Optional[str] = None):
        target_model = model or self.model
        messages = [{"role": "system", "content": system_prompt}]
        if context:
            messages[0]["content"] += f"\n\nRelevant Context from Memory:\n{context}"
        messages.extend(history)
        messages.append({"role": "user", "content": user_input})

        payload = {
            "model": target_model,
            "messages": messages,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
            "stream": True,
            "stop": ["User:", "System:", "\nUser:", "\nSystem:"]  # removed [SYSTEM: — was cutting replies
        }
        if tools:
            payload["tools"] = tools

        base = self.base_url
        if base.endswith("/api"): base = base[:-4]
        endpoints = [f"{base}/v1/chat/completions", f"{self.base_url}/chat/completions"]

        last_exception = None
        for ep in endpoints:
            try:
                response = requests.post(ep, json=payload, timeout=60, stream=True)
                response.raise_for_status()
                for line in response.iter_lines():
                    if line:
                        line_text = line.decode("utf-8")
                        if line_text.startswith("data: "):
                            data_str = line_text[6:]
                            if data_str.strip() == "[DONE]": break
                            data = json.loads(data_str)
                            delta = data.get("choices", [{}])[0].get("delta", {})
                            chunk = delta.get("content", "")
                            if chunk: yield chunk
                            if "tool_calls" in delta:
                                yield f"TOOL_CALLS: {json.dumps(delta['tool_calls'])}"
                return
            except Exception as e:
                last_exception = e
                continue
        if last_exception:
            raise last_exception

    async def _stream_openai_async(self, system_prompt: str, user_input: str, history: List[Dict[str, str]], context: str, tools=None, model: Optional[str] = None):
        target_model = model or self.model
        messages = [{"role": "system", "content": system_prompt}]
        if context:
            messages[0]["content"] += f"\n\nRelevant Context from Memory:\n{context}"
        messages.extend(history)
        messages.append({"role": "user", "content": user_input})

        payload = {
            "model": target_model,
            "messages": messages,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
            "stream": True,
            "stop": ["User:", "System:", "\nUser:", "\nSystem:"]  # removed [SYSTEM: — was cutting replies
        }
        if tools:
            payload["tools"] = tools

        base = self.base_url
        if base.endswith("/api"): base = base[:-4]
        endpoint = f"{base}/v1/chat/completions"

        session = await self._get_session()
        try:
            async with session.post(endpoint, json=payload) as response:
                response.raise_for_status()
                async for line in response.content:
                    if line:
                        line_text = line.decode("utf-8")
                        if line_text.startswith("data: "):
                            data_str = line_text[6:]
                            if data_str.strip() == "[DONE]": break
                            data = json.loads(data_str)
                            delta = data.get("choices", [{}])[0].get("delta", {})
                            chunk = delta.get("content", "")
                            if chunk: yield chunk
                            if "tool_calls" in delta:
                                yield f"TOOL_CALLS: {json.dumps(delta['tool_calls'])}"
        except Exception as e:
            logger.error(f"Async OpenAI stream failed: {e}")
            raise

    def _inject_tool_instructions(self, system_prompt: str, tools: List[Dict]) -> str:
        """Injects tool definitions and calling instructions into the system prompt."""
        tool_desc = ""
        for tool in tools:
            fn = tool.get('function', {})
            name = fn.get('name')
            desc = fn.get('description')
            params = fn.get('parameters', {}).get('properties', {})
            tool_desc += f"- {name}: {desc} (Parameters: {list(params.keys())})\n"

        instruction = (
            "\n\n### [SYSTEM] TOOL USE\n"
            "The following tools are available to you if needed:\n"
            f"{tool_desc}\n"
            "To use a tool, you MUST output the following exact format on a new line:\n"
            "TOOL_CALLS: [{\"function\": {\"name\": \"tool_name\", \"arguments\": {\"arg\": \"val\"}}}]\n"
            "Follow the exact JSON format. The engine will catch this and provide the result in the next turn.\n"
        )
        return system_prompt + instruction

    def perform_diagnostics(self) -> str:
        base = self.base_url
        if base.endswith("/api"): base = base[:-4]
        report = f"--- OLLAMA DIAGNOSTIC REPORT ---\nTarget Server: {base}\nTarget Model: {self.model} (fallback: {self.fallback_model})\n\n"
        try:
            r = requests.get(base, timeout=5)
            report += f"1. Root Server Check: SUCCESS (Status {r.status_code})\n"
        except Exception as e:
            report += f"1. Root Server Check: FAILED ({e})\n"
        try:
            r = requests.get(f"{base}/api/tags", timeout=5)
            if r.status_code == 200:
                models = [m.get("name") for m in r.json().get("models", [])]
                report += f"2. Models Found: {models}\n"
            else:
                report += f"2. Models Found: FAILED\n"
        except Exception as e:
            report += f"2. Models Found: ERROR ({e})\n"
        return report
