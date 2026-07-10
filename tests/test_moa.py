"""
Tests for the Mixture-of-Agents (MoA) provider.

Covers:
- Preset loading (built-in defaults + env var override)
- Reference-prompt construction (system/tool stripping)
- Aggregator-message construction (reference output injection)
- Full chat flow with mock sub-providers
- Partial reference failure (one ref dies, remaining content still flows)
- Unknown preset error
- Factory registration
"""

from __future__ import annotations

import json
import os
import pytest

from backend.llm.base import ChatMessage, ChatResponse, Role, Usage
from backend.llm.providers.mock import MockProvider
from backend.llm.config import LLMConfig


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def config() -> LLMConfig:
    return LLMConfig()


@pytest.fixture
def registry(monkeypatch):
    """Return a clean registry with only mock and moa registered."""
    monkeypatch.setenv("JAMBU_DB_PATH", ":memory:")
    from backend.llm.registry import get_registry, reset_registry
    reset_registry()
    reg = get_registry()
    # Register mock
    reg.register("mock", MockProvider(LLMConfig()))
    # Import and register moa
    from backend.llm.providers.moa import register as moa_register
    moa_register(reg)
    return reg


# ── helpers ──────────────────────────────────────────────────────────────────


def _msg(role: str, content: str) -> ChatMessage:
    return ChatMessage(role=Role(role), content=content)


# ── preset loading ───────────────────────────────────────────────────────────


class TestPresetLoading:
    def test_default_presets_exist(self, config):
        from backend.llm.providers.moa import MixtureOfAgentsProvider
        provider = MixtureOfAgentsProvider(config)
        assert "default" in provider._presets
        assert "quality" in provider._presets
        assert "local" in provider._presets

    def test_env_var_override(self, monkeypatch, config):
        custom = {
            "my_preset": {
                "reference_models": [
                    {"provider": "mock", "model": "mock-echo"},
                ],
                "aggregator": {"provider": "mock", "model": "mock-echo"},
                "reference_temperature": 0.5,
                "aggregator_temperature": 0.3,
                "max_tokens": 2048,
                "enabled": True,
            }
        }
        monkeypatch.setenv("JAMBU_LLM_MOA_PRESETS", json.dumps(custom))
        from backend.llm.providers.moa import MixtureOfAgentsProvider
        provider = MixtureOfAgentsProvider(config)
        assert "my_preset" in provider._presets
        assert "default" not in provider._presets  # fully replaced
        assert provider._presets["my_preset"]["reference_temperature"] == 0.5

    def test_invalid_env_var_falls_back(self, monkeypatch, config):
        monkeypatch.setenv("JAMBU_LLM_MOA_PRESETS", "not-json")
        from backend.llm.providers.moa import MixtureOfAgentsProvider
        provider = MixtureOfAgentsProvider(config)
        assert "default" in provider._presets  # falls back to built-in


# ── reference prompt building ────────────────────────────────────────────────


class TestReferencePrompt:
    def test_strips_system_and_tool_messages(self):
        from backend.llm.providers.moa import _build_reference_prompt
        msgs = [
            _msg("system", "You are a helpful assistant"),
            _msg("user", "Hello"),
            _msg("assistant", "Hi there"),
            _msg("tool", "Tool result"),
            _msg("user", "Follow up"),
        ]
        result = _build_reference_prompt(msgs)
        # First message is the ref system prompt
        assert result[0].role == Role.SYSTEM
        assert "concise second opinion" in result[0].content
        # User/assistant turns preserved
        contents = [m.content for m in result]
        assert "Hello" in contents
        assert "Hi there" in contents
        assert "Follow up" in contents
        # Tool and original system messages stripped
        assert "You are a helpful assistant" not in contents
        assert "Tool result" not in contents

    def test_empty_messages(self):
        from backend.llm.providers.moa import _build_reference_prompt
        result = _build_reference_prompt([])
        assert len(result) == 1  # just the ref system prompt


# ── aggregator message building ──────────────────────────────────────────────


class TestAggregatorMessages:
    def test_appends_reference_outputs(self):
        from backend.llm.providers.moa import _build_aggregator_messages
        original = [
            _msg("system", "You are a helpful assistant"),
            _msg("user", "What is Python?"),
        ]
        refs = ["Python is a programming language", "Python is dynamically typed"]
        result = _build_aggregator_messages(original, refs)
        # Original messages preserved
        assert len(result) >= 2
        assert result[0].content == "You are a helpful assistant"
        assert result[1].content == "What is Python?"
        # Reference section appended
        last = result[-1]
        assert last.role == Role.USER
        assert "Python is a programming language" in last.content
        assert "Python is dynamically typed" in last.content
        assert "Reference 1" in last.content
        assert "Reference 2" in last.content

    def test_no_references(self):
        from backend.llm.providers.moa import _build_aggregator_messages
        original = [_msg("user", "Hi")]
        result = _build_aggregator_messages(original, [])
        assert len(result) == 2
        assert "Reference" in result[-1].content  # empty references section


