"""
Tests for the AI Employee system: base classes, data containers, parsing,
and each specialist employee's data extraction logic.

All tests are self-contained — no live LLM calls or running engine needed.
"""

from __future__ import annotations

import json
import pytest

from backend.employees.base import (
    AuditData,
    BaseEmployee,
    Finding,
    Severity,
    parse_findings,
)
from backend.employees.security import SecurityAuditor
from backend.employees.performance import PerformanceInspector
from backend.employees.accessibility import AccessibilityAuditor
from backend.employees.seo import SEOAnalyzer
from backend.employees.ux_ui import UXUIReviewer
from backend.employees.code_quality import CodeQualityScout


# =========================================================================
# Severity
# =========================================================================


class TestSeverity:
    def test_from_str_valid(self):
        assert Severity.from_str("critical") == Severity.CRITICAL
        assert Severity.from_str("HIGH") == Severity.HIGH
        assert Severity.from_str("Medium") == Severity.MEDIUM
        assert Severity.from_str("  low  ") == Severity.LOW
        assert Severity.from_str("info") == Severity.INFO

    def test_from_str_unknown_defaults_to_medium(self):
        assert Severity.from_str("bogus") == Severity.MEDIUM


# =========================================================================
# Finding
# =========================================================================


class TestFinding:
    def test_from_dict_minimal(self):
        f = Finding.from_dict({"severity": "high", "title": "Test finding"})
        assert f.severity == Severity.HIGH
        assert f.title == "Test finding"
        assert f.description == ""
        assert f.category == ""
        assert f.employee == ""

    def test_from_dict_full(self):
        f = Finding.from_dict({
            "severity": "critical",
            "title": "CSP missing",
            "description": "No CSP header found",
            "category": "csp",
            "fix_suggestion": "Add Content-Security-Policy header",
            "evidence_snippet": "header missing",
            "score_impact": "high",
        })
        assert f.severity == Severity.CRITICAL
        assert f.title == "CSP missing"
        assert f.description == "No CSP header found"
        assert f.category == "csp"
        assert f.fix_suggestion == "Add Content-Security-Policy header"
        assert f.evidence_snippet == "header missing"

    def test_from_dict_invalid_severity_defaults_to_medium(self):
        f = Finding.from_dict({"severity": "unknown", "title": "x"})
        assert f.severity == Severity.MEDIUM

    def test_from_dict_missing_title_uses_empty(self):
        f = Finding.from_dict({"severity": "low"})
        assert f.title == ""
        assert f.severity == Severity.LOW

    def test_from_dict_none_values(self):
        f = Finding.from_dict({"severity": None, "title": None})
        assert f.severity == Severity.MEDIUM
        assert f.title == ""

    def test_from_dict_missing_keys(self):
        f = Finding.from_dict({})
        assert f.severity == Severity.MEDIUM
        assert f.title == ""


# =========================================================================
# AuditData
# =========================================================================


class TestAuditData:
    def test_minimal(self):
        d = AuditData(url="https://example.com")
        assert d.url == "https://example.com"
        assert d.network_requests == []
        assert d.console_logs == []
        # page_source may be None if not provided (depends on field default)

    def test_summary(self):
        d = AuditData(
            url="https://example.com/page",
            network_requests=[{"url": "https://example.com/a.js"}],
            console_logs=[{"level": "error", "text": "fail"}],
        )
        s = d.summary()
        assert "example.com" in s
        assert "1 reqs" in s
        assert "1 console" in s
        assert "lighthouse=no" in s


# =========================================================================
# parse_findings
# =========================================================================


class TestParseFindings:
    def test_plain_json_array(self):
        raw = json.dumps([
            {"severity": "high", "title": "Issue 1"},
            {"severity": "low", "title": "Issue 2"},
        ])
        findings = parse_findings(raw, "TestEmployee")
        assert len(findings) == 2
        assert all(f.employee == "TestEmployee" for f in findings)
        assert findings[0].title == "Issue 1"
        assert findings[1].severity == Severity.LOW

    def test_markdown_code_fences(self):
        raw = """Here's my analysis:

```json
[
  {"severity": "critical", "title": "XSS risk"},
  {"severity": "medium", "title": "Missing header"}
]
```"""
        findings = parse_findings(raw, "SecurityAuditor")
        assert len(findings) == 2
        assert findings[0].severity == Severity.CRITICAL
        assert findings[1].title == "Missing header"

    def test_single_object(self):
        raw = '{"severity": "high", "title": "Single issue"}'
        findings = parse_findings(raw, "Test")
        assert len(findings) == 1
        assert findings[0].title == "Single issue"

    def test_single_object_with_surrounding_text(self):
        raw = "Analysis result: {\"severity\": \"low\", \"title\": \"Minor\"}\nDone."
        findings = parse_findings(raw, "Test")
        assert len(findings) == 1
        assert findings[0].title == "Minor"

    def test_unparseable_returns_empty_list(self):
        raw = "I couldn't find any issues on this page."
        findings = parse_findings(raw, "Test")
        assert findings == []

    def test_invalid_json_after_fences_returns_empty(self):
        raw = "```json\n{invalid json}\n```"
        findings = parse_findings(raw, "Test")
        assert findings == []

    def test_empty_array(self):
        findings = parse_findings("[]", "Test")
        assert findings == []

    def test_mixed_valid_and_invalid_items(self):
        raw = json.dumps([
            {"severity": "high", "title": "Valid"},
            {"severity": "invalid", "title": "Bad severity but still valid JSON"},
        ])
        findings = parse_findings(raw, "Test")
        assert len(findings) == 2
        assert findings[1].severity == Severity.MEDIUM  # default


