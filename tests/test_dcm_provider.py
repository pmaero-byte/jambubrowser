"""
Tests for the DecentraCode Mesh (DCM) LLM provider and REST client.

The DCM wire format is pinned against the real DCM source
(``routes/inferenceStream.js`` native frames, ``routes/inference.js``
OpenAI-compatible facade), so these tests fail loudly if the mesh's
contract drifts.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from backend.llm.base import ChatMessage, Role
from backend.llm.config import LLMConfig
from backend.llm.providers.dcm import DCMProvider, MODELS

BASE = "http://dcm.test:3001"


def _config(**overrides) -> LLMConfig:
    cfg = LLMConfig.from_env()
    cfg.dcm_base_url = BASE
    cfg.dcm_model = "qwen1.5-moe-a2.7b"
    cfg.dcm_auth = ""
    cfg.health_timeout = 1.0
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _provider(handler, **overrides) -> DCMProvider:
    return DCMProvider(_config(**overrides), transport=httpx.MockTransport(handler))


def _messages() -> list[ChatMessage]:
    return [ChatMessage(role=Role.USER, content="hello mesh")]


def run(coro):
    return asyncio.run(coro)


def _sse(*frames: dict) -> bytes:
    return b"".join(
        f"data: {json.dumps(f)}\n\n".encode() for f in frames
    )


class TestHealth:
    def test_healthy_node_reports_ready_runtime(self):
        def handler(request):
            if request.url.path == "/health":
                return httpx.Response(200, json={"status": "ok"})
            assert request.url.path == "/api/inference/status"
            return httpx.Response(200, json={"engine_ready": True})

        assert run(_provider(handler).health()) is True

    def test_503_with_ready_secondary_runtime_is_healthy(self):
        """Live-verified shape: candle runtime missing (503) but the MoE
        sidecar is ready — the node can still serve inference."""
        def handler(request):
            if request.url.path == "/health":
                return httpx.Response(200, json={"status": "ok"})
            return httpx.Response(503, json={
                "runtime": "candle-dense", "available": False, "ready": False,
                "error": "Binary not found: .../dcm-infer-candle",
                "moe": {"runtime": "python-moe", "available": True, "ready": True},
            })

        assert run(_provider(handler).health()) is True

    def test_503_with_no_ready_runtime_is_unhealthy(self):
        def handler(request):
            if request.url.path == "/health":
                return httpx.Response(200, json={"status": "ok"})
            return httpx.Response(503, json={
                "available": False, "ready": False,
                "moe": {"available": False, "ready": False},
            })

        assert run(_provider(handler).health()) is False

    def test_node_down_is_unhealthy(self):
        def handler(request):
            return httpx.Response(503, text="nginx")

        assert run(_provider(handler).health()) is False

    def test_connection_error_is_unhealthy(self):
        def handler(request):
            raise httpx.ConnectError("refused", request=request)

        assert run(_provider(handler).health()) is False


class TestChat:
    def test_parses_openai_shape_and_costs_zero(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={
                "id": "chatcmpl-1",
                "model": "qwen1.5-moe-a2.7b",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "hi from the mesh"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            })

        resp = run(_provider(handler).chat(_messages(), max_tokens=16))
        assert resp.content == "hi from the mesh"
        assert resp.provider == "dcm"
        assert resp.usage.total_tokens == 7
        assert resp.usage.cost_usd == 0.0  # mesh = user-operated, no USD cost
        assert captured["body"]["model"] == "qwen1.5-moe-a2.7b"
        assert captured["body"]["stream"] is False

    def test_auth_header_is_forwarded(self):
        captured = {}

        def handler(request):
            captured["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

        provider = _provider(handler, dcm_auth="DID-Sig did:dcm:abc:signature")
        run(provider.chat(_messages()))
        assert captured["auth"] == "DID-Sig did:dcm:abc:signature"

    def test_501_runtime_not_implemented_is_actionable(self):
        def handler(request):
            return httpx.Response(501, json={
                "error": "vLLM runtime is not implemented yet",
                "code": "RUNTIME_NOT_IMPLEMENTED",
            })

        from backend.llm.base import ProviderUnavailable

        with pytest.raises(ProviderUnavailable) as e:
            run(_provider(handler).chat(_messages()))
        assert "build backend/p2pd binaries" in str(e.value)

    def test_500_spawn_enoent_is_actionable(self):
        """DCM answers 500 with an ENOENT spawn error when a runtime binary
        is missing (live-verified against a real node)."""
        def handler(request):
            return httpx.Response(500, json={
                "success": False,
                "error": "spawn .../dcm-infer-candle ENOENT",
                "code": "ENOENT",
                "model": "qwen2-0.5b-instruct",
            })

        from backend.llm.base import ProviderUnavailable

        with pytest.raises(ProviderUnavailable) as e:
            run(_provider(handler).chat(_messages()))
        assert "runtime not available" in str(e.value)

    def test_401_is_auth_error(self):
        def handler(request):
            return httpx.Response(401, json={"message": "DID-Sig required"})

        from backend.llm.base import ProviderAuthError

        with pytest.raises(ProviderAuthError):
            run(_provider(handler).chat(_messages()))

    def test_404_names_the_endpoint(self):
        def handler(request):
            return httpx.Response(404, text="Not Found")

        from backend.llm.base import ProviderError

        with pytest.raises(ProviderError) as e:
            run(_provider(handler).chat(_messages()))
        assert "/api/inference/v1/chat/completions" in str(e.value)

    def test_connect_error_is_unavailable(self):
        def handler(request):
            raise httpx.ConnectError("refused", request=request)

        from backend.llm.base import ProviderUnavailable

        with pytest.raises(ProviderUnavailable):
            run(_provider(handler).chat(_messages()))


class TestStream:
    def test_native_dcm_frames_stream_token_by_token(self):
        def handler(request):
            body = json.loads(request.content)
            assert body["stream"] is True
            return httpx.Response(200, content=_sse(
                {"token": 1, "text": "Hello", "finished": False},
                {"token": 2, "text": " mesh", "finished": False},
                {"token": 3, "text": "!", "finished": False},
                {
                    "finished": True, "success": True,
                    "outputText": "Hello mesh!",
                    "outputTokens": [1, 2, 3],
                    "perStepMs": [10.0, 20.0, 30.0],
                    "tokPerSec": 50.0,
                },
            ), headers={"Content-Type": "text/event-stream"})

        async def collect():
            chunks = []
            async for chunk in _provider(handler).stream(_messages()):
                chunks.append(chunk)
            return chunks

        chunks = run(collect())
        deltas = [c.delta for c in chunks if c.delta]
        # Per-token deltas plus the terminal frame's full text.
        assert deltas == ["Hello", " mesh", "!", "Hello mesh!"]
        final = chunks[-1]
        assert final.finish_reason == "stop"
        assert final.usage is not None
        assert final.usage.completion_tokens == 3
        assert final.usage.cost_usd == 0.0

    def test_snake_case_terminal_frame_is_accepted(self):
        def handler(request):
            return httpx.Response(200, content=_sse(
                {"text": "hi", "finished": False},
                {"finished": True, "output_text": "hi", "output_tokens": [7]},
            ), headers={"Content-Type": "text/event-stream"})

        async def collect():
            return [c async for c in _provider(handler).stream(_messages())]

        chunks = run(collect())
        assert chunks[-1].usage.completion_tokens == 1

    def test_openai_style_deltas_are_accepted(self):
        def handler(request):
            frames = [
                {"choices": [{"delta": {"content": "Open"}}]},
                {"choices": [{"delta": {"content": "AI"}}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            ]
            content = _sse(*frames) + b"data: [DONE]\n\n"
            return httpx.Response(200, content=content,
                                  headers={"Content-Type": "text/event-stream"})

        async def collect():
            return [c async for c in _provider(handler).stream(_messages())]

        chunks = run(collect())
        deltas = "".join(c.delta for c in chunks)
        assert deltas == "OpenAI"
        assert any(c.finish_reason == "stop" for c in chunks)

    def test_terminal_error_frame_raises(self):
        def handler(request):
            return httpx.Response(200, content=_sse(
                {"finished": True, "error": "dcm-infer-candle exited 1"},
            ), headers={"Content-Type": "text/event-stream"})

        from backend.llm.base import ProviderError

        async def collect():
            return [c async for c in _provider(handler).stream(_messages())]

        with pytest.raises(ProviderError) as e:
            run(collect())
        assert "dcm-infer-candle exited 1" in str(e.value)

    def test_stream_501_is_unavailable(self):
        def handler(request):
            return httpx.Response(501, json={"code": "RUNTIME_NOT_IMPLEMENTED"})

        from backend.llm.base import ProviderUnavailable

        async def collect():
            return [c async for c in _provider(handler).stream(_messages())]

        with pytest.raises(ProviderUnavailable):
            run(collect())

    def test_stream_without_terminal_frame_still_finishes(self):
        def handler(request):
            return httpx.Response(200, content=_sse(
                {"text": "partial", "finished": False},
            ), headers={"Content-Type": "text/event-stream"})

        async def collect():
            return [c async for c in _provider(handler).stream(_messages())]

        chunks = run(collect())
        assert "".join(c.delta for c in chunks) == "partial"
        assert chunks[-1].finish_reason == "stop"


class TestRegistryIntegration:
    def test_provider_is_discovered_and_local(self):
        from backend.llm.registry import get_registry, reset_registry

        reset_registry()
        registry = get_registry()
        provider = registry.get("dcm")
        assert provider.name == "dcm"
        assert "dcm" in registry.list_available()
        assert MODELS[0] in provider.models

    def test_model_for_dcm(self):
        cfg = _config()
        assert cfg.model_for("dcm") == "qwen1.5-moe-a2.7b"

    def test_local_only_mode_accepts_dcm(self):
        from backend.llm.registry import ProviderRegistry

        cfg = _config(default_provider="auto", force_local_only=True,
                      fallback_chain=["dcm"])
        registry = ProviderRegistry(cfg)
        assert registry.get_default().name == "dcm"


class TestDcmClient:
    def _client(self, handler, **kw):
        from backend.modules.dcm_client import DcmClient
        return DcmClient(base_url=BASE, transport=httpx.MockTransport(handler), **kw)

    def test_models_list(self):
        def handler(request):
            assert request.url.path == "/api/models"
            return httpx.Response(200, json={"models": [
                {"id": "qwen1.5-moe-a2.7b", "status": "available"},
                {"id": "glm-5.2", "status": "planned"},
            ]})

        models = run(self._client(handler).models())
        assert len(models) == 2
        assert models[0]["id"] == "qwen1.5-moe-a2.7b"

    def test_earnings_and_settlement_log(self):
        seen = []

        def handler(request):
            seen.append(request.url.path)
            if "earnings" in request.url.path:
                return httpx.Response(200, json={"did": "did:dcm:x", "pendingDct": 12.5})
            return httpx.Response(200, json={"entries": [], "valid": True})

        client = self._client(handler)
        earn = run(client.earnings("did:dcm:x"))
        log = run(client.settlement_log(limit=10))
        assert earn["pendingDct"] == 12.5
        assert log["valid"] is True
        assert seen == ["/api/billing/earnings/did:dcm:x", "/api/billing/settlement-log"]

    def test_http_error_becomes_dcm_error(self):
        from backend.modules.dcm_client import DcmError

        def handler(request):
            return httpx.Response(500, text="boom")

        with pytest.raises(DcmError) as e:
            run(self._client(handler).mesh_status())
        assert e.value.status_code == 500

    def test_unreachable_is_reported(self):
        from backend.modules.dcm_client import DcmError

        def handler(request):
            raise httpx.ConnectError("refused", request=request)

        with pytest.raises(DcmError) as e:
            run(self._client(handler).inference_status())
        assert e.value.status_code == 0
        assert "unreachable" in str(e.value)

    def test_summary_composes_endpoints(self):
        def handler(request):
            path = request.url.path
            if path == "/health":
                return httpx.Response(200, json={"status": "online"})
            if path == "/api/inference/status":
                return httpx.Response(200, json={"engine_ready": True})
            if path == "/api/network/status":
                return httpx.Response(200, json={"nodeId": "n1", "peers": []})
            if path == "/api/models":
                return httpx.Response(200, json={"models": [
                    {"id": "qwen1.5-moe-a2.7b", "status": "available"},
                ]})
            return httpx.Response(404)

        summary = run(self._client(handler).summary())
        assert summary["reachable"] is True
        assert summary["mesh_status"]["nodeId"] == "n1"
        assert summary["models"][0]["id"] == "qwen1.5-moe-a2.7b"
