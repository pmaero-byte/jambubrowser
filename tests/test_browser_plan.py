"""Tests for natural-language browser test-flow planning."""
from __future__ import annotations

from backend.modules import browser_plan


def _actions(plan: dict) -> list[str]:
    return [s.get("action") for s in plan["steps"]]


class TestTemplates:
    def test_login_plan(self):
        plan = browser_plan.plan_flow("test login with a valid user",
                                      "http://localhost:3000")
        assert plan["kind"] == "login"
        assert "click" in _actions(plan)
        assert "email" in plan["placeholders"]
        assert "password" in plan["placeholders"]

    def test_checkout_plan(self):
        plan = browser_plan.plan_flow("checkout flow", "http://localhost:3000")
        assert plan["kind"] == "checkout"

    def test_accessibility_plan(self):
        plan = browser_plan.plan_flow("check accessibility / wcag", "http://x")
        assert plan["kind"] == "accessibility"
        assert "assert_no_a11y_violations" in _actions(plan)

    def test_performance_plan(self):
        plan = browser_plan.plan_flow("performance / load time", "http://x")
        assert plan["kind"] == "performance"
        assert "assert_lcp" in _actions(plan)

    def test_smoke_fallback(self):
        plan = browser_plan.plan_flow("does something random", "http://x")
        assert plan["kind"] == "smoke"
        assert plan["steps"][0]["action"] == "navigate"

    def test_kind_override(self):
        plan = browser_plan.plan_flow("ignored goal", "http://x", kind="search")
        assert plan["kind"] == "search"
        assert "press" in _actions(plan)


class TestPlanEntry:
    def test_no_llm_uses_template(self):
        plan = browser_plan.plan("login", "http://x", use_llm=False)
        assert plan["source"] == "template"

    def test_llm_result_is_used_when_available(self, monkeypatch):
        def fake(goal, url, page_summary="", provider=""):
            return {"goal": goal, "url": url, "kind": "llm",
                    "steps": [{"action": "navigate", "url": url}],
                    "placeholders": [], "source": "llm", "notes": "x"}

        monkeypatch.setattr(browser_plan, "synthesize_with_llm", fake)
        plan = browser_plan.plan("login", "http://x", use_llm=True)
        assert plan["source"] == "llm"

    def test_llm_failure_falls_back(self, monkeypatch):
        monkeypatch.setattr(browser_plan, "synthesize_with_llm",
                            lambda *a, **k: None)
        plan = browser_plan.plan("login", "http://x", use_llm=True)
        assert plan["source"] == "template"


class TestRoutes:
    def test_plan_route(self):
        from fastapi.testclient import TestClient
        from backend.engine import app

        with TestClient(app) as client:
            resp = client.post("/browser/sessions/plan", json={
                "url": "http://localhost:3000",
                "goal": "test login",
            })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["kind"] == "login"
        assert any(s["action"] == "click" for s in body["steps"])
