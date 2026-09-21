"""Tests for Milestone 2 — datasets, binding order, matrix, JUnit."""
from __future__ import annotations

import asyncio
import copy

import pytest

from backend.modules import qa_cases as qa
from backend.modules import qa_datasets as qd


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "qa_m2.db")
    monkeypatch.setenv("JAMBU_DB_PATH", db_path)
    from backend.core import database as db_mod
    monkeypatch.setattr(db_mod, "DB_PATH", db_path)
    if db_mod._memory_db_conn is not None:
        try:
            db_mod._memory_db_conn.close()
        except Exception:
            pass
        db_mod._memory_db_conn = None
    db_mod.init_db(db_path)
    from backend.memory import reset_memory
    reset_memory()
    yield


STEPS = [
    {"action": "navigate", "url": "https://example.com/login"},
    {"action": "type", "target": "Email", "value": "{{email}}"},
    {"action": "type", "target": "Password",
     "value": "{{vault_password}}"},
    {"action": "click", "target": "Sign in"},
]


def _ok_report(n=4):
    return {"ok": True, "passed": n, "failed": 0, "total": n,
            "duration_ms": 5,
            "steps": [{"i": i + 1, "action": "x", "status": "passed"}
                      for i in range(n)],
            "console_errors": [], "artifacts": {}}


def _patch_run(monkeypatch, seen, report=None):
    import backend.modules.browser_agent as ba

    async def fake_run_test(self, **kwargs):
        seen.append(copy.deepcopy(kwargs.get("steps")))
        return copy.deepcopy(report or _ok_report())

    monkeypatch.setattr(ba.BrowserAgentService, "run_test", fake_run_test)


class TestDatasets:
    def test_crud_and_validation(self):
        created = qd.create_dataset("logins", [
            {"email": "a@x.com"}, {"email": "b@x.com"}])
        assert created["id"] and len(created["rows"]) == 2
        listed = qd.list_datasets()
        assert listed[0]["row_count"] == 2
        assert "email" in listed[0]["columns"]
        assert qd.delete_dataset(created["id"]) is True
        assert qd.get_dataset(created["id"]) is None

    def test_rejects_bad_rows(self):
        with pytest.raises(ValueError):
            qd.create_dataset("x", [])
        with pytest.raises(ValueError):
            qd.create_dataset("x", ["nope"])
        with pytest.raises(ValueError):
            qd.create_dataset("x", [{"a": 1}] * 51)


class TestBinding:
    def test_dataset_beats_env(self, monkeypatch):
        monkeypatch.setenv("email", "env@x.com")
        bound, unbound = qd.bind_placeholders(
            [{"action": "type", "value": "{{email}}"}],
            {"email": "row@x.com"}, "example.com")
        assert bound[0]["value"] == "row@x.com"
        assert unbound == []

    def test_env_fallback(self, monkeypatch):
        monkeypatch.setenv("QUERY", "from-env")
        bound, unbound = qd.bind_placeholders(
            [{"action": "type", "value": "{{query}}"}], {}, "example.com")
        assert bound[0]["value"] == "from-env"
        assert unbound == []

    def test_unbound_reported(self):
        bound, unbound = qd.bind_placeholders(
            [{"action": "type", "value": "{{missing}}"}], {}, "example.com")
        assert bound[0]["value"] == "{{missing}}"
        assert unbound == ["missing"]

    def test_vault_key(self, monkeypatch):
        import backend.modules.qa_datasets as qdm

        monkeypatch.setattr(qdm, "_vault_lookup",
                            lambda host, key: "s3cret")
        bound, unbound = qd.bind_placeholders(
            [{"action": "type", "value": "{{vault_password}}"}],
            {}, "example.com")
        assert bound[0]["value"] == "s3cret"
        assert unbound == []


