"""Test packs: a named set of browser/API flows run with one command.

A pack is a directory holding ``pack.json`` (and optionally ``baselines/``
for visual regression). ``jambu pack run <name> --base URL`` loads it,
substitutes variables, runs each flow through the in-process browser agent
and writes one JSON report. Nothing here calls a model: every assertion a
pack can use is deterministic.

``pack.json``::

    {
      "name": "example",
      "description": "...",
      "probe": "/health",                 # GET {base}{probe} must answer first
      "vars": {"font": {"env": "EXAMPLE_FONT", "default": ""}},
      "requires_vars": ["font"],          # refuse to run when these are empty
      "findings": {"BUG-1": {"title": "...", "severity": "high"}},
      "flows": [
        {"id": "caps", "title": "...", "path": "/", "steps": [
          {"action": "api", "path": "/api/caps", "params": {"q": "x"}},
          {"action": "assert_status", "value": "200", "finding": "BUG-1"}
        ]}
      ]
    }

Runner-only step keys (removed before the flow engine sees the step):

* ``path`` + ``params`` on ``api``/``navigate`` steps build the URL from
  ``{base}`` and URL-encode the parameters (so non-ASCII prompts stay
  readable in the pack).
* ``finding`` names a known defect of the product under test. The step is
  expected to *fail* while that defect exists; a failing step is then
  reported as the finding reproduced, and a passing one as "not reproduced"
  (fixed, or the check went stale). Findings never turn a failure into a
  pass: the pack keeps failing until the product is fixed.

Variables: ``{base}``, ``{pack_dir}``, ``{baselines}`` and every key of
``vars`` are substituted in all strings; unknown ``{...}`` text is left as
is, so JSON-schema-like content is safe.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKS_DIR = REPO_ROOT / "packs"
RUNNER_KEYS = ("finding", "params", "note")
MAX_EVIDENCE_STRING = 2000

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ERROR = 2


class PackError(Exception):
    """The pack could not be run at all (bad pack, missing var, target down)."""


# --------------------------------------------------------------------------
# Loading


def pack_dirs(root: Optional[Path] = None) -> list:
    root = Path(root or PACKS_DIR)
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if (p / "pack.json").is_file())


def find_pack(name_or_path: str, root: Optional[Path] = None) -> Path:
    """Resolve a pack name (``packs/<name>``) or a path to a pack dir/file."""
    candidate = Path(name_or_path).expanduser()
    if candidate.is_file() and candidate.name.endswith(".json"):
        return candidate.parent
    if (candidate / "pack.json").is_file():
        return candidate
    named = Path(root or PACKS_DIR) / name_or_path
    if (named / "pack.json").is_file():
        return named
    known = ", ".join(p.name for p in pack_dirs(root)) or "none"
    raise PackError(f"no pack named {name_or_path!r} (known packs: {known})")


def load_pack(pack_dir: Path) -> dict:
    try:
        pack = json.loads((Path(pack_dir) / "pack.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PackError(f"cannot read {pack_dir}/pack.json: {exc}") from exc
    if not isinstance(pack, dict) or not isinstance(pack.get("flows"), list):
        raise PackError("pack.json needs a 'flows' list")
    seen = set()
    for flow in pack["flows"]:
        if not isinstance(flow, dict) or not flow.get("id"):
            raise PackError("every flow needs an 'id'")
        if flow["id"] in seen:
            raise PackError(f"duplicate flow id {flow['id']!r}")
        seen.add(flow["id"])
        if not isinstance(flow.get("steps"), list) or not flow["steps"]:
            raise PackError(f"flow {flow['id']!r} needs a non-empty 'steps' list")
        for step in flow["steps"]:
            finding = step.get("finding") if isinstance(step, dict) else None
            if finding and finding not in (pack.get("findings") or {}):
                raise PackError(f"flow {flow['id']!r} names unknown finding {finding!r}")
    return pack


# --------------------------------------------------------------------------
# Variables and step preparation

_VAR = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def substitute(value: Any, variables: dict) -> Any:
    """Replace ``{name}`` for known names, recursively; leave the rest."""
    if isinstance(value, str):
        return _VAR.sub(lambda m: str(variables[m.group(1)])
                        if m.group(1) in variables else m.group(0), value)
    if isinstance(value, list):
        return [substitute(v, variables) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, variables) for k, v in value.items()}
    return value


def resolve_vars(pack: dict, pack_dir: Path, base: str,
                 overrides: Optional[dict] = None,
                 environ: Optional[dict] = None) -> dict:
    """Build the variable table: built-ins, then pack defaults/env, then overrides."""
    environ = os.environ if environ is None else environ
    overrides = overrides or {}
    base = base.rstrip("/")
    variables = {"base": base, "pack_dir": str(pack_dir),
                 "baselines": str(Path(pack_dir) / "baselines")}
    templated = {}
    for name, spec in (pack.get("vars") or {}).items():
        if isinstance(spec, dict):
            raw = environ.get(spec["env"], "") if spec.get("env") else ""
            variables[name] = raw or spec.get("default", "")
            if not raw and name not in overrides:
                templated[name] = variables[name]
        else:
            variables[name] = spec
    variables.update(overrides)
    # Defaults may refer to other variables ({oviyan_root}/assets/...).
    for _ in range(3):
        variables = {k: substitute(v, variables) if isinstance(v, str) else v
                     for k, v in variables.items()}
    # A default built from an empty variable is itself unset, so requires_vars
    # does not accept "/assets/font.ttf" when {oviyan_root} was never given.
    for name, template in templated.items():
        if isinstance(template, str) and any(
                ref in variables and not variables[ref] for ref in _VAR.findall(template)):
            variables[name] = ""
    missing = [v for v in pack.get("requires_vars") or [] if not variables.get(v)]
    if missing:
        hints = []
        for name in missing:
            spec = (pack.get("vars") or {}).get(name)
            env = spec.get("env") if isinstance(spec, dict) else None
            hints.append(f"--var {name}=..." + (f" or ${env}" if env else ""))
        raise PackError("missing required variable(s): " + ", ".join(missing)
                        + " (set " + "; ".join(hints) + ")")
    return variables


def _build_url(base: str, path: str, params: Optional[dict]) -> str:
    url = path if re.match(r"^[a-z]+://", path) else base + "/" + path.lstrip("/")
    if params:
        query = urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None}, quote_via=urllib.parse.quote)
        url += ("&" if "?" in url else "?") + query
    return url


def prepare_flow(flow: dict, variables: dict, *, update_baselines: bool = False) -> tuple:
    """Return ``(engine_steps, step_meta)``; step indices match the report."""
    base = variables["base"]
    steps = substitute(flow["steps"], variables)
    engine_steps: list = []
    meta: list = []
    first = (steps[0].get("action") or "").lower() if steps else ""
    if first != "navigate":
        # An explicit navigate keeps report indices aligned with the pack.
        engine_steps.append({"action": "navigate",
                             "url": _build_url(base, substitute(flow.get("path") or "/",
                                                                variables), None)})
        meta.append({"implicit": True})
    for step in steps:
        step = dict(step)
        action = (step.get("action") or "").lower()
        info = {key: step.pop(key) for key in RUNNER_KEYS if key in step}
        if action in ("api", "http", "request", "navigate") and not step.get("url") \
                and step.get("path"):
            step["url"] = _build_url(base, step.pop("path"), info.get("params"))
        elif info.get("params") and step.get("url"):
            step["url"] = _build_url(base, step["url"], info["params"])
        if action in ("assert_screenshot", "screenshot_diff") and update_baselines:
            step["update_baseline"] = True
        if action == "assert_screenshot" and step.get("baseline_dir") and step.get("name"):
            info["baseline_file"] = str(Path(step["baseline_dir"]) / f"{step['name']}.png")
        engine_steps.append(step)
        meta.append(info)
    return engine_steps, meta


# --------------------------------------------------------------------------
# Running


def probe(base: str, path: str, timeout: float = 10.0) -> tuple:
    """GET ``base+path``; ``(True, status)`` when the target answers at all."""
    url = _build_url(base.rstrip("/"), path or "/", None)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return True, f"{url} → {resp.status}"
    except urllib.error.HTTPError as exc:
        return True, f"{url} → {exc.code}"
    except (OSError, ValueError) as exc:
        return False, f"{url} unreachable: {exc}"


def _trim(value: Any, limit: int = MAX_EVIDENCE_STRING) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f"… (+{len(value) - limit})"
    if isinstance(value, list):
        return [_trim(v, limit) for v in value[:50]]
    if isinstance(value, dict):
        return {k: _trim(v, limit) for k, v in value.items()}
    return value


def _step_record(raw: dict, info: dict, pack: dict, update_baselines: bool,
                 baseline_existed: Optional[bool]) -> dict:
    status = raw.get("status") or "unknown"
    record = {"i": raw.get("i"), "action": raw.get("action"), "status": status}
    for key in ("detail", "error", "reason"):
        if raw.get(key):
            record[key] = _trim(raw[key], 600)
    if raw.get("evidence"):
        record["evidence"] = _trim(raw["evidence"])
    if baseline_existed is False and not update_baselines and status == "passed":
        # A baseline the run had to create asserts nothing; a pack must ship it.
        record["status"] = "failed"
        record["error"] = ("baseline was missing (created now at "
                           f"{info.get('baseline_file')}); review it, commit it, "
                           "and re-run — run with --update-baselines to refresh")
    finding = info.get("finding")
    if finding:
        record["finding"] = finding
        record["finding_title"] = (pack.get("findings") or {}).get(finding, {}).get("title")
    return record


async def run_pack(pack: dict, pack_dir: Path, variables: dict, *, service=None,
                   flow_ids: Optional[list] = None, update_baselines: bool = False,
                   artifacts_dir: Optional[str] = None, skip_probe: bool = False,
                   context_options: Optional[dict] = None) -> dict:
    """Run every (selected) flow; returns the JSON report. Never raises for a
    failing flow — only :class:`PackError` when nothing could be run."""
    base = variables["base"]
    if not skip_probe:
        reachable, detail = probe(base, pack.get("probe") or "/")
        if not reachable:
            raise PackError(f"target not reachable: {detail}")
    flows = pack["flows"]
    if flow_ids:
        unknown = sorted(set(flow_ids) - {f["id"] for f in flows})
        if unknown:
            raise PackError(f"unknown flow id(s): {', '.join(unknown)}")
        flows = [f for f in flows if f["id"] in flow_ids]
    if service is None:
        from backend.modules.browser_agent_service import get_browser_agent_service
        service = get_browser_agent_service()

    artifacts_root = Path(artifacts_dir or Path.cwd() / "jambu-pack-artifacts"
                          / (pack.get("name") or Path(pack_dir).name))
    results = []
    for flow in flows:
        engine_steps, meta = prepare_flow(flow, variables,
                                          update_baselines=update_baselines)
        existed = [Path(m["baseline_file"]).is_file() if m.get("baseline_file") else None
                   for m in meta]
        flow_dir = artifacts_root / flow["id"]
        flow_dir.mkdir(parents=True, exist_ok=True)
        entry = {"id": flow["id"], "title": flow.get("title") or flow["id"]}
        try:
            report = await service.run_test(
                url=engine_steps[0].get("url") or base, steps=engine_steps, local=True,
                # A UI flow that clicks or types must declare it in the pack.
                approve=bool(flow.get("approve", False)),
                stop_on_failure=bool(flow.get("stop_on_failure", False)),
                artifacts_dir=str(flow_dir), context_options=context_options,
                freeze_animations=True,
            )
        except Exception as exc:  # harness error: the flow did not run
            entry.update(status="error", ok=False,
                         error=f"{type(exc).__name__}: {str(exc)[:500]}", steps=[])
            results.append(entry)
            continue
        raw_steps = report.get("steps") or []
        steps = []
        for idx, info in enumerate(meta):
            raw = raw_steps[idx] if idx < len(raw_steps) else {
                "i": idx, "action": engine_steps[idx].get("action"), "status": "not_run"}
            if info.get("implicit"):
                if raw.get("status") == "passed":
                    continue
            steps.append(_step_record(raw, info, pack, update_baselines, existed[idx]))
        ok = all(s["status"] == "passed" for s in steps)
        entry.update(status="passed" if ok else "failed", ok=ok, steps=steps,
                     artifacts_dir=str(flow_dir))
        if report.get("artifacts"):
            entry["artifacts"] = report["artifacts"]
        results.append(entry)
    return summarize(pack, base, results, update_baselines=update_baselines)


def summarize(pack: dict, base: str, flows: list, *, update_baselines: bool = False) -> dict:
    steps = [s for f in flows for s in f.get("steps", [])]
    failed = [s for s in steps if s["status"] != "passed"]
    findings = []
    for name, spec in (pack.get("findings") or {}).items():
        hits = [(f, s) for f in flows for s in f.get("steps", []) if s.get("finding") == name]
        if not hits:
            continue
        failing = [(f, s) for f, s in hits if s["status"] != "passed"]
        findings.append({
            "id": name, "title": spec.get("title"), "severity": spec.get("severity"),
            "status": "reproduced" if failing else "not_reproduced",
            "checks": [{"flow": f["id"], "step": s["i"], "status": s["status"],
                        "evidence": s.get("error") or s.get("detail")} for f, s in hits],
        })
    unexpected = [{"flow": f["id"], "step": s["i"], "action": s["action"],
                   "error": s.get("error")}
                  for f in flows for s in f.get("steps", [])
                  if s["status"] != "passed" and not s.get("finding")]
    errors = [f["id"] for f in flows if f.get("status") == "error"]
    return {
        "pack": pack.get("name"), "base": base, "ok": not failed and not errors,
        "update_baselines": update_baselines,
        "summary": {
            "flows": len(flows),
            "flows_passed": sum(1 for f in flows if f.get("ok")),
            "flow_errors": errors,
            "steps": len(steps), "steps_passed": len(steps) - len(failed),
            "steps_failed": len(failed),
            "findings_reproduced": sum(1 for f in findings if f["status"] == "reproduced"),
            "findings_not_reproduced": [f["id"] for f in findings
                                        if f["status"] == "not_reproduced"],
            "unexpected_failures": len(unexpected),
        },
        "findings": findings, "unexpected_failures": unexpected, "flows": flows,
    }


def exit_code(result: dict) -> int:
    if result["summary"]["flow_errors"]:
        return EXIT_ERROR
    return EXIT_OK if result["ok"] else EXIT_FAILED


def format_text(result: dict) -> str:
    """Human summary for the terminal (the JSON report holds the evidence)."""
    s = result["summary"]
    lines = [f"pack {result['pack']} @ {result['base']}: "
             f"{s['flows_passed']}/{s['flows']} flows, {s['steps_passed']}/{s['steps']} "
             f"steps passed"]
    for flow in result["flows"]:
        mark = {"passed": "PASS", "failed": "FAIL", "error": "ERR "}[flow["status"]]
        lines.append(f"  {mark} {flow['id']}: {flow['title']}")
        if flow.get("error"):
            lines.append(f"        {flow['error']}")
        for step in flow.get("steps", []):
            if step["status"] == "passed":
                continue
            tag = f" [{step['finding']}]" if step.get("finding") else ""
            lines.append(f"        step {step['i']} {step['action']}{tag}: "
                         f"{(step.get('error') or step.get('detail') or step['status'])[:220]}")
    if result["findings"]:
        lines.append("known findings:")
        for f in result["findings"]:
            lines.append(f"  {f['status']:<14} {f['id']}: {f['title']}")
    if s["unexpected_failures"]:
        lines.append(f"{s['unexpected_failures']} failure(s) not covered by a known finding")
    return "\n".join(lines)
