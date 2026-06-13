"""Tests: backend/core/security_headers.py."""
import asyncio
import pytest
from unittest.mock import AsyncMock, Mock
from starlette.types import Scope, Receive, Send


class TestSecurityHeadersMiddleware:
    def _make_middleware(self, custom_headers=None):
        from backend.core.security_headers import SecurityHeadersMiddleware
        inner_app = AsyncMock()
        mw = SecurityHeadersMiddleware(inner_app, headers=custom_headers or {})
        return mw, inner_app

    def test_default_headers_added(self):
        from backend.core.security_headers import SecurityHeadersMiddleware, _SECURITY_HEADERS
        mw, _ = self._make_middleware()
        assert mw.headers == _SECURITY_HEADERS
        assert "X-Content-Type-Options" in mw.headers
        assert "X-Frame-Options" in mw.headers
        assert "Content-Security-Policy" in mw.headers

    def test_custom_headers_overrides_defaults(self):
        custom = {"X-Custom-Header": "value"}
        mw, _ = self._make_middleware(custom)
        assert mw.headers == custom

    def test_http_response_gets_headers(self):
        from backend.core.security_headers import SecurityHeadersMiddleware

        async def test():
            async def inner(scope, receive, send):
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b""})

            captured = []

            async def send_capture(msg):
                captured.append(msg)

            scope = {"type": "http", "method": "GET", "path": "/"}
            mw = SecurityHeadersMiddleware(inner)
            await mw(scope, AsyncMock(), send_capture)

            start = captured[0]
            header_names = {h[0].lower() for h in start["headers"]}
            assert b"x-content-type-options" in header_names
            assert b"x-frame-options" in header_names
            assert b"content-security-policy" in header_names

        asyncio.run(test())

    def test_existing_headers_preserved(self):
        from backend.core.security_headers import SecurityHeadersMiddleware

        async def test():
            async def inner(scope, receive, send):
                await send({
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"x-custom", b"original")],
                })
                await send({"type": "http.response.body", "body": b""})

            captured = []

            async def send_capture(msg):
                captured.append(msg)

            scope = {"type": "http", "method": "GET", "path": "/"}
            mw = SecurityHeadersMiddleware(inner)
            await mw(scope, AsyncMock(), send_capture)

            header_names = {h[0].lower() for h in captured[0]["headers"]}
            assert b"x-custom" in header_names
            assert b"x-content-type-options" in header_names

        asyncio.run(test())

    def test_non_http_passes_through(self):
        from backend.core.security_headers import SecurityHeadersMiddleware

        async def test():
            async def inner(scope, receive, send):
                await send({"type": "lifespan.startup"})

            captured = []

            async def send_capture(msg):
                captured.append(msg)

            scope = {"type": "lifespan"}
            mw = SecurityHeadersMiddleware(inner)
            await mw(scope, AsyncMock(), send_capture)
            assert len(captured) == 1

        asyncio.run(test())

    def test_csp_default_value(self):
        from backend.core.security_headers import _DEFAULT_CSP
        assert "default-src 'self'" in _DEFAULT_CSP
        assert "frame-ancestors 'none'" in _DEFAULT_CSP

    def test_permissions_policy_default(self):
        from backend.core.security_headers import _DEFAULT_PERMISSIONS
        assert "camera=()" in _DEFAULT_PERMISSIONS
        assert "microphone=()" in _DEFAULT_PERMISSIONS
