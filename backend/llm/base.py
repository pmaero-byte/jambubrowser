"""
Core types and Provider protocol for the unified LLM layer.

Defines the contract every provider implementation must satisfy. Adding a new
provider means: subclass nothing, just implement the `Provider` protocol.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Optional, Protocol, runtime_checkable


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------

class ProviderError(Exception):
    """Base class for provider-level errors."""


class ProviderTimeout(ProviderError):
    """Provider did not respond within the configured timeout."""


class ProviderAuthError(ProviderError):
    """Missing or invalid credentials."""


class ProviderRateLimit(ProviderError):
    """Provider rate-limited the request."""


class ProviderUnavailable(ProviderError):
    """Provider is not reachable (connection refused, DNS, etc.)."""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ChatMessage:
    """A single message in a chat conversation.

    For tool use, populate `tool_calls` on the assistant message and reference
    the assistant's call from the tool message via `tool_call_id`.
    """
    role: Role
    content: str
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[list[dict]] = None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"role": self.role.value, "content": self.content}
        if self.name:
            d["name"] = self.name
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        return d


@dataclass
class Usage:
    """Token usage + cost for a single request/response."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
        )


@dataclass
class ChatResponse:
    """The result of a non-streaming chat call."""
    content: str
    model: str
    provider: str
    usage: Usage
    finish_reason: str = "stop"
    tool_calls: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)
    latency_ms: float = 0.0

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"ChatResponse(provider={self.provider!r}, model={self.model!r}, "
            f"tokens={self.usage.total_tokens}, cost=${self.usage.cost_usd:.4f}, "
            f"latency={self.latency_ms:.0f}ms)"
        )


@dataclass
class StreamChunk:
    """A single chunk from a streaming response.

    `delta` is the incremental text. The final chunk will have `finish_reason`
    set and (optionally) a populated `usage` once the provider reports totals.
    """
    delta: str = ""
    finish_reason: Optional[str] = None
    usage: Optional[Usage] = None
    tool_calls: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class Provider(Protocol):
    """Every LLM provider implements this protocol.

    Implementations should be safe to instantiate once and reuse across
    concurrent requests. The registry holds provider instances per-name.
    """
    name: str
    models: list[str]

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
        """Make a single chat call and return the full response."""
        ...

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
        """Stream the response token-by-token."""
        ...

    async def health(self) -> bool:
        """Return True if the provider is reachable and healthy."""
        ...

    def estimate_cost(self, usage: Usage, model: Optional[str] = None) -> float:
        """Estimate the dollar cost of a request with the given usage."""
        ...


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

# Cost in USD per 1M tokens. Keys are (provider_prefix, model_substring).
# Substring match is case-insensitive. The first match wins.
_PRICING_TABLE: list[tuple[str, str, float, float]] = [
    # (provider, model_substring, prompt_per_1m, completion_per_1m)
    ("anthropic", "claude-opus", 15.0, 75.0),
    ("anthropic", "claude-sonnet", 3.0, 15.0),
    ("anthropic", "claude-haiku", 0.25, 1.25),
    ("openai", "gpt-4o", 2.5, 10.0),
    ("openai", "gpt-4.1", 2.0, 8.0),
    ("openai", "gpt-4-turbo", 10.0, 30.0),
    ("openai", "o1", 15.0, 60.0),
    ("openai", "o1-mini", 3.0, 12.0),
    ("openai", "gpt-3.5", 0.5, 1.5),
    ("minimax", "minimax", 1.0, 3.0),
]

# Local providers: zero marginal cost (electricity aside).
_LOCAL_PROVIDERS = {"ollama", "mlx", "mock"}


def estimate_cost_for_model(provider: str, model: str, usage: Usage) -> float:
    """Look up pricing for (provider, model) and compute dollar cost."""
    if provider in _LOCAL_PROVIDERS:
        return 0.0
    pl = provider.lower()
    ml = model.lower()
    for p_prefix, m_sub, p_ppm, c_ppm in _PRICING_TABLE:
        if p_prefix in pl and m_sub in ml:
            return (usage.prompt_tokens / 1_000_000) * p_ppm + (usage.completion_tokens / 1_000_000) * c_ppm
    # Unknown model on a paid provider — assume conservative $5/$15
    if usage.prompt_tokens or usage.completion_tokens:
        return (usage.prompt_tokens / 1_000_000) * 5.0 + (usage.completion_tokens / 1_000_000) * 15.0
    return 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def collect_stream(stream: AsyncIterator[StreamChunk]) -> ChatResponse:
    """Drain a stream into a ChatResponse, concatenating deltas."""
    content_parts: list[str] = []
    usage: Optional[Usage] = None
    finish_reason: Optional[str] = None
    tool_calls: list[dict] = []
    started = time.monotonic()
    async for chunk in stream:
        if chunk.delta:
            content_parts.append(chunk.delta)
        if chunk.usage is not None:
            usage = chunk.usage
        if chunk.finish_reason is not None:
            finish_reason = chunk.finish_reason
        if chunk.tool_calls:
            tool_calls.extend(chunk.tool_calls)
    content = "".join(content_parts)
    final_usage = usage or Usage()
    return ChatResponse(
        content=content,
        model="",
        provider="",
        usage=final_usage,
        finish_reason=finish_reason or "stop",
        tool_calls=tool_calls,
        latency_ms=(time.monotonic() - started) * 1000,
    )


# ---------------------------------------------------------------------------
# Response normalization — strip model-specific preambles so JSON parsers
# downstream don't choke on the first character.
# ---------------------------------------------------------------------------

def normalize_llm_response(content: str) -> str:
    """Normalize an LLM response by stripping model-specific preambles.

    Some reasoning models (e.g. minimax M3, DeepSeek R1) emit a
    ``<think>...</think>`` block before their actual answer. If the caller
    expects JSON, ``json.loads()`` will throw on the first character of the
    think block. This helper strips the preamble so the caller's parser sees
    only the actual answer.

    Currently handles:
      - ``<think>...</think>`` blocks (M3, R1, Qwen3-thinking, etc.)
      - ```json ... ``` fenced JSON blocks
      - ``` ... ``` fenced blocks (any language)
      - leading/trailing whitespace
    """
    text = content.strip()

    # Strip <think>...</think> preambles. Some models emit a single block;
    # others emit multiple — take everything after the *last* close tag so we
    # never leave an unclosed think block in the result.
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1].strip()

    # Strip ```json ... ``` or ``` ... ``` fenced blocks (keep inner content).
    if text.startswith("```"):
        # Find the end of the opening fence (e.g. ```json)
        first_newline = text.find("\n")
        if first_newline == -1:
            # Single-line fenced — just drop fences
            text = text.strip("`").strip()
        else:
            body = text[first_newline + 1 :]
            if body.endswith("```"):
                body = body[:-3]
            text = body.strip()

    return text

