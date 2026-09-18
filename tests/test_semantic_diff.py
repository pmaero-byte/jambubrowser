"""Tests for semantic UI diffing."""
from __future__ import annotations


from backend.modules.semantic_diff import explain_diff, semantic_diff_elements


class TestSemanticDiffElements:
    def test_added_and_removed(self):
        before = [{"ref": "@e1", "name": "Home", "tag": "a"}]
        after = [
            {"ref": "@e1", "name": "Home", "tag": "a"},
            {"ref": "@e2", "name": "Error banner", "tag": "div"},
        ]
        diff = semantic_diff_elements(before, after)
        assert diff["changed"] is True
        assert diff["counts"]["added"] == 1
        assert "Error banner" in diff["summary"]

    def test_state_change(self):
        before = [{"ref": "@e1", "name": "Remember me", "checked": False}]
        after = [{"ref": "@e1", "name": "Remember me", "checked": True}]
        diff = semantic_diff_elements(before, after)
        assert diff["counts"]["state_changed"] == 1
        assert diff["state_changed"][0]["property"] == "checked"

    def test_no_change(self):
        same = [{"ref": "@e1", "name": "Home"}]
        diff = semantic_diff_elements(same, same)
        assert diff["changed"] is False
        assert diff["summary"] == "no semantic change"

    def test_explain_falls_back_without_llm(self):
        diff = semantic_diff_elements([], [{"ref": "@e1", "name": "New"}])
        assert "New" in explain_diff(diff)


class TestSemanticDiffRoute:
    def test_route_returns_summary(self):
        from fastapi.testclient import TestClient
        from backend.engine import app

        with TestClient(app) as client:
            resp = client.post("/browser/sessions/semantic-diff", json={
                "before_elements": [{"ref": "@e1", "name": "A"}],
                "after_elements": [
                    {"ref": "@e1", "name": "A"},
                    {"ref": "@e2", "name": "B"},
                ],
            })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["counts"]["added"] == 1
        assert "explanation" not in body
