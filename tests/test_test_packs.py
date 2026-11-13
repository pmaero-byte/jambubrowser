"""The pack runner (``jambu pack run``), driven by a fake flow service."""
from __future__ import annotations

import asyncio
import json
import types
from pathlib import Path

import pytest

from backend.modules import test_packs as tp
from cli.jambu_cli.commands import pack as pack_cmd

PACK = {
    "name": "demo",
    "probe": "/health",
    "vars": {"font": {"env": "DEMO_FONT", "default": "{root}/font.ttf"},
             "root": {"env": "DEMO_ROOT", "default": ""}},
    "requires_vars": ["root"],
    "findings": {"BUG-1": {"title": "known bug", "severity": "high"},
                 "BUG-2": {"title": "fixed bug", "severity": "low"}},
    "flows": [
        {"id": "api", "steps": [
            {"action": "api", "path": "/api/prompt", "params": {"q": "ஒரு சிறுமி"}},
            {"action": "assert_status", "value": "200"},
            {"action": "assert_json", "path": "font", "op": "equals",
             "expected": "{font}", "finding": "BUG-1"},
        ]},
        {"id": "ui", "approve": True, "path": "/studio", "steps": [
            {"action": "click", "selector": "#go"},
            {"action": "assert_text", "text": "ok", "finding": "BUG-2"},
        ]},
    ],
}


class FakeService:
    """Records calls; fails any step whose action is listed in ``fail``."""

    def __init__(self, fail=(), crash=False):
        self.fail = set(fail)
        self.crash = crash
        self.calls = []

    async def run_test(self, **kwargs):
        self.calls.append(kwargs)
        if self.crash:
            raise RuntimeError("browser died")
        steps = []
        for i, step in enumerate(kwargs["steps"]):
            bad = step["action"] in self.fail
            steps.append({"i": i, "action": step["action"],
                          "status": "failed" if bad else "passed",
                          **({"error": f"{step['action']} failed"} if bad else {})})
        return {"steps": steps}


def write_pack(tmp_path: Path, pack: dict = PACK) -> Path:
    d = tmp_path / "demo"
    d.mkdir()
    (d / "pack.json").write_text(json.dumps(pack), encoding="utf-8")
    return d


def variables(tmp_path, **over):
    d = write_pack(tmp_path)
    return d, tp.resolve_vars(PACK, d, "http://127.0.0.1:9/", {"root": "/r", **over}, environ={})


def run(pack, d, vs, service, **kw):
    return asyncio.run(tp.run_pack(pack, d, vs, service=service, skip_probe=True,
                                   artifacts_dir=str(d / "art"), **kw))


def test_substitute_leaves_unknown_braces():
    assert tp.substitute({"a": ["{x}", "{y}", '{"type": "object"}']}, {"x": 1}) == \
        {"a": ["1", "{y}", '{"type": "object"}']}


def test_vars_nest_and_env_beats_default(tmp_path):
    d, vs = variables(tmp_path)
    assert vs["base"] == "http://127.0.0.1:9"
    assert vs["font"] == "/r/font.ttf"
    vs2 = tp.resolve_vars(PACK, d, "http://h", environ={"DEMO_ROOT": "/e"})
    assert vs2["font"] == "/e/font.ttf"


def test_missing_required_var_names_the_flag_and_env(tmp_path):
    d = write_pack(tmp_path)
    with pytest.raises(tp.PackError, match=r"--var root=\.\.\. or \$DEMO_ROOT"):
        tp.resolve_vars(PACK, d, "http://h", environ={})


def test_prepare_flow_builds_encoded_urls_and_strips_runner_keys(tmp_path):
    _, vs = variables(tmp_path)
    steps, meta = tp.prepare_flow(PACK["flows"][0], vs)
    assert steps[0] == {"action": "navigate", "url": "http://127.0.0.1:9/"}
    assert meta[0] == {"implicit": True}
    assert steps[1]["url"] == ("http://127.0.0.1:9/api/prompt?q="
                               "%E0%AE%92%E0%AE%B0%E0%AF%81%20%E0%AE%9A%E0%AE%BF%E0%AE%B1"
                               "%E0%AF%81%E0%AE%AE%E0%AE%BF")
    assert "params" not in steps[1] and "finding" not in steps[3]
    assert steps[3]["expected"] == "/r/font.ttf" and meta[3]["finding"] == "BUG-1"


def test_findings_reproduced_and_not_reproduced(tmp_path):
    d, vs = variables(tmp_path)
    service = FakeService(fail={"assert_json"})
    result = run(PACK, d, vs, service)
    assert [c["approve"] for c in service.calls] == [False, True]
    assert service.calls[1]["url"] == "http://127.0.0.1:9/studio"
    by_id = {f["id"]: f for f in result["findings"]}
    assert by_id["BUG-1"]["status"] == "reproduced"
    assert by_id["BUG-2"]["status"] == "not_reproduced"
    assert result["summary"]["findings_not_reproduced"] == ["BUG-2"]
    # A reproduced finding still fails the pack: it is a real product bug.
    assert result["ok"] is False and tp.exit_code(result) == tp.EXIT_FAILED
    assert result["summary"]["unexpected_failures"] == 0
    # Passing implicit navigates are not reported.
    assert [s["action"] for s in result["flows"][0]["steps"]] == \
        ["api", "assert_status", "assert_json"]
    assert "known findings:" in tp.format_text(result)


