"""
MLX provider — Apple Silicon native inference via the mlx-vlm OpenAI-compatible
server. Wraps the OpenAI /v1/chat/completions endpoint exposed by
backend/scripts/mlx_vlm_server.py.
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

NAME = "mlx"
MODELS = [
    "gemma-4-12b-it-4bit",
    "gemma-4-12b-it-8bit",
    "gemma-4-9b-it-4bit",
    "llama-3.2-11b-vision-instruct-4bit",
]
supports_tools = False


class MLXProvider:
    name = NAME
    models = MODELS
    supports_tools = False

    def __init__(self, config: LLMConfig):
        self.config = config
        self.base_url = config.mlx_base_url.rstrip("/")
        self.default_model = config.mlx_model

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(f"{self.base_url}/v1/models", timeout=self.config.health_timeout)
                if r.status_code == 200:
                    return True
                # mlx-vlm server may not expose /v1/models; try /health or root
                r = await client.get(self.base_url.rstrip("/v1") + "/", timeout=self.config.health_timeout)
                return r.status_code in (200, 404)  # 404 = server is up, just no root route
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
        timeout: float = 60.0,  # MLX cold starts need more time
        **kwargs,
    ) -> ChatResponse:
        mdl = model or self.default_model
        # mlx-vlm expects 'max_tokens' or 'max_completion_tokens' depending on version
        payload: dict[str, Any] = {
            "model": mdl,
            "messages": [m.to_dict() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        started = time.monotonic()
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                    timeout=timeout,
                )
        except httpx.TimeoutException as e:
            raise ProviderTimeout(f"MLX timeout: {e}") from e
        except httpx.ConnectError as e:
            raise ProviderUnavailable(f"MLX server not running: {e}. Start with: mlx-venv/bin/python3 backend/scripts/mlx_vlm_server.py --port 8080") from e
        if r.status_code != 200:
            raise ProviderError(f"MLX {r.status_code}: {r.text[:200]}")
        data = r.json()
        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        u = data.get("usage", {}) or {}
        usage = Usage(
            prompt_tokens=int(u.get("prompt_tokens", 0)),
            completion_tokens=int(u.get("completion_tokens", 0)),
            total_tokens=int(u.get("total_tokens", 0)) or (
                int(u.get("prompt_tokens", 0)) + int(u.get("completion_tokens", 0))
            ),
            cost_usd=0.0,  # local
        )
        return ChatResponse(
            content=msg.get("content", "") or "",
            model=mdl,
            provider=self.name,
            usage=usage,
            finish_reason=choice.get("finish_reason", "stop") or "stop",
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
        timeout: float = 60.0,
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        mdl = model or self.default_model
        payload = {
            "model": mdl,
            "messages": [m.to_dict() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                    timeout=timeout,
                ) as r:
                    if r.status_code != 200:
                        body = await r.aread()
                        raise ProviderError(f"MLX stream {r.status_code}: {body[:200]!r}")
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
                        cost_usd=0.0,
                    )
                    yield StreamChunk(delta="", finish_reason="stop", usage=usage)
        except httpx.TimeoutException as e:
            raise ProviderTimeout(f"MLX stream timeout: {e}") from e

    def estimate_cost(self, usage: Usage, model: Optional[str] = None) -> float:
        return 0.0  # local


def register(registry) -> None:
    registry.register_factory(NAME, MLXProvider)
