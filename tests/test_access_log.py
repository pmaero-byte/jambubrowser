"""Tests: backend/core/access_log.py."""
import logging
import pytest
from unittest.mock import AsyncMock, MagicMock


class TestAccessLogMiddleware:
    def _make_scope(self, path="/test", method="GET", request_id="rid-123"):
        return {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [],
            "client": ("10.0.0.1", 50000),
            "request_id": request_id,
        }

    async def _run(self, middleware, scope, status=200):
        receive = AsyncMock(return_value={"type": "http.request", "body": b"", "more_body": False})
        sent_messages = []

        async def send(message):
            if message["type"] == "http.response.start":
                sent_messages.append(("start", message.get("status")))
            elif message["type"] == "http.response.body":
                sent_messages.append(("body",))

        if status is not None:
            async def app(scope, receive, send):
                await send({"type": "http.response.start", "status": status, "headers": []})
                await send({"type": "http.response.body", "body": b""})
        else:
            app = AsyncMock()

        await middleware(scope, receive, send)
        return sent_messages, app

    def test_non_http_passes_through(self):
        from backend.core.access_log import AccessLogMiddleware
        app = AsyncMock()
        middleware = AccessLogMiddleware(app)

        scope = {"type": "websocket", "path": "/ws/test"}
        receive = AsyncMock()
        send = AsyncMock()

        import asyncio
        asyncio.run(middleware(scope, receive, send))
        app.assert_awaited_once()

    def test_skips_health_path(self):
        from backend.core.access_log import AccessLogMiddleware
        app = AsyncMock()
        middleware = AccessLogMiddleware(app)

        scope = {"type": "http", "method": "GET", "path": "/health"}
        import asyncio
        asyncio.run(middleware(scope, AsyncMock(), AsyncMock()))
        app.assert_awaited_once()

    def test_logs_request(self, caplog):
        from backend.core.access_log import AccessLogMiddleware

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = AccessLogMiddleware(app, skip_paths=set())
        scope = self._make_scope(path="/api/users", method="POST")

        with caplog.at_level(logging.INFO, logger="jambu.access"):
            import asyncio
            asyncio.run(middleware(scope, AsyncMock(), AsyncMock()))

        assert any("POST /api/users 200" in r.message for r in caplog.records)

    def test_logs_request_id(self, caplog):
        from backend.core.access_log import AccessLogMiddleware

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = AccessLogMiddleware(app, skip_paths=set())
        scope = self._make_scope(request_id="my-correlation-id")

        with caplog.at_level(logging.INFO, logger="jambu.access"):
            import asyncio
            asyncio.run(middleware(scope, AsyncMock(), AsyncMock()))

        assert any("rid=my-correlation-id" in r.message for r in caplog.records)

    def test_logs_client_ip(self, caplog):
        from backend.core.access_log import AccessLogMiddleware

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = AccessLogMiddleware(app, skip_paths=set())
        scope = self._make_scope()
        scope["client"] = ("203.0.113.7", 50000)

        with caplog.at_level(logging.INFO, logger="jambu.access"):
            import asyncio
            asyncio.run(middleware(scope, AsyncMock(), AsyncMock()))

        assert any("ip=203.0.113.7" in r.message for r in caplog.records)

    def test_logs_slow_threshold(self, caplog):
        from backend.core.access_log import AccessLogMiddleware
        import time

        async def app(scope, receive, send):
            time.sleep(0.05)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = AccessLogMiddleware(app, skip_paths=set(), slow_threshold_ms=1.0)
        scope = self._make_scope()

        with caplog.at_level(logging.INFO, logger="jambu.access"):
            import asyncio
            asyncio.run(middleware(scope, AsyncMock(), AsyncMock()))

        assert any("[SLOW]" in r.message for r in caplog.records)

    def test_logs_ok_for_fast_requests(self, caplog):
        from backend.core.access_log import AccessLogMiddleware

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = AccessLogMiddleware(app, skip_paths=set(), slow_threshold_ms=10000.0)
        scope = self._make_scope()

        with caplog.at_level(logging.INFO, logger="jambu.access"):
            import asyncio
            asyncio.run(middleware(scope, AsyncMock(), AsyncMock()))

        assert any("[OK]" in r.message for r in caplog.records)

    def test_logs_status_code_500(self, caplog):
        from backend.core.access_log import AccessLogMiddleware

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 500, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = AccessLogMiddleware(app, skip_paths=set())
        scope = self._make_scope()

        with caplog.at_level(logging.INFO, logger="jambu.access"):
            import asyncio
            asyncio.run(middleware(scope, AsyncMock(), AsyncMock()))

        assert any("500" in r.message for r in caplog.records)

    def test_logs_even_on_exception(self, caplog):
        from backend.core.access_log import AccessLogMiddleware

        async def app(scope, receive, send):
            raise RuntimeError("downstream boom")

        middleware = AccessLogMiddleware(app, skip_paths=set())
        scope = self._make_scope()

        with caplog.at_level(logging.INFO, logger="jambu.access"):
            import asyncio
            with pytest.raises(RuntimeError):
                asyncio.run(middleware(scope, AsyncMock(), AsyncMock()))

        assert any("/test" in r.message for r in caplog.records)
        assert any("500" in r.message for r in caplog.records)

    def test_custom_skip_paths(self):
        from backend.core.access_log import AccessLogMiddleware
        app = AsyncMock()
        middleware = AccessLogMiddleware(app, skip_paths={"/metrics"})

        scope = {"type": "http", "method": "GET", "path": "/metrics"}
        import asyncio
        asyncio.run(middleware(scope, AsyncMock(), AsyncMock()))
        app.assert_awaited_once()
