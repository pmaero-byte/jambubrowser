"""
Tests for the finding-export and dismiss-findings features.

Covers:
- SARIF 2.1.0 envelope structure (rules, results, levels, run id, schema).
- Canonical JSON envelope (severity / employee / category aggregations, content hash).
- Markdown export (severity ordering, sections, evidence / fix rendering).
- Dismiss / undismiss endpoints (round-trip, idempotency, URL safety).
- Saved-audit export endpoints (SARIF / JSON / Markdown downloads).
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone

import pytest

from backend.employees.base import Finding, Severity
from backend.employees.export import (
    findings_to_canonical_json,
    findings_to_html,
    findings_to_markdown,
    findings_to_sarif,
    sarif_to_json,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def sample_findings():
    return [
        Finding(
            id="a1",
            employee="Security Auditor",
            severity=Severity.CRITICAL,
            category="csp",
            title="Missing CSP header",
            description="No Content-Security-Policy header is set.",
            fix_suggestion="Add a strict CSP header.",
            evidence_snippet="strict-transport-security: max-age=31536000",
        ),
        Finding(
            id="b2",
            employee="Performance Inspector",
            severity=Severity.HIGH,
            category="lcp",
            title="Slow LCP",
            description="Largest Contentful Paint is 4.2s.",
            fix_suggestion="Preload the hero image.",
            evidence_snippet="lcp: 4200ms",
            score_impact="-12 Lighthouse points",
        ),
        Finding(
            id="c3",
            employee="Accessibility Auditor",
            severity=Severity.MEDIUM,
            category="contrast",
            title="Low contrast text",
            description="Body text contrast ratio is 3.1:1.",
            fix_suggestion="Use #1a1a1a on #fff.",
            evidence_snippet="color: #888 on #fff",
            wcag_criterion="1.4.3",
        ),
        Finding(
            id="d4",
            employee="Performance Inspector",
            severity=Severity.LOW,
            category="render_blocking",
            title="Render-blocking CSS",
            description="A 200KB CSS file blocks first paint.",
            fix_suggestion="Inline critical CSS.",
            evidence_snippet="link rel=stylesheet href=/main.css",
        ),
        Finding(
            id="e5",
            employee="SEO Analyzer",
            severity=Severity.INFO,
            category="og_tags",
            title="Missing OG image",
            description="og:image is not set.",
            fix_suggestion="Add og:image meta tag.",
            evidence_snippet="<head></head>",
        ),
    ]


# ---------------------------------------------------------------------------
# SARIF
# ---------------------------------------------------------------------------
class TestSarifExport:
    def test_sarif_envelope_shape(self, sample_findings):
        sarif = findings_to_sarif(sample_findings, audited_url="https://example.com")
        assert sarif["version"] == "2.1.0"
        assert sarif["$schema"].startswith("https://")
        assert len(sarif["runs"]) == 1
        run = sarif["runs"][0]
        assert run["tool"]["driver"]["name"] == "Jambubrowser AI Employees"
        assert "rules" in run["tool"]["driver"]
        assert len(run["results"]) == len(sample_findings)

    def test_sarif_severity_mapping(self, sample_findings):
        sarif = findings_to_sarif(sample_findings, audited_url="https://example.com")
        by_id = {r["ruleId"]: r for r in sarif["runs"][0]["results"]}
        # critical + high → error; medium → warning; low → note; info → none
        assert by_id["jambu/security-auditor/csp"]["level"] == "error"
        assert by_id["jambu/performance-inspector/lcp"]["level"] == "error"
        assert by_id["jambu/accessibility-auditor/contrast"]["level"] == "warning"
        assert by_id["jambu/performance-inspector/render-blocking"]["level"] == "note"
        assert by_id["jambu/seo-analyzer/og-tags"]["level"] == "none"

    def test_sarif_rule_ids_contain_employee_and_category(self, sample_findings):
        sarif = findings_to_sarif(sample_findings, audited_url="https://example.com")
        rule_ids = [r["id"] for r in sarif["runs"][0]["tool"]["driver"]["rules"]]
        # Each rule id must start with "jambu/" and contain both an employee
        # slug and a category slug.
        for rid in rule_ids:
            assert rid.startswith("jambu/")
            parts = rid.split("/")
            assert len(parts) == 3
            assert all(p for p in parts)

    def test_sarif_preserves_jambu_properties(self, sample_findings):
        sarif = findings_to_sarif(sample_findings, audited_url="https://example.com")
        first = sarif["runs"][0]["results"][0]
        jambu = first["properties"]["jambu"]
        assert jambu["employee"] == "Security Auditor"
        assert jambu["severity"] == "critical"
        assert jambu["title"] == "Missing CSP header"

    def test_sarif_locations_point_at_audited_url(self, sample_findings):
        sarif = findings_to_sarif(sample_findings, audited_url="https://example.com")
        loc = sarif["runs"][0]["results"][0]["locations"][0]
        assert loc["physicalLocation"]["artifactLocation"]["uriBaseId"] == "AUDITED_URL"
        base_ids = sarif["runs"][0]["originalUriBaseIds"]
        assert base_ids["AUDITED_URL"]["uri"] == "https://example.com"

    def test_sarif_includes_fixes(self, sample_findings):
        sarif = findings_to_sarif(sample_findings, audited_url="https://example.com")
        first = sarif["runs"][0]["results"][0]
        assert "fixes" in first
        assert "description" in first["fixes"][0]

    def test_sarif_run_id_override(self, sample_findings):
        sarif = findings_to_sarif(
            sample_findings,
            audited_url="https://example.com",
            run_id="test-run-42",
        )
        inv = sarif["runs"][0]["invocations"][0]
        assert inv["properties"]["jambu"]["run_id"] == "test-run-42"

    def test_sarif_serializes_to_json(self, sample_findings):
        sarif = findings_to_sarif(sample_findings, audited_url="https://example.com")
        text = sarif_to_json(sarif)
        reparsed = json.loads(text)
        assert reparsed["version"] == "2.1.0"
        assert len(reparsed["runs"][0]["results"]) == len(sample_findings)

    def test_sarif_handles_empty_findings(self):
        sarif = findings_to_sarif([], audited_url="https://example.com")
        assert sarif["runs"][0]["results"] == []
        assert sarif["runs"][0]["tool"]["driver"]["rules"] == []

    def test_sarif_handles_missing_evidence(self):
        f = Finding(
            id="x",
            employee="Test",
            severity=Severity.LOW,
            category="misc",
            title="No evidence finding",
            description="Just a description.",
            fix_suggestion="Do the thing.",
            evidence_snippet="",
        )
        sarif = findings_to_sarif([f], audited_url="https://example.com")
        loc = sarif["runs"][0]["results"][0]["locations"][0]
        assert loc["physicalLocation"]["artifactLocation"]["uri"] == "page"


# ---------------------------------------------------------------------------
# Canonical JSON
# ---------------------------------------------------------------------------
class TestCanonicalJson:
    def test_envelope_includes_aggregations(self, sample_findings):
        body = findings_to_canonical_json(
            sample_findings, audited_url="https://example.com"
        )
        assert body["audited_url"] == "https://example.com"
        assert body["total_findings"] == 5
        assert body["by_severity"]["critical"] == 1
        assert body["by_severity"]["high"] == 1
        assert body["by_severity"]["medium"] == 1
        assert body["by_severity"]["low"] == 1
        assert body["by_severity"]["info"] == 1
        assert body["by_employee"]["Security Auditor"] == 1
        assert body["by_employee"]["Performance Inspector"] == 2
        assert body["by_category"]["csp"] == 1

    def test_content_hash_is_stable(self, sample_findings):
        body_a = findings_to_canonical_json(
            sample_findings, audited_url="https://example.com"
        )
        body_b = findings_to_canonical_json(
            sample_findings, audited_url="https://example.com"
        )
        hashes_a = [f["content_hash"] for f in body_a["findings"]]
        hashes_b = [f["content_hash"] for f in body_b["findings"]]
        assert hashes_a == hashes_b

    def test_content_hash_differs_per_finding(self, sample_findings):
        body = findings_to_canonical_json(
            sample_findings, audited_url="https://example.com"
        )
        hashes = {f["content_hash"] for f in body["findings"]}
        # All five findings are unique → all five hashes should be unique.
        assert len(hashes) == len(sample_findings)

    def test_summary_envelope_passthrough(self, sample_findings):
        body = findings_to_canonical_json(
            sample_findings,
            audited_url="https://example.com",
            summary={"mode": "full", "duration_ms": 1234.5},
        )
        assert body["summary"]["mode"] == "full"
        assert body["summary"]["duration_ms"] == 1234.5

    def test_serializes_via_json_dumps(self, sample_findings):
        body = findings_to_canonical_json(
            sample_findings, audited_url="https://example.com"
        )
        text = json.dumps(body, default=str)
        reparsed = json.loads(text)
        assert reparsed["total_findings"] == 5


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
class TestMarkdownExport:
    def test_contains_header(self, sample_findings):
        md = findings_to_markdown(sample_findings, audited_url="https://example.com")
        assert "# Audit Report — https://example.com" in md
        assert "**Total findings:** 5" in md

    def test_groups_by_employee(self, sample_findings):
        md = findings_to_markdown(sample_findings, audited_url="https://example.com")
        # Both Performance Inspector findings appear under one section.
        assert "## Performance Inspector (2 findings)" in md
        # One finding per section for the rest.
        assert "## Security Auditor (1 findings)" in md
        assert "## Accessibility Auditor (1 findings)" in md
        assert "## SEO Analyzer (1 findings)" in md

    def test_severity_summary_present(self, sample_findings):
        md = findings_to_markdown(sample_findings, audited_url="https://example.com")
        assert "## Summary by Severity" in md
        assert "**Critical**: 1" in md
        assert "**High**: 1" in md

    def test_includes_evidence_and_fix(self, sample_findings):
        md = findings_to_markdown(sample_findings, audited_url="https://example.com")
        assert "Missing CSP header" in md
        assert "Add a strict CSP header" in md
        assert "strict-transport-security: max-age=31536000" in md

    def test_includes_wcag_and_score_impact(self, sample_findings):
        md = findings_to_markdown(sample_findings, audited_url="https://example.com")
        assert "WCAG:** 1.4.3" in md
        assert "-12 Lighthouse points" in md

    def test_empty_findings(self):
        md = findings_to_markdown([], audited_url="https://example.com")
        assert "**Total findings:** 0" in md


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------
class TestHtmlExport:
    def test_self_contained_document(self, sample_findings):
        html = findings_to_html(sample_findings, audited_url="https://example.com")
        assert html.startswith("<!doctype html>")
        assert "<style>" in html
        assert "https://example.com" in html
        # No external assets — reports must render offline / from a share link.
        assert "<script" not in html
        assert "http-equiv" not in html or "content-security" not in html

    def test_renders_stats_and_severity_sections(self, sample_findings):
        html = findings_to_html(sample_findings, audited_url="https://example.com")
        for sev in ("Critical", "High", "Medium", "Low", "Info"):
            assert f">{sev}<" in html
        assert "Missing CSP header" in html
        assert "Add a strict CSP header" in html
        assert "1.4.3" in html  # WCAG reference survives
        assert "-12 Lighthouse points" in html

    def test_escapes_hostile_content(self):
        """Findings are LLM prose about arbitrary pages — everything must be
        escaped before it reaches a shared report (stored XSS guard)."""
        hostile = Finding(
            id="xss",
            employee='<script>alert("emp")</script>',
            severity=Severity.CRITICAL,
            category="<img src=x onerror=alert(1)>",
            title="<b>bold</b> title",
            description="</p><script>evil()</script>",
            fix_suggestion="<script>bad()</script>",
            evidence_snippet="<svg onload=alert(1)>",
        )
        html = findings_to_html(
            [hostile], audited_url='https://example.com/?q=<script>alert(1)</script>'
        )
        # No executable tags make it through, from any field or the URL.
        assert "<script>evil()" not in html
        assert "<script>bad()" not in html
        assert "<script>alert" not in html
        assert "<img" not in html
        assert "<svg" not in html
        assert "<b>bold</b>" not in html
        # ...but the escaped text is present for humans to see.
        assert "&lt;b&gt;bold&lt;/b&gt; title" in html
        assert "&lt;script&gt;evil()&lt;/script&gt;" in html

    def test_empty_state(self):
        html = findings_to_html([], audited_url="https://example.com")
        assert "No active findings" in html

    def test_summary_rendered_as_table(self, sample_findings):
        html = findings_to_html(
            sample_findings,
            audited_url="https://example.com",
            summary={"mode": "full", "dismissed": 2},
        )
        assert "<th>mode</th><td>full</td>" in html
        assert "<th>dismissed</th><td>2</td>" in html

    def test_print_friendly(self, sample_findings):
        html = findings_to_html(sample_findings, audited_url="https://example.com")
        assert "@media print" in html
        assert "page-break-inside" in html


# ---------------------------------------------------------------------------
# History timestamp normalisation
# ---------------------------------------------------------------------------
class TestHistoryCreatedAtNormalization:
    def test_created_at_epoch_conversion(self):
        import time

        from backend.routes.audit import _created_at_epoch

        now = time.time()
        assert _created_at_epoch(None) is None
        assert abs(_created_at_epoch(now) - now) < 1
        # Julian Day values (legacy rows) convert to plausible epoch seconds.
        assert _created_at_epoch(2460570.5) > 1_000_000_000
        assert _created_at_epoch("not-a-date") is None

    def test_history_route_normalises_both_formats(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "history.db")
        monkeypatch.setenv("JAMBU_DB_PATH", db_path)
        from backend.core import database as db_mod
        monkeypatch.setattr(db_mod, "DB_PATH", db_path)
        if db_mod._memory_db_conn is not None:
            try:
                db_mod._memory_db_conn.close()
            except Exception:
                pass
            db_mod._memory_db_conn = None
        from backend.core.database import get_db
        with get_db() as conn:
            conn.execute(
                "INSERT INTO audit_history (url, title, mode, total_findings,"
                " findings_json, created_at) VALUES (?, ?, ?, ?, ?, julianday('now'))",
                ("https://jd.example.com", "JD", "quick", 0, "[]"),
            )
            conn.execute(
                "INSERT INTO audit_history (url, title, mode, total_findings,"
                " findings_json, created_at) VALUES (?, ?, ?, ?, ?, strftime('%s','now'))",
                ("https://epoch.example.com", "Epoch", "quick", 0, "[]"),
            )
            conn.commit()

        import time
        from fastapi.testclient import TestClient
        from backend.engine import app

        with TestClient(app) as client:
            audits = client.get("/audit/history").json()["audits"]

        by_url = {a["url"]: a for a in audits}
        now = time.time()
        for url in ("https://jd.example.com", "https://epoch.example.com"):
            created = by_url[url]["created_at"]
            assert abs(created - now) < 120, f"{url} created_at={created}"


# ---------------------------------------------------------------------------
# Dismiss / undismiss endpoints
# ---------------------------------------------------------------------------
class TestDismissFlow:
    """End-to-end tests for /audit/dismiss, /audit/dismissed, /audit/dismiss.

    These talk to the real DB via the FastAPI TestClient. They use a temp
    DB path so they're hermetic and don't pollute rag_data.db.
    """

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        # Redirect the database to a fresh file inside tmp_path. We have to
        # mutate the module-level DB_PATH (not just the env var) because it
        # was already read at import time, and we have to drop the cached
        # in-memory singleton so the new file is used.
        db_path = str(tmp_path / "test_dismiss.db")
        monkeypatch.setenv("JAMBU_DB_PATH", db_path)
        from backend.core import database as db_mod
        monkeypatch.setattr(db_mod, "DB_PATH", db_path)
        if db_mod._memory_db_conn is not None:
            try:
                db_mod._memory_db_conn.close()
            except Exception:
                pass
            db_mod._memory_db_conn = None

        from fastapi.testclient import TestClient
        from backend.engine import app

        with TestClient(app) as c:
            yield c

    def test_dismiss_then_list(self, client):
        url = "https://example.com"
        finding = {
            "employee": "Security Auditor",
            "category": "csp",
            "severity": "critical",
            "title": "Missing CSP header",
        }
        # Dismiss
        resp = client.post(
            "/audit/dismiss",
            json={"url": url, "finding": finding, "reason": "not applicable"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "dismissed"
        assert data["fingerprint"]
        assert data["url"] == url

        # List
        resp = client.get("/audit/dismissed", params={"url": url})
        assert resp.status_code == 200, resp.text
        listed = resp.json()
        assert listed["count"] == 1
        assert listed["dismissed"][0]["title"] == "Missing CSP header"
        assert listed["dismissed"][0]["reason"] == "not applicable"

    def test_dismiss_is_idempotent(self, client):
        url = "https://example.com"
        finding = {
            "employee": "Security Auditor",
            "category": "csp",
            "severity": "critical",
            "title": "Missing CSP header",
        }
        first = client.post(
            "/audit/dismiss", json={"url": url, "finding": finding}
        ).json()
        second = client.post(
            "/audit/dismiss",
            json={"url": url, "finding": finding, "reason": "second pass"},
        ).json()
        assert first["fingerprint"] == second["fingerprint"]

        listed = client.get("/audit/dismissed", params={"url": url}).json()
        assert listed["count"] == 1
        # Second pass should have updated the reason.
        assert listed["dismissed"][0]["reason"] == "second pass"

    def test_undismiss(self, client):
        url = "https://example.com"
        finding = {
            "employee": "Performance Inspector",
            "category": "lcp",
            "severity": "high",
            "title": "Slow LCP",
        }
        fp = client.post(
            "/audit/dismiss", json={"url": url, "finding": finding}
        ).json()["fingerprint"]

        resp = client.request(
            "DELETE",
            "/audit/dismiss",
            params={"url": url, "fingerprint": fp},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "undismissed"

        listed = client.get("/audit/dismissed", params={"url": url}).json()
        assert listed["count"] == 0

    def test_undismiss_unknown_returns_404(self, client):
        resp = client.request(
            "DELETE",
            "/audit/dismiss",
            params={"url": "https://example.com", "fingerprint": "nonexistent"},
        )
        assert resp.status_code == 404

    def test_dismiss_rejects_unsafe_url(self, client):
        finding = {
            "employee": "X",
            "category": "y",
            "severity": "low",
            "title": "z",
        }
        resp = client.post(
            "/audit/dismiss",
            json={"url": "http://localhost:9999", "finding": finding},
        )
        # Pydantic validator raises ValueError → 422
        assert resp.status_code in (400, 422)

    def test_dismissed_finding_is_suppressed_on_reaudit(self, client):
        """The audit pipeline must filter dismissed findings server-side,
        not rely on the UI to hide them."""
        from backend.employees.base import Finding, Severity
        from backend.employees.export import content_fingerprint
        from backend.routes.audit import _split_dismissed_findings

        url = "https://example.com"
        finding = Finding(
            id="live-1",
            employee="Security Auditor",
            severity=Severity.CRITICAL,
            category="csp",
            title="Missing CSP header",
            description="No CSP header.",
            fix_suggestion="Add CSP.",
        )

        # Before dismissal: the finding is active.
        active, suppressed = _split_dismissed_findings([finding], url)
        assert active == [finding]
        assert suppressed == []

        # Dismiss via the API and confirm the two hashing paths agree.
        fp = content_fingerprint(finding)
        resp = client.post(
            "/audit/dismiss",
            json={"url": url, "finding": finding.to_dict()},
        )
        assert resp.status_code == 200
        assert resp.json()["fingerprint"] == fp

        # Re-audit with a *new* per-audit id but identical content:
        # the finding must be suppressed by fingerprint.
        reaudited = Finding(
            id="live-2",
            employee="Security Auditor",
            severity=Severity.CRITICAL,
            category="csp",
            title="Missing CSP header",
            description="No CSP header (re-run).",
            fix_suggestion="Add CSP.",
        )
        active, suppressed = _split_dismissed_findings([reaudited], url)
        assert active == []
        assert len(suppressed) == 1
        assert suppressed[0]["fingerprint"] == fp
        assert suppressed[0]["title"] == "Missing CSP header"

    def test_dismissal_is_scoped_per_url(self, client):
        from backend.employees.base import Finding, Severity
        from backend.routes.audit import _split_dismissed_findings

        finding = Finding(
            id="f",
            employee="SEO Analyzer",
            severity=Severity.LOW,
            category="og_tags",
            title="Missing OG image",
        )
        client.post(
            "/audit/dismiss",
            json={"url": "https://example.com", "finding": finding.to_dict()},
        )
        # A different URL is unaffected by the dismissal.
        active, suppressed = _split_dismissed_findings([finding], "https://other.com")
        assert active == [finding]
        assert suppressed == []

    def test_undismiss_restores_finding(self, client):
        from backend.employees.base import Finding, Severity
        from backend.routes.audit import _split_dismissed_findings

        url = "https://example.com"
        finding = Finding(
            id="u1",
            employee="Performance Inspector",
            severity=Severity.HIGH,
            category="lcp",
            title="Slow LCP",
        )
        fp = client.post(
            "/audit/dismiss", json={"url": url, "finding": finding.to_dict()}
        ).json()["fingerprint"]
        assert _split_dismissed_findings([finding], url)[0] == []

        resp = client.request(
            "DELETE", "/audit/dismiss", params={"url": url, "fingerprint": fp}
        )
        assert resp.status_code == 200
        assert _split_dismissed_findings([finding], url)[0] == [finding]


class TestAuditRouteRegistration:
    def test_quick_endpoint_is_registered(self):
        """The UI calls POST /audit/quick for quick scans; it must exist."""
        from backend.engine import app
        paths = app.openapi().get("paths", {})
        assert "/audit/quick" in paths

    def test_quick_endpoint_validates_url(self):
        """POST /audit/quick exists *and* validates URLs (422, not 404)."""
        from backend.engine import app
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            resp = client.post("/audit/quick", json={"url": "http://169.254.169.254/"})
            assert resp.status_code in (400, 422)


# ---------------------------------------------------------------------------
# Saved-audit export endpoints (GET)
# ---------------------------------------------------------------------------
class TestSavedAuditExport:
    """End-to-end tests for GET /audit/export/{format}?audit_id=...

    We seed a saved audit directly into the DB, then verify the export
    endpoints return correctly-shaped downloads.
    """

    @pytest.fixture
    def client_with_seed(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test_export.db")
        monkeypatch.setenv("JAMBU_DB_PATH", db_path)
        from backend.core import database as db_mod
        monkeypatch.setattr(db_mod, "DB_PATH", db_path)
        if db_mod._memory_db_conn is not None:
            try:
                db_mod._memory_db_conn.close()
            except Exception:
                pass
            db_mod._memory_db_conn = None

        from backend.core.database import get_db
        with get_db() as conn:
            findings = [
                {
                    "id": "f1",
                    "employee": "Security Auditor",
                    "severity": "critical",
                    "category": "csp",
                    "title": "Missing CSP",
                    "description": "No CSP header.",
                    "fix_suggestion": "Add CSP.",
                    "evidence_snippet": "strict-transport-security: max-age=31536000",
                },
                {
                    "id": "f2",
                    "employee": "Performance Inspector",
                    "severity": "high",
                    "category": "lcp",
                    "title": "Slow LCP",
                    "description": "LCP is 4s.",
                    "fix_suggestion": "Preload hero image.",
                    "evidence_snippet": "lcp: 4200ms",
                },
            ]
            conn.execute(
                """
                INSERT INTO audit_history
                    (url, title, mode, total_findings, critical_count,
                     high_count, medium_count, low_count, info_count,
                     findings_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "https://seeded.example.com",
                    "Seeded Site",
                    "full",
                    2,
                    1,
                    1,
                    0,
                    0,
                    0,
                    json.dumps(findings),
                ),
            )
            conn.commit()

        from fastapi.testclient import TestClient
        from backend.engine import app
        with TestClient(app) as c:
            yield c

    def test_export_sarif_returns_application_sarif_json(self, client_with_seed):
        resp = client_with_seed.get("/audit/export/sarif", params={"audit_id": 1})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/sarif+json")
        assert "attachment" in resp.headers["content-disposition"]
        assert "jambu-audit-1.sarif" in resp.headers["content-disposition"]
        sarif = resp.json()
        assert sarif["version"] == "2.1.0"
        assert len(sarif["runs"][0]["results"]) == 2

    def test_export_json_returns_application_json(self, client_with_seed):
        resp = client_with_seed.get("/audit/export/json", params={"audit_id": 1})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        body = resp.json()
        assert body["total_findings"] == 2
        assert body["audited_url"] == "https://seeded.example.com"
        assert body["by_severity"]["critical"] == 1

    def test_export_markdown_returns_text_markdown(self, client_with_seed):
        resp = client_with_seed.get("/audit/export/markdown", params={"audit_id": 1})
        assert resp.status_code == 200
        assert "text/markdown" in resp.headers["content-type"]
        # Markdown is sent as text/markdown, not JSON. Use .text.
        body = resp.text
        assert "# Audit Report — https://seeded.example.com" in body
        assert "Missing CSP" in body

    def test_export_unknown_audit_returns_404(self, client_with_seed):
        resp = client_with_seed.get("/audit/export/sarif", params={"audit_id": 99999})
        assert resp.status_code == 404

    def test_html_report_for_saved_audit(self, client_with_seed):
        resp = client_with_seed.get("/audit/report/1")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        body = resp.text
        assert "<!doctype html>" in body
        assert "Jambubrowser Audit Report" in body
        assert "Missing CSP" in body
        assert "https://seeded.example.com" in body

    def test_html_report_unknown_audit_returns_404(self, client_with_seed):
        assert client_with_seed.get("/audit/report/99999").status_code == 404

    def test_html_report_for_share_token(self, client_with_seed):
        share = client_with_seed.post("/audit/history/1/share").json()
        token = share["share_token"]
        assert share["share_url"] == f"/audit/shared/{token}"

        resp = client_with_seed.get(f"/audit/shared/{token}/report")
        assert resp.status_code == 200
        assert "Missing CSP" in resp.text

        assert client_with_seed.get("/audit/shared/unknown-token/report").status_code == 404


