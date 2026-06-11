"""
Tests for the eval harness (backend.eval).

Covers:
- Task registration + listing
- Metrics: exact_match, contains_match, fuzzy_match, number_match, email_redaction_match
- Harness: single task, full suite, error handling
- Store: save/retrieve, list, compare
- Report: markdown + json rendering
- CLI: --list, --help
"""

import asyncio
import json
import os

import pytest


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv("JAMBU_LLM_PROVIDER", "mock")
    monkeypatch.setenv("JAMBU_DB_PATH", ":memory:")
    from backend.llm.registry import reset_registry
    from backend.llm import reload_config
    from backend.eval.store import reset_store
    reload_config()
    reset_registry()
    reset_store()
    # Importing the tasks package triggers all @register_task decorators
    from backend.eval import tasks as _tasks  # noqa: F401
    yield


# ---------------------------------------------------------------------------
# Task registration
# ---------------------------------------------------------------------------

class TestTaskRegistry:
    def test_smoke_suite_has_5_tasks(self):
        from backend.eval.harness import list_tasks
        tasks = list_tasks(suite="smoke")
        assert len(tasks) == 5

    def test_gaia_suite_has_10_tasks(self):
        from backend.eval.harness import list_tasks
        tasks = list_tasks(suite="gaia-mini")
        assert len(tasks) == 10

    def test_webarena_suite_has_8_tasks(self):
        from backend.eval.harness import list_tasks
        tasks = list_tasks(suite="webarena-mini")
        assert len(tasks) == 8

    def test_privacy_suite_has_7_tasks(self):
        from backend.eval.harness import list_tasks
        tasks = list_tasks(suite="privacy")
        assert len(tasks) == 7

    def test_memory_suite_has_5_tasks(self):
        from backend.eval.harness import list_tasks
        tasks = list_tasks(suite="memory")
        assert len(tasks) == 5

    def test_list_suites(self):
        from backend.eval.harness import list_suites
        suites = list_suites()
        assert "smoke" in suites
        assert "gaia-mini" in suites
        assert "webarena-mini" in suites
        assert "privacy" in suites
        assert "memory" in suites

    def test_get_task_raises_for_unknown(self):
        from backend.eval.harness import get_task
        with pytest.raises(KeyError):
            get_task("nonexistent.task")

    def test_task_metadata(self):
        from backend.eval.harness import get_task
        t = get_task("smoke.1.hello")
        assert t.suite == "smoke"
        assert t.category == "qa"
        assert t.difficulty == 1
        assert t.timeout_seconds > 0


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

class TestMetrics:
    def test_exact_match_pass(self):
        from backend.eval.metrics import exact_match
        assert exact_match("Paris", "Paris") == 1.0
        assert exact_match("  paris  ", "PARIS") == 1.0
        assert exact_match("Tokyo", ["Tokyo", "Kyoto"]) == 1.0

    def test_exact_match_fail(self):
        from backend.eval.metrics import exact_match
        assert exact_match("Paris", "London") == 0.0
        assert exact_match("", "anything") == 0.0

    def test_contains_match(self):
        from backend.eval.metrics import contains_match
        assert contains_match("The capital is Paris, France.", "Paris") == 1.0
        assert contains_match("Paris is beautiful", ["Tokyo", "Paris"]) == 1.0
        assert contains_match("London is great", "Paris") == 0.0

    def test_fuzzy_match_partial(self):
        from backend.eval.metrics import fuzzy_match
        # "Rust programming" — "Rust" matches (1/2 words = 0.5)
        s = fuzzy_match("I love Rust for its type system", "Rust programming")
        assert 0 < s <= 1.0

    def test_fuzzy_match_full(self):
        from backend.eval.metrics import fuzzy_match
        s = fuzzy_match("Rust and machine learning", "Rust machine learning")
        assert s == 1.0

    def test_number_match(self):
        from backend.eval.metrics import number_match
        assert number_match("The answer is 56", "56") == 1.0
        assert number_match("Answer: 4,096 bytes", "4096") == 1.0
        assert number_match("Result: 99.5 degrees", "99.5") == 1.0
        assert number_match("Result: 100", "99.5") == 0.0

    def test_email_redaction_clean(self):
        from backend.eval.metrics import email_redaction_match
        assert email_redaction_match("Contact [REDACTED] for info", "") == 1.0

    def test_email_redaction_leaked(self):
        from backend.eval.metrics import email_redaction_match
        score = email_redaction_match("Email me at john@x.com", "")
        assert score < 1.0
        assert score >= 0.0

    def test_all_metrics_returns_dict(self):
        from backend.eval.metrics import all_metrics
        m = all_metrics()
        assert "exact_match" in m
        assert "contains_match" in m
        assert "fuzzy_match" in m
        assert "number_match" in m
        assert "email_redaction_match" in m


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