# ── full chat flow ───────────────────────────────────────────────────────────


class TestChat:
    @pytest.mark.asyncio
    async def test_basic_chat_with_mock_refs(self, registry):
        """Reference models are mock, aggregator is mock — should return a response."""
        from backend.llm.providers.moa import MixtureOfAgentsProvider
        provider = MixtureOfAgentsProvider(LLMConfig())
        # Override presets to use only mock (no network needed)
        provider._presets = {
            "test": {
                "reference_models": [
                    {"provider": "mock", "model": "mock-echo"},
                    {"provider": "mock", "model": "mock-echo"},
                ],
                "aggregator": {"provider": "mock", "model": "mock-echo"},
                "reference_temperature": 0.6,
                "aggregator_temperature": 0.4,
                "max_tokens": 1024,
                "enabled": True,
            }
        }
        msgs = [
            _msg("system", "You are helpful"),
            _msg("user", "What is the answer?"),
        ]
        response = await provider.chat(msgs, model="test")
        assert response.provider == "moa"
        assert "moa:test" in response.model
        assert response.content
        assert response.tool_calls is not None  # mock auto-suggests tools

    @pytest.mark.asyncio
    async def test_partial_reference_failure(self, registry):
        """One reference fails but the remaining result still flows."""
        from backend.llm.providers.moa import MixtureOfAgentsProvider

        # Register a second mock that will fail
        class FailingMock:
            name = "fail-mock"
            models = ["fail"]
            supports_tools = False

            async def chat(self, **kwargs):
                raise RuntimeError("Intentional failure")

            async def stream(self, **kwargs):
                raise RuntimeError("Intentional failure")

            async def health(self):
                return False

            def estimate_cost(self, usage, model=None):
                return 0.0

        registry.register("failing", FailingMock())

        provider = MixtureOfAgentsProvider(LLMConfig())
        provider._presets = {
            "test": {
                "reference_models": [
                    {"provider": "failing", "model": ""},
                    {"provider": "mock", "model": "mock-echo"},
                ],
                "aggregator": {"provider": "mock", "model": "mock-echo"},
                "reference_temperature": 0.6,
                "aggregator_temperature": 0.4,
                "max_tokens": 1024,
                "enabled": True,
            }
        }
        msgs = [_msg("user", "Hello")]
        response = await provider.chat(msgs, model="test")
        assert response.provider == "moa"
        assert response.content

    @pytest.mark.asyncio
    async def test_unknown_preset_raises(self, registry):
        from backend.llm.providers.moa import MixtureOfAgentsProvider
        from backend.llm.base import ProviderError
        provider = MixtureOfAgentsProvider(LLMConfig())
        msgs = [_msg("user", "Hi")]
        with pytest.raises(ProviderError, match="Unknown MoA preset"):
            await provider.chat(msgs, model="nonexistent")

    @pytest.mark.asyncio
    async def test_stream_replays_chat(self, registry):
        from backend.llm.providers.moa import MixtureOfAgentsProvider
        provider = MixtureOfAgentsProvider(LLMConfig())
        provider._presets = {
            "test": {
                "reference_models": [
                    {"provider": "mock", "model": "mock-echo"},
                ],
                "aggregator": {"provider": "mock", "model": "mock-echo"},
                "reference_temperature": 0.6,
                "aggregator_temperature": 0.4,
                "max_tokens": 1024,
                "enabled": True,
            }
        }
        msgs = [_msg("user", "Stream test")]
        chunks = []
        async for chunk in provider.stream(msgs, model="test"):
            chunks.append(chunk)
        assert len(chunks) >= 1
        # Last chunk should have finish_reason
        assert chunks[-1].finish_reason is not None

    @pytest.mark.asyncio
    async def test_health_with_mock_aggregator(self, registry):
        from backend.llm.providers.moa import MixtureOfAgentsProvider
        provider = MixtureOfAgentsProvider(LLMConfig())
        healthy = await provider.health()
        assert healthy is True  # mock is always healthy

    def test_estimate_cost(self, registry):
        from backend.llm.providers.moa import MixtureOfAgentsProvider
        provider = MixtureOfAgentsProvider(LLMConfig())
        usage = Usage(prompt_tokens=100, completion_tokens=50)
        cost = provider.estimate_cost(usage)
        assert cost == usage.cost_usd  # returns whatever was set


# ── registry integration ─────────────────────────────────────────────────────


class TestRegistration:
    def test_moa_is_registered(self, registry):
        assert registry.has("moa")

    def test_moa_listed_in_available(self, registry):
        available = registry.list_available()
        assert "moa" in available

    def test_config_model_for_moa(self, config):
        model = config.model_for("moa")
        assert model == "default"  # from default_model or literal "default"

    def test_factory_creates_provider(self, registry):
        provider = registry.get("moa")
        assert provider.name == "moa"
        assert provider.supports_tools is True
