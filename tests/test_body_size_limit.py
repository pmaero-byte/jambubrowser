"""Tests: backend/core/body_size_limit.py."""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock


class TestBodySizeLimitMiddleware:
    def _make_middleware(self, max_bytes=1024, inner=None):
        from backend.core.body_size_limit import BodySizeLimitMiddleware
        if inner is None:
            async def inner(scope, receive, send):
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"ok"})
        return BodySizeLimitMiddleware(inner, max_bytes=max_bytes), inner

    def test_default_max_bytes(self):
        from backend.core.body_size_limit import _DEFAULT_MAX_BYTES
        assert _DEFAULT_MAX_BYTES == 1 * 1024 * 1024

    def test_custom_max_bytes(self):
        mw, _ = self._make_middleware(max_bytes=512)
        assert mw.max_bytes == 512

    def test_content_length_within_limit_passes(self):
        mw, _ = self._make_middleware(max_bytes=1024)

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            scope = {
                "type": "http",
                "method": "POST",
                "path": "/test",
                "headers": [(b"content-length", b"512")],
            }
            receive = AsyncMock()
            await mw(scope, receive, send_capture)
            assert captured[0]["status"] == 200

        asyncio.run(test())

    def test_content_length_exceeds_limit_rejects(self):
        mw, _ = self._make_middleware(max_bytes=1024)

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            scope = {
                "type": "http",
                "method": "POST",
                "path": "/test",
                "headers": [(b"content-length", b"2048")],
            }
            receive = AsyncMock()
            await mw(scope, receive, send_capture)
            assert captured[0]["status"] == 413
            body = json.loads(captured[1]["body"].decode("utf-8"))
            assert "too large" in body["detail"]
            assert body["max_bytes"] == 1024

        asyncio.run(test())

    def test_no_content_length_passes(self):
        mw, _ = self._make_middleware(max_bytes=1024)

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            scope = {"type": "http", "method": "POST", "path": "/test", "headers": []}
            await mw(scope, AsyncMock(), send_capture)
            assert captured[0]["status"] == 200

        asyncio.run(test())

    def test_invalid_content_length_rejects(self):
        mw, _ = self._make_middleware(max_bytes=1024)

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            scope = {
                "type": "http",
                "method": "POST",
                "path": "/test",
                "headers": [(b"content-length", b"not-a-number")],
            }
            await mw(scope, AsyncMock(), send_capture)
            assert captured[0]["status"] == 413

        asyncio.run(test())

    def test_non_http_passes_through(self):
        mw, _ = self._make_middleware(max_bytes=1024)

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            async def inner(scope, receive, send):
                await send({"type": "lifespan.startup"})

            mw_local, _ = self._make_middleware(max_bytes=1024, inner=inner)
            scope = {"type": "lifespan"}
            await mw_local(scope, AsyncMock(), send_capture)
            assert captured[0]["type"] == "lifespan.startup"

        asyncio.run(test())

    def test_chunked_body_under_limit_passes(self):
        mw, _ = self._make_middleware(max_bytes=1024)

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            async def receive():
                return {"type": "http.request", "body": b"hello", "more_body": False}

            scope = {"type": "http", "method": "POST", "path": "/test", "headers": []}
            await mw(scope, receive, send_capture)
            assert captured[0]["status"] == 200

        asyncio.run(test())

    def test_body_size_at_exact_limit_passes(self):
        mw, _ = self._make_middleware(max_bytes=5)

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            scope = {
                "type": "http",
                "method": "POST",
                "path": "/test",
                "headers": [(b"content-length", b"5")],
            }
            await mw(scope, AsyncMock(), send_capture)
            assert captured[0]["status"] == 200

        asyncio.run(test())
