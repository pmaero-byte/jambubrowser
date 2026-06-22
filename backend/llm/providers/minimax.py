"""
MiniMax cloud provider. OpenAI-compatible /v1/chat/completions endpoint.

Used as a privacy-aware cloud fallback (configurable). Pricing is approximate.
"""

from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator, Optional

import httpx

try:
    from backend.core.socks import make_async_client
except ImportError:
    make_async_client = httpx.AsyncClient

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

NAME = "minimax"
MODELS = [
    "MiniMax-M2.7",
    "MiniMax-M3",
    "MiniMax-Text-01",
]
supports_tools = True


class MiniMaxProvider:
    name = NAME
    models = MODELS
    supports_tools = True

    def __init__(self, config: LLMConfig):
        self.config = config
        self.api_key = config.minimax_api_key
        self.base_url = config.minimax_base_url.rstrip("/")
        self.default_model = config.minimax_model

    def _headers(self) -> dict:
        if not self.api_key:
            raise ProviderAuthError("MINIMAX_API_KEY is not set")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def health(self) -> bool:
        if not self.api_key:
            return False
        try:
            async with make_async_client() as client:
                r = await client.get(
                    f"{self.base_url}/models",
                    headers=self._headers(),
                    timeout=self.config.health_timeout,
                )
                return r.status_code in (200, 404)
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
            raise ProviderAuthError("MINIMAX_API_KEY is not set")
        mdl = model or self.default_model
        payload: dict[str, Any] = {
            "model": mdl,
            "messages": [m.to_dict() for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]
        started = time.monotonic()
        try:
            async with make_async_client() as client:
                r = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                    timeout=timeout,
                )
        except httpx.TimeoutException as e:
            raise ProviderTimeout(f"MiniMax timeout: {e}") from e
        except httpx.ConnectError as e:
            raise ProviderUnavailable(f"MiniMax unreachable: {e}") from e
        if r.status_code == 401 or r.status_code == 403:
            raise ProviderAuthError(f"MiniMax auth failed: {r.text[:200]}")
        if r.status_code == 429:
            raise ProviderRateLimit(f"MiniMax rate limited: {r.text[:200]}")
        if r.status_code != 200:
            raise ProviderError(f"MiniMax {r.status_code}: {r.text[:200]}")
        data = r.json()
        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        u = data.get("usage", {}) or {}
        usage = Usage(
            prompt_tokens=int(u.get("prompt_tokens", 0)),
            completion_tokens=int(u.get("completion_tokens", 0)),
            total_tokens=int(u.get("total_tokens", 0)),
        )
        usage.cost_usd = estimate_cost_for_model(self.name, mdl, usage)
        return ChatResponse(
            content=msg.get("content", "") or "",
            model=mdl,
            provider=self.name,
            usage=usage,
            finish_reason=choice.get("finish_reason", "stop") or "stop",
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
            raise ProviderAuthError("MINIMAX_API_KEY is not set")
        mdl = model or self.default_model
        payload = {
            "model": mdl,
            "messages": [m.to_dict() for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
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
                        raise ProviderError(f"MiniMax stream {r.status_code}: {body[:200]!r}")
                    usage = Usage()
                    started = time.monotonic()
                    async for line in r.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        ds = line[6:]
                        if ds.strip() == "[DONE]":
                            break
                        try:
                            ev = json.loads(ds)
                        except json.JSONDecodeError:
                            continue
                        for ch in ev.get("choices", []):
                            d = ch.get("delta", {})
                            if d.get("content"):
                                yield StreamChunk(delta=d["content"])
                            if ch.get("finish_reason"):
                                yield StreamChunk(delta="", finish_reason=ch["finish_reason"])
                    u = ev.get("usage", {}) if "ev" in locals() else {}
                    usage = Usage(
                        prompt_tokens=int(u.get("prompt_tokens", 0)),
                        completion_tokens=int(u.get("completion_tokens", 0)),
                        total_tokens=int(u.get("total_tokens", 0)),
                    )
                    usage.cost_usd = estimate_cost_for_model(self.name, mdl, usage)
                    yield StreamChunk(delta="", finish_reason="stop", usage=usage)
        except httpx.TimeoutException as e:
            raise ProviderTimeout(f"MiniMax stream timeout: {e}") from e

    def estimate_cost(self, usage: Usage, model: Optional[str] = None) -> float:
        return estimate_cost_for_model(self.name, model or self.default_model, usage)


def register(registry) -> None:
    registry.register_factory(NAME, MiniMaxProvider)
