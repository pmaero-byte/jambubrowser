"""Tests: cli/jambu.py — CLI commands, especially `jambu status` and the
CI audit exports (`--sarif`, `--json`, `--markdown`, `--fail-on`)."""
import io
import json
import sys
from unittest.mock import patch


def _run_argv(argv: list, mock_responses: dict) -> str:
    """Run cli.jambu.main() with patched api_request that returns mock_responses."""
    from cli import jambu

    def fake_api_request(method, path, data=None, stream=False):
        return mock_responses.get(path)

    captured = io.StringIO()
    with patch.object(jambu, "api_request", side_effect=fake_api_request), \
         patch.object(sys, "argv", ["jambu"] + argv), \
         patch.object(sys, "stdout", captured):
        try:
            jambu.main()
        except SystemExit:
            pass
    return captured.getvalue()


class TestJambuStatus:
    def test_status_runs_all_five_sections(self):
        output = _run_argv(
            ["status"],
            {
                "/health": {"status": "ok", "ram_used_gb": 2.0, "ram_total_gb": 16.0, "cpu_percent": 12.5, "checks": {"db": "ok"}},
                "/security/verify": {
                    "packages": {
                        "fastapi": {"version": "0.110", "verified": True},
                        "pydantic": {"version": "2.6", "verified": True},
                    },
                    "system_components": {"python": True},
                },
                "/v2/llm/providers": {"providers": [{"name": "mock", "healthy": True}, {"name": "openai", "healthy": False}]},
                "/stats": {"missions": 5, "documents": 42, "credentials": 3},
                "/vault/status": {"locked": True, "credential_count": 0},
            },
        )
        assert "[1] Engine health" in output
        assert "[2] Supply chain" in output
        assert "[3] LLM providers" in output
        assert "[4] Database" in output
        assert "[5] Vault" in output

    def test_status_handles_unreachable_engine(self):
        output = _run_argv(["status"], {})  # all endpoints return None
        assert "Engine unreachable" in output
        assert "Cannot reach supply chain verifier" in output
        assert "Cannot reach LLM registry" in output
        assert "Cannot reach /stats" in output
        assert "Cannot reach /vault/status" in output

    def test_status_shows_verified_package_count(self):
        output = _run_argv(
            ["status"],
            {
                "/health": {"status": "ok"},
                "/security/verify": {
                    "packages": {
                        "fastapi": {"version": "0.110", "verified": True},
                        "pydantic": {"version": "2.6", "verified": False},
                    },
                },
                "/v2/llm/providers": [],
                "/stats": {},
                "/vault/status": {"locked": False, "credential_count": 7},
            },
        )
        assert "1/2 packages verified" in output
        assert "locked=False" in output

    def test_status_truncates_packages_after_five(self):
        packages = {f"pkg{i}": {"version": "1.0", "verified": True} for i in range(20)}
        output = _run_argv(
            ["status"],
            {
                "/health": {"status": "ok"},
                "/security/verify": {"packages": packages},
                "/v2/llm/providers": [],
                "/stats": {},
                "/vault/status": {"locked": True, "credential_count": 0},
            },
        )
        assert "20/20 packages verified" in output
        assert "and 15 more" in output

    def test_status_shows_engine_url(self):
        output = _run_argv(["status"], {})
        assert "Jambubrowser System Status" in output

    def test_help_includes_status_command(self):
        output = _run_argv(["--help"], {})
        assert "status" in output
        assert "Aggregate system health" in output


class TestJambuStatusImports:
    def test_cmd_status_is_callable(self):
        from cli.jambu import cmd_status
        assert callable(cmd_status)

    def test_status_subparser_registered(self):
        from cli.jambu import main
        import argparse
        # Verify the parser structure without invoking api_request
        import io
        import sys as _sys
        with patch.object(_sys, "argv", ["jambu", "status", "--help"]), \
             patch.object(_sys, "stdout", io.StringIO()), \
             patch.object(_sys, "stderr", io.StringIO()):
            try:
                main()
            except SystemExit:
                pass