class TestHarness:
    def test_run_task_returns_result(self):
        from backend.eval.harness import Harness, get_task
        h = Harness(provider="mock")
        result = asyncio.run(h.run_task(get_task("smoke.1.hello")))
        assert result.task_id == "smoke.1.hello"
        assert result.status.value in ("passed", "failed", "error", "timeout")
        assert result.duration_ms > 0
        assert result.provider == "mock"

    def test_run_suite_executes_all_tasks(self):
        from backend.eval.harness import Harness
        h = Harness(provider="mock")
        sr = asyncio.run(h.run_suite("smoke"))
        assert len(sr.results) == 5
        assert sr.run_id
        assert sr.suite == "smoke"

    def test_run_suite_task_ids_filter(self):
        from backend.eval.harness import Harness
        h = Harness(provider="mock")
        sr = asyncio.run(h.run_suite("smoke", task_ids=["smoke.1.hello"]))
        assert len(sr.results) == 1

    def test_run_suite_empty_raises(self):
        from backend.eval.harness import Harness
        h = Harness(provider="mock")
        with pytest.raises(ValueError):
            asyncio.run(h.run_suite("nonexistent-suite"))

    def test_suite_result_metrics(self):
        from backend.eval.harness import Harness, SuiteResult, TaskResult, TaskStatus
        sr = SuiteResult(
            run_id="test",
            suite="t",
            provider="mock",
            model="m",
            results=[
                TaskResult(task_id="a", suite="t", provider="mock", model="m",
                           status=TaskStatus.PASSED, total_tokens=10, cost_usd=0.01, duration_ms=100),
                TaskResult(task_id="b", suite="t", provider="mock", model="m",
                           status=TaskStatus.FAILED, total_tokens=20, cost_usd=0.02, duration_ms=200),
            ],
        )
        assert sr.total == 2
        assert sr.passed == 1
        assert sr.failed == 1
        assert sr.success_rate == 0.5
        assert sr.total_tokens == 30
        assert abs(sr.total_cost_usd - 0.03) < 1e-9
        assert sr.avg_duration_ms == 150.0

    def test_run_suite_completes_within_timeout(self):
        from backend.eval.harness import Harness
        h = Harness(provider="mock")
        sr = asyncio.run(h.run_suite("smoke"))
        assert sr.completed_at is not None
        assert sr.completed_at >= sr.started_at


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class TestStore:
    def test_save_and_retrieve_suite(self):
        from backend.eval.harness import SuiteResult, TaskResult, TaskStatus
        from backend.eval.store import get_store
        from backend.eval.harness import Harness
        h = Harness(provider="mock")
        sr = asyncio.run(h.run_suite("smoke"))
        get_store().save_suite(sr)
        # Retrieve
        loaded = get_store().get_run(sr.run_id)
        assert loaded is not None
        assert loaded["suite"] == "smoke"
        assert loaded["passed"] == sr.passed
        assert loaded["total"] == sr.total
        assert len(loaded["tasks"]) == 5

    def test_list_runs(self):
        from backend.eval.harness import Harness
        from backend.eval.store import get_store
        h = Harness(provider="mock")
        for _ in range(3):
            sr = asyncio.run(h.run_suite("smoke"))
            get_store().save_suite(sr)
        runs = get_store().list_runs(suite="smoke", limit=10)
        assert len(runs) == 3
        # Newest first
        assert runs[0]["started_at"] >= runs[1]["started_at"]

    def test_get_run_unknown_returns_none(self):
        from backend.eval.store import get_store
        assert get_store().get_run("nonexistent-run-id") is None

    def test_compare_runs(self):
        from backend.eval.harness import Harness
        from backend.eval.store import get_store
        h = Harness(provider="mock")
        sr1 = asyncio.run(h.run_suite("smoke"))
        sr2 = asyncio.run(h.run_suite("smoke"))
        get_store().save_suite(sr1)
        get_store().save_suite(sr2)
        runs = get_store().compare_runs([sr1.run_id, sr2.run_id])
        assert len(runs) == 2


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

