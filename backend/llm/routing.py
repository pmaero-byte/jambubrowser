"""
Smart routing across multiple LLM providers.

Strategies
----------
- `cheapest`     prefer local/free providers, escalate to cloud only on need
- `fastest`      pick the provider with lowest observed rolling p50 latency
- `quality`      prefer the strongest model available (Claude Opus, GPT-4o, etc.)
- `local_only`   refuse to call any cloud provider (privacy enforcement)
- `fallback`     try the primary, cascade through chain on error
- `auto`         heuristic: local if available, else cheapest paid, else any

The router is the single entry point the agent loop uses for chat/stream. It
informs the audit log which provider was selected and why.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .base import (
    ChatMessage,
    ChatResponse,
    Provider,
    ProviderError,
    ProviderUnavailable,
    StreamChunk,
)
from .registry import ProviderRegistry, get_registry

log = logging.getLogger("jambu.llm.routing")


class RoutingStrategy(str, Enum):
    CHEAPEST = "cheapest"
    FASTEST = "fastest"
    QUALITY = "quality"
    LOCAL_ONLY = "local_only"
    FALLBACK = "fallback"
    AUTO = "auto"


@dataclass
class RoutingDecision:
    provider: str
    reason: str
    candidates_considered: list[str] = field(default_factory=list)
    latency_ms: float = 0.0


class _LatencyTracker:
    """Rolling p50 latency per provider."""
    WINDOW = 20

    def __init__(self):
        self.samples: dict[str, deque[float]] = {}

    def record(self, name: str, ms: float) -> None:
        dq = self.samples.setdefault(name, deque(maxlen=self.WINDOW))
        dq.append(ms)

    def p50(self, name: str) -> float:
        dq = self.samples.get(name)
        if not dq:
            return 0.0
        s = sorted(dq)
        return s[len(s) // 2]

    def fastest(self) -> Optional[str]:
        candidates = [(n, self.p50(n)) for n in self.samples if self.p50(n) > 0]
        if not candidates:
            return None
        return min(candidates, key=lambda x: x[1])[0]


class Router:
    """Picks a provider per request based on a strategy."""

    LOCAL_PROVIDERS = {"ollama", "mlx", "mock"}
    QUALITY_PREFERENCE = [
        "moa",       # Mixture-of-Agents (aggregates multiple perspectives)
        "anthropic",  # Claude Opus / Sonnet
        "openai",     # GPT-4o / o1
        "minimax",
        "ollama",
        "mlx",
    ]

    def __init__(self, registry: Optional[ProviderRegistry] = None, strategy: RoutingStrategy = RoutingStrategy.AUTO):
        self.registry = registry or get_registry()
        self.strategy = strategy
        self._latency = _LatencyTracker()
        self._last_decision: Optional[RoutingDecision] = None

    # -- public API ----------------------------------------------------------

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        strategy: Optional[RoutingStrategy] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        tools: Optional[list[dict]] = None,
        timeout: Optional[float] = None,
    ) -> ChatResponse:
        strat = strategy or self.strategy
        provider, decision = await self._select(messages, strategy=strat, tools=tools)
        if provider is None:
            raise ProviderUnavailable(f"No provider available for strategy {strat!r}")
        self._last_decision = decision
        started = time.monotonic()
        try:
            response = await provider.chat(
                messages,
                model=model,
                max_tokens=max_tokens or 1024,
                temperature=temperature if temperature is not None else 0.3,
                tools=tools,
                timeout=timeout or 30.0,
            )
        except ProviderError:
            # Single failure inside _select: try the next fallback if strategy=FALLBACK
            if strat == RoutingStrategy.FALLBACK:
                return await self._chat_with_fallback(
                    messages, model=model, max_tokens=max_tokens,
                    temperature=temperature, tools=tools, timeout=timeout,
                )
            raise
        self._latency.record(provider.name, response.latency_ms or ((time.monotonic() - started) * 1000))
        return response

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        strategy: Optional[RoutingStrategy] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        tools: Optional[list[dict]] = None,
        timeout: Optional[float] = None,
    ):
        strat = strategy or self.strategy
        provider, decision = await self._select(messages, strategy=strat, tools=tools)
        if provider is None:
            raise ProviderUnavailable(f"No provider available for strategy {strat!r}")
        self._last_decision = decision
        started = time.monotonic()
        try:
            async for chunk in provider.stream(
                messages,
                model=model,
                max_tokens=max_tokens or 1024,
                temperature=temperature if temperature is not None else 0.3,
                tools=tools,
                timeout=timeout or 30.0,
            ):
                yield chunk
        except ProviderError:
            if strat == RoutingStrategy.FALLBACK:
                # Switch to next provider mid-stream — close current and start over
                raise
        finally:
            self._latency.record(provider.name, (time.monotonic() - started) * 1000)

    # -- selection -----------------------------------------------------------

    async def _select(
        self,
        messages: list[ChatMessage],
        *,
        strategy: RoutingStrategy,
        tools: Optional[list[dict]],
    ) -> tuple[Optional[Provider], RoutingDecision]:
        chain = self.registry._config.fallback_chain
        available = [p for p in chain if self.registry.has(p)]
        decision = RoutingDecision(provider="", reason="", candidates_considered=available)

        if not available:
            return None, decision

        chosen: Optional[str] = None
        reason: str = ""

        if strategy == RoutingStrategy.LOCAL_ONLY:
            locals_ = [p for p in available if p in self.LOCAL_PROVIDERS]
            chosen = locals_[0] if locals_ else None
            reason = "local_only enforcement"

        elif strategy == RoutingStrategy.CHEAPEST:
            locals_ = [p for p in available if p in self.LOCAL_PROVIDERS]
            chosen = locals_[0] if locals_ else available[0]
            reason = "prefer local first"

        elif strategy == RoutingStrategy.FASTEST:
            chosen = self._latency.fastest() or available[0]
            reason = "lowest observed p50 latency"

        elif strategy == RoutingStrategy.QUALITY:
            for candidate in self.QUALITY_PREFERENCE:
                if candidate in available:
                    chosen = candidate
                    reason = "quality preference"
                    break
            if not chosen:
                chosen = available[0]
                reason = "fallback to first available"

        elif strategy == RoutingStrategy.FALLBACK:
            chosen = available[0]
            reason = "fallback chain head"

        else:  # AUTO
            locals_ = [p for p in available if p in self.LOCAL_PROVIDERS]
            if locals_:
                chosen = locals_[0]
                reason = "auto: local available"
            else:
                chosen = available[0]
                reason = "auto: no local, taking head of chain"

        if not chosen:
            return None, decision

        try:
            provider = self.registry.get(chosen)
        except Exception as e:
            log.debug("Could not instantiate %s: %s", chosen, e)
            return None, decision

        # If tool use is required and the provider doesn't support it, bump up
        if tools and not getattr(provider, "supports_tools", True):
            for upgrade in available:
                if upgrade == chosen:
                    continue
                p2 = self.registry.get(upgrade)
                if getattr(p2, "supports_tools", True):
                    chosen = upgrade
                    provider = p2
                    reason += " (bumped for tool support)"
                    break

        decision.provider = chosen
        decision.reason = reason
        return provider, decision

    async def _chat_with_fallback(
        self,
        messages: list[ChatMessage],
        *,
        model: Optional[str],
        max_tokens: Optional[int],
        temperature: Optional[float],
        tools: Optional[list[dict]],
        timeout: Optional[float],
    ) -> ChatResponse:
        """Try each provider in the chain until one succeeds."""
        chain = [p for p in self.registry._config.fallback_chain if self.registry.has(p)]
        last_err: Optional[Exception] = None
        for name in chain:
            try:
                provider = self.registry.get(name)
                started = time.monotonic()
                resp = await provider.chat(
                    messages,
                    model=model,
                    max_tokens=max_tokens or 1024,
                    temperature=temperature if temperature is not None else 0.3,
                    tools=tools,
                    timeout=timeout or 30.0,
                )
                self._latency.record(name, resp.latency_ms or ((time.monotonic() - started) * 1000))
                return resp
            except Exception as e:
                log.warning("Provider %s failed: %s", name, e)
                last_err = e
                continue
        if last_err:
            raise last_err
        raise ProviderUnavailable("No providers in fallback chain succeeded")

    # -- introspection -------------------------------------------------------

    def last_decision(self) -> Optional[RoutingDecision]:
        return self._last_decision

    def stats(self) -> dict:
        return {
            "strategy": self.strategy.value,
            "providers": {
                name: {
                    "samples": len(self._latency.samples.get(name, [])),
                    "p50_ms": self._latency.p50(name),
                }
                for name in self.registry.list_available()
            },
        }
