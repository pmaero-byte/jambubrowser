"""
Ollama provider — local model server. Uses OpenAI-compatible /v1/chat/completions
when available, falls back to native /api/generate and /api/chat endpoints.

Ollama is free and local, so cost is always $0.
"""

from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator, Optional

import httpx

from ..base import (
    ChatMessage,
    ChatResponse,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
    StreamChunk,
    Usage,
)
from ..config import LLMConfig

NAME = "ollama"
MODELS = [
    "gemma3:4b",
    "gemma3:12b-it-qat",
    "gemma3:27b",
    "llama3.2:latest",
    "qwen2.5:14b",
    "mistral:latest",
    "phi3:medium",
]
supports_tools = False  # depends on model; default off for safety


class OllamaProvider:
    name = NAME
    models = MODELS
    supports_tools = False

    def __init__(self, config: LLMConfig):
        self.config = config
        self.base_url = config.ollama_base_url.rstrip("/")
        self.default_model = config.ollama_model
        # Ollama serves both /v1/chat/completions (OpenAI-compatible) and
        # /api/chat, /api/generate (native). We try OpenAI-compatible first.
        self._root = self.base_url
        if self._root.endswith("/v1"):
            self._root = self._root[:-3]

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(f"{self._root}/api/tags", timeout=self.config.health_timeout)
                return r.status_code == 200
        except Exception:
            return False

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        tools: Optional[list[dict]] = None,
        timeout: float = 30.0,
    ) -> ChatResponse:
        mdl = model or self.default_model
        started = time.monotonic()
        try:
            async with httpx.AsyncClient() as client:
                # Use native /api/chat (handles system+user+assistant correctly)
                url = f"{self._root}/api/chat"
                payload = {
                    "model": mdl,
                    "messages": [m.to_dict() for m in messages],
                    "stream": False,
                    "options": {"temperature": temperature, "num_predict": max_tokens},
                }
                r = await client.post(url, json=payload, timeout=timeout)
                if r.status_code == 404:
                    # Model not pulled — try /api/generate with concatenated prompt
                    return await self._chat_generate(messages, model=mdl, max_tokens=max_tokens, temperature=temperature, timeout=timeout, started=started)
                if r.status_code != 200:
                    raise ProviderError(f"Ollama {r.status_code}: {r.text[:200]}")
                data = r.json()
        except httpx.TimeoutException as e:
            raise ProviderTimeout(f"Ollama timeout: {e}") from e
        except httpx.ConnectError as e:
            raise ProviderUnavailable(f"Ollama unreachable: {e}") from e

        msg = data.get("message", {}) or {}
        content = msg.get("content", "")
        u = data.get("usage", {}) or {}
        # Ollama's /api/chat returns counts under different keys
        prompt_tokens = u.get("prompt_tokens") or data.get("prompt_eval_count", 0)
        completion_tokens = u.get("completion_tokens") or data.get("eval_count", 0)
        usage = Usage(
            prompt_tokens=int(prompt_tokens or 0),
            completion_tokens=int(completion_tokens or 0),
            total_tokens=int((prompt_tokens or 0) + (completion_tokens or 0)),
            cost_usd=0.0,
        )
        return ChatResponse(
            content=content,
            model=mdl,
            provider=self.name,
            usage=usage,
            finish_reason=data.get("done_reason", "stop") or "stop",
            raw=data,
            latency_ms=(time.monotonic() - started) * 1000,
        )

    async def _chat_generate(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        timeout: float,
        started: float,
    ) -> ChatResponse:
        """Fallback to /api/generate when /api/chat 404s (older Ollama versions)."""
        # Concatenate messages into a single prompt
        parts: list[str] = []
        for m in messages:
            if m.role.value == "system":
                parts.append(f"System: {m.content}")
            elif m.role.value == "user":
                parts.append(f"User: {m.content}")
            elif m.role.value == "assistant":
                parts.append(f"Assistant: {m.content}")
        prompt = "\n\n".join(parts) + "\n\nAssistant:"
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self._root}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": temperature, "num_predict": max_tokens},
                },
                timeout=timeout,
            )
        if r.status_code != 200:
            raise ProviderError(f"Ollama generate {r.status_code}: {r.text[:200]}")
        data = r.json()
        return ChatResponse(
            content=data.get("response", ""),
            model=model,
            provider=self.name,
            usage=Usage(
                prompt_tokens=int(data.get("prompt_eval_count", 0)),
                completion_tokens=int(data.get("eval_count", 0)),
                total_tokens=int(data.get("prompt_eval_count", 0)) + int(data.get("eval_count", 0)),
                cost_usd=0.0,
            ),
            raw=data,
            latency_ms=(time.monotonic() - started) * 1000,
        )

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        model: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        tools: Optional[list[dict]] = None,
        timeout: float = 30.0,
    ) -> AsyncIterator[StreamChunk]:
        mdl = model or self.default_model
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    f"{self._root}/api/chat",
                    json={
                        "model": mdl,
                        "messages": [m.to_dict() for m in messages],
                        "stream": True,
                        "options": {"temperature": temperature, "num_predict": max_tokens},
                    },
                    timeout=timeout,
                ) as r:
                    if r.status_code != 200:
                        body = await r.aread()
                        raise ProviderError(f"Ollama stream {r.status_code}: {body[:200]!r}")
                    final_usage: Optional[Usage] = None
                    async for line in r.aiter_lines():
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        msg = data.get("message", {}) or {}
                        if msg.get("content"):
                            yield StreamChunk(delta=msg["content"])
                        if data.get("done"):
                            u = data
                            final_usage = Usage(
                                prompt_tokens=int(u.get("prompt_eval_count", 0)),
                                completion_tokens=int(u.get("eval_count", 0)),
                                total_tokens=int(u.get("prompt_eval_count", 0)) + int(u.get("eval_count", 0)),
                                cost_usd=0.0,
                            )
                            yield StreamChunk(delta="", finish_reason="stop", usage=final_usage)
                            return
        except httpx.TimeoutException as e:
            raise ProviderTimeout(f"Ollama stream timeout: {e}") from e

    def estimate_cost(self, usage: Usage, model: Optional[str] = None) -> float:
        return 0.0  # local = free


def register(registry) -> None:
    registry.register_factory(NAME, OllamaProvider)