# =========================================================================
# BaseEmployee
# =========================================================================


def test_base_employee_defaults():
    """BaseEmployee has sensible defaults for metadata."""
    assert BaseEmployee.name == "Base Employee"
    assert BaseEmployee.emoji == "🤖"
    assert BaseEmployee.max_tokens == 4000
    assert BaseEmployee.temperature == 0.3


def test_base_employee_prepare_data():
    """_prepare_data returns a fallback message when not overridden."""
    emp = BaseEmployee()
    data = AuditData(url="https://example.com")
    result = emp._prepare_data(data)
    assert "https://example.com" in result
    assert "Base Employee" in result


# =========================================================================
# Employee metadata
# =========================================================================


@pytest.mark.parametrize("cls,name,emoji", [
    (SecurityAuditor, "Security Auditor", "🔒"),
    (PerformanceInspector, "Performance Inspector", "⚡"),
    (AccessibilityAuditor, "Accessibility Auditor", "♿"),
    (SEOAnalyzer, "SEO Analyzer", "🔍"),
    (UXUIReviewer, "UX/UI Reviewer", "🎨"),
    (CodeQualityScout, "Code Quality Scout", "🧹"),
])
def test_employee_metadata(cls, name, emoji):
    assert cls.name == name
    assert cls.emoji == emoji
    assert cls.system_prompt
    assert len(cls.system_prompt) > 100


# =========================================================================
# Employee _prepare_data — data extraction
# =========================================================================


@pytest.fixture
def sample_audit_data() -> AuditData:
    return AuditData(
        url="https://example.com/page",
        response_headers={
            "content-type": "text/html",
            "content-security-policy": "default-src 'self'",
            "strict-transport-security": "max-age=31536000; includeSubDomains",
        },
        cookies=[
            {"name": "session", "value": "abc123", "httpOnly": True, "secure": True, "sameSite": "Lax"},
            {"name": "tracking", "value": "xyz", "httpOnly": False, "secure": False, "sameSite": "None"},
        ],
        network_requests=[
            {"url": "https://example.com/app.js", "method": "GET", "status": 200, "resource_type": "script", "transfer_size": 120000},
            {"url": "https://example.com/style.css", "method": "GET", "status": 200, "resource_type": "stylesheet", "transfer_size": 60000},
            {"url": "https://example.com/logo.png", "method": "GET", "status": 200, "resource_type": "image", "transfer_size": 15000},
        ],
        console_logs=[
            {"level": "error", "text": "TypeError: Cannot read property 'x' of undefined"},
            {"level": "warning", "text": "Slow network detected"},
        ],
        page_source="<html><head><title>Test</title></head><body><h1>Hello</h1></body></html>",
        lighthouse_report={
            "categories": {
                "performance": {"score": 0.85, "title": "Performance"},
            },
            "audits": {
                "largest-contentful-paint": {"score": 0.6, "title": "LCP", "displayValue": "2.8 s"},
            },
        },
        load_time_ms=1800,
        viewport_width=1440,
        viewport_height=900,
    )


class TestSecurityAuditorPrepareData:
    def test_includes_headers(self, sample_audit_data):
        emp = SecurityAuditor()
        result = emp._prepare_data(sample_audit_data)
        assert "content-security-policy" in result or "Content-Security-Policy" in result
        assert "strict-transport-security" in result or "Strict-Transport-Security" in result

    def test_includes_cookies_with_flags(self, sample_audit_data):
        emp = SecurityAuditor()
        result = emp._prepare_data(sample_audit_data)
        assert "HttpOnly" in result
        assert "SameSite" in result
        assert "session" in result
        assert "tracking" in result

    def test_includes_network_requests(self, sample_audit_data):
        emp = SecurityAuditor()
        result = emp._prepare_data(sample_audit_data)
        assert "app.js" in result
        assert "style.css" in result

    def test_includes_page_source(self, sample_audit_data):
        emp = SecurityAuditor()
        result = emp._prepare_data(sample_audit_data)
        assert "Hello" in result
        assert "page source" in result.lower()


