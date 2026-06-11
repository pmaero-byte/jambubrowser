"""
Provider registry — singleton that holds provider instances by name.

The registry:
- Auto-discovers all provider classes in `backend.llm.providers.*`
- Instantiates them lazily on first use
- Routes `chat()` / `stream_chat()` to the configured default
- Supports env-driven default + fallback chain

External code should NOT instantiate providers directly — go through this
registry.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import pkgutil
import threading
from typing import AsyncIterator, Callable, Optional

from .base import (
    ChatMessage,
    ChatResponse,
    Provider,
    ProviderError,
    ProviderUnavailable,
    StreamChunk,
    estimate_cost_for_model,
)
from .config import LLMConfig, get_config

log = logging.getLogger("jambu.llm.registry")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class ProviderRegistry:
    """Holds instantiated providers by name, with default + fallback resolution."""

    def __init__(self, config: Optional[LLMConfig] = None):
        self._config = config or get_config()
        self._providers: dict[str, Provider] = {}
        self._factories: dict[str, Callable[[LLMConfig], Provider]] = {}
        self._lock = threading.RLock()
        self._last_latency_ms: dict[str, float] = {}

    # -- factory registration ------------------------------------------------

    def register_factory(self, name: str, factory: Callable[[LLMConfig], Provider]) -> None:
        """Register a provider factory. Used at module import time."""
        with self._lock:
            self._factories[name] = factory
            # Drop any cached instance so the new factory is used
            self._providers.pop(name, None)

    def register(self, name: str, provider: Provider) -> None:
        """Register a pre-built provider instance (used in tests)."""
        with self._lock:
            self._providers[name] = provider

    # -- lookup --------------------------------------------------------------

    def get(self, name: str) -> Provider:
        """Return a provider by name, instantiating it on first use."""
        with self._lock:
            if name in self._providers:
                return self._providers[name]
            if name not in self._factories:
                self._discover_providers()
            if name not in self._factories:
                raise KeyError(f"Unknown LLM provider: {name!r}. Known: {sorted(self._factories)}")
            provider = self._factories[name](self._config)
            self._providers[name] = provider
            return provider

    def has(self, name: str) -> bool:
        return name in self._factories or name in self._providers

    def list_available(self) -> list[str]:
        return sorted(set(self._factories) | set(self._providers))

    def _discover_providers(self) -> None:
        """Auto-discover provider classes in backend.llm.providers package."""
        try:
            from . import providers as providers_pkg
        except ImportError:
            return
        for mod_info in pkgutil.iter_modules(providers_pkg.__path__):
            module_name = mod_info.name
            if module_name.startswith("_"):
                continue
            full_name = f"{providers_pkg.__name__}.{module_name}"
            try:
                module = importlib.import_module(full_name)
            except Exception as e:  # pragma: no cover
                log.debug("Skipping provider module %s: %s", full_name, e)
                continue
            register = getattr(module, "register", None)
            if callable(register):
                try:
                    register(self)
                except Exception as e:  # pragma: no cover
                    log.warning("Provider %s register() failed: %s", module_name, e)

    # -- default resolution --------------------------------------------------

    def get_default(self) -> Provider:
        """Resolve the default provider based on env config + health."""
        name = self._config.default_provider
        if name and name != "auto":
            return self.get(name)

        # "auto": pick the first healthy provider in fallback chain
        chain = self._config.fallback_chain
        for candidate in chain:
            if not self.has(candidate):
                continue
            if self._config.force_local_only and candidate not in ("ollama", "mlx", "mock"):
                continue
            try:
                provider = self.get(candidate)
                return provider
            except Exception as e:
                log.debug("Provider %s unavailable: %s", candidate, e)
                continue
        # Last resort: mock
        return self.get("mock")

    def set_default(self, name: str) -> None:
        self._config.default_provider = name

    # -- convenience: chat / stream against default --------------------------

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        tools: Optional[list[dict]] = None,
        timeout: Optional[float] = None,
    ) -> ChatResponse:
        prov = self.get(provider) if provider else self.get_default()
        cfg = self._config
        response = await prov.chat(
            messages,
            model=model or prov.models[0] if prov.models else (model or cfg.model_for(prov.name)),
            max_tokens=max_tokens or cfg.max_tokens,
            temperature=temperature if temperature is not None else cfg.temperature,
            tools=tools,
            timeout=timeout or cfg.request_timeout,
        )
        # Backfill provider/model if the implementation left them blank
        if not response.provider:
            response.provider = prov.name
        if not response.model:
            response.model = model or cfg.model_for(prov.name)
        # Compute cost if the provider didn't
        if not response.usage.cost_usd:
            response.usage.cost_usd = estimate_cost_for_model(
                response.provider, response.model, response.usage
            )
        return response

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        tools: Optional[list[dict]] = None,
        timeout: Optional[float] = None,
    ) -> AsyncIterator[StreamChunk]:
        prov = self.get(provider) if provider else self.get_default()
        cfg = self._config
        async for chunk in prov.stream(
            messages,
            model=model or cfg.model_for(prov.name),
            max_tokens=max_tokens or cfg.max_tokens,
            temperature=temperature if temperature is not None else cfg.temperature,
            tools=tools,
            timeout=timeout or cfg.request_timeout,
        ):
            yield chunk

    # -- observability -------------------------------------------------------

    def record_latency(self, name: str, ms: float) -> None:
        self._last_latency_ms[name] = ms

    def last_latency(self, name: str) -> float:
        return self._last_latency_ms.get(name, 0.0)


# ---------------------------------------------------------------------------
# Module-level singleton + shortcuts
# ---------------------------------------------------------------------------

_REGISTRY: Optional[ProviderRegistry] = None
_REGISTRY_LOCK = threading.Lock()


def get_registry() -> ProviderRegistry:
    global _REGISTRY
    with _REGISTRY_LOCK:
        if _REGISTRY is None:
            _REGISTRY = ProviderRegistry()
            # Eagerly discover built-in providers
            _REGISTRY._discover_providers()
        return _REGISTRY


def reset_registry() -> None:
    """Clear the registry — used by tests."""
    global _REGISTRY
    with _REGISTRY_LOCK:
        _REGISTRY = None


def get_provider(name: str) -> Provider:
    return get_registry().get(name)


def get_default() -> Provider:
    return get_registry().get_default()


def register(name: str, provider: Provider) -> None:
    """Register a provider instance by name."""
    get_registry().register(name, provider)


def set_default_provider(name: str) -> None:
    get_registry().set_default(name)


async def chat(
    messages: list[ChatMessage],
    *,
    provider: Optional[str] = None,
    **kwargs,
) -> ChatResponse:
    """One-shot chat against the configured default provider."""
    return await get_registry().chat(messages, provider=provider, **kwargs)


async def stream_chat(
    messages: list[ChatMessage],
    *,
    provider: Optional[str] = None,
    **kwargs,
) -> AsyncIterator[StreamChunk]:
    """Stream chat from the configured default provider."""
    async for chunk in get_registry().stream(messages, provider=provider, **kwargs):
        yield chunk