class TestJambuDiff:
    def test_diff_shows_text_and_source_changes(self):
        output = _run_argv(
            ["diff", "m1"],
            {
                "/mission/m1/results?limit=2": {
                    "mission_id": "m1",
                    "results": [
                        {"id": 2, "run_at": 200.0, "result_text": "new", "success": True},
                        {"id": 1, "run_at": 100.0, "result_text": "old", "success": True},
                    ],
                    "count": 2,
                },
                "/mission/results/compare?result_a=1&result_b=2": {
                    "result_a": {"id": 1, "run_at": 100.0},
                    "result_b": {"id": 2, "run_at": 200.0},
                    "text": {
                        "length_a": 3, "length_b": 3, "length_delta": 0,
                        "words_a": 1, "words_b": 1,
                        "words_added": ["new"], "words_removed": ["old"],
                        "similarity": 0.0, "changed": True,
                    },
                    "sources": {
                        "added": ["https://b.com"],
                        "removed": ["https://a.com"],
                        "kept": [],
                    },
                    "status": {"success_a": True, "success_b": True, "changed": False},
                },
            },
        )
        assert "Mission m1" in output
        assert "diff result 1 → 2" in output
        assert "https://b.com" in output
        assert "https://a.com" in output
        assert "Similarity: 0%" in output

    def test_diff_no_mission_id_shows_usage(self):
        output = _run_argv(["diff"], {})
        assert "Usage: jambu diff" in output

    def test_diff_needs_two_results(self):
        output = _run_argv(
            ["diff", "m1"],
            {
                "/mission/m1/results?limit=2": {
                    "mission_id": "m1", "results": [{"id": 1, "run_at": 1.0, "result_text": "x", "success": True}], "count": 1,
                },
            },
        )
        assert "1 result" in output
        assert "at least 2" in output

    def test_diff_no_source_changes_message(self):
        output = _run_argv(
            ["diff", "m1"],
            {
                "/mission/m1/results?limit=2": {
                    "mission_id": "m1",
                    "results": [
                        {"id": 2, "run_at": 200.0, "result_text": "x", "success": True},
                        {"id": 1, "run_at": 100.0, "result_text": "x", "success": True},
                    ],
                    "count": 2,
                },
                "/mission/results/compare?result_a=1&result_b=2": {
                    "text": {"length_a": 1, "length_b": 1, "length_delta": 0, "words_a": 1, "words_b": 1, "words_added": [], "words_removed": [], "similarity": 1.0, "changed": False},
                    "sources": {"added": [], "removed": [], "kept": ["https://same.com"]},
                    "status": {"success_a": True, "success_b": True, "changed": False},
                },
            },
        )
        assert "no source changes" in output
        assert "Sources kept: 1" in output

    def test_help_includes_diff_command(self):
        output = _run_argv(["--help"], {})
        assert "diff" in output
        assert "diff between the two most recent" in output


# ---------------------------------------------------------------------------
# Audit exports + CI gate (--sarif / --json / --markdown / --fail-on)
# ---------------------------------------------------------------------------

_CRITICAL_FINDING = {
    "id": "f1",
    "employee": "Security Auditor",
    "severity": "critical",
    "category": "csp",
    "title": "Missing CSP header",
    "description": "No Content-Security-Policy header is set.",
    "fix_suggestion": "Add a strict CSP header.",
    "evidence_snippet": "strict-transport-security: max-age=31536000",
}


class _FakeSSEResponse:
    """Minimal streaming response for api_request(..., stream=True)."""

    def __init__(self, payload: bytes):
        self._buf = io.BytesIO(payload)

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)


def _sse(events: list[tuple[str, dict]]) -> bytes:
    out = []
    for event, data in events:
        out.append(f"event: {event}\ndata: {json.dumps(data)}\n\n")
    return "".join(out).encode()


def _done_event(**overrides) -> tuple[str, dict]:
    data = {
        "total_findings": 1,
        "dismissed_count": 0,
        "dismissed": [],
        "by_severity": {"critical": 1, "high": 0, "medium": 0, "low": 0, "info": 0},
        "url": "https://example.com",
        "mode": "quick",
        "findings": [dict(_CRITICAL_FINDING)],
    }
    data.update(overrides)
    return ("done", data)


def _employee_done() -> tuple[str, dict]:
    return ("employee_done", {
        "employee": "Security Auditor",
        "emoji": "🛡️",
        "findings_count": 1,
        "elapsed_ms": 5,
        "findings": [dict(_CRITICAL_FINDING)],
    })


def _run_audit(argv: list, response) -> tuple[str, int]:
    """Run `jambu <argv>` with api_request patched; returns (stdout, code)."""
    from cli import jambu

    def fake_api_request(method, path, data=None, stream=False):
        return response

    captured = io.StringIO()
    code = 0
    with patch.object(jambu, "api_request", side_effect=fake_api_request), \
         patch.object(sys, "argv", ["jambu"] + argv), \
         patch.object(sys, "stdout", captured):
        try:
            code = jambu.main()
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 0
    return captured.getvalue(), code