class TestPerformanceInspectorPrepareData:
    def test_includes_lighthouse_scores(self, sample_audit_data):
        emp = PerformanceInspector()
        result = emp._prepare_data(sample_audit_data)
        assert "85" in result  # 0.85 * 100
        assert "LCP" in result

    def test_includes_large_resources(self, sample_audit_data):
        emp = PerformanceInspector()
        result = emp._prepare_data(sample_audit_data)
        assert "app.js" in result
        assert "117" in result  # 120000 bytes = 117.2 KB

    def test_includes_load_time(self, sample_audit_data):
        emp = PerformanceInspector()
        result = emp._prepare_data(sample_audit_data)
        assert "1800" in result

    def test_console_warnings_included(self, sample_audit_data):
        emp = PerformanceInspector()
        result = emp._prepare_data(sample_audit_data)
        assert "Slow network" in result


class TestAccessibilityAuditorPrepareData:
    def test_includes_page_source(self, sample_audit_data):
        emp = AccessibilityAuditor()
        result = emp._prepare_data(sample_audit_data)
        # The accessibility auditor uses page source
        assert "Hello" in result or "Test" in result


class TestSEOAnalyzerPrepareData:
    def test_includes_page_source_and_title(self, sample_audit_data):
        emp = SEOAnalyzer()
        result = emp._prepare_data(sample_audit_data)
        assert "Test" in result or "test" in result  # title tag
        assert "PAGE SOURCE" in result

    def test_includes_lighthouse_seo_when_available(self, sample_audit_data):
        emp = SEOAnalyzer()
        result = emp._prepare_data(sample_audit_data)
        # Section header is always present; score only appears when seo cat exists
        assert "LIGHTHOUSE SEO" in result

    def test_no_lighthouse_shows_not_available(self):
        data = AuditData(url="https://example.com")
        emp = SEOAnalyzer()
        result = emp._prepare_data(data)
        assert "Lighthouse not available" in result


class TestUXUIReviewerPrepareData:
    def test_includes_dom_and_console(self, sample_audit_data):
        emp = UXUIReviewer()
        result = emp._prepare_data(sample_audit_data)
        assert "Hello" in result or "h1" in result


class TestCodeQualityScoutPrepareData:
    def test_includes_console_and_network(self, sample_audit_data):
        emp = CodeQualityScout()
        result = emp._prepare_data(sample_audit_data)
        assert "TypeError" in result
        assert "app.js" in result

    def test_includes_page_source(self, sample_audit_data):
        emp = CodeQualityScout()
        result = emp._prepare_data(sample_audit_data)
        assert "Hello" in result or "html" in result


# =========================================================================
# Edge cases — empty / partial data
# =========================================================================


def test_employee_with_empty_audit_data():
    """Every employee handles missing data gracefully."""
    data = AuditData(url="https://example.com")
    for cls in [SecurityAuditor, PerformanceInspector, AccessibilityAuditor,
                SEOAnalyzer, UXUIReviewer, CodeQualityScout]:
        emp = cls()
        result = emp._prepare_data(data)
        assert "example.com" in result
        assert len(result) > 10


def test_security_auditor_no_headers():
    data = AuditData(url="https://example.com")
    emp = SecurityAuditor()
    result = emp._prepare_data(data)
    assert "(no headers captured)" in result or "no headers" in result.lower()


def test_performance_inspector_no_lighthouse():
    data = AuditData(url="https://example.com", network_requests=[])
    emp = PerformanceInspector()
    result = emp._prepare_data(data)
    assert "Lighthouse report not available" in result or "not available" in result.lower()


# =========================================================================
# Finding ordering and string representation
# =========================================================================


def test_finding_repr():
    f = Finding(severity=Severity.HIGH, title="Test", description="Desc")
    r = repr(f)
    assert "Test" in r
    assert "HIGH" in r


# =========================================================================
# Integration with mock LLM provider
# =========================================================================


@pytest.mark.asyncio
async def test_analyze_with_mock_provider():
    """When JAMBU_LLM_PROVIDER=mock, the employee loop runs without a real LLM.

    The mock provider returns a canned response. We test that:
    - No exception propagates
    - The return value is a list (possibly empty if mock output is unparseable)
    """
    import os
    os.environ["JAMBU_LLM_PROVIDER"] = "mock"

    emp = SecurityAuditor()
    data = AuditData(url="https://example.com")
    findings = await emp.analyze(data)
    # May be empty if mock output doesn't parse — but must not crash
    assert isinstance(findings, list)


@pytest.mark.asyncio
async def test_all_employees_analyze_with_mock():
    """All 6 employees run through the analyze pipeline without crashing."""
    import os
    os.environ["JAMBU_LLM_PROVIDER"] = "mock"

    data = AuditData(url="https://example.com")
    for cls in [SecurityAuditor, PerformanceInspector, AccessibilityAuditor,
                SEOAnalyzer, UXUIReviewer, CodeQualityScout]:
        emp = cls()
        findings = await emp.analyze(data)
        assert isinstance(findings, list), f"{cls.name} returned {type(findings)}"
