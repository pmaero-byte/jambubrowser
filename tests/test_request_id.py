"""Tests: backend/core/request_id.py."""
import asyncio
import pytest
from unittest.mock import AsyncMock


class TestRequestIDMiddleware:
    def _make_middleware(self, header_name="X-Request-ID"):
        from backend.core.request_id import RequestIDMiddleware
        async def inner(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
        return RequestIDMiddleware(inner, header_name=header_name), inner

    def test_generates_new_id_when_missing(self):
        mw, _ = self._make_middleware()

        async def test():
            captured_scope = {}

            async def inner(scope, receive, send):
                captured_scope.update(scope)
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b""})

            mw_local = type(mw)(inner, header_name="X-Request-ID")
            scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
            await mw_local(scope, AsyncMock(), AsyncMock())
            assert "request_id" in captured_scope
            assert len(captured_scope["request_id"]) == 12

        asyncio.run(test())

    def test_uses_provided_request_id(self):
        from backend.core.request_id import RequestIDMiddleware

        async def test():
            captured_scope = {}

            async def inner(scope, receive, send):
                captured_scope.update(scope)
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b""})

            mw = RequestIDMiddleware(inner, header_name="X-Request-ID")
            scope = {
                "type": "http", "method": "GET", "path": "/",
                "headers": [(b"x-request-id", b"my-trace-12345")],
            }
            await mw(scope, AsyncMock(), AsyncMock())
            assert captured_scope["request_id"] == "my-trace-12345"

        asyncio.run(test())

    def test_response_includes_request_id(self):
        from backend.core.request_id import RequestIDMiddleware

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            async def inner(scope, receive, send):
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b""})

            mw = RequestIDMiddleware(inner, header_name="X-Request-ID")
            scope = {
                "type": "http", "method": "GET", "path": "/",
                "headers": [(b"x-request-id", b"test-abc-123")],
            }
            await mw(scope, AsyncMock(), send_capture)
            headers = {k.lower(): v for k, v in captured[0]["headers"]}
            assert headers.get(b"x-request-id") == b"test-abc-123"

        asyncio.run(test())

    def test_generated_id_is_in_response(self):
        from backend.core.request_id import RequestIDMiddleware

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            async def inner(scope, receive, send):
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b""})

            mw = RequestIDMiddleware(inner, header_name="X-Request-ID")
            scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
            await mw(scope, AsyncMock(), send_capture)
            headers = {k.lower(): v for k, v in captured[0]["headers"]}
            assert b"x-request-id" in headers
            assert len(headers[b"x-request-id"]) == 12

        asyncio.run(test())

    def test_existing_request_id_header_preserved(self):
        from backend.core.request_id import RequestIDMiddleware

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            async def inner(scope, receive, send):
                await send({
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"x-request-id", b"already-set")],
                })
                await send({"type": "http.response.body", "body": b""})

            mw = RequestIDMiddleware(inner, header_name="X-Request-ID")
            scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
            await mw(scope, AsyncMock(), send_capture)
            headers = captured[0]["headers"]
            x_request_id_values = [v for k, v in headers if k.lower() == b"x-request-id"]
            assert len(x_request_id_values) == 1
            assert x_request_id_values[0] == b"already-set"

        asyncio.run(test())

    def test_empty_request_id_replaced(self):
        from backend.core.request_id import RequestIDMiddleware

        async def test():
            captured_scope = {}

            async def inner(scope, receive, send):
                captured_scope.update(scope)
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b""})

            mw = RequestIDMiddleware(inner, header_name="X-Request-ID")
            scope = {
                "type": "http", "method": "GET", "path": "/",
                "headers": [(b"x-request-id", b"   ")],
            }
            await mw(scope, AsyncMock(), AsyncMock())
            assert captured_scope["request_id"] != "   "
            assert len(captured_scope["request_id"]) == 12

        asyncio.run(test())

    def test_overly_long_request_id_replaced(self):
        from backend.core.request_id import RequestIDMiddleware

        async def test():
            captured_scope = {}

            async def inner(scope, receive, send):
                captured_scope.update(scope)
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b""})

            mw = RequestIDMiddleware(inner, header_name="X-Request-ID")
            long_id = b"a" * 200
            scope = {
                "type": "http", "method": "GET", "path": "/",
                "headers": [(b"x-request-id", long_id)],
            }
            await mw(scope, AsyncMock(), AsyncMock())
            assert captured_scope["request_id"] != "a" * 200
            assert len(captured_scope["request_id"]) == 12

        asyncio.run(test())

    def test_custom_header_name(self):
        from backend.core.request_id import RequestIDMiddleware

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            async def inner(scope, receive, send):
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b""})

            mw = RequestIDMiddleware(inner, header_name="X-Correlation-ID")
            scope = {
                "type": "http", "method": "GET", "path": "/",
                "headers": [(b"x-correlation-id", b"corr-999")],
            }
            await mw(scope, AsyncMock(), send_capture)
            headers = {k.lower(): v for k, v in captured[0]["headers"]}
            assert headers.get(b"x-correlation-id") == b"corr-999"

        asyncio.run(test())

    def test_non_http_passes_through(self):
        from backend.core.request_id import RequestIDMiddleware

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            async def inner(scope, receive, send):
                await send({"type": "lifespan.startup"})

            mw = RequestIDMiddleware(inner, header_name="X-Request-ID")
            scope = {"type": "lifespan"}
            await mw(scope, AsyncMock(), send_capture)
            assert captured[0]["type"] == "lifespan.startup"

        asyncio.run(test())

    def test_get_request_id_helper(self):
        from backend.core.request_id import get_request_id
        scope = {"request_id": "abc123"}
        assert get_request_id(scope) == "abc123"

    def test_get_request_id_missing(self):
        from backend.core.request_id import get_request_id
        assert get_request_id({}) == ""
        assert get_request_id(None) == ""
