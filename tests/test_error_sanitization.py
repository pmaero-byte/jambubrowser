"""Tests: error response sanitization in engine.py."""
import os
import json
import pytest
from unittest.mock import MagicMock


class TestIsDebug:
    def test_debug_off(self, monkeypatch):
        monkeypatch.setenv("JAMBU_DEBUG", "false")
        from backend.engine import _is_debug
        assert _is_debug() is False

    def test_debug_on(self, monkeypatch):
        monkeypatch.setenv("JAMBU_DEBUG", "true")
        from backend.engine import _is_debug
        assert _is_debug() is True

    def test_truthy_values(self, monkeypatch):
        for val in ("1", "yes", "TRUE", "True", "tRuE"):
            monkeypatch.setenv("JAMBU_DEBUG", val)
            from backend.engine import _is_debug
            assert _is_debug() is True, f"Failed for {val!r}"

    def test_falsy_values(self, monkeypatch):
        for val in ("0", "no", "false", "FALSE", "random", ""):
            monkeypatch.setenv("JAMBU_DEBUG", val)
            from backend.engine import _is_debug
            assert _is_debug() is False, f"Failed for {val!r}"

    def test_unset(self, monkeypatch):
        monkeypatch.delenv("JAMBU_DEBUG", raising=False)
        from backend.engine import _is_debug
        assert _is_debug() is False


class TestErrorResponseSanitization:
    def _make_request(self, path="/api/test", request_id="req-12345"):
        req = MagicMock()
        req.url.path = path
        req.scope = {"request_id": request_id}
        return req

    def test_production_hides_error_string(self, monkeypatch):
        monkeypatch.setenv("JAMBU_DEBUG", "false")
        from backend.engine import global_exception_handler

        async def run():
            req = self._make_request()
            exc = ValueError("Database password leaked: secret123")
            return await global_exception_handler(req, exc)

        import asyncio
        response = asyncio.run(run())
        body = json.loads(response.body.decode("utf-8"))
        assert body["detail"] == "Internal server error"
        assert body["request_id"] == "req-12345"
        assert "error" not in body
        assert "secret123" not in response.body.decode("utf-8")

    def test_debug_shows_error_string(self, monkeypatch):
        monkeypatch.setenv("JAMBU_DEBUG", "true")
        from backend.engine import global_exception_handler

        async def run():
            req = self._make_request(request_id="req-debug")
            exc = ValueError("Internal error: missing field")
            return await global_exception_handler(req, exc)

        import asyncio
        response = asyncio.run(run())
        body = json.loads(response.body.decode("utf-8"))
        assert body["detail"] == "Internal server error"
        assert body["error"] == "Internal error: missing field"
        assert body["request_id"] == "req-debug"

    def test_http_exception_includes_request_id(self, monkeypatch):
        monkeypatch.setenv("JAMBU_DEBUG", "false")
        from backend.engine import http_exception_handler
        from fastapi import HTTPException

        async def run():
            req = self._make_request(path="/api/missing", request_id="req-httpex")
            exc = HTTPException(status_code=404, detail="Resource not found")
            return await http_exception_handler(req, exc)

        import asyncio
        response = asyncio.run(run())
        body = json.loads(response.body.decode("utf-8"))
        assert body["detail"] == "Resource not found"
        assert body["request_id"] == "req-httpex"
        assert body["path"] == "/api/missing"

    def test_http_exception_without_request_id(self, monkeypatch):
        monkeypatch.setenv("JAMBU_DEBUG", "false")
        from backend.engine import http_exception_handler
        from fastapi import HTTPException

        async def run():
            req = MagicMock()
            req.url.path = "/api/x"
            req.scope = {}
            exc = HTTPException(status_code=400, detail="Bad request")
            return await http_exception_handler(req, exc)

        import asyncio
        response = asyncio.run(run())
        body = json.loads(response.body.decode("utf-8"))
        assert body["detail"] == "Bad request"
        assert "request_id" not in body

    def test_debug_flag_runtime_check(self, monkeypatch):
        from backend.engine import global_exception_handler

        async def call_handler():
            req = self._make_request()
            exc = ValueError("leaked-secret")
            return await global_exception_handler(req, exc)

        import asyncio

        monkeypatch.setenv("JAMBU_DEBUG", "false")
        response = asyncio.run(call_handler())
        assert "leaked-secret" not in response.body.decode("utf-8")

        monkeypatch.setenv("JAMBU_DEBUG", "true")
        response = asyncio.run(call_handler())
        body = json.loads(response.body.decode("utf-8"))
        assert body["error"] == "leaked-secret"
