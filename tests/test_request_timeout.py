"""Tests: backend/core/request_timeout.py."""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock


class TestRequestTimeoutMiddleware:
    def _make_middleware(self, timeout=1.0, exclude=None):
        from backend.core.request_timeout import RequestTimeoutMiddleware
        async def inner(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
        return RequestTimeoutMiddleware(inner, timeout_seconds=timeout, exclude_paths=exclude or [])

    def test_default_timeout(self):
        from backend.core.request_timeout import _DEFAULT_TIMEOUT
        assert _DEFAULT_TIMEOUT == 60.0

    def test_non_http_passes_through(self):
        mw = self._make_middleware()

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            scope = {"type": "lifespan"}
            await mw(scope, AsyncMock(), send_capture)
            assert captured[0]["type"] == "http.response.start"
            assert captured[0]["status"] == 200

        asyncio.run(test())

    def test_excluded_path_passes(self):
        mw = self._make_middleware(timeout=0.05, exclude=["/research"])

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            async def slow(scope, receive, send):
                await asyncio.sleep(0.2)
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"slow"})

            from backend.core.request_timeout import RequestTimeoutMiddleware
            mw_local = RequestTimeoutMiddleware(slow, timeout_seconds=0.05, exclude_paths=["/research"])
            scope = {"type": "http", "method": "POST", "path": "/research", "headers": []}
            await mw_local(scope, AsyncMock(), send_capture)
            assert captured[0]["status"] == 200

        asyncio.run(test())

    def test_fast_request_succeeds(self):
        mw = self._make_middleware(timeout=2.0)

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            scope = {"type": "http", "method": "GET", "path": "/test", "headers": []}
            await mw(scope, AsyncMock(), send_capture)
            assert captured[0]["status"] == 200

        asyncio.run(test())

    def test_slow_request_times_out(self):
        from backend.core.request_timeout import RequestTimeoutMiddleware

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            async def slow(scope, receive, send):
                await asyncio.sleep(0.5)
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"slow"})

            mw = RequestTimeoutMiddleware(slow, timeout_seconds=0.05)
            scope = {"type": "http", "method": "GET", "path": "/test", "headers": []}
            await mw(scope, AsyncMock(), send_capture)
            assert captured[0]["status"] == 504
            body = json.loads(captured[1]["body"].decode("utf-8"))
            assert "timed out" in body["detail"]
            assert body["timeout_seconds"] == 0.05

        asyncio.run(test())

    def test_timeout_includes_request_id(self):
        from backend.core.request_timeout import RequestTimeoutMiddleware

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            async def slow(scope, receive, send):
                await asyncio.sleep(0.5)

            mw = RequestTimeoutMiddleware(slow, timeout_seconds=0.05)
            scope = {
                "type": "http", "method": "GET", "path": "/test",
                "headers": [], "request_id": "req-timeout-1",
            }
            await mw(scope, AsyncMock(), send_capture)
            body = json.loads(captured[1]["body"].decode("utf-8"))
            assert body["request_id"] == "req-timeout-1"

        asyncio.run(test())

    def test_timeout_no_request_id(self):
        from backend.core.request_timeout import RequestTimeoutMiddleware

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            async def slow(scope, receive, send):
                await asyncio.sleep(0.5)

            mw = RequestTimeoutMiddleware(slow, timeout_seconds=0.05)
            scope = {"type": "http", "method": "GET", "path": "/test", "headers": []}
            await mw(scope, AsyncMock(), send_capture)
            body = json.loads(captured[1]["body"].decode("utf-8"))
            assert "request_id" not in body

        asyncio.run(test())

    def test_excluded_path_prefix_match(self):
        from backend.core.request_timeout import RequestTimeoutMiddleware

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            async def slow(scope, receive, send):
                await asyncio.sleep(0.2)
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"slow"})

            mw = RequestTimeoutMiddleware(slow, timeout_seconds=0.05, exclude_paths=["/v2/"])
            scope = {"type": "http", "method": "POST", "path": "/v2/llm/chat", "headers": []}
            await mw(scope, AsyncMock(), send_capture)
            assert captured[0]["status"] == 200

        asyncio.run(test())

    def test_path_starts_with_excluded(self):
        from backend.core.request_timeout import RequestTimeoutMiddleware

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            async def fast(scope, receive, send):
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"ok"})

            mw = RequestTimeoutMiddleware(fast, timeout_seconds=30.0, exclude_paths=["/health"])
            scope = {"type": "http", "method": "GET", "path": "/health", "headers": []}
            await mw(scope, AsyncMock(), send_capture)
            assert captured[0]["status"] == 200

        asyncio.run(test())
