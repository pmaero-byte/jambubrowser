"""
DecentraCode Mesh (DCM) provider.

Talks to a local DCM node's OpenAI-compatible inference endpoint
(``POST {base}/api/inference/v1/chat/completions``) and exposes the mesh as
a first-class Jambubrowser LLM provider (``JAMBU_LLM_PROVIDER=dcm``).

Wire reality (jambubrowser-side adapters must match DCM, not the other way
around). Two response shapes exist in practice:

- **Non-streaming**: standard OpenAI ``chat.completion`` JSON with a
  ``usage`` block (DCM adds ``total_ms``).
- **Streaming**: DCM forwards its native inference frames over SSE —
  per-token ``{"token": ..., "text": "...", "finished": false}`` and a
  terminal ``{"finished": true, "success": true, "outputText": ...,
  "outputTokens": [...], "perStepMs": [...], "tokPerSec": ...}`` frame
  (see DCM ``backend/routes/inferenceStream.js``). A terminal frame may
  instead carry ``{"finished": true, "error": "..."}``. For robustness the
  parser also accepts OpenAI-style ``choices[].delta.content`` deltas.

The mesh is user-operated infrastructure: cost is zero in USD (DCT
metering happens mesh-side). DCM's production deployments require a
``DID-Sig`` Authorization header; set ``JAMBU_LLM_DCM_AUTH`` to pass one
through (local dev needs none — DCM's auth gate defaults open).
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
    ProviderUnavailable,
    StreamChunk,
    Usage,
    estimate_cost_for_model,
)
from ..config import LLMConfig

NAME = "dcm"

# Models that are actually runnable in a stock DCM checkout (MoE mesh +
# candle dense). Others in DCM's catalog (qwen3.8-27b, gemma-4-26b-a4b-it,
# …) are pass-through: specify them explicitly and DCM resolves or rejects.
MODELS = [
    "qwen1.5-moe-a2.7b",
    "qwen2-0.5b-instruct",
]

supports_tools = False  # DCM's OpenAI facade translates messages to a prompt

# Error-code fragments DCM returns when a runtime/binary is missing. DCM uses
# the JS error name (RuntimeNotImplementedError), the API code
# (RUNTIME_NOT_IMPLEMENTED), and raw spawn failures (ENOENT) depending on
# which runtime the model routes to.
_RUNTIME_MISSING_HINTS = (
    "runtimenotimplemented",
    "runtime_not_implemented",
    "enoent",
    "binary not found",
)

_API_PREFIX = "/api/inference"


class DCMProvider:
    name = NAME
    models = MODELS
    supports_tools = False

    def __init__(self, config: LLMConfig, transport: Optional[httpx.AsyncBaseTransport] = None):
        self.config = config
        self.base_url = config.dcm_base_url.rstrip("/")
        self.default_model = config.dcm_model or MODELS[0]
        self.auth = (config.dcm_auth or "").strip()
        self._transport = transport  # injectable for tests

    # -- helpers -------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.auth:
            headers["Authorization"] = self.auth
        return headers

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport)

    def _status_url(self) -> str:
        return f"{self.base_url}{_API_PREFIX}/status"

    def _chat_url(self) -> str:
        return f"{self.base_url}{_API_PREFIX}/v1/chat/completions"

    def _payload(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        stream: bool,
    ) -> dict[str, Any]:
        return {
            "model": model,
            "messages": [m.to_dict() for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }

    def _raise_for_status(self, status_code: int, body: str, model: str) -> None:
        snippet = body[:300]
        lowered = body.lower()
        if status_code in (401, 403):
            raise ProviderAuthError(
                f"DCM auth failed ({status_code}): {snippet} "
                "(set JAMBU_LLM_DCM_AUTH for production nodes)"
            )
        if status_code in (404, 405):
            raise ProviderError(
                f"DCM {status_code}: inference endpoint not found at "
                f"{self._chat_url()} — is this a DecentraCode backend?"
            )
        if status_code == 503 or any(h in lowered for h in _RUNTIME_MISSING_HINTS):
            raise ProviderUnavailable(
                f"DCM runtime not available for {model}: {snippet} "
                "(build backend/p2pd binaries or start the dense coordinator)"
            )
        raise ProviderError(f"DCM {status_code}: {snippet}")

    # -- Provider protocol ---------------------------------------------------

    async def health(self) -> bool:
        """True when the node answers ``/health`` and some runtime is ready.

        DCM's ``/api/inference/status`` returns 503 *with a state body* when
        the default runtime is missing while another runtime (e.g. the MoE
        sidecar) is ready — health must reflect runtime readiness, not just
        the HTTP status of the default model.
        """
        try:
            async with self._client() as client:
                r = await client.get(
                    f"{self.base_url}/health",
                    timeout=self.config.health_timeout,
                )
                if r.status_code != 200:
                    return False
                s = await client.get(
                    self._status_url(),
                    headers=self._headers(),
                    timeout=self.config.health_timeout,
                )
        except Exception:
            return False
        if s.status_code == 200:
            return True
        if s.status_code == 503:
            try:
                body = s.json()
            except ValueError:
                return False
            if body.get("ready") or body.get("available"):
                return True
            moe = body.get("moe") or {}
            return bool(moe.get("ready") or moe.get("available"))
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
        payload = self._payload(
            messages, model=mdl, max_tokens=max_tokens,
            temperature=temperature, stream=False,
        )
        started = time.monotonic()
        try:
            async with self._client() as client:
                r = await client.post(
                    self._chat_url(), headers=self._headers(),
                    json=payload, timeout=timeout,
                )
        except httpx.TimeoutException as e:
            raise ProviderUnavailable(f"DCM timeout: {e}") from e
        except httpx.ConnectError as e:
            raise ProviderUnavailable(f"DCM unreachable at {self.base_url}: {e}") from e
        if r.status_code != 200:
            self._raise_for_status(r.status_code, r.text, mdl)

        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        u = data.get("usage") or {}
        usage = Usage(
            prompt_tokens=u.get("prompt_tokens", 0),
            completion_tokens=u.get("completion_tokens", 0),
            total_tokens=u.get("total_tokens", 0),
        )
        usage.cost_usd = self.estimate_cost(usage, mdl)
        return ChatResponse(
            content=message.get("content", "") or "",
            model=data.get("model", mdl),
            provider=self.name,
            usage=usage,
            finish_reason=choice.get("finish_reason", "stop"),
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
        payload = self._payload(
            messages, model=mdl, max_tokens=max_tokens,
            temperature=temperature, stream=True,
        )
        try:
            async with self._client() as client:
                async with client.stream(
                    "POST", self._chat_url(), headers=self._headers(),
                    json=payload, timeout=timeout,
                ) as r:
                    if r.status_code != 200:
                        body = (await r.aread()).decode(errors="replace")
                        self._raise_for_status(r.status_code, body, mdl)

                    usage: Optional[Usage] = None
                    async for line in r.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        raw = line[6:].strip()
                        if raw in ("[DONE]", ""):
                            break
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        # Terminal DCM frame (success or error).
                        if event.get("finished"):
                            if event.get("error"):
                                raise ProviderError(
                                    f"DCM stream error: {event['error']}"
                                )
                            output_text = (
                                event.get("outputText")
                                or event.get("output_text")
                                or ""
                            )
                            tokens = (
                                event.get("outputTokens")
                                or event.get("output_tokens")
                                or []
                            )
                            completion_tokens = (
                                len(tokens) if isinstance(tokens, list)
                                else int(tokens or 0)
                            )
                            usage = Usage(
                                prompt_tokens=0,
                                completion_tokens=completion_tokens,
                                total_tokens=completion_tokens,
                            )
                            usage.cost_usd = self.estimate_cost(usage, mdl)
                            yield StreamChunk(
                                delta=output_text,
                                finish_reason="stop",
                                usage=usage,
                            )
                            return
                        if event.get("error"):
                            raise ProviderError(
                                f"DCM stream error: {event['error']}"
                            )

                        # DCM native per-token frame.
                        text = event.get("text")
                        if text:
                            yield StreamChunk(delta=text)
                            continue

                        # OpenAI-style delta (kept for forward compatibility).
                        for choice in event.get("choices") or []:
                            delta = choice.get("delta") or {}
                            if delta.get("content"):
                                yield StreamChunk(delta=delta["content"])
                            if choice.get("finish_reason"):
                                yield StreamChunk(
                                    delta="", finish_reason=choice["finish_reason"],
                                )
                    # Stream ended without a terminal frame.
                    yield StreamChunk(
                        delta="", finish_reason="stop",
                        usage=usage or Usage(),
                    )
        except httpx.TimeoutException as e:
            raise ProviderUnavailable(f"DCM stream timeout: {e}") from e
        except httpx.ConnectError as e:
            raise ProviderUnavailable(f"DCM unreachable at {self.base_url}: {e}") from e

    def estimate_cost(self, usage: Usage, model: Optional[str] = None) -> float:
        # Mesh inference is user-operated: no USD cost. DCT metering is
        # handled by the DCM node itself and surfaced via its billing API.
        return estimate_cost_for_model(self.name, model or self.default_model, usage)


def register(registry) -> None:
    registry.register_factory(NAME, DCMProvider)