class TestMatrix:
    def test_matrix_binds_each_row(self, monkeypatch):
        seen: list = []
        _patch_run(monkeypatch, seen)
        case = qa.create_case("login", "https://example.com/login",
                              STEPS, kind="login")
        rows = [{"email": "a@x.com"}, {"email": "b@x.com"}]
        import backend.modules.qa_datasets as qdm
        monkeypatch.setattr(qdm, "_vault_lookup",
                            lambda host, key: "pw")
        result = asyncio.run(qa.run_case(case["id"], dataset_rows=rows))
        assert result["matrix"] is True
        assert result["rows"] == 2 and result["ok"] is True
        assert result["passed_rows"] == 2
        assert seen[0][1]["value"] == "a@x.com"
        assert seen[1][1]["value"] == "b@x.com"
        assert seen[0][2]["value"] == "pw"
        runs = qa.list_runs(case["id"])
        assert len(runs) == 2
        assert runs[0]["dataset_rows"] == 2

    def test_unbound_row_fails_loudly(self, monkeypatch):
        seen: list = []
        _patch_run(monkeypatch, seen)
        case = qa.create_case("login", "https://example.com/login",
                              STEPS, kind="login")
        result = asyncio.run(qa.run_case(case["id"],
                                         dataset_rows=[{"email": "a@x.com"}]))
        assert result["ok"] is False
        assert result["runs"][0]["unbound"] == ["vault_password"]

    def test_case_default_dataset(self, monkeypatch):
        seen: list = []
        _patch_run(monkeypatch, seen)
        dataset = qd.create_dataset("logins", [{"email": "a@x.com"}])
        steps = [s for s in STEPS if "vault_" not in str(s)]
        case = qa.create_case("login", "https://example.com/login",
                              steps, dataset_id=dataset["id"])
        assert case["dataset_id"] == dataset["id"]
        result = asyncio.run(qa.run_case(case["id"]))
        assert result["matrix"] is True and result["ok"] is True

    def test_bad_dataset_rejected(self):
        with pytest.raises(ValueError):
            qa.create_case("x", "https://example.com/", STEPS[:1],
                           dataset_id=999)


class TestJUnit:
    def test_junit_marks_failures(self):
        xml = qd.runs_to_junit("login smoke", [
            {"ok": True, "duration_ms": 100, "failed_steps": [],
             "console_errors": [], "error": None,
             "dataset_rows": 0, "dataset_index": None},
            {"ok": False, "duration_ms": 200,
             "failed_steps": [{"i": 2, "action": "click",
                               "reason": "target_not_found",
                               "error": "nope"}],
             "console_errors": ["boom"], "error": None,
             "dataset_rows": 2, "dataset_index": 1},
        ])
        assert 'tests="2" failures="1"' in xml
        assert "<failure" in xml and "target_not_found" in xml
        assert "[row 1/2]" in xml

    def test_run_with_junit_flag(self, monkeypatch):
        seen: list = []
        _patch_run(monkeypatch, seen)
        steps = [s for s in STEPS if "vault_" not in str(s)]
        case = qa.create_case("smoke", "https://example.com/", steps)
        result = asyncio.run(qa.run_case(
            case["id"], dataset_rows=[{"email": "a@x.com"}], junit=True))
        assert "<testsuite" in result["junit"]
        single = asyncio.run(qa.run_case(case["id"], junit=True))
        assert "<testcase" in single["junit"]


class TestQaApiM2:
    @pytest.fixture
    def api(self, test_client, monkeypatch, tmp_path):
        from backend.core import database as db_mod

        db_path = str(tmp_path / "qa_m2_api.db")
        monkeypatch.setattr(db_mod, "DB_PATH", db_path)
        db_mod.init_db(db_path)
        return test_client

    def test_dataset_endpoints(self, api):
        created = api.post("/qa/datasets", json={
            "name": "logins",
            "rows": [{"email": "a@x.com"}, {"email": "b@x.com"}]})
        assert created.status_code == 200, created.text
        listed = api.get("/qa/datasets")
        assert listed.json()["count"] == 1
        detail = api.get(f"/qa/datasets/{created.json()['id']}")
        assert detail.json()["row_count"] == 2

    def test_run_matrix_with_junit(self, api, monkeypatch):
        import backend.modules.browser_agent as ba

        async def fake_run_test(self, **kwargs):
            return {"ok": True, "passed": 2, "failed": 0, "total": 2,
                    "duration_ms": 5, "steps": [
                        {"i": 1, "action": "navigate", "status": "passed"},
                        {"i": 2, "action": "type", "status": "passed"}],
                    "console_errors": [], "artifacts": {}}

        monkeypatch.setattr(ba.BrowserAgentService, "run_test",
                            fake_run_test)
        case = api.post("/qa/cases", json={
            "name": "search", "url": "https://example.com/",
            "steps": [{"action": "navigate",
                       "url": "https://example.com/"},
                      {"action": "type", "target": "Search",
                       "value": "{{query}}"}]})
        case_id = case.json()["id"]
        run = api.post(f"/qa/cases/{case_id}/run", json={
            "dataset_rows": [{"query": "a"}, {"query": "b"}],
            "junit": True})
        assert run.status_code == 200, run.text
        body = run.json()
        assert body["matrix"] is True and body["ok"] is True
        assert "<testsuite" in body["junit"]
        junit = api.get(f"/qa/cases/{case_id}/junit")
        assert junit.status_code == 200
        assert "<testsuite" in junit.text

