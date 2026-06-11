"""
OpenAI provider — covers GPT-4o, GPT-4.1, o1, and any other OpenAI-compatible
endpoint (vLLM, Together, etc., by setting JAMBU_LLM_OPENAI_BASE_URL).
"""

from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator, Optional

import httpx

from ..base import (
    ChatMessage,
    ChatResponse,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimit,
    ProviderTimeout,
    ProviderUnavailable,
    StreamChunk,
    Usage,
    estimate_cost_for_model,
)
from ..config import LLMConfig

NAME = "openai"
MODELS = [
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4.1",
    "gpt-4.1-mini",
    "o1",
    "o1-mini",
    "o3-mini",
]
supports_tools = True


class OpenAIProvider:
    name = NAME
    models = MODELS
    supports_tools = True

    def __init__(self, config: LLMConfig):
        self.config = config
        self.api_key = config.openai_api_key
        self.base_url = config.openai_base_url.rstrip("/")
        self.default_model = config.openai_model

    def _headers(self) -> dict:
        if not self.api_key:
            raise ProviderAuthError("OPENAI_API_KEY is not set")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        tools: Optional[list[dict]],
        stream: bool = False,
    ) -> dict:
        out: list[dict] = []
        for m in messages:
            d = m.to_dict()
            # OpenAI expects tool_calls only on assistant, tool result on tool role
            if m.role.value == "tool":
                d = {"role": "tool", "content": m.content, "tool_call_id": m.tool_call_id}
            out.append(d)
        payload: dict[str, Any] = {
            "model": model,
            "messages": out,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
        if tools:
            payload["tools"] = [
                {"type": "function", "function": t} for t in tools
            ]
        return payload

    async def health(self) -> bool:
        if not self.api_key:
            return False
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{self.base_url}/models",
                    headers=self._headers(),
                    timeout=self.config.health_timeout,
                )
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
        if not self.api_key:
            raise ProviderAuthError("OPENAI_API_KEY is not set")
        mdl = model or self.default_model
        payload = self._payload(messages, model=mdl, max_tokens=max_tokens, temperature=temperature, tools=tools)
        started = time.monotonic()
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                    timeout=timeout,
                )
        except httpx.TimeoutException as e:
            raise ProviderTimeout(f"OpenAI timeout: {e}") from e
        except httpx.ConnectError as e:
            raise ProviderUnavailable(f"OpenAI unreachable: {e}") from e
        if r.status_code == 401 or r.status_code == 403:
            raise ProviderAuthError(f"OpenAI auth failed: {r.text[:200]}")
        if r.status_code == 429:
            raise ProviderRateLimit(f"OpenAI rate limited: {r.text[:200]}")
        if r.status_code != 200:
            raise ProviderError(f"OpenAI {r.status_code}: {r.text[:200]}")
        data = r.json()
        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        u = data.get("usage", {})
        usage = Usage(
            prompt_tokens=u.get("prompt_tokens", 0),
            completion_tokens=u.get("completion_tokens", 0),
            total_tokens=u.get("total_tokens", 0),
        )
        usage.cost_usd = estimate_cost_for_model(self.name, mdl, usage)
        return ChatResponse(
            content=msg.get("content", "") or "",
            model=mdl,
            provider=self.name,
            usage=usage,
            finish_reason=choice.get("finish_reason", "stop"),
            tool_calls=msg.get("tool_calls") or [],
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
        if not self.api_key:
            raise ProviderAuthError("OPENAI_API_KEY is not set")
        mdl = model or self.default_model
        payload = self._payload(messages, model=mdl, max_tokens=max_tokens, temperature=temperature, tools=tools, stream=True)
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                    timeout=timeout,
                ) as r:
                    if r.status_code != 200:
                        body = await r.aread()
                        raise ProviderError(f"OpenAI {r.status_code}: {body[:200]!r}")
                    usage = Usage()
                    started = time.monotonic()
                    async for line in r.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        for choice in event.get("choices", []):
                            delta = choice.get("delta", {})
                            if delta.get("content"):
                                yield StreamChunk(delta=delta["content"])
                            if delta.get("tool_calls"):
                                yield StreamChunk(delta="", tool_calls=delta["tool_calls"])
                            if choice.get("finish_reason"):
                                yield StreamChunk(delta="", finish_reason=choice["finish_reason"])
                    u = event.get("usage", {}) if "event" in locals() else {}
                    usage = Usage(
                        prompt_tokens=u.get("prompt_tokens", 0),
                        completion_tokens=u.get("completion_tokens", 0),
                        total_tokens=u.get("total_tokens", 0),
                    )
                    usage.cost_usd = estimate_cost_for_model(self.name, mdl, usage)
                    yield StreamChunk(delta="", finish_reason="stop", usage=usage)
        except httpx.TimeoutException as e:
            raise ProviderTimeout(f"OpenAI stream timeout: {e}") from e

    def estimate_cost(self, usage: Usage, model: Optional[str] = None) -> float:
        return estimate_cost_for_model(self.name, model or self.default_model, usage)


def register(registry) -> None:
    registry.register_factory(NAME, OpenAIProvider)
