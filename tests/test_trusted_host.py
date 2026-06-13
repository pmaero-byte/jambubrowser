"""Tests: backend/core/trusted_host.py."""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock


class TestStripPort:
    def test_basic_hostname(self):
        from backend.core.trusted_host import _strip_port
        assert _strip_port("example.com") == "example.com"

    def test_hostname_with_port(self):
        from backend.core.trusted_host import _strip_port
        assert _strip_port("example.com:8000") == "example.com"

    def test_ipv4_with_port(self):
        from backend.core.trusted_host import _strip_port
        assert _strip_port("127.0.0.1:8080") == "127.0.0.1"

    def test_ipv6_with_brackets(self):
        from backend.core.trusted_host import _strip_port
        assert _strip_port("[::1]:8080") == "[::1]"

    def test_empty(self):
        from backend.core.trusted_host import _strip_port
        assert _strip_port("") == ""

    def test_uppercase_normalized(self):
        from backend.core.trusted_host import _strip_port
        assert _strip_port("Example.COM") == "example.com"


class TestLoadAllowedHosts:
    def test_env_var_parsing(self, monkeypatch):
        from backend.core import trusted_host
        monkeypatch.setattr(trusted_host.os.environ, "get", lambda k, d=None: "a.com,b.com,c.com")
        hosts = trusted_host._load_allowed_hosts()
        assert hosts == {"a.com", "b.com", "c.com"}

    def test_env_var_with_whitespace(self, monkeypatch):
        from backend.core import trusted_host
        monkeypatch.setattr(trusted_host.os.environ, "get", lambda k, d=None: "  a.com , b.com  ")
        hosts = trusted_host._load_allowed_hosts()
        assert hosts == {"a.com", "b.com"}

    def test_unset_returns_dev_hosts(self, monkeypatch):
        from backend.core import trusted_host
        monkeypatch.setattr(trusted_host.os.environ, "get", lambda k, d=None: "")
        hosts = trusted_host._load_allowed_hosts()
        assert "localhost" in hosts
        assert "127.0.0.1" in hosts


class TestTrustedHostMiddleware:
    def _make_middleware(self, allowed=None):
        from backend.core.trusted_host import TrustedHostMiddleware
        async def inner(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
        return TrustedHostMiddleware(inner, allowed_hosts=allowed)

    def test_trusted_host_passes(self):
        mw = self._make_middleware(allowed={"example.com"})

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            scope = {
                "type": "http",
                "method": "GET",
                "path": "/",
                "headers": [(b"host", b"example.com")],
            }
            await mw(scope, AsyncMock(), send_capture)
            assert captured[0]["status"] == 200

        asyncio.run(test())

    def test_trusted_host_with_port_passes(self):
        mw = self._make_middleware(allowed={"example.com"})

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            scope = {
                "type": "http",
                "method": "GET",
                "path": "/",
                "headers": [(b"host", b"example.com:8000")],
            }
            await mw(scope, AsyncMock(), send_capture)
            assert captured[0]["status"] == 200

        asyncio.run(test())

    def test_untrusted_host_rejected(self):
        mw = self._make_middleware(allowed={"example.com"})

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            scope = {
                "type": "http",
                "method": "GET",
                "path": "/",
                "headers": [(b"host", b"evil.com")],
            }
            await mw(scope, AsyncMock(), send_capture)
            assert captured[0]["status"] == 421
            body = json.loads(captured[1]["body"].decode("utf-8"))
            assert body["host"] == "evil.com"
            assert "Untrusted" in body["detail"]

        asyncio.run(test())

    def test_missing_host_header_rejected(self):
        mw = self._make_middleware(allowed={"example.com"})

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
            await mw(scope, AsyncMock(), send_capture)
            assert captured[0]["status"] == 421

        asyncio.run(test())

    def test_localhost_always_trusted(self):
        mw = self._make_middleware(allowed={"example.com"})

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            for h in [b"localhost", b"127.0.0.1", b"localhost:8000"]:
                captured.clear()
                scope = {
                    "type": "http", "method": "GET", "path": "/",
                    "headers": [(b"host", h)],
                }
                await mw(scope, AsyncMock(), send_capture)
                assert captured[0]["status"] == 200, f"Failed for {h}"

        asyncio.run(test())

    def test_case_insensitive(self):
        mw = self._make_middleware(allowed={"Example.com"})

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            scope = {
                "type": "http", "method": "GET", "path": "/",
                "headers": [(b"host", b"EXAMPLE.com")],
            }
            await mw(scope, AsyncMock(), send_capture)
            assert captured[0]["status"] == 200

        asyncio.run(test())

    def test_non_http_passes_through(self):
        mw = self._make_middleware(allowed={"example.com"})

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            scope = {"type": "lifespan"}
            await mw(scope, AsyncMock(), send_capture)
            assert captured[0]["type"] == "http.response.start"
            assert captured[0]["status"] == 200

        asyncio.run(test())

    def test_ipv6_trusted(self):
        mw = self._make_middleware(allowed={"[::1]"})

        async def test():
            captured = []

            async def send_capture(msg):
                captured.append(msg)

            scope = {
                "type": "http", "method": "GET", "path": "/",
                "headers": [(b"host", b"[::1]:8000")],
            }
            await mw(scope, AsyncMock(), send_capture)
            assert captured[0]["status"] == 200

        asyncio.run(test())
