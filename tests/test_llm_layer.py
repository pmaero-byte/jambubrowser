"""
Tests for the unified LLM provider layer (backend.llm).

Covers:
- Base types (ChatMessage, Usage, ChatResponse, StreamChunk)
- Provider protocol conformance (mock)
- Registry: registration, lookup, default resolution
- Routing strategies: cheapest, fastest, quality, fallback, local_only
- Cost estimation
- Tool call serialization (Anthropic + OpenAI formats)
"""

import asyncio
import json
import os
import time

import pytest


@pytest.fixture(autouse=True)
def isolate_registry(monkeypatch):
    """Reset the LLM registry + config before each test."""
    from backend.llm import reload_config
    from backend.llm.registry import reset_registry
    reload_config()
    reset_registry()
    yield
    reset_registry()


# ---------------------------------------------------------------------------
# Base types
# ---------------------------------------------------------------------------

class TestBaseTypes:
    def test_chat_message_to_dict(self):
        from backend.llm.base import ChatMessage, Role
        m = ChatMessage(role=Role.USER, content="hi")
        d = m.to_dict()
        assert d["role"] == "user"
        assert d["content"] == "hi"

    def test_chat_message_with_tool_call_id(self):
        from backend.llm.base import ChatMessage, Role
        m = ChatMessage(role=Role.TOOL, content="42", tool_call_id="call_1")
        d = m.to_dict()
        assert d["role"] == "tool"
        assert d["tool_call_id"] == "call_1"

    def test_usage_addition(self):
        from backend.llm.base import Usage
        a = Usage(prompt_tokens=10, completion_tokens=20, total_tokens=30, cost_usd=0.01)
        b = Usage(prompt_tokens=5, completion_tokens=15, total_tokens=20, cost_usd=0.02)
        c = a + b
        assert c.prompt_tokens == 15
        assert c.completion_tokens == 35
        assert c.total_tokens == 50
        assert abs(c.cost_usd - 0.03) < 1e-9

    def test_cost_estimation_local(self):
        from backend.llm.base import Usage, estimate_cost_for_model
        u = Usage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)
        assert estimate_cost_for_model("ollama", "gemma4:12b", u) == 0.0
        assert estimate_cost_for_model("mlx", "gemma-4-12b", u) == 0.0
        assert estimate_cost_for_model("mock", "mock", u) == 0.0

    def test_cost_estimation_anthropic_sonnet(self):
        from backend.llm.base import Usage, estimate_cost_for_model
        u = Usage(prompt_tokens=1_000_000, completion_tokens=1_000_000, total_tokens=2_000_000)
        c = estimate_cost_for_model("anthropic", "claude-sonnet-4-6", u)
        assert abs(c - (3.0 + 15.0)) < 0.001  # $3/M input + $15/M output

    def test_cost_estimation_anthropic_opus(self):
        from backend.llm.base import Usage, estimate_cost_for_model
        u = Usage(prompt_tokens=1_000_000, completion_tokens=1_000_000, total_tokens=2_000_000)
        c = estimate_cost_for_model("anthropic", "claude-opus-4-8", u)
        assert abs(c - (15.0 + 75.0)) < 0.001

    def test_cost_estimation_openai_gpt4o(self):
        from backend.llm.base import Usage, estimate_cost_for_model
        u = Usage(prompt_tokens=1_000_000, completion_tokens=1_000_000, total_tokens=2_000_000)
        c = estimate_cost_for_model("openai", "gpt-4o", u)
        assert abs(c - (2.5 + 10.0)) < 0.001

    def test_cost_estimation_unknown_model_paid(self):
        from backend.llm.base import Usage, estimate_cost_for_model
        u = Usage(prompt_tokens=100, completion_tokens=100, total_tokens=200)
        c = estimate_cost_for_model("unknown_provider", "weird-model", u)
        assert c > 0  # conservative estimate


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_default_config(self, monkeypatch):
        # Neutralize JAMBU_LLM_PROVIDER and JAMBU_LLM_FALLBACK_CHAIN so we
        # assert the true code defaults, not whatever the calling environment
        # (.env, CI) overrides them to. Without the fallback-chain delenv,
        # a harness run that exports JAMBU_LLM_FALLBACK_CHAIN=minimax in
        # .env leaks into this test when it runs after a mutating test.
        monkeypatch.delenv("JAMBU_LLM_PROVIDER", raising=False)
        monkeypatch.delenv("JAMBU_LLM_FALLBACK_CHAIN", raising=False)
        from backend.llm import reload_config
        c = reload_config()
        assert c.default_provider == "auto"
        assert "ollama" in c.fallback_chain
        assert c.max_tokens == 1024
        assert c.temperature == 0.3

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("JAMBU_LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-12345")
        monkeypatch.setenv("JAMBU_LLM_TEMPERATURE", "0.7")
        monkeypatch.setenv("JAMBU_LLM_MAX_TOKENS", "2048")
        from backend.llm import reload_config
        c = reload_config()
        assert c.default_provider == "anthropic"
        assert c.anthropic_api_key == "test-key-12345"
        assert abs(c.temperature - 0.7) < 0.01
        assert c.max_tokens == 2048

    def test_force_local_only(self, monkeypatch):
        monkeypatch.setenv("JAMBU_LLM_LOCAL_ONLY", "true")
        from backend.llm import reload_config
        c = reload_config()
        assert c.force_local_only is True

    def test_model_for(self):
        from backend.llm import get_config
        c = get_config()
        assert c.model_for("anthropic") == c.anthropic_model
        assert c.model_for("openai") == c.openai_model
        assert c.model_for("ollama") == c.ollama_model
        assert c.model_for("mlx") == c.mlx_model
        assert c.model_for("minimax") == c.minimax_model
        assert c.model_for("unknown") == ""


