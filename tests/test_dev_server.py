"""Tests for local dev-server discovery and framework detection."""
from __future__ import annotations

import asyncio

from backend.modules import dev_server


def run(coro):
    return asyncio.run(coro)


class TestDetection:
    def test_vite(self):
        assert dev_server.detect_framework({}, '<script src="/@vite/client">') == "vite"

    def test_next_from_header(self):
        assert dev_server.detect_framework(
            {"x-powered-by": "Next.js"}, "<div id=\"__next\">"
        ) == "next"

    def test_cra(self):
        assert dev_server.detect_framework({}, "/static/js/bundle.js") == "create-react-app"

    def test_unknown(self):
        assert dev_server.detect_framework({"content-type": "text/html"}, "<h1>hi</h1>") is None

    def test_extract_title(self):
        assert dev_server.extract_title("<html><title> My App </title></html>") == "My App"
        assert dev_server.extract_title("<html></html>") == ""

    def test_is_loopback(self):
        assert dev_server.is_loopback("http://localhost:3000/") is True
        assert dev_server.is_loopback("http://127.0.0.1:5173/") is True
        assert dev_server.is_loopback("https://example.com/") is False


class TestScan:
    def test_scan_returns_reachable(self, monkeypatch):
        async def fake_probe(url, timeout=2.0):
            if ":3000/" in url:
                return {"url": url, "reachable": True, "status": 200,
                        "framework": "vite", "title": "App"}
            return {"url": url, "reachable": False}

        monkeypatch.setattr(dev_server, "probe_url", fake_probe)
        found = run(dev_server.scan_local_ports(ports=(3000, 5173)))
        assert len(found) == 1
        assert found[0]["port"] == 3000 and found[0]["framework"] == "vite"

    def test_wait_for_server_true(self, monkeypatch):
        async def fake_probe(url, timeout=2.0):
            return {"reachable": True, "status": 200}

        monkeypatch.setattr(dev_server, "probe_url", fake_probe)
        assert run(dev_server.wait_for_server("http://localhost:3000", timeout=1)) is True

    def test_wait_for_server_timeout(self, monkeypatch):
        async def fake_probe(url, timeout=2.0):
            return {"reachable": False}

        monkeypatch.setattr(dev_server, "probe_url", fake_probe)
        assert run(dev_server.wait_for_server("http://localhost:3000", timeout=0.2)) is False


class TestRoutes:
    def test_scan_route(self, monkeypatch):
        from fastapi.testclient import TestClient
        from backend.engine import app

        async def fake_scan(host="127.0.0.1", ports=None, timeout=0.5):
            return [{"port": 3000, "framework": "vite", "title": "App", "url": "http://127.0.0.1:3000/"}]

        monkeypatch.setattr(dev_server, "scan_local_ports", fake_scan)
        with TestClient(app) as client:
            resp = client.get("/browser/dev-servers")
        assert resp.status_code == 200, resp.text
        assert resp.json()["count"] == 1
