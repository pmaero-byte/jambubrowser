"""
Regression & unit tests for the dedicated Desktop / YaadhumAI endpoints.
"""
import pytest
from fastapi.testclient import TestClient

from backend.routes.browser_desktop import (
    _normalize_url,
    _clean_markdown_text,
    DesktopInspectRequest,
    DesktopActRequest,
    DesktopAuditRequest,
    DesktopResearchRequest,
)


def test_url_normalization():
    assert _normalize_url("example.com") == "https://example.com"
    assert _normalize_url("https://example.com") == "https://example.com"
    assert _normalize_url("http://127.0.0.1:8001") == "http://127.0.0.1:8001"
    assert _normalize_url("file:///tmp/doc.html") == "file:///tmp/doc.html"
    assert _normalize_url("") == "about:blank"
    assert _normalize_url("about:blank") == "about:blank"


def test_clean_markdown_text():
    raw = "   \n\n  Hello World   \n   \n   Second Line   \n\n  "
    cleaned = _clean_markdown_text(raw)
    assert cleaned == "Hello World\n\nSecond Line"


@pytest.fixture
def client():
    from backend.engine import app
    return TestClient(app)


def test_desktop_routes_registered(client):
    from backend.engine import app
    paths = set(app.openapi()["paths"].keys())
    expected = [
        "/browser/desktop/status",
        "/browser/desktop/inspect",
        "/browser/desktop/act",
        "/browser/desktop/audit",
        "/browser/desktop/research",
        "/browser/yaadhum/status",
        "/browser/yaadhum/inspect",
        "/browser/yaadhum/act",
        "/browser/yaadhum/audit",
        "/browser/yaadhum/research",
    ]
    for p in expected:
        assert p in paths, f"Route {p} not registered in app"


def test_desktop_status_endpoint(client):
    res = client.get("/browser/desktop/status")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "engine" in data
    assert data["engine"]["version"] == "3.4.0"
    assert "local_workspaces" in data
    assert "memory_bridge" in data
    assert "greenwich_llm" in data


def test_yaadhum_alias_status_endpoint(client):
    res = client.get("/browser/yaadhum/status")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["engine"]["version"] == "3.4.0"


def test_desktop_audit_pii_and_scoring(client):
    payload = {
        "url": "http://127.0.0.1:8001/health",
        "sync_memory": False,
    }
    res = client.post("/browser/desktop/audit", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "score" in data
    assert 0 <= data["score"] <= 100
    assert "findings" in data
    assert "summary" in data


def test_desktop_act_validation(client):
    # Missing both ref and coordinates should return 400 or 404
    res = client.post("/browser/desktop/act", json={"session_id": "fake-session", "action": "click"})
    assert res.status_code in (400, 404)


def test_inspect_blocked_url(client):
    # A disallowed private IP should return 403
    res = client.post("/browser/desktop/inspect", json={"url": "http://192.168.1.55/admin"})
    assert res.status_code == 403