# ---------------------------------------------------------------------------
# Provider implementations (using mock to avoid network)
# ---------------------------------------------------------------------------

class TestMockProvider:
    def test_health_always_true(self):
        from backend.llm import get_config
        from backend.llm.providers.mock import MockProvider
        p = MockProvider(get_config())
        assert asyncio.run(p.health()) is True

    def test_chat_echo(self):
        from backend.llm import get_config
        from backend.llm.providers.mock import MockProvider
        from backend.llm.base import ChatMessage, Role
        p = MockProvider(get_config())
        resp = asyncio.run(p.chat([ChatMessage(role=Role.USER, content="hello")]))
        assert "hello" in resp.content
        assert resp.provider == "mock"
        assert resp.usage.total_tokens > 0

    def test_chat_with_tool_call(self):
        from backend.llm import get_config
        from backend.llm.providers.mock import MockProvider
        from backend.llm.base import ChatMessage, Role
        p = MockProvider(get_config())
        resp = asyncio.run(p.chat(
            [ChatMessage(role=Role.USER, content='{"tool": "web_search", "args": {"query": "rust"}}')],
        ))
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0]["name"] == "web_search"

    def test_stream_collects_to_same_content(self):
        from backend.llm import get_config
        from backend.llm.providers.mock import MockProvider
        from backend.llm.base import ChatMessage, Role, collect_stream
        p = MockProvider(get_config())
        async def go():
            stream = p.stream([ChatMessage(role=Role.USER, content="hello world")])
            return await collect_stream(stream)
        resp = asyncio.run(go())
        assert resp.content
        assert "hello world" in resp.content


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_providers_discovered(self):
        from backend.llm.registry import get_registry
        reg = get_registry()
        names = reg.list_available()
        for expected in ["mock", "ollama", "mlx", "anthropic", "openai", "minimax"]:
            assert expected in names, f"missing provider: {expected}"

    def test_get_provider_returns_provider(self):
        from backend.llm.registry import get_registry
        reg = get_registry()
        p = reg.get("mock")
        assert p.name == "mock"
        assert "mock-echo" in p.models

    def test_get_unknown_raises(self):
        from backend.llm.registry import get_registry
        reg = get_registry()
        with pytest.raises(KeyError):
            reg.get("nonexistent_provider")

    def test_set_default(self):
        from backend.llm.registry import get_registry
        reg = get_registry()
        reg.set_default("mock")
        from backend.llm import get_default
        assert get_default().name == "mock"

    def test_register_custom_provider(self):
        from backend.llm.base import ChatMessage, Role, Provider
        from backend.llm.registry import get_registry
        class CustomProv:
            name = "test_custom"
            models = ["custom-1"]
            async def chat(self, messages, **kwargs):
                from backend.llm.base import ChatResponse, Usage
                return ChatResponse(content="custom", model="custom-1", provider="test_custom", usage=Usage())
            async def stream(self, messages, **kwargs):
                from backend.llm.base import StreamChunk
                yield StreamChunk(delta="custom", finish_reason="stop")
            async def health(self): return True
            def estimate_cost(self, usage, model=None): return 0.0
        reg = get_registry()
        reg.register("test_custom", CustomProv())
        assert reg.has("test_custom")
        p = reg.get("test_custom")
        assert p.name == "test_custom"


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

