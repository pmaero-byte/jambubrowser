"""
Unified LLM Provider Layer
===========================

A single, well-typed interface for talking to any LLM provider (Anthropic,
OpenAI, Ollama, MLX, MiniMax, mock). This is the only LLM abstraction the rest
of the codebase should import from.

Public API
----------
- `get_provider(name)`         — resolve a provider by name (lazy init)
- `get_default()`              — env-driven default provider
- `chat(messages, **kwargs)`   — one-shot chat call against the default
- `stream_chat(messages, ...)` — async iterator over StreamChunks
- `register(name, factory)`    — register a new provider
- `Router`                     — smart routing across multiple providers
- `ChatMessage`, `ChatResponse`, `Usage`, `StreamChunk` — data types

Usage
-----
    from backend.llm import chat, ChatMessage, Role

    answer = await chat(
        [ChatMessage(role=Role.USER, content="Hello, world!")],
        temperature=0.7,
    )
    print(answer.content)
"""

from .base import (
    Role,
    ChatMessage,
    Usage,
    ChatResponse,
    StreamChunk,
    Provider,
    ProviderError,
    ProviderTimeout,
    ProviderAuthError,
    ProviderRateLimit,
    estimate_cost_for_model,
    normalize_llm_response,
)
from .registry import (
    ProviderRegistry,
    get_registry,
    get_provider,
    get_default,
    register,
    chat,
    stream_chat,
    set_default_provider,
)
from .routing import Router, RoutingStrategy
from .config import LLMConfig, get_config, reload_config

__all__ = [
    # Types
    "Role",
    "ChatMessage",
    "Usage",
    "ChatResponse",
    "StreamChunk",
    "Provider",
    "ProviderError",
    "ProviderTimeout",
    "ProviderAuthError",
    "ProviderRateLimit",
    "estimate_cost_for_model",
    "normalize_llm_response",
    # Registry
    "ProviderRegistry",
    "get_registry",
    "get_provider",
    "get_default",
    "register",
    "chat",
    "stream_chat",
    "set_default_provider",
    # Routing
    "Router",
    "RoutingStrategy",
    # Config
    "LLMConfig",
    "get_config",
    "reload_config",
]
