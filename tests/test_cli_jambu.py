"""Tests: cli/jambu.py — CLI commands, especially the new `jambu status`."""
import io
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
                "/mission/m1/results": {
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
                "/mission/m1/results": {
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
                "/mission/m1/results": {
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