class TestReport:
    def test_to_markdown_includes_summary(self):
        from backend.eval.harness import SuiteResult, TaskResult, TaskStatus
        from backend.eval.report import to_markdown
        sr = SuiteResult(
            run_id="r1", suite="t", provider="mock", model="m",
            results=[
                TaskResult(task_id="a", suite="t", provider="mock", model="m",
                           status=TaskStatus.PASSED, total_tokens=10, cost_usd=0.01, duration_ms=100),
            ],
        )
        md = to_markdown(sr)
        assert "# Eval Run: t" in md
        assert "**Provider:**" in md
        assert "Success rate" in md
        assert "Per-Task Results" in md

    def test_to_json_valid(self):
        from backend.eval.harness import SuiteResult
        from backend.eval.report import to_json
        sr = SuiteResult(run_id="r1", suite="t", provider="mock", model="m")
        out = to_json(sr)
        d = json.loads(out)
        assert d["run_id"] == "r1"
        assert d["suite"] == "t"

    def test_compare_markdown_table(self):
        from backend.eval.harness import SuiteResult
        from backend.eval.report import compare_markdown
        s1 = SuiteResult(run_id="r1", suite="t", provider="mock", model="m",
                         results=[])
        s1.started_at = 100.0
        s1.completed_at = 100.1
        s2 = SuiteResult(run_id="r2", suite="t", provider="anthropic", model="claude",
                         results=[])
        s2.started_at = 200.0
        s2.completed_at = 200.5
        md = compare_markdown([s1, s2])
        assert "Provider Comparison" in md
        assert "mock" in md
        assert "anthropic" in md
        assert "Best success rate" in md

    def test_generate_report_dispatches_correctly(self):
        from backend.eval.harness import SuiteResult
        from backend.eval.report import generate_report
        sr = SuiteResult(run_id="r1", suite="t", provider="mock", model="m")
        assert "Eval Run" in generate_report(sr, fmt="markdown")
        assert "Eval Run" in generate_report(sr, fmt="json") or '"suite":' in generate_report(sr, fmt="json")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCLI:
    def test_cli_list(self, capsys):
        from backend.eval.cli import main
        rc = main(["list"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Suites" in out
        assert "smoke" in out

    def test_cli_help(self, capsys):
        from backend.eval.cli import main
        with pytest.raises(SystemExit):
            main(["--help"])

    def test_cli_report_list_runs(self, capsys):
        from backend.eval.harness import Harness
        from backend.eval.store import get_store
        from backend.eval.cli import main
        h = Harness(provider="mock")
        sr = asyncio.run(h.run_suite("smoke"))
        get_store().save_suite(sr)
        rc = main(["report", "--limit", "5"])
        assert rc == 0

    def test_cli_report_specific_run(self, capsys):
        from backend.eval.harness import Harness
        from backend.eval.store import get_store
        from backend.eval.cli import main
        h = Harness(provider="mock")
        sr = asyncio.run(h.run_suite("smoke"))
        get_store().save_suite(sr)
        rc = main(["report", "--run-id", sr.run_id, "--format", "markdown"])
        assert rc == 0
        out = capsys.readouterr().out
        assert sr.run_id in out