class TestJambuAuditExports:
    def test_sarif_export_writes_valid_sarif(self, tmp_path):
        out = tmp_path / "audit.sarif"
        response = _FakeSSEResponse(_sse([_employee_done(), _done_event()]))
        text, code = _run_audit(
            ["quick", "https://example.com", "--sarif", str(out)], response
        )
        assert code == 0
        assert out.exists()
        sarif = json.loads(out.read_text())
        assert sarif["version"] == "2.1.0"
        assert len(sarif["runs"][0]["results"]) == 1
        assert sarif["runs"][0]["results"][0]["ruleId"] == "jambu/security-auditor/csp"
        assert "SARIF written" in text

    def test_json_export_has_summary_and_content_hash(self, tmp_path):
        out = tmp_path / "audit.json"
        response = _FakeSSEResponse(_sse([_employee_done(), _done_event()]))
        _, code = _run_audit(
            ["quick", "https://example.com", "--json", str(out)], response
        )
        assert code == 0
        body = json.loads(out.read_text())
        assert body["audited_url"] == "https://example.com"
        assert body["total_findings"] == 1
        assert body["by_severity"]["critical"] == 1
        assert body["summary"]["mode"] == "quick"
        assert body["summary"]["dismissed_count"] == 0
        assert body["findings"][0]["content_hash"]

    def test_markdown_export(self, tmp_path):
        out = tmp_path / "report.md"
        response = _FakeSSEResponse(_sse([_employee_done(), _done_event()]))
        _, code = _run_audit(
            ["quick", "https://example.com", "--markdown", str(out)], response
        )
        assert code == 0
        md = out.read_text()
        assert "# Audit Report — https://example.com" in md
        assert "Missing CSP header" in md

    def test_stdout_export_uses_dash(self):
        response = _FakeSSEResponse(_sse([_employee_done(), _done_event()]))
        text, code = _run_audit(
            ["quick", "https://example.com", "--json", "-"], response
        )
        assert code == 0
        assert '"audited_url": "https://example.com"' in text

    def test_fail_on_gate_fails_on_critical(self, tmp_path):
        response = _FakeSSEResponse(_sse([_employee_done(), _done_event()]))
        text, code = _run_audit(
            ["quick", "https://example.com",
             "--sarif", str(tmp_path / "a.sarif"), "--fail-on", "high"],
            response,
        )
        assert code == 1
        assert "Gate failed" in text
        # The SARIF file is still written before the gate fires.
        assert (tmp_path / "a.sarif").exists()

    def test_fail_on_gate_passes_when_below_threshold(self):
        done = _done_event(
            by_severity={"critical": 0, "high": 0, "medium": 0, "low": 1, "info": 0},
        )
        response = _FakeSSEResponse(_sse([done]))
        text, code = _run_audit(
            ["quick", "https://example.com", "--fail-on", "high"], response
        )
        assert code == 0
        assert "Gate passed" in text

    def test_fail_on_none_never_fails(self):
        response = _FakeSSEResponse(_sse([_employee_done(), _done_event()]))
        _, code = _run_audit(["quick", "https://example.com"], response)
        assert code == 0

    def test_engine_unreachable_exits_2(self):
        text, code = _run_audit(["quick", "https://example.com"], None)
        assert code == 2
        assert "Error" not in text  # api_request (patched) prints nothing

    def test_audit_error_event_exits_2(self):
        response = _FakeSSEResponse(_sse([
            ("error", {"phase": "collect", "error": "navigation timeout"}),
        ]))
        text, code = _run_audit(["quick", "https://example.com"], response)
        assert code == 2
        assert "navigation timeout" in text

    def test_dismissed_findings_excluded_from_export(self, tmp_path):
        """Server-side dismissals survive into CI: the `done` event's
        findings (post-dismissal) are the export source."""
        done = _done_event(
            total_findings=0,
            dismissed_count=1,
            findings=[],
            by_severity={"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0},
        )
        out = tmp_path / "audit.sarif"
        response = _FakeSSEResponse(_sse([_employee_done(), done]))
        text, code = _run_audit(
            ["quick", "https://example.com",
             "--sarif", str(out), "--fail-on", "high"],
            response,
        )
        assert code == 0
        assert "1 dismissed" in text
        sarif = json.loads(out.read_text())
        assert sarif["runs"][0]["results"] == []

    def test_help_lists_ci_flags(self):
        output = _run_argv(["quick", "--help"], {})
        assert "--sarif" in output
        assert "--fail-on" in output
        assert "--markdown" in output
        assert "--json" in output


# ---------------------------------------------------------------------------
# Audit monitors (jambu monitor ...)
# ---------------------------------------------------------------------------

class TestJambuMonitor:
    def test_usage_without_subcommand(self):
        output = _run_argv(["monitor"], {})
        assert "jambu monitor" in output
        assert "add" in output

    def test_add_shows_monitor_and_baseline(self):
        output = _run_argv(
            ["monitor", "add", "https://example.com",
             "--interval", "60", "--fail-on", "high", "--run-now"],
            {
                "/audit/monitors": {
                    "monitor": {
                        "id": 3, "url": "https://example.com", "mode": "quick",
                        "interval_minutes": 60, "fail_on": "high",
                        "webhook_url": None,
                    },
                    "initial_run": {
                        "status": "ok", "baseline": True, "total_findings": 2,
                        "new_findings": 2, "resolved_findings": 0,
                        "alerted": False, "alert_findings": [],
                    },
                },
            },
        )
        assert "Monitor #3 created" in output
        assert "every 60 min" in output
        assert "Baseline run" in output
        assert "2 findings" in output

    def test_list_shows_monitors_and_state(self):
        output = _run_argv(
            ["monitor", "list"],
            {
                "/audit/monitors": {
                    "monitors": [
                        {
                            "id": 1, "url": "https://a.com", "mode": "quick",
                            "interval_minutes": 60, "fail_on": "high",
                            "enabled": True, "last_run_at": 1700000000,
                            "last_finding_count": 4,
                        },
                        {
                            "id": 2, "url": "https://b.com", "mode": "full",
                            "interval_minutes": 1440, "fail_on": "critical",
                            "enabled": False, "last_run_at": None,
                            "last_finding_count": None,
                        },
                    ],
                    "count": 2,
                },
            },
        )
        assert "https://a.com" in output
        assert "https://b.com" in output
        assert " off " in output  # disabled monitor
        assert "never" in output  # never-run monitor

    def test_list_empty(self):
        output = _run_argv(["monitor", "list"], {"/audit/monitors": {"monitors": [], "count": 0}})
        assert "No monitors yet" in output

    def test_rm(self):
        output = _run_argv(
            ["monitor", "rm", "5"],
            {"/audit/monitors/5": {"status": "deleted", "monitor_id": 5}},
        )
        assert "Monitor #5 deleted" in output

    def test_run_reports_alert_and_findings(self):
        output = _run_argv(
            ["monitor", "run", "7"],
            {
                "/audit/monitors/7/run": {
                    "status": "ok", "monitor_id": 7, "url": "https://x.com",
                    "baseline": False, "total_findings": 3, "new_findings": 1,
                    "resolved_findings": 0, "alerted": True,
                    "alert_findings": [{"severity": "critical", "title": "New XSS"}],
                    "duration_ms": 1234,
                },
            },
        )
        assert "ALERTED" in output
        assert "New XSS" in output

    def test_runs_history(self):
        output = _run_argv(
            ["monitor", "runs", "7"],
            {
                "/audit/monitors/7/runs?limit=10": {
                    "monitor_id": 7,
                    "runs": [
                        {
                            "run_at": 1700000000, "status": "ok",
                            "total_findings": 3, "new_findings": 1,
                            "resolved_findings": 2, "baseline": False,
                            "alerted": True,
                        },
                    ],
                    "count": 1,
                },
            },
        )
        assert "run(s)" in output
        assert "+1 new" in output
        assert "-2 resolved" in output

    def test_runs_empty(self):
        output = _run_argv(
            ["monitor", "runs", "7"],
            {"/audit/monitors/7/runs?limit=10": {"monitor_id": 7, "runs": [], "count": 0}},
        )
        assert "no runs yet" in output

    def test_help_lists_monitor_command(self):
        output = _run_argv(["--help"], {})
        assert "monitor" in output
        assert "Recurring audit monitors" in output


# ---------------------------------------------------------------------------
# jambu dcm (DecentraCode Mesh)
# ---------------------------------------------------------------------------

class TestJambuDcm:
    def _run_dcm(self, argv, responses):
        """Run `jambu dcm ...` with _dcm_request patched to a response map."""
        from cli import jambu

        def fake_dcm(method, path, data=None, timeout=30.0):
            return responses.get((method, path), (0, None))

        captured = io.StringIO()
        with patch.object(jambu, "_dcm_request", side_effect=fake_dcm), \
             patch.object(sys, "argv", ["jambu"] + argv), \
             patch.object(sys, "stdout", captured):
            try:
                code = jambu.main()
            except SystemExit as e:
                code = e.code
        return captured.getvalue(), code

    def test_usage_without_subcommand(self):
        output, _ = self._run_dcm(["dcm"], {})
        assert "jambu dcm" in output

    def test_status_shows_node_mesh_and_models(self):
        output, code = self._run_dcm(["dcm", "status"], {
            ("GET", "/health"): (200, {"status": "online"}),
            ("GET", "/api/inference/status"): (200, {"engine_ready": True, "mode": "moe"}),
            ("GET", "/api/models"): (200, {"models": [
                {"id": "qwen1.5-moe-a2.7b", "available": True, "status": "ready"},
                {"id": "glm-5.2", "status": "planned"},
            ]}),
            ("GET", "/api/network/status"): (200, {"nodeId": "node-123456", "peers": []}),
        })
        assert code in (0, None)
        assert "DecentraCode node" in output
        assert "ready" in output
        assert "1 available" in output
        assert "qwen1.5-moe-a2.7b" in output
        assert "0 peer(s)" in output

    def test_status_ready_moe_sidecar_when_default_runtime_down(self):
        """Live-verified shape: candle 503/not-ready, MoE sidecar ready."""
        output, code = self._run_dcm(["dcm", "status"], {
            ("GET", "/health"): (200, {"status": "online"}),
            ("GET", "/api/inference/status"): (200, {
                "runtime": "candle-dense", "ready": False,
                "error": "Binary not found: dcm-infer-candle",
                "moe": {"runtime": "python-moe", "ready": True, "available": True},
            }),
            ("GET", "/api/models"): (200, {"models": []}),
            ("GET", "/api/network/status"): (200, {"nodeId": "n1", "peers": []}),
        })
        assert code in (0, None)
        assert "python-moe ready" in output
        assert "dcm-infer-candle" in output

    def test_status_unreachable_exits_2(self):
        output, code = self._run_dcm(["dcm", "status"], {})
        assert code == 2

    def test_infer_prints_completion(self):
        output, code = self._run_dcm(["dcm", "infer", "hello", "--max-tokens", "8"], {
            ("GET", "/health"): (200, {"status": "online"}),
            ("POST", "/api/inference/v1/chat/completions"): (200, {
                "model": "qwen1.5-moe-a2.7b",
                "choices": [{"message": {"role": "assistant", "content": "Hello from the mesh"}}],
                "usage": {"completion_tokens": 4, "total_ms": 2500},
            }),
        })
        assert code in (0, None)
        assert "Hello from the mesh" in output
        assert "4 completion tokens" in output

    def test_infer_runtime_missing_is_actionable(self):
        output, code = self._run_dcm(["dcm", "infer", "hi"], {
            ("POST", "/api/inference/v1/chat/completions"): (
                501, {"error": "runtime missing", "code": "RUNTIME_NOT_IMPLEMENTED"},
            ),
        })
        assert code == 2
        assert "RUNTIME_NOT_IMPLEMENTED" in output

    def test_screenshot_writes_png(self, tmp_path):
        from cli import jambu

        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        out = tmp_path / "shot.png"
        captured = io.StringIO()
        argv = ["monitor", "screenshot", "7", "42", "--out", str(out)]
        with patch.object(jambu, "api_request_bytes", return_value=png), \
             patch.object(sys, "argv", ["jambu"] + argv), \
             patch.object(sys, "stdout", captured):
            try:
                code = jambu.main()
            except SystemExit as e:
                code = e.code
        assert code in (0, None)
        assert out.read_bytes() == png
        assert "Screenshot written to" in captured.getvalue()

    def test_screenshot_engine_error(self):
        from cli import jambu

        captured = io.StringIO()
        argv = ["monitor", "screenshot", "7", "42"]
        with patch.object(jambu, "api_request_bytes", return_value=None), \
             patch.object(sys, "argv", ["jambu"] + argv), \
             patch.object(sys, "stdout", captured):
            try:
                code = jambu.main()
            except SystemExit as e:
                code = e.code
        assert code == 2

    def test_diff_writes_heatmap_png(self, tmp_path):
        from cli import jambu

        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        out = tmp_path / "diff.png"
        captured = io.StringIO()
        argv = ["monitor", "diff", "7", "42", "--out", str(out)]
        with patch.object(jambu, "api_request_bytes", return_value=png) as dl, \
             patch.object(sys, "argv", ["jambu"] + argv), \
             patch.object(sys, "stdout", captured):
            try:
                code = jambu.main()
            except SystemExit as e:
                code = e.code
        assert code in (0, None)
        assert dl.call_args[0][0] == "/audit/monitors/7/runs/42/diff"
        assert out.read_bytes() == png
        assert "Diff image written to" in captured.getvalue()


# ---------------------------------------------------------------------------
# jambu report / share
# ---------------------------------------------------------------------------

class TestJambuReport:
    def _run_report(self, argv: list, html: str | None) -> tuple[str, int]:
        from cli import jambu

        captured = io.StringIO()
        code = 0
        with patch.object(jambu, "api_request_text", return_value=html), \
             patch.object(sys, "argv", ["jambu"] + argv), \
             patch.object(sys, "stdout", captured):
            try:
                code = jambu.main()
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 0
        return captured.getvalue(), code

    def test_report_writes_file(self, tmp_path):
        out = tmp_path / "report.html"
        text, code = self._run_report(
            ["report", "7", "--out", str(out)],
            "<!doctype html><html><body>Jambubrowser Audit Report</body></html>",
        )
        assert code == 0
        assert out.exists()
        assert "Jambubrowser Audit Report" in out.read_text()
        assert "Report written" in text

    def test_report_stdout(self):
        text, code = self._run_report(["report", "7", "--out", "-"], "<html>hi</html>")
        assert code == 0
        assert "<html>hi</html>" in text

    def test_report_engine_error_exits_2(self):
        text, code = self._run_report(["report", "7"], None)
        assert code == 2

    def test_share_prints_json_and_report_urls(self):
        output = _run_argv(
            ["share", "3"],
            {"/audit/history/3/share": {"share_token": "tok", "share_url": "/audit/shared/tok"}},
        )
        assert "/audit/shared/tok" in output
        assert "/audit/shared/tok/report" in output


class TestJambuBrowserCommands:
    @staticmethod
    def _run(argv: list, response) -> tuple[str, int]:
        from cli import jambu

        def fake_api_request(method, path, data=None, stream=False):
            return response

        captured = io.StringIO()
        code = 0
        with patch.object(jambu, "api_request", side_effect=fake_api_request), \
             patch.object(sys, "argv", ["jambu"] + argv), \
             patch.object(sys, "stdout", captured):
            try:
                code = jambu.main()
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 0
        return captured.getvalue(), code

    def test_test_command_passes(self):
        response = {"ok": True, "passed": 2, "total": 2, "duration_ms": 10,
                    "steps": [{"i": 1, "action": "navigate", "status": "passed"}],
                    "title": "App", "final_url": "http://localhost:3000/"}
        text, code = self._run(["test", "--url", "http://localhost:3000", "--local"], response)
        assert code == 0
        assert "PASS" in text

    def test_test_command_fails_gate(self):
        response = {"ok": False, "passed": 1, "total": 2, "duration_ms": 10,
                    "steps": [{"i": 2, "action": "click", "status": "failed",
                               "reason": "assertion_failed", "error": "nope"}],
                    "final_url": "http://localhost:3000/"}
        text, code = self._run(["test", "--url", "http://localhost:3000"], response)
        assert code == 1
        assert "FAIL" in text

    def test_test_reads_flow_file(self, tmp_path):
        flow = tmp_path / "flow.json"
        flow.write_text(json.dumps({
            "url": "http://localhost:3000",
            "steps": [{"action": "navigate", "url": "http://localhost:3000"}],
        }))
        response = {"ok": True, "passed": 1, "total": 1, "steps": [], "final_url": "x"}
        text, code = self._run(["test", str(flow), "--local"], response)
        assert code == 0

    def test_export_command_writes_spec(self, tmp_path):
        flow = tmp_path / "flow.json"
        flow.write_text(json.dumps({
            "steps": [{"action": "navigate", "url": "http://x"}],
        }))
        out = tmp_path / "out.spec.ts"
        response = {"code": "import { test } from '@playwright/test';\n"}
        text, code = self._run(["export", str(flow), "--out", str(out)], response)
        assert code == 0
        assert "Wrote" in text
        assert "playwright/test" in out.read_text()

    def test_plan_command(self):
        from cli import jambu

        response = {"kind": "login", "source": "template",
                    "steps": [{"action": "navigate", "url": "http://x"}],
                    "placeholders": ["email"]}
        text, code = self._run(["plan", "test", "login", "--url", "http://x"], response)
        assert code == 0
        assert "login" in text