def test_unexpected_failure_and_all_green(tmp_path):
    d, vs = variables(tmp_path)
    result = run(PACK, d, vs, FakeService(fail={"click"}))
    assert result["unexpected_failures"] == [
        {"flow": "ui", "step": 1, "action": "click", "error": "click failed"}]
    green = run(PACK, d, vs, FakeService())
    assert green["ok"] is True and tp.exit_code(green) == tp.EXIT_OK


def test_crashed_flow_is_an_error(tmp_path):
    d, vs = variables(tmp_path)
    result = run(PACK, d, vs, FakeService(crash=True))
    assert result["summary"]["flow_errors"] == ["api", "ui"]
    assert tp.exit_code(result) == tp.EXIT_ERROR


def test_missing_baseline_fails_unless_updating(tmp_path):
    pack = {"name": "v", "flows": [{"id": "shot", "steps": [
        {"action": "assert_screenshot", "name": "home", "baseline_dir": "{baselines}"}]}]}
    d = write_pack(tmp_path, pack)
    vs = tp.resolve_vars(pack, d, "http://h", environ={})
    result = run(pack, d, vs, FakeService())
    step = result["flows"][0]["steps"][0]
    assert step["status"] == "failed" and "baseline was missing" in step["error"]
    service = FakeService()
    updated = run(pack, d, vs, service, update_baselines=True)
    assert updated["ok"] is True
    assert service.calls[0]["steps"][1]["update_baseline"] is True
    (d / "baselines").mkdir()
    (d / "baselines" / "home.png").write_bytes(b"png")
    assert run(pack, d, vs, FakeService())["ok"] is True


def test_unknown_flow_and_unreachable_target(tmp_path):
    d, vs = variables(tmp_path)
    with pytest.raises(tp.PackError, match="unknown flow"):
        run(PACK, d, vs, FakeService(), flow_ids=["nope"])
    with pytest.raises(tp.PackError, match="not reachable"):
        asyncio.run(tp.run_pack(PACK, d, vs, service=FakeService()))


def test_load_pack_rejects_undeclared_finding(tmp_path):
    bad = {"flows": [{"id": "a", "steps": [{"action": "x", "finding": "NOPE"}]}]}
    with pytest.raises(tp.PackError, match="unknown finding"):
        tp.load_pack(write_pack(tmp_path, bad))


def test_bundled_oviyan_pack_loads():
    d = tp.find_pack("oviyan")
    pack = tp.load_pack(d)
    ids = [f["id"] for f in pack["flows"]]
    assert len(ids) == len(set(ids)) >= 10
    used = {s.get("finding") for f in pack["flows"] for s in f["steps"]} - {None}
    assert used == set(pack["findings"])
    with pytest.raises(tp.PackError, match="font"):
        tp.resolve_vars(pack, d, "http://h", environ={})


def _args(tmp_path, d, **kw):
    ns = dict(pack=str(d), base="http://127.0.0.1:9", json=str(tmp_path / "out.json"),
              var=["root=/r"], flow="api", update_baselines=False,
              artifacts=str(tmp_path / "art"))
    ns.update(kw)
    return types.SimpleNamespace(**ns)


def test_cli_run_writes_json_and_exit_code(tmp_path, monkeypatch, capsys):
    d = write_pack(tmp_path)
    monkeypatch.setattr(tp, "probe", lambda base, path, timeout=10.0: (True, "ok"))
    code = pack_cmd.run_pack_command(_args(tmp_path, d), service=FakeService(fail={"assert_json"}))
    assert code == 1
    report = json.loads((tmp_path / "out.json").read_text())
    assert report["findings"][0]["id"] == "BUG-1" and len(report["flows"]) == 1
    assert "FAIL api" in capsys.readouterr().out


def test_cli_run_reports_pack_errors_as_exit_2(tmp_path, capsys):
    d = write_pack(tmp_path)
    code = pack_cmd.run_pack_command(_args(tmp_path, d, var=[]), service=FakeService())
    assert code == 2
    assert json.loads((tmp_path / "out.json").read_text())["ok"] is False
    assert pack_cmd.run_pack_command(_args(tmp_path, d, var=["oops"]), service=FakeService()) == 2


def test_cli_pack_list(capsys):
    assert pack_cmd.cmd_pack(types.SimpleNamespace(pack_command="list")) == 0
    assert "oviyan" in capsys.readouterr().out
