"""Regression: a quick scan whose employees all crashed reported "0 findings".

Failures must surface as errors (stdout, JSON, SARIF) and turn the exit code
non-zero; the Security Auditor's deterministic rules must run with no LLM.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from backend.employees import EmployeeError, SecurityAuditor
from backend.employees.base import AuditData, Finding, Severity
from backend.employees.security_rules import run_security_rules
from tests.test_cli_jambu import _FakeSSEResponse, _run_audit, _sse

# ---------------------------------------------------------------------------
# BaseEmployee.analyze
# ---------------------------------------------------------------------------


class _Registry:
    def __init__(self, content="", provider="anthropic", exc=None):
        self.content, self.provider, self.exc = content, provider, exc

    async def chat(self, messages, **kwargs):
        if self.exc:
            raise self.exc
        return SimpleNamespace(content=self.content, provider=self.provider)


def _analyze(monkeypatch, registry, data=None):
    monkeypatch.setattr("backend.llm.get_registry", lambda: registry)
    return asyncio.run(SecurityAuditor().analyze(data or AuditData(url="https://x.test")))


def test_llm_exception_raises_with_rule_findings(monkeypatch):
    with pytest.raises(EmployeeError) as exc:
        _analyze(monkeypatch, _Registry(exc=RuntimeError("connection refused")))
    assert exc.value.kind == "llm_failed"
    assert "connection refused" in exc.value.reason
    assert exc.value.partial_findings  # rules still ran


def test_unparseable_output_is_a_failure(monkeypatch):
    with pytest.raises(EmployeeError) as exc:
        _analyze(monkeypatch, _Registry(content="I could not find anything."))
    assert exc.value.kind == "unparseable_output"


def test_mock_provider_is_not_an_analysis(monkeypatch):
    with pytest.raises(EmployeeError) as exc:
        _analyze(monkeypatch, _Registry(content="[]", provider="mock"))
    assert exc.value.kind == "llm_unavailable"


def test_success_merges_rules_and_llm_findings(monkeypatch):
    content = json.dumps([{"severity": "high", "category": "secrets", "title": "API key in JS"}])
    findings = _analyze(monkeypatch, _Registry(content=content))
    titles = [f.title for f in findings]
    assert "API key in JS" in titles
    assert "Missing Content-Security-Policy" in titles


# ---------------------------------------------------------------------------
# Deterministic security rules
# ---------------------------------------------------------------------------


def test_rules_flag_missing_headers_and_inline_handlers():
    data = AuditData(
        url="https://shop.test/",
        response_headers={"Server": "nginx/1.18.0"},
        cookies=[{"name": "sid", "secure": False, "httpOnly": False}],
        page_source=('<html><body><button onclick="go()">Go</button>'
                     '<a href="javascript:void(0)">x</a>'
                     '<script src="http://cdn.test/a.js"></script></body></html>'),
    )
    titles = {f.title for f in run_security_rules(data)}
    for expected in ("Missing Content-Security-Policy",
                     "Missing X-Frame-Options / frame-ancestors",
                     "Missing X-Content-Type-Options: nosniff",
                     "Missing Referrer-Policy",
                     "Missing Strict-Transport-Security",
                     "Version disclosure in server header",
                     "1 inline event handler(s)",
                     "1 javascript: URL(s)",
                     "1 mixed-content resource(s)"):
        assert expected in titles, expected
    assert any(t.startswith("Cookie 'sid' lacks") for t in titles)


def test_rules_quiet_on_hardened_page_and_deterministic():
    data = AuditData(
        url="https://safe.test/",
        response_headers={
            "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Permissions-Policy": "camera=()",
            "Strict-Transport-Security": "max-age=31536000",
        },
        page_source="<html><body><p>hi</p></body></html>",
    )
    assert run_security_rules(data) == []
    loose = AuditData(url="http://a.test/", page_source="<b onmouseover='x()'>")
    ids_a = [f.id for f in run_security_rules(loose)]
    ids_b = [f.id for f in run_security_rules(loose)]
    assert ids_a == ids_b and ids_a


def test_unsafe_inline_csp_flagged():
    data = AuditData(url="http://a.test/", response_headers={
        "Content-Security-Policy": "script-src 'self' 'unsafe-inline'"})
    assert "CSP allows 'unsafe-inline' scripts" in {f.title for f in run_security_rules(data)}


# ---------------------------------------------------------------------------
# Pipeline: done event carries the errors
# ---------------------------------------------------------------------------


@pytest.fixture
def failing_pipeline(monkeypatch, tmp_path):
    db_path = str(tmp_path / "p.db")
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

    async def fake_collect(req):
        return AuditData(url=req.url, title="T", page_source="<html></html>")

    monkeypatch.setattr("backend.routes.audit.collect_page_data", fake_collect)
    from backend.employees.product_context import ProductContext, ProductContextExtractor

    async def failed_context(self, data):
        return ProductContext("Unable to determine", "Unknown", [], "Unknown", [], "Unknown",
                              "Unknown", "Unknown", "Unknown", "", error="llm call failed: boom")

    monkeypatch.setattr(ProductContextExtractor, "extract_context", failed_context)
    from backend.employees import QUICK_SCAN_EMPLOYEES

    async def crash(self, data):
        raise EmployeeError(self.name, "llm call failed: boom", kind="llm_failed",
                            partial_findings=[Finding(id=f"r-{self.name}", employee=self.name,
                                                      severity=Severity.LOW, category="headers",
                                                      title=f"rule {self.name}")])

    for cls in QUICK_SCAN_EMPLOYEES:
        monkeypatch.setattr(cls, "analyze", crash)
    return QUICK_SCAN_EMPLOYEES


def test_done_event_reports_incomplete(failing_pipeline):
    from backend.routes.audit import AuditRunRequest, _audit_event_stream

    async def go():
        return [ev async for ev in _audit_event_stream(AuditRunRequest(url="https://x.test",
                                                                       mode="quick"))]

    events = asyncio.run(go())
    names = [e for e, _ in events]
    assert names.count("employee_error") == len(failing_pipeline)
    assert "product_context_error" in names
    done = dict(events)["done"]
    assert done["status"] == "incomplete"
    assert done["employees_failed"] == len(failing_pipeline)
    assert {e["employee"] for e in done["errors"]} >= {"Product Context"}
    # Partial rule findings are kept, not discarded.
    assert done["total_findings"] == len(failing_pipeline)


# ---------------------------------------------------------------------------
# CLI: output, exports and exit code
# ---------------------------------------------------------------------------

_ERRORS = [{"employee": "Security Auditor", "kind": "llm_failed", "error": "boom",
            "partial_findings": 0}]


def _failed_stream():
    return _FakeSSEResponse(_sse([
        ("employee_error", {"employee": "Security Auditor", "kind": "llm_failed",
                            "error": "boom", "findings": []}),
        ("done", {"total_findings": 0, "dismissed_count": 0, "by_severity": {},
                  "findings": [], "status": "incomplete", "errors": _ERRORS}),
    ]))


def test_cli_failed_employee_exits_nonzero_and_exports_errors(tmp_path):
    sarif_path, json_path = tmp_path / "a.sarif", tmp_path / "a.json"
    text, code = _run_audit(["quick", "https://x.test", "--sarif", str(sarif_path),
                             "--json", str(json_path)], _failed_stream())
    assert code == 2
    assert "INCOMPLETE" in text and "Security Auditor" in text
    body = json.loads(json_path.read_text())
    assert body["status"] == "incomplete"
    assert body["errors"][0]["employee"] == "Security Auditor"
    inv = json.loads(sarif_path.read_text())["runs"][0]["invocations"][0]
    assert inv["executionSuccessful"] is False
    assert inv["toolExecutionNotifications"][0]["level"] == "error"


def test_cli_allow_partial_keeps_exit_zero():
    text, code = _run_audit(["quick", "https://x.test", "--allow-partial"], _failed_stream())
    assert code == 0
    assert "INCOMPLETE" in text


def test_cli_truncated_stream_is_an_error():
    response = _FakeSSEResponse(_sse([("status", {"phase": "collecting"})]))
    text, code = _run_audit(["quick", "https://x.test"], response)
    assert code == 2
    assert "without a 'done' event" in text