# ---------------------------------------------------------------------------
# Live (POST) export endpoints
# ---------------------------------------------------------------------------
class TestLiveExport:
    """The POST endpoints let the AuditPanel export current in-memory
    findings without persisting them first.
    """

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test_live.db")
        monkeypatch.setenv("JAMBU_DB_PATH", db_path)
        from backend.core import database as db_mod
        monkeypatch.setattr(db_mod, "DB_PATH", db_path)
        if db_mod._memory_db_conn is not None:
            try:
                db_mod._memory_db_conn.close()
            except Exception:
                pass
            db_mod._memory_db_conn = None
        from fastapi.testclient import TestClient
        from backend.engine import app
        with TestClient(app) as c:
            yield c

    def test_post_sarif(self, client):
        resp = client.post(
            "/audit/export/sarif",
            json={
                "url": "https://example.com",
                "findings": [
                    {
                        "id": "f1",
                        "employee": "Security Auditor",
                        "severity": "critical",
                        "category": "csp",
                        "title": "Missing CSP",
                        "description": "No CSP header.",
                        "fix_suggestion": "Add CSP.",
                        "evidence_snippet": "...",
                    }
                ],
            },
        )
        assert resp.status_code == 200
        sarif = resp.json()["sarif"]
        assert sarif["version"] == "2.1.0"
        assert len(sarif["runs"][0]["results"]) == 1

    def test_post_json(self, client):
        resp = client.post(
            "/audit/export/json",
            json={
                "url": "https://example.com",
                "findings": [
                    {
                        "id": "f1",
                        "employee": "Performance Inspector",
                        "severity": "high",
                        "category": "lcp",
                        "title": "Slow LCP",
                        "description": "LCP is 4s.",
                        "fix_suggestion": "Preload.",
                        "evidence_snippet": "lcp: 4000ms",
                    }
                ],
                "summary": {"mode": "quick"},
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_findings"] == 1
        assert body["summary"]["mode"] == "quick"

    def test_post_markdown(self, client):
        resp = client.post(
            "/audit/export/markdown",
            json={
                "url": "https://example.com",
                "findings": [
                    {
                        "id": "f1",
                        "employee": "Accessibility Auditor",
                        "severity": "medium",
                        "category": "contrast",
                        "title": "Low contrast",
                        "description": "Contrast 3:1.",
                        "fix_suggestion": "Darker text.",
                        "evidence_snippet": "color: #888 on #fff",
                    }
                ],
            },
        )
        assert resp.status_code == 200
        body = resp.json()["markdown"]
        assert "Low contrast" in body

    def test_post_rejects_unsafe_url(self, client):
        resp = client.post(
            "/audit/export/json",
            json={
                "url": "http://169.254.169.254/latest/meta-data/",  # AWS IMDS
                "findings": [],
            },
        )
        # Pydantic validator rejects → 422
        assert resp.status_code in (400, 422)
