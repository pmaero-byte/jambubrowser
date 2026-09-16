"""
Integration tests for the shared audit pipeline (``_audit_event_stream``).

These stub the two expensive boundaries — Playwright collection and the LLM
employees — so the full pipeline (dedupe → dismissals → history → ``done``)
runs in-process in milliseconds. The contract under test:

- ``done`` carries the persisted ``audit_id`` (used by the UI's Report /
  Share / Export actions).
- History reflects *active* (post-dismissal) findings only.
- Dismissals filter interactive and scheduled runs identically, because
  both consume this one generator.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from backend.employees.base import AuditData, Finding, Severity


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    """Isolate the DB and stub collection / product context / employees."""
    db_path = str(tmp_path / "pipeline.db")
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
        return AuditData(
            url=req.url,
            title="Example App",
            load_time_ms=42.0,
            dom_snapshot="<html></html>",
            page_source="<html></html>",
        )

    monkeypatch.setattr("backend.routes.audit.collect_page_data", fake_collect)

    from backend.employees.product_context import (
        ProductContext,
        ProductContextExtractor,
    )

    async def fake_context(self, data):
        return ProductContext(
            what_it_does="test app",
            target_audience="devs",
            key_features=[],
            value_proposition="v",
            tech_stack=[],
            business_model="free",
            user_journey="j",
            conversion_flow="c",
            competitive_advantage="a",
            raw_analysis="",
        )

    monkeypatch.setattr(ProductContextExtractor, "extract_context", fake_context)

    from backend.employees import QUICK_SCAN_EMPLOYEES

    async def fake_analyze(self, data):
        return [Finding(
            id=f"f-{self.name}",
            employee=self.name,
            severity=Severity.HIGH,
            category="test",
            title=f"Finding from {self.name}",
            description="description",
            fix_suggestion="fix",
        )]

    for cls in QUICK_SCAN_EMPLOYEES:
        monkeypatch.setattr(cls, "analyze", fake_analyze)

    return QUICK_SCAN_EMPLOYEES


def _run_pipeline(url: str = "https://example.com"):
    from backend.routes.audit import AuditRunRequest, _audit_event_stream

    async def go():
        return [ev async for ev in _audit_event_stream(
            AuditRunRequest(url=url, mode="quick")
        )]

    return asyncio.run(go())


class TestAuditPipeline:
    def test_done_event_carries_persisted_audit_id(self, pipeline):
        events = _run_pipeline()
        done = dict(events)["done"]
        assert done["audit_id"] is not None
        assert done["total_findings"] == len(pipeline)
        assert done["url"] == "https://example.com"

        from backend.core.database import get_db
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, url, title, mode, total_findings, findings_json"
                " FROM audit_history"
            ).fetchall()
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == done["audit_id"]
        assert row["url"] == "https://example.com"
        assert row["title"] == "Example App"
        assert row["mode"] == "quick"
        assert row["total_findings"] == len(pipeline)
        stored = json.loads(row["findings_json"])
        assert {f["employee"] for f in stored} == {c.name for c in pipeline}

    def test_dismissals_filter_pipeline_and_history(self, pipeline):
        from backend.core.database import get_db
        from backend.employees.export import content_fingerprint

        dismissed_employee = pipeline[0].name
        target = Finding(
            id="dismiss-me",
            employee=dismissed_employee,
            severity=Severity.HIGH,
            category="test",
            title=f"Finding from {dismissed_employee}",
        )
        with get_db() as conn:
            conn.execute(
                "INSERT INTO dismissed_findings"
                " (fingerprint, url, employee, category, severity, title)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    content_fingerprint(target), "https://example.com",
                    target.employee, "test", "high", target.title,
                ),
            )
            conn.commit()

        done = dict(_run_pipeline())["done"]
        assert done["total_findings"] == len(pipeline) - 1
        assert done["dismissed_count"] == 1
        assert done["dismissed"][0]["employee"] == dismissed_employee

        with get_db() as conn:
            row = conn.execute(
                "SELECT findings_json FROM audit_history WHERE id = ?",
                (done["audit_id"],),
            ).fetchone()
        stored = json.loads(row["findings_json"])
        assert all(f["employee"] != dismissed_employee for f in stored)

    def test_html_report_renders_pipeline_findings(self, pipeline):
        """End-to-end: run → persist → HTML report includes the findings."""
        from backend.core.database import get_db
        from backend.employees.export import findings_to_html

        done = dict(_run_pipeline())["done"]
        with get_db() as conn:
            row = conn.execute(
                "SELECT url, findings_json FROM audit_history WHERE id = ?",
                (done["audit_id"],),
            ).fetchone()
        findings = [Finding.from_dict(f) for f in json.loads(row["findings_json"])]
        html = findings_to_html(findings, audited_url=row["url"])
        assert all(c.name in html for c in pipeline)


class TestMonitorExecutorErrorSurface:
    def test_collect_error_is_included_in_the_raised_message(self, monkeypatch):
        """Monitor runs must record why an audit failed (e.g. missing
        Playwright browser), not a generic 'no done event'."""
        from backend.modules.audit_monitor import _execute_audit

        async def boom(req):
            raise RuntimeError("Executable doesn't exist at chrome-headless-shell")

        monkeypatch.setattr("backend.routes.audit.collect_page_data", boom)

        with pytest.raises(RuntimeError) as excinfo:
            asyncio.run(_execute_audit("https://example.com", "quick"))
        message = str(excinfo.value)
        assert "collect" in message
        assert "Executable doesn't exist" in message
