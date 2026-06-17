"""
Tests for the eval CLI (`python -m backend.eval run --suite smoke`).

These exercise the wiring between the registered tasks, the eval harness, the
report generator, and the CLI. They run under mock so no real LLM is required.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Test env (same as conftest, but explicit for subprocess runs)
TEST_ENV = {
    **os.environ,
    "JAMBU_DB_PATH": ":memory:",
    "JAMBU_VAULT_KEY": "test-key-do-not-use-in-production-32bytes!",
    "JAMBU_LLM_PROVIDER": "mock",
    "PYTHONPATH": str(REPO_ROOT),
}


def test_eval_list_command_succeeds():
    """`python -m backend.eval list` must list at least the smoke suite."""
    result = subprocess.run(
        [sys.executable, "-m", "backend.eval", "list"],
        cwd=REPO_ROOT,
        env=TEST_ENV,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "smoke" in result.stdout, f"smoke suite not in output: {result.stdout}"


def test_eval_run_smoke_produces_json_report():
    """`python -m backend.eval run --suite smoke --format json` must emit valid JSON."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        out_path = f.name
    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "backend.eval", "run",
                "--suite", "smoke",
                "--provider", "mock",
                "--format", "json",
                "--out", out_path,
            ],
            cwd=REPO_ROOT,
            env=TEST_ENV,
            capture_output=True,
            text=True,
            timeout=60,
        )
        # The CLI may exit non-zero if pass rate is below threshold; that's
        # expected for the mock — the report file is what we care about.
        assert Path(out_path).exists(), f"report not written; stderr: {result.stderr}"
        data = json.loads(Path(out_path).read_text())
        # Shape assertions
        assert "run_id" in data, f"missing run_id: {data}"
        assert "suite" in data and data["suite"] == "smoke", f"wrong suite: {data}"
        assert "passed" in data and "total" in data
        assert isinstance(data["passed"], int)
        assert isinstance(data["total"], int)
        assert "results" in data and isinstance(data["results"], list)
        # Each result has a status
        for r in data["results"]:
            assert "task_id" in r
            assert "status" in r
    finally:
        Path(out_path).unlink(missing_ok=True)


def test_eval_run_unknown_suite_errors_gracefully():
    """An unknown suite must return a non-zero exit code, not crash."""
    result = subprocess.run(
        [sys.executable, "-m", "backend.eval", "run", "--suite", "nonexistent-suite-xyz"],
        cwd=REPO_ROOT,
        env=TEST_ENV,
        capture_output=True,
        text=True,
        timeout=30,
    )
    # Should fail cleanly (KeyError or "no tasks" message), not a stack trace dump
    assert result.returncode != 0
    # Either stderr has a sensible message or stdout is empty; either way
    # the error must not be a Python traceback (we want graceful errors).
    assert "Traceback" not in result.stderr, f"unexpected traceback: {result.stderr}"
