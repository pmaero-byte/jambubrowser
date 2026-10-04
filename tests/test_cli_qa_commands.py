"""`jambu qa` sub-subcommands: dispatch, exit codes and rendering.

`cmd_qa` used to be a 196-line if-chain over nine sub-subcommands, and nothing
tested it — the CLI suite covered audit, status, diff, dcm and the rest, but not
`jambu qa`. Now that it is a dispatch table over `_qa_*` handlers, each verb is
callable directly with a fake `args` namespace and a stubbed engine, so the
things a CI user actually reads are pinned:

* the exit code is the gate — `EXIT_GATE_FAILED` when a run fails, even though
  the command worked perfectly;
* `flaky` renders as its own state and never as plain PASS;
* `accept` and `reject` share one handler and send the opposite boolean;
* an unknown verb prints the usage line rather than doing something surprising.
"""
from __future__ import annotations

import argparse
import io
import contextlib

import pytest

from cli.jambu_cli import core
from cli.jambu_cli.commands import qa as qa_cmds


def run(argv: list[str], responses: dict | None = None):
    """Invoke a handler with a stubbed engine; returns (exit code, stdout)."""
    calls: list[tuple] = []
    queue = dict(responses or {})

    def fake_api_request(method, path, data=None):
        calls.append((method, path, data))
        for key, value in queue.items():
            if key in path:
                return value() if callable(value) else value
        return {}

    out = io.StringIO()
    original = core.api_request
    core.api_request = fake_api_request
    try:
        with contextlib.redirect_stdout(out):
            code = qa_cmds.cmd_qa(argparse.Namespace(**argv))
    finally:
        core.api_request = original
    return code, out.getvalue(), calls


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

class TestDispatch:
    def test_every_advertised_verb_has_a_handler(self):
        """The usage line and the table must not drift apart."""
        table = qa_cmds._qa_dispatch(argparse.Namespace())
        for verb in ("create", "list", "run", "heals", "accept", "reject",
                     "quarantine", "unquarantine", "auto-retry"):
            assert verb in table, f"{verb} has no handler"

    def test_unknown_verb_prints_usage_and_errors(self):
        code, out, _ = run({"qa_command": "teleport"})
        assert code == core.EXIT_ENGINE_ERROR
        assert "jambu qa create|list|run" in out

    def test_missing_verb_prints_usage(self):
        code, out, _ = run({})
        assert code == core.EXIT_ENGINE_ERROR
        assert "--help" in out


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

class TestList:
    def test_renders_each_case_with_its_state(self):
        code, out, _ = run({"qa_command": "list"}, {"/qa/cases": {
            "cases": [
                {"id": 1, "enabled": True, "name": "login", "kind": "smoke",
                 "last_status": "passed"},
                {"id": 2, "enabled": False, "name": "checkout", "kind": "e2e",
                 "last_status": None},
            ],
            "count": 2,
        }})
        assert code == core.EXIT_OK
        assert "#1 [on ] login (smoke) — passed" in out
        assert "#2 [off] checkout (e2e) — never run" in out
        assert "2 case(s)" in out

    def test_empty_list(self):
        code, out, _ = run({"qa_command": "list"}, {"/qa/cases": {"cases": []}})
        assert code == core.EXIT_OK
        assert "0 case(s)" in out


# ---------------------------------------------------------------------------
# run: exit codes and rendering
# ---------------------------------------------------------------------------

def run_args(**over):
    args = {
        "qa_command": "run", "case_id": 7, "local": True, "approve": False,
        "stop_on_failure": False, "dataset_file": None, "viewports": None,
        "junit_out": None, "sarif_out": None, "force": False,
    }
    args.update(over)
    return args


