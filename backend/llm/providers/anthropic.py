"""
Anthropic Claude provider.

Uses the `anthropic` SDK if available, otherwise falls back to direct HTTP.
Supports both `messages` (Anthropic-native) and tool use.
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

NAME = "anthropic"
MODELS = [
    "claude-opus-4-5",
    "claude-sonnet-4-5",
    "claude-haiku-4-5-20251001",
]
supports_tools = True


def _messages_to_anthropic(messages: list[ChatMessage], system: Optional[str]) -> tuple[list[dict], Optional[str]]:
    """Convert internal ChatMessages to Anthropic messages format.

    Anthropic expects system as a separate top-level field, not as a message.
    """
    out = []
    sys = system
    for m in messages:
        if m.role.value == "system":
            sys = (sys + "\n\n" if sys else "") + m.content
            continue
        msg: dict[str, Any] = {"role": m.role.value, "content": m.content}
        if m.tool_calls:
            msg["content"] = []
            for tc in m.tool_calls:
                msg["content"].append({
                    "type": "tool_use",
                    "id": tc.get("id"),
                    "name": tc.get("name"),
                    "input": tc.get("input", {}),
                })
        if m.tool_call_id:
            msg["content"] = [{
                "type": "tool_result",
                "tool_use_id": m.tool_call_id,
                "content": m.content,
            }]
        out.append(msg)
    return out, sys


def _tools_to_anthropic(tools: list[dict]) -> list[dict]:
    """Convert OpenAI-style tool spec to Anthropic tool spec.

    Input:  [{"name": "x", "description": "...", "parameters": {...JSON Schema...}}]
    Output: [{"name": "x", "description": "...", "input_schema": {...}}]
    """
    out = []
    for t in tools:
        out.append({
            "name": t.get("name"),
            "description": t.get("description", ""),
            "input_schema": t.get("parameters", {"type": "object", "properties": {}}),
        })
    return out


class AnthropicProvider:
    name = NAME
    models = MODELS
    supports_tools = True

    def __init__(self, config: LLMConfig):
        self.config = config
        self.api_key = config.anthropic_api_key
        self.base_url = config.anthropic_base_url.rstrip("/")
        self.default_model = config.anthropic_model

    def _headers(self) -> dict:
        if not self.api_key:
            raise ProviderAuthError("ANTHROPIC_API_KEY is not set")
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    async def health(self) -> bool:
        if not self.api_key:
            return False
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"{self.base_url}/v1/messages",
                    headers=self._headers(),
                    json={"model": self.default_model, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]},
                    timeout=self.config.health_timeout,
                )
                return r.status_code in (200, 400, 429)  # 400 = bad params but key is valid
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
            raise ProviderAuthError("ANTHROPIC_API_KEY is not set")
        mdl = model or self.default_model
        msgs, sys = _messages_to_anthropic(messages, None)
        payload: dict[str, Any] = {
            "model": mdl,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": msgs,
        }
        if sys:
            payload["system"] = sys
        if tools:
            payload["tools"] = _tools_to_anthropic(tools)
        started = time.monotonic()
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"{self.base_url}/v1/messages",
                    headers=self._headers(),
                    json=payload,
                    timeout=timeout,
                )
        except httpx.TimeoutException as e:
            raise ProviderTimeout(f"Anthropic timeout: {e}") from e
        except httpx.ConnectError as e:
            raise ProviderUnavailable(f"Anthropic unreachable: {e}") from e
        if r.status_code == 401 or r.status_code == 403:
            raise ProviderAuthError(f"Anthropic auth failed: {r.text[:200]}")
        if r.status_code == 429:
            raise ProviderRateLimit(f"Anthropic rate limited: {r.text[:200]}")
        if r.status_code != 200:
            raise ProviderError(f"Anthropic {r.status_code}: {r.text[:200]}")
        data = r.json()
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id"),
                    "name": block.get("name"),
                    "input": block.get("input", {}),
                })
        usage_data = data.get("usage", {})
        usage = Usage(
            prompt_tokens=usage_data.get("input_tokens", 0),
            completion_tokens=usage_data.get("output_tokens", 0),
            total_tokens=usage_data.get("input_tokens", 0) + usage_data.get("output_tokens", 0),
        )
        usage.cost_usd = estimate_cost_for_model(self.name, mdl, usage)
        return ChatResponse(
            content="".join(text_parts),
            model=mdl,
            provider=self.name,
            usage=usage,
            finish_reason=data.get("stop_reason", "stop"),
            tool_calls=tool_calls,
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
            raise ProviderAuthError("ANTHROPIC_API_KEY is not set")
        mdl = model or self.default_model
        msgs, sys = _messages_to_anthropic(messages, None)
        payload: dict[str, Any] = {
            "model": mdl,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": msgs,
            "stream": True,
        }
        if sys:
            payload["system"] = sys
        if tools:
            payload["tools"] = _tools_to_anthropic(tools)
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/v1/messages",
                    headers=self._headers(),
                    json=payload,
                    timeout=timeout,
                ) as r:
                    if r.status_code != 200:
                        body = await r.aread()
                        raise ProviderError(f"Anthropic {r.status_code}: {body[:200]!r}")
                    usage = Usage()
                    started = time.monotonic()
                    async for line in r.aiter_lines():
                        if not line:
                            continue
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        et = event.get("type")
                        if et == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta":
                                yield StreamChunk(delta=delta.get("text", ""))
                        elif et == "message_delta":
                            u = event.get("usage", {})
                            usage = Usage(
                                prompt_tokens=u.get("input_tokens", 0),
                                completion_tokens=u.get("output_tokens", 0),
                                total_tokens=u.get("input_tokens", 0) + u.get("output_tokens", 0),
                            )
                            yield StreamChunk(delta="", finish_reason=event.get("delta", {}).get("stop_reason"))
                        elif et == "message_start":
                            u = event.get("message", {}).get("usage", {})
                            usage = Usage(
                                prompt_tokens=u.get("input_tokens", 0),
                                completion_tokens=u.get("output_tokens", 0),
                                total_tokens=u.get("input_tokens", 0) + u.get("output_tokens", 0),
                            )
                    usage.cost_usd = estimate_cost_for_model(self.name, mdl, usage)
                    yield StreamChunk(delta="", finish_reason="stop", usage=usage)
        except httpx.TimeoutException as e:
            raise ProviderTimeout(f"Anthropic stream timeout: {e}") from e

    def estimate_cost(self, usage: Usage, model: Optional[str] = None) -> float:
        return estimate_cost_for_model(self.name, model or self.default_model, usage)


def register(registry) -> None:
    """Register this provider with the given registry."""
    registry.register_factory(NAME, AnthropicProvider)
