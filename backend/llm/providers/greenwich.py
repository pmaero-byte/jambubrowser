"""Greenwich provider — local Safe Qwen 3.8 27B + Quantum Daemon."""
from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from ..base import (
    ChatResponse,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
    StreamChunk,
    Usage,
    normalize_llm_response,
)
from ..config import LLMConfig

NAME = "greenwich"
MODELS = ["qwen3.8-27b", "qwen3-27b", "greenwich-quantum-1m"]
supports_tools = True
class GreenwichProvider:
    name = NAME
    models = MODELS
    supports_tools = True
    def __init__(self, config: LLMConfig):
        self.config = config
        self.base_url = (config.greenwich_base_url or "http://127.0.0.1:8085/v1").rstrip("/")
        self.extended_url = (config.greenwich_extended_url or "http://127.0.0.1:8090/v1").rstrip("/")
        self.default_model = config.greenwich_model or "qwen3.8-27b"
        raw = os.environ.get("GREENWICH_ENABLE_THINKING", "").lower().strip()
        self.enable_thinking = raw in ("1", "true", "yes", "on")
    def _candidate_bases(self) -> list[str]:
        bases = [self.base_url]
        if self.extended_url and self.extended_url != self.base_url:
            bases.append(self.extended_url)
        return bases
    def _headers(self) -> dict:
        api_key = (self.config.greenwich_api_key or "").strip()
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers
    def _payload(self, messages, *, model, max_tokens, temperature, tools, stream=False) -> dict:
        payload: dict[str, Any] = {"model": model, "messages": [m.to_dict() for m in messages], "max_tokens": max_tokens, "temperature": temperature, "stream": stream}
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]
        payload["chat_template_kwargs"] = {"enable_thinking": bool(self.enable_thinking)}
        return payload
    def _normalize(self, text: str) -> str:
        if self.enable_thinking:
            return text or ""
        return normalize_llm_response(text or "")
    async def health(self) -> bool:
        for base in self._candidate_bases():
            try:
                async with httpx.AsyncClient() as client:
                    r = await client.get(f"{base}/models", headers=self._headers(), timeout=self.config.health_timeout)
                    if r.status_code in (200, 404):
                        return True
            except Exception:
                continue
        return False

    async def _post_chat(self, base: str, payload: dict, timeout: float):
        try:
            async with httpx.AsyncClient() as client:
                return await client.post(f"{base}/chat/completions", headers=self._headers(), json=payload, timeout=timeout)
        except httpx.TimeoutException as e:
            raise ProviderTimeout(f"Greenwich timeout: {e}") from e
        except httpx.ConnectError as e:
            raise ProviderUnavailable(f"Greenwich unreachable at {base}: {e}") from e
    async def chat(self, messages, *, model=None, max_tokens=1024, temperature=0.3, tools=None, timeout=60.0):
        mdl = model or self.default_model
        payload = self._payload(messages, model=mdl, max_tokens=max_tokens, temperature=temperature, tools=tools, stream=False)
        started = time.monotonic()
        last_err = None
        for base in self._candidate_bases():
            try:
                r = await self._post_chat(base, payload, timeout)
            except (ProviderUnavailable, ProviderTimeout) as e:
                last_err = e
                continue
            if r.status_code != 200:
                last_err = ProviderError(f"Greenwich {r.status_code}: {r.text[:200]}")
                continue
            data = r.json()
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message", {}) or {}
            content = self._normalize(msg.get("content", "") or "")
            u = data.get("usage", {}) or {}
            usage = Usage(prompt_tokens=int(u.get("prompt_tokens", 0)), completion_tokens=int(u.get("completion_tokens", 0)), total_tokens=int(u.get("total_tokens", 0)) or (int(u.get("prompt_tokens", 0)) + int(u.get("completion_tokens", 0))), cost_usd=0.0)
            return ChatResponse(content=content, model=mdl, provider=self.name, usage=usage, finish_reason=choice.get("finish_reason", "stop") or "stop", tool_calls=msg.get("tool_calls") or [], raw=data, latency_ms=(time.monotonic() - started) * 1000)
        if last_err:
            raise last_err
        raise ProviderUnavailable("Greenwich stack unreachable on :8085 and :8090")
    async def stream(self, messages, *, model=None, max_tokens=1024, temperature=0.3, tools=None, timeout=60.0):
        mdl = model or self.default_model
        payload = self._payload(messages, model=mdl, max_tokens=max_tokens, temperature=temperature, tools=tools, stream=True)
        last_err = None
        for base in self._candidate_bases():
            try:
                async with httpx.AsyncClient() as client:
                    async with client.stream("POST", f"{base}/chat/completions", headers=self._headers(), json=payload, timeout=timeout) as r:
                        if r.status_code != 200:
                            body = await r.aread()
                            last_err = ProviderError(f"Greenwich stream {r.status_code}: {body[:200]!r}")
                            break
                        usage = Usage()
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
                        usage = Usage(prompt_tokens=int(u.get("prompt_tokens", 0)), completion_tokens=int(u.get("completion_tokens", 0)), total_tokens=int(u.get("total_tokens", 0)), cost_usd=0.0)
                        yield StreamChunk(delta="", finish_reason="stop", usage=usage)
                        return
            except httpx.TimeoutException as e:
                last_err = ProviderTimeout(f"Greenwich stream timeout: {e}")
                continue
            except httpx.ConnectError as e:
                last_err = ProviderUnavailable(f"Greenwich unreachable at {base}: {e}")
                continue
        if last_err:
            raise last_err
        raise ProviderUnavailable("Greenwich stack unreachable on :8085 and :8090")
    def estimate_cost(self, usage, model=None) -> float:
        return 0.0
def register(registry) -> None:
    registry.register_factory(NAME, GreenwichProvider)
