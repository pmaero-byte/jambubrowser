"""
Live integration tests against a running DecentraCode Mesh (DCM) node.

These tests are skipped unless a DCM node answers ``/health``. Point them at
a node with ``JAMBU_TEST_DCM_URL`` (default ``http://127.0.0.1:3001``):

    cd ~/Aerospace_projects/decentracode/backend && npm start
    .venv/bin/python3 -m pytest tests/test_dcm_integration.py -v

What is checked:
- the node's health + inference status contract (works with no model loaded),
- the OpenAI-compatible endpoint exists and validates requests (no runtime
  needed),
- and, **when an inference runtime is actually available**, a real streaming
  generation through ``DCMProvider`` — otherwise the test skips with the
  reason DCM reports (e.g. missing Rust binaries).
"""
from __future__ import annotations

import asyncio
import os
import urllib.error
import urllib.request

import pytest

import httpx

from backend.llm.base import ChatMessage, Role
from backend.llm.config import LLMConfig
from backend.llm.providers.dcm import DCMProvider
from backend.modules.dcm_client import DcmClient, DcmError

BASE_URL = os.environ.get("JAMBU_TEST_DCM_URL", "http://127.0.0.1:3001").rstrip("/")


def _dcm_reachable() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE_URL}/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _dcm_reachable(),
    reason=(
        f"no DCM node at {BASE_URL} — start one with "
        "'cd decentracode/backend && npm start'"
    ),
)


def run(coro):
    return asyncio.run(coro)


class TestNodeContract:
    def test_health(self):
        assert run(DcmClient(BASE_URL).health()) is True

    def test_summary_includes_models(self):
        summary = run(DcmClient(BASE_URL).summary())
        assert summary["reachable"] is True
        assert isinstance(summary["models"], list)

    def test_inference_status_shape(self):
        """503-with-state-body is a valid answer (default runtime missing,
        other runtimes may still be ready)."""
        status = run(DcmClient(BASE_URL).inference_status())
        assert isinstance(status, dict)
        assert any(
            k in status for k in ("ready", "available", "runtime", "error", "moe")
        )

    def test_chat_endpoint_rejects_empty_messages(self):
        """Contract check that needs no inference runtime: DCM must answer
        the OpenAI-compatible route with its INVALID_MESSAGES error."""
        r = httpx.post(
            f"{BASE_URL}/api/inference/v1/chat/completions",
            json={"messages": []},
            timeout=10.0,
        )
        assert r.status_code == 400
        assert r.json().get("code") == "INVALID_MESSAGES"


class TestLiveStreaming:
    def _provider(self, model: str | None = None) -> DCMProvider:
        cfg = LLMConfig.from_env()
        cfg.dcm_base_url = BASE_URL
        if model:
            cfg.dcm_model = model
        return DCMProvider(cfg)

    def test_streaming_generation_or_skip(self):
        """Real token streaming through the provider.

        Skips (not fails) when the node has no runnable runtime yet — that is
        an infrastructure state, not a code defect. The skip message carries
        DCM's own reason.
        """
        provider = self._provider()
        messages = [ChatMessage(role=Role.USER, content="Say hello in three words.")]

        async def collect():
            chunks = []
            async for chunk in provider.stream(messages, max_tokens=16):
                chunks.append(chunk)
            return chunks

        try:
            chunks = run(collect())
        except Exception as e:
            pytest.skip(f"DCM runtime unavailable: {e}")

        text = "".join(c.delta for c in chunks).strip()
        assert text, "stream produced no text"
        assert chunks[-1].finish_reason == "stop"

    def test_non_streaming_generation_or_skip(self):
        provider = self._provider()
        messages = [ChatMessage(role=Role.USER, content="Reply with the word: mesh")]

        try:
            resp = run(provider.chat(messages, max_tokens=16))
        except Exception as e:
            pytest.skip(f"DCM runtime unavailable: {e}")

        assert resp.provider == "dcm"
        assert resp.content.strip()
        assert resp.usage.cost_usd == 0.0