class TestRun:
    def test_passing_run_exits_zero(self):
        code, out, _ = run(run_args(), {"/run": {
            "ok": True, "case_id": 7, "status": "passed",
            "passed": 4, "total": 4, "run_id": 99, "healed_steps": 1,
        }})
        assert code == core.EXIT_OK
        assert "QA case #7 PASS — 4/4 steps (+1 healed)" in out
        assert "run #99" in out

    def test_failing_run_exits_gate_failed(self):
        """The command succeeded; the *gate* did not. CI must see that."""
        code, out, _ = run(run_args(), {"/run": {
            "ok": False, "case_id": 7, "status": "failed",
            "passed": 3, "total": 4, "run_id": 99, "healed_steps": 0,
            "failed_steps": [{"i": 2, "action": "click", "reason": "blocked",
                              "error": "nope"}],
        }})
        assert code == core.EXIT_GATE_FAILED
        assert "FAIL — 3/4 steps" in out
        assert "FAIL #2 click — blocked: nope" in out

    def test_flaky_is_its_own_state_not_a_pass(self):
        code, out, _ = run(run_args(), {"/run": {
            "ok": True, "case_id": 7, "status": "flaky", "passed": 4,
            "total": 4, "run_id": 99, "healed_steps": 0,
        }})
        assert code == core.EXIT_OK
        assert "FLAKY (green)" in out
        assert " PASS " not in out

    def test_quarantine_and_health_lines_are_surfaced(self):
        code, out, _ = run(run_args(), {"/run": {
            "ok": False, "case_id": 7, "status": "failed", "passed": 0,
            "total": 4, "run_id": 99, "healed_steps": 0,
            "quarantine_reason": "3 failures in a row",
            "health": {"action": "quarantined",
                       "quarantine_reason": "3 failures in a row"},
        }})
        assert "quarantined: 3 failures in a row" in out
        assert "health: quarantined" in out

    def test_unbound_placeholders_are_reported(self):
        code, out, _ = run(run_args(), {"/run": {
            "ok": True, "case_id": 7, "status": "passed", "passed": 2,
            "total": 2, "run_id": 1, "healed_steps": 0,
            "unbound": ["email", "password"],
        }})
        assert "unbound placeholders: email, password" in out

    def test_proposed_heals_tell_the_user_how_to_accept(self):
        code, out, _ = run(run_args(), {"/run": {
            "ok": True, "case_id": 7, "status": "passed", "passed": 1,
            "total": 1, "run_id": 1, "healed_steps": 0,
            "heals": [{"id": 12, "old_target": "#old", "new_target": "#new"}],
        }})
        assert "heal proposed #12: '#old' → '#new'" in out
        assert "jambu qa accept 12" in out

    def test_engine_error_is_reported(self):
        code, out, _ = run(run_args(), {"/run": {"error": "case disabled"}})
        assert code == core.EXIT_ENGINE_ERROR
        assert "Run failed: case disabled" in out

    def test_matrix_run_reports_rows(self):
        code, out, _ = run(run_args(), {"/run": {
            "ok": True, "case_id": 7, "matrix": True, "rows": 2,
            "passed_rows": 2,
            "runs": [{"dataset_index": 0, "ok": True, "passed": 3, "total": 3,
                      "run_id": 1},
                     {"dataset_index": 1, "ok": True, "passed": 3, "total": 3,
                      "run_id": 2}],
        }})
        assert code == core.EXIT_OK
        assert "2/2 rows" in out
        assert "row 0: PASS 3/3 steps" in out

    def test_matrix_failure_is_a_gate_failure(self):
        code, _, _ = run(run_args(), {"/run": {
            "ok": False, "case_id": 7, "matrix": True, "rows": 2,
            "passed_rows": 1, "runs": [],
        }})
        assert code == core.EXIT_GATE_FAILED

    def test_dataset_file_is_sent_as_rows(self, tmp_path):
        dataset = tmp_path / "rows.json"
        dataset.write_text('{"rows": [{"user": "a"}, {"user": "b"}]}')
        _code, _out, calls = run(
            run_args(dataset_file=str(dataset)),
            {"/run": {"ok": True, "case_id": 7, "status": "passed",
                      "passed": 1, "total": 1, "run_id": 1, "healed_steps": 0}})
        assert calls[0][2]["dataset_rows"] == [{"user": "a"}, {"user": "b"}]

    def test_bad_dataset_file_is_reported_not_raised(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        code, out, _ = run(run_args(dataset_file=str(bad)), {"/run": {}})
        assert code == core.EXIT_ENGINE_ERROR
        assert "Could not read dataset file" in out

    def test_viewport_specs_become_a_matrix(self):
        code, _out, calls = run(
            run_args(viewports=["mobile=390x844", "desktop=1280x800"]),
            {"/run": {"ok": True, "case_id": 7, "status": "passed",
                      "passed": 1, "total": 1, "run_id": 1, "healed_steps": 0}})
        assert code == core.EXIT_OK
        assert calls[0][2]["viewport_matrix"] == [
            {"name": "mobile", "viewport": {"width": 390, "height": 844}},
            {"name": "desktop", "viewport": {"width": 1280, "height": 800}},
        ]

    def test_bad_viewport_spec_names_the_expected_format(self):
        code, out, _ = run(run_args(viewports=["mobile-ish"]), {"/run": {}})
        assert code == core.EXIT_ENGINE_ERROR
        assert "NAME=WIDTHxHEIGHT" in out


# ---------------------------------------------------------------------------
# heals / accept / reject / quarantine / unquarantine / auto-retry
# ---------------------------------------------------------------------------

class TestHeals:
    def test_lists_proposed_heals(self):
        code, out, _ = run({"qa_command": "heals"}, {"/qa/heals": {"heals": [
            {"id": 3, "case_id": 1, "step_index": 2,
             "old_target": "#a", "new_target": "#b"},
        ]}})
        assert code == core.EXIT_OK
        assert "#3 case #1 step 2: '#a' → '#b'" in out

    def test_empty_state_says_the_selectors_are_healthy(self):
        code, out, _ = run({"qa_command": "heals"}, {"/qa/heals": {"heals": []}})
        assert code == core.EXIT_OK
        assert "No proposed heals" in out
        assert "healthy" in out

    def test_accept_sends_accept_true(self):
        code, out, calls = run(
            {"qa_command": "accept", "heal_id": 5, "actor": "alice"},
            {"/qa/heals/5": {"id": 5, "status": "accepted"}})
        assert code == core.EXIT_OK
        assert calls[0][2] == {"accept": True, "actor": "alice"}
        assert "Heal #5 accepted." in out

    def test_reject_sends_accept_false(self):
        """Same handler, opposite boolean — they must not drift apart."""
        _code, _out, calls = run(
            {"qa_command": "reject", "heal_id": 5, "actor": "alice"},
            {"/qa/heals/5": {"id": 5, "status": "rejected"}})
        assert calls[0][2]["accept"] is False


class TestQuarantine:
    def test_quarantine_reports_the_reason(self):
        code, out, calls = run(
            {"qa_command": "quarantine", "case_id": 4, "reason": "flaky",
             "actor": "lead"},
            {"/quarantine": {"id": 4, "quarantine_reason": "flaky"}})
        assert code == core.EXIT_OK
        assert calls[0][2] == {"reason": "flaky", "actor": "lead"}
        assert "Case #4 quarantined (flaky)." in out

    def test_quarantine_without_a_reason_says_so(self):
        code, out, _ = run(
            {"qa_command": "quarantine", "case_id": 4, "reason": None,
             "actor": "lead"},
            {"/quarantine": {"id": 4}})
        assert "no reason" in out

    def test_unquarantine(self):
        code, out, _ = run({"qa_command": "unquarantine", "case_id": 4},
                           {"/unquarantine": {"id": 4}})
        assert code == core.EXIT_OK
        assert "Case #4 unquarantined." in out

    def test_auto_retry_toggle_reports_the_new_state(self):
        code, out, _ = run({"qa_command": "auto-retry", "case_id": 4,
                            "enabled": True},
                           {"/auto-retry": {"id": 4, "auto_retry": True}})
        assert code == core.EXIT_OK
        assert "auto-retry on" in out

    def test_auto_retry_off(self):
        code, out, _ = run({"qa_command": "auto-retry", "case_id": 4,
                            "enabled": False},
                           {"/auto-retry": {"id": 4, "auto_retry": False}})
        assert "auto-retry off" in out

    def test_engine_failure_is_reported_per_verb(self):
        code, out, _ = run({"qa_command": "quarantine", "case_id": 4,
                            "reason": "x", "actor": "a"},
                           {"/quarantine": {"error": "already quarantined"}})
        assert code == core.EXIT_ENGINE_ERROR
        assert "already quarantined" in out