class TestRouting:
    def test_local_only_skips_cloud(self, monkeypatch):
        monkeypatch.setenv("JAMBU_LLM_FALLBACK_CHAIN", "ollama,anthropic,openai")
        from backend.llm import reload_config
        reload_config()
        from backend.llm.registry import reset_registry
        reset_registry()

        # Mock the Ollama provider's chat to avoid real HTTP calls
        from backend.llm.base import ChatResponse, Usage
        async def _mock_chat(self, messages, **kwargs):
            return ChatResponse(
                content="mock response",
                model="gemma4:12b-it-qat",
                provider="ollama",
                usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
                latency_ms=5.0,
            )
        from backend.llm.providers.ollama import OllamaProvider
        monkeypatch.setattr(OllamaProvider, "chat", _mock_chat)

        from backend.llm.routing import Router, RoutingStrategy
        router = Router(strategy=RoutingStrategy.LOCAL_ONLY)
        # Should pick ollama (local), not anthropic
        async def go():
            from backend.llm.base import ChatMessage, Role
            return await router.chat([ChatMessage(role=Role.USER, content="hi")])
        resp = asyncio.run(go())
        assert resp.provider in ("ollama", "mlx", "mock")

    def test_fallback_strategy(self, monkeypatch):
        monkeypatch.setenv("JAMBU_LLM_FALLBACK_CHAIN", "mock,ollama")
        from backend.llm import reload_config
        reload_config()
        from backend.llm.registry import reset_registry
        reset_registry()
        from backend.llm.routing import Router, RoutingStrategy
        router = Router(strategy=RoutingStrategy.FALLBACK)
        async def go():
            from backend.llm.base import ChatMessage, Role
            return await router.chat([ChatMessage(role=Role.USER, content="hi")])
        resp = asyncio.run(go())
        # Should succeed via mock
        assert resp.provider == "mock"

    def test_routing_decision_recorded(self, monkeypatch):
        monkeypatch.setenv("JAMBU_LLM_FALLBACK_CHAIN", "mock")
        from backend.llm import reload_config
        reload_config()
        from backend.llm.registry import reset_registry
        reset_registry()
        from backend.llm.routing import Router, RoutingStrategy
        router = Router(strategy=RoutingStrategy.AUTO)
        async def go():
            from backend.llm.base import ChatMessage, Role
            await router.chat([ChatMessage(role=Role.USER, content="hi")])
        asyncio.run(go())
        d = router.last_decision()
        assert d is not None
        assert d.provider
        assert d.reason

    def test_latency_tracking(self):
        from backend.llm.routing import Router, _LatencyTracker
        t = _LatencyTracker()
        t.record("mock", 50.0)
        t.record("mock", 100.0)
        t.record("mock", 75.0)
        # p50 of [50, 75, 100] is 75
        assert t.p50("mock") == 75.0
        assert t.fastest() == "mock"


# ---------------------------------------------------------------------------
# Tool format serialization (for Anthropic + OpenAI)
# ---------------------------------------------------------------------------

class TestToolFormatConversion:
    def test_anthropic_conversion(self):
        from backend.llm.providers.anthropic import _tools_to_anthropic
        openai_style = [
            {"name": "web_search", "description": "Search the web", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}
        ]
        out = _tools_to_anthropic(openai_style)
        assert out[0]["name"] == "web_search"
        assert "input_schema" in out[0]
        assert out[0]["input_schema"]["type"] == "object"

    def test_anthropic_system_extraction(self):
        from backend.llm.providers.anthropic import _messages_to_anthropic
        from backend.llm.base import ChatMessage, Role
        msgs = [
            ChatMessage(role=Role.SYSTEM, content="You are helpful"),
            ChatMessage(role=Role.USER, content="hi"),
        ]
        anthropic_msgs, system = _messages_to_anthropic(msgs, None)
        assert system == "You are helpful"
        assert len(anthropic_msgs) == 1
        assert anthropic_msgs[0]["role"] == "user"

    def test_anthropic_system_concatenation(self):
        from backend.llm.providers.anthropic import _messages_to_anthropic
        from backend.llm.base import ChatMessage, Role
        msgs = [
            ChatMessage(role=Role.SYSTEM, content="Be brief"),
            ChatMessage(role=Role.SYSTEM, content="Cite sources"),
        ]
        _, system = _messages_to_anthropic(msgs, None)
        assert "Be brief" in system
        assert "Cite sources" in system
