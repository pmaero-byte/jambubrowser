"""Managed QA cases, codegen round-trips and the dev-server loop.

Handlers call the shared transport and formatters as `core.<name>`, so a
test patches one object (`cli.jambu_cli.core`) no matter which command it
is exercising.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from cli.jambu_cli import core


def cmd_watch(args) -> int:
    """Rerun a flow whenever watched files change (dev-loop staple)."""
    import argparse as _argparse

    watch_paths = [args.flow] + list(args.watch_dir or [])
    test_args = _argparse.Namespace(
        url=args.url, flow=args.flow, local=args.local, approve=args.approve,
        stop_on_failure=args.stop_on_failure, trace=args.trace, har=args.har,
        video=args.video, resolve_sources=args.resolve_sources, json=args.json,
        forbid_evaluate=args.forbid_evaluate,
    )

    def run_once() -> int:
        # Reload the flow from disk each run so edits apply immediately.
        try:
            core._load_flow_file(args.flow)
        except Exception as exc:
            print(f"Could not read flow file {args.flow}: {exc}")
            return core.EXIT_ENGINE_ERROR
        print(f"--- jambu watch: {time.strftime('%H:%M:%S')} ---")
        return cmd_test(test_args)

    if args.once:
        return run_once()

    print(f"Watching {len(watch_paths)} path(s), every {args.interval:g}s "
          f"(Ctrl-C to stop).")
    last = core._snapshot_mtimes(watch_paths)
    code = run_once()
    if code not in (core.EXIT_OK, core.EXIT_GATE_FAILED):
        print("(engine unreachable — will keep retrying on change)")
    try:
        while True:
            time.sleep(args.interval)
            current = core._snapshot_mtimes(watch_paths)
            changed = core._changed_files(last, current)
            last = current
            if not changed:
                continue
            print(f"changed: {', '.join(changed[:5])}"
                  + (f" (+{len(changed) - 5} more)" if len(changed) > 5 else ""))
            cmd_test(test_args)
    except KeyboardInterrupt:
        print("\nStopped watching.")
    return core.EXIT_OK


def cmd_qa(args) -> int:
    """Managed QA cases: the AI QA team in the terminal.

    `jambu qa` has nine sub-subcommands, so this is a dispatch table rather
    than an if-chain: each verb is a `_qa_*` handler below, and adding one is
    a function plus an entry in `_qa_dispatch`. The fallback print lists the
    verbs, so an unknown one tells the user what exists.
    """
    sub = getattr(args, "qa_command", None)
    handler = _qa_dispatch(args).get(sub)
    if handler is None:
        print("jambu qa create|list|run|heals|accept|reject|quarantine|"
              "unquarantine|auto-retry|dataset --help")
        return core.EXIT_ENGINE_ERROR
    # accept/reject share one handler; the verb decides the boolean. Every
    # other handler takes `args` alone, so the kwarg is only passed where it
    # means something.
    if handler is _qa_review_heal:
        return handler(args, accept=(sub == "accept"))
    return handler(args)

# ── jambu qa sub-subcommands ────────────────────────────────────────
def _qa_dispatch(args) -> dict:
    """sub-subcommand -> handler. Built per call so tests can inspect it."""
    return {
        "create": _qa_create,
        "list": _qa_list,
        "run": _qa_run,
        "heals": _qa_heals,
        "accept": _qa_review_heal,
        "reject": _qa_review_heal,
        "quarantine": _qa_quarantine,
        "unquarantine": _qa_unquarantine,
        "auto-retry": _qa_auto_retry,
    }


def _qa_failed(result, prefix: str) -> bool:
    """True when the engine call failed; prints why. Keeps the error wording
    identical across handlers — a caller should not have to learn two
    vocabularies for "the engine said no"."""
    if result is None or "error" in (result or {}):
        print(f"{prefix}: {(result or {}).get('error', 'engine unreachable')}")
        return True
    return False

def _qa_create(args) -> int:
    """Create a QA case, optionally authoring steps from a goal."""

    flow: dict = {}
    if getattr(args, "flow", None):
        try:
            flow = core._load_flow_file(args.flow)
        except Exception as exc:
            print(f"Could not read flow file {args.flow}: {exc}")
            return core.EXIT_ENGINE_ERROR
    url = args.url or flow.get("url")
    if not url:
        print("Provide --url or include an 'url' in the flow file.")
        return core.EXIT_ENGINE_ERROR
    steps = flow.get("steps") or []
    if args.goal and not steps:
        # NL goal → template plan, no browser needed to author.
        planned = core.api_request("POST", "/browser/sessions/plan", {
            "url": url, "goal": args.goal,
            "kind": args.kind or None,
        })
        if planned is None or "error" in (planned or {}):
            print(f"Plan failed: {(planned or {}).get('error', 'engine unreachable')}")
            return core.EXIT_ENGINE_ERROR
        steps = planned.get("steps") or []
    if not steps:
        print("No steps: pass --goal to author from NL or --flow FILE.")
        return core.EXIT_ENGINE_ERROR
    result = core.api_request("POST", "/qa/cases", {
        "name": args.name, "url": url, "steps": steps,
        "goal": args.goal or "", "kind": args.kind or "smoke",
        "severity": args.severity, "owner": args.owner or "",
        "local": args.local, "dataset_id": args.dataset_id,
    })
    if result is None or "error" in (result or {}):
        print(f"Create failed: {(result or {}).get('error', 'engine unreachable')}")
        return core.EXIT_ENGINE_ERROR
    if result.get("placeholders"):
        print(f"  placeholders: {', '.join(result['placeholders'])}")
    print(f"QA case #{result['id']} '{result['name']}' "
          f"({result['kind']}, {len(result['steps'])} steps)")
    return core.EXIT_OK


def _qa_list(args) -> int:
    """List cases with their enabled flag and last verdict."""

    result = core.api_request("GET", "/qa/cases")
    if result is None:
        return core.EXIT_ENGINE_ERROR
    for case in result.get("cases") or []:
        mark = "on " if case.get("enabled") else "off"
        print(f"  #{case['id']} [{mark}] {case['name']} "
              f"({case['kind']}) — {case.get('last_status') or 'never run'}")
    print(f"{result.get('count', 0)} case(s)")
    return core.EXIT_OK


def _qa_run(args) -> int:
    """Run a case, optionally across dataset rows and viewports."""

    rows = None
    if getattr(args, "dataset_file", None):
        try:
            raw = Path(args.dataset_file).read_text()
            doc = json.loads(raw)
            rows = doc.get("rows", doc) if isinstance(doc, dict) else doc
            if not isinstance(rows, list):
                raise ValueError("expected a list or {'rows': [...]}")
        except Exception as exc:
            print(f"Could not read dataset file: {exc}")
            return core.EXIT_ENGINE_ERROR
    viewport_matrix = None
    if getattr(args, "viewports", None):
        viewport_matrix = []
        for spec in args.viewports:
            try:
                name, size = spec.split("=", 1)
                w, h = size.lower().split("x", 1)
                viewport_matrix.append({
                    "name": name.strip(),
                    "viewport": {"width": int(w), "height": int(h)},
                })
            except ValueError:
                print(f"Bad --viewport spec {spec!r} "
                      "(want NAME=WIDTHxHEIGHT, e.g. mobile=390x844)")
                return core.EXIT_ENGINE_ERROR
    result = core.api_request("POST", f"/qa/cases/{args.case_id}/run", {
        "local": args.local, "approve": args.approve,
        "stop_on_failure": args.stop_on_failure,
        "dataset_rows": rows, "junit": bool(args.junit_out),
        "viewport_matrix": viewport_matrix,
        "force": bool(getattr(args, "force", False)),
    })
    if result is None:
        return core.EXIT_ENGINE_ERROR
    if "error" in result:
        print(f"Run failed: {result['error']}")
        return core.EXIT_ENGINE_ERROR
    if result.get("matrix"):
        print(f"QA case #{result['case_id']} "
              f"{'PASS' if result.get('ok') else 'FAIL'} — "
              f"{result.get('passed_rows', 0)}/{result.get('rows', 0)} rows")
        for one in result.get("runs") or []:
            mark = "PASS" if one.get("ok") else "FAIL"
            print(f"  row {one.get('dataset_index')}: {mark} "
                  f"{one.get('passed', 0)}/{one.get('total', 0)} steps "
                  f"(run #{one.get('run_id')})")
            if one.get("unbound"):
                print(f"    unbound: {', '.join(one['unbound'])}")
        xml = result.get("junit")
        if xml and args.junit_out:
            Path(args.junit_out).write_text(xml)
            print(f"  junit: {args.junit_out}")
        if getattr(args, "sarif_out", None):
            sarif = core.api_request(
                "GET", f"/qa/cases/{args.case_id}/sarif?limit=1")
            if sarif is not None:
                Path(args.sarif_out).write_text(json.dumps(sarif))
                print(f"  sarif: {args.sarif_out}")
        return core.EXIT_OK if result.get("ok") else core.EXIT_GATE_FAILED
    status = "PASS" if result.get("ok") else "FAIL"
    if result.get("status") == "flaky":
        status = "FLAKY (green)"
    heals = result.get("healed_steps", 0)
    extra = f" (+{heals} healed)" if heals else ""
    print(f"QA case #{result['case_id']} {status} — "
          f"{result.get('passed', 0)}/{result.get('total', 0)} steps{extra} "
          f"(run #{result.get('run_id')})")
    if result.get("unbound"):
        print(f"  unbound placeholders: {', '.join(result['unbound'])}")
    health = result.get("health") or {}
    if health.get("action"):
        print(f"  health: {health['action']} — "
              f"{health.get('quarantine_reason') or 'green streak'}")
    if result.get("quarantine_reason"):
        print(f"  quarantined: {result['quarantine_reason']}")
    xml = result.get("junit")
    if xml:
        if args.junit_out == "-":
            print(xml)
        elif args.junit_out:
            Path(args.junit_out).write_text(xml)
            print(f"  junit: {args.junit_out}")
    for heal in result.get("heals") or []:
        print(f"  heal proposed #{heal['id']}: "
              f"'{heal['old_target']}' → '{heal['new_target']}' "
              f"(accept: jambu qa accept {heal['id']})")
    for step in result.get("failed_steps") or []:
        print(f"  FAIL #{step.get('i')} {step.get('action')} — "
              f"{step.get('reason')}: {step.get('error')}")
    return core.EXIT_OK if result.get("ok") else core.EXIT_GATE_FAILED


def _qa_heals(args) -> int:
    """List proposed selector heals."""

    result = core.api_request("GET", "/qa/heals?status=proposed")
    if result is None:
        return core.EXIT_ENGINE_ERROR
    heals = result.get("heals") or []
    if not heals:
        print("No proposed heals. Selectors are healthy.")
        return core.EXIT_OK
    for heal in heals:
        print(f"  #{heal['id']} case #{heal['case_id']} "
              f"step {heal['step_index']}: "
              f"'{heal['old_target']}' → '{heal['new_target']}'")
    return core.EXIT_OK


def _qa_review_heal(args, accept: bool = True) -> int:
    """Accept or reject a proposed heal.

    One handler for both verbs: the request body differs by a
    boolean, and keeping them together is what makes it obvious
    that they cannot drift apart."""

    result = core.api_request("POST", f"/qa/heals/{args.heal_id}", {
        "accept": accept, "actor": args.actor,
    })
    if result is None or "error" in (result or {}):
        print(f"Decide failed: {(result or {}).get('error', 'engine unreachable')}")
        return core.EXIT_ENGINE_ERROR
    print(f"Heal #{result['id']} {result['status']}.")
    return core.EXIT_OK


def _qa_quarantine(args) -> int:
    """Quarantine a flaky case so the gate stops trusting it."""

    result = core.api_request("POST", f"/qa/cases/{args.case_id}/quarantine", {
        "reason": args.reason, "actor": args.actor})
    if result is None or "error" in (result or {}):
        print(f"Quarantine failed: {(result or {}).get('error', 'engine unreachable')}")
        return core.EXIT_ENGINE_ERROR
    print(f"Case #{result['id']} quarantined "
          f"({result.get('quarantine_reason') or 'no reason'}).")
    return core.EXIT_OK


def _qa_unquarantine(args) -> int:
    """Release a quarantined case."""

    result = core.api_request("POST", f"/qa/cases/{args.case_id}/unquarantine",
                         {"reason": "", "actor": "qa-lead"})
    if result is None or "error" in (result or {}):
        print(f"Unquarantine failed: {(result or {}).get('error', 'engine unreachable')}")
        return core.EXIT_ENGINE_ERROR
    print(f"Case #{result['id']} unquarantined.")
    return core.EXIT_OK


def _qa_auto_retry(args) -> int:
    """Toggle per-case auto-retry."""

    result = core.api_request("POST", f"/qa/cases/{args.case_id}/auto-retry",
                         {"enabled": args.enabled})
    if result is None or "error" in (result or {}):
        print(f"Toggle failed: {(result or {}).get('error', 'engine unreachable')}")
        return core.EXIT_ENGINE_ERROR
    print(f"Case #{result['id']} auto-retry "
          f"{'on' if result.get('auto_retry') else 'off'}.")
    return core.EXIT_OK




def cmd_qa_dataset(args) -> int:
    """Datasets live under `jambu qa dataset ...`."""
    sub = getattr(args, "qa_dataset_command", None)
    if sub == "create":
        try:
            raw = Path(args.rows_file).read_text()
            doc = json.loads(raw)
            rows = doc.get("rows", doc) if isinstance(doc, dict) else doc
        except Exception as exc:
            print(f"Could not read rows file: {exc}")
            return core.EXIT_ENGINE_ERROR
        result = core.api_request("POST", "/qa/datasets",
                             {"name": args.name, "rows": rows})
        if result is None or "error" in (result or {}):
            print(f"Create failed: {(result or {}).get('error', 'engine unreachable')}")
            return core.EXIT_ENGINE_ERROR
        print(f"Dataset #{result['id']} '{result['name']}' "
              f"({len(result['rows'])} rows)")
        return core.EXIT_OK
    if sub == "list":
        result = core.api_request("GET", "/qa/datasets")
        if result is None:
            return core.EXIT_ENGINE_ERROR
        for dataset in result.get("datasets") or []:
            print(f"  #{dataset['id']} {dataset['name']} "
                  f"({dataset['row_count']} rows: "
                  f"{', '.join(dataset['columns'][:6])})")
        return core.EXIT_OK
    print("jambu qa dataset create|list --help")
    return core.EXIT_ENGINE_ERROR


def _check_engine_version() -> bool:
    """Refuse to run against an engine older than the CLI.

    The trap this guards: a stale engine is still listening on the default
    port (v3.3.0 next to a v3.4.0 checkout), and the fields this command
    emits (viewport, device, network_idle, storage_state) are silently
    ignored there, so every run "passes" against behaviour nobody verified.
    An engine *newer* than the CLI only gets a warning; a missing version
    (very old engine, or a test double) is tolerated rather than fatal.
    """
    try:
        from backend import __version__ as cli_version
    except Exception:
        return True  # CLI installed without the backend package: nothing to compare
    health = core.api_request("GET", "/health")
    if health is None:
        return False  # unreachable; api_request already printed why
    engine_version = str(health.get("version") or "")
    if not engine_version:
        return True
    def _parts(v: str) -> tuple:
        out = []
        for piece in v.split(".")[:3]:
            digits = "".join(ch for ch in piece if ch.isdigit())
            out.append(int(digits) if digits else 0)
        return tuple(out)
    if _parts(engine_version) < _parts(cli_version):
        print(f"\033[91mError: engine at {core.get_engine_url()} is v{engine_version}, "
              f"CLI is v{cli_version} — feature flags would be silently ignored.\033[0m")
        print("Start a current engine from this checkout: "
              ".venv/bin/python -m uvicorn backend.engine:app --port 8001")
        return False
    if _parts(engine_version) > _parts(cli_version):
        print(f"note: engine v{engine_version} is newer than CLI v{cli_version}")
    return True


def cmd_test(args) -> int:
    """Run a browser test flow (local dev friendly) and report pass/fail."""
    if not _check_engine_version():
        return core.EXIT_ENGINE_ERROR
    flow: dict = {}
    if getattr(args, "flow", None):
        try:
            flow = core._load_flow_file(args.flow)
        except Exception as exc:
            print(f"Could not read flow file {args.flow}: {exc}")
            return core.EXIT_ENGINE_ERROR
    url = args.url or flow.get("url")
    if not url:
        print("Provide --url or include an 'url' in the flow file.")
        return core.EXIT_ENGINE_ERROR
    steps = flow.get("steps") or []
    if args.url and steps and not any(s.get("action") == "navigate" for s in steps):
        steps = [{"action": "navigate", "url": args.url}] + steps
    payload = {
        "url": url,
        "steps": steps,
        "local": args.local,
        "approve": args.approve,
        "stop_on_failure": args.stop_on_failure,
        "trace": args.trace,
        "har": args.har,
        "video": args.video,
        "resolve_sources": args.resolve_sources,
        "forbid_evaluate": args.forbid_evaluate,
        "network": flow.get("network"),
    }
    # The engine honours both keys; dropping them here used to leave a flow's
    # onboarding/auth state unset (the classic symptom: a modal covering every
    # click) with no hint the CLI had silently ignored them.
    for key in ("storage_state", "context_options"):
        if flow.get(key) is not None:
            payload[key] = flow[key]
    if getattr(args, "storage_state_file", None):
        try:
            payload["storage_state"] = json.loads(Path(args.storage_state_file).read_text())
        except Exception as exc:
            print(f"Could not read storage state {args.storage_state_file}: {exc}")
            return core.EXIT_ENGINE_ERROR
    if getattr(args, "clock", ""):
        payload["clock"] = core._json_load_arg(args.clock)
    if getattr(args, "throttle", ""):
        payload["throttle"] = core._json_load_arg(args.throttle)
    if getattr(args, "coverage", False):
        payload["coverage"] = True
    # Geometry and scrubbing flags pass straight through: the flags are omitted
    # when unset so the engine's policy decides (fixed 1440x900 default; no
    # scrubbing for a local target), rather than the CLI hardcoding a default
    # that then differs from the API's.
    for flag in ("viewport", "device", "color_scheme", "reduced_motion",
                 "scrub_pii", "network_idle"):
        value = getattr(args, flag, None)
        if value is not None and value is not False:
            payload[flag] = value
    result = core.api_request("POST", "/browser/sessions/run", payload)
    if result is None:
        return core.EXIT_ENGINE_ERROR
    if "error" in result:
        print(f"Test flow failed: {result['error']}")
        return core.EXIT_ENGINE_ERROR

    if not args.json:
        _print_human_report(result)
    return _emit_json_and_exports(args, result)


def _print_human_report(result: dict) -> None:
    """The readable pass/fail report, printed only when --json is not set.

    ``--json`` means stdout is a single JSON document a pipeline can parse;
    mixing the prose report in front of it made that impossible without
    scraping.
    """
    status = "PASS" if result.get("ok") else "FAIL"
    print(f"Browser test {status} — {result.get('passed', 0)}/{result.get('total', 0)} steps "
          f"in {result.get('duration_ms', 0)}ms")
    print(f"  final: {result.get('title', '') or '(untitled)'} — {result.get('final_url', '')}")
    for step in result.get("steps") or []:
        mark = "ok " if step.get("status") == "passed" else "FAIL"
        line = f"  {mark} #{step.get('i')} {step.get('action')}"
        if step.get("detail"):
            line += f" — {step['detail']}"
        if step.get("status") == "failed":
            line += f" — {step.get('reason')}: {step.get('error')}"
            cause = step.get("failure_cause") or {}
            if cause.get("likely_cause"):
                line += f"\n         why: {cause['likely_cause']}"
            if cause.get("covered_by"):
                covering = cause["covered_by"]
                line += (f"\n         covered by: {covering.get('selector')}"
                         f" {covering.get('text', '')!r}")
            for hint in (cause.get("suggestions") or [])[:3]:
                line += f"\n         try: {hint}"
        print(line)
    for err in (result.get("console_errors") or [])[:5]:
        print(f"  console: {err[:160]}")
    for bad in (result.get("bad_responses") or [])[:5]:
        print(f"  http {bad.get('status')}: {bad.get('url', '')[:120]}")
    artifacts = result.get("artifacts") or {}
    if artifacts:
        print("  artifacts: " + ", ".join(f"{k}={v}" for k, v in artifacts.items()))
    cov = result.get("coverage") or {}
    if cov.get("supported") and cov.get("script_count"):
        print(f"  coverage: {cov.get('pct')}% of {cov.get('total_bytes')} JS bytes "
              f"across {cov['script_count']} script(s)")
    det = result.get("determinism") or {}
    if det:
        print(f"  determinism: {json.dumps(det)}")
    _print_failure_causes(result)
    for evaluated in (result.get("evaluated") or [])[:10]:
        print(f"  step #{evaluated.get('i')} evaluated: "
              f"{str(evaluated.get('value', ''))[:120]}")


def _print_failure_causes(result: dict) -> None:
    """Explain each failed step in DOM terms, above the raw JSON.

    A failed step used to report only a reason and a Playwright call log. The
    engine now also says whether the element was found, on screen, and what was
    covering it; printing it here answers the common "why did this time out?"
    without opening the JSON by hand.
    """
    for step in result.get("steps") or []:
        cause = step.get("failure_cause") or {}
        if not cause:
            continue
        bits = []
        if cause.get("found") is not None:
            bits.append("found" if cause["found"] else "NOT FOUND")
        if cause.get("enabled") is not None:
            bits.append("enabled" if cause["enabled"] else "DISABLED")
        if cause.get("in_viewport") is not None:
            bits.append("in viewport" if cause["in_viewport"] else "OFF-SCREEN")
        if cause.get("hidden_by_css"):
            bits.append(f"hidden by {cause['hidden_by_css']}")
        if cause.get("viewport") and not cause.get("in_viewport"):
            view, rect = cause["viewport"], cause.get("rect") or {}
            bits.append(f"element at y={rect.get('y')} in {view.get('width')}x"
                        f"{view.get('height')} viewport")
        if bits:
            print(f"  #{step.get('i')} {step.get('action')}: {', '.join(bits)}")


def _emit_json_and_exports(args, result: dict) -> None:
    """``--json`` plus the SARIF export, in one place.

    ``--json`` prints the report exactly as the API returned it. It used to be
    filtered down to a digest, which meant a dashboard consuming the CLI could
    not see the evidence a failure is diagnosed from and had to drive HTTP
    directly instead.
    """
    if args.json:
        print(json.dumps(result, indent=2))
    sarif_path = getattr(args, "sarif", None)
    if sarif_path:
        from backend.employees.flow_sarif import flow_report_to_sarif, flow_sarif_to_json

        core._write_export(
            sarif_path,
            flow_sarif_to_json(flow_report_to_sarif(result)),
            "SARIF",
        )
    return core.EXIT_OK if result.get("ok") else core.EXIT_GATE_FAILED


def cmd_export(args) -> int:
    """Export a flow to Playwright Test source (or JSON)."""
    try:
        flow = core._load_flow_file(args.flow)
    except Exception as exc:
        print(f"Could not read flow file {args.flow}: {exc}")
        return core.EXIT_ENGINE_ERROR
    steps = flow.get("steps") or []
    url = args.url or flow.get("url") or ""
    if args.json:
        print(json.dumps({"steps": steps}, indent=2))
        return core.EXIT_OK
    result = core.api_request("POST", "/browser/sessions/export", {
        "steps": steps, "name": args.name or "jambubrowser flow",
        "url": url, "base_url": args.base_url or "",
    })
    if result is None:
        return core.EXIT_ENGINE_ERROR
    if "error" in result:
        print(f"Export failed: {result['error']}")
        return core.EXIT_ENGINE_ERROR
    code = result.get("code", "")
    if args.out:
        Path(args.out).write_text(code)
        print(f"Wrote {args.out}")
    else:
        print(code)
    return core.EXIT_OK


def cmd_import(args) -> int:
    """Convert a Playwright .spec.ts into a Jambubrowser flow JSON."""
    try:
        code = Path(args.spec).read_text()
    except Exception as exc:
        print(f"Could not read {args.spec}: {exc}")
        return core.EXIT_ENGINE_ERROR
    result = core.api_request("POST", "/browser/sessions/import", {"code": code})
    if result is None or "error" in result:
        print(f"Import failed: {(result or {}).get('error', 'engine unreachable')}")
        return core.EXIT_ENGINE_ERROR
    doc = {"url": args.url or "", "steps": result.get("steps") or []}
    unparsed = result.get("unparsed") or []
    if args.out:
        Path(args.out).write_text(json.dumps(doc, indent=2))
        print(f"Wrote {args.out} ({len(doc['steps'])} steps)")
    else:
        print(json.dumps(doc, indent=2))
    if unparsed:
        print(f"unparsed lines: {len(unparsed)}")
        for item in unparsed[:10]:
            print(f"  line {item.get('line')}: {item.get('text')}")
    return core.EXIT_OK


def cmd_plan(args) -> int:
    """Propose a test flow from a natural-language goal."""
    result = core.api_request("POST", "/browser/sessions/plan", {
        "url": args.url, "goal": " ".join(args.goal),
        "kind": args.kind or None, "use_llm": args.use_llm,
    })
    if result is None:
        return core.EXIT_ENGINE_ERROR
    if "error" in result:
        print(f"Plan failed: {result['error']}")
        return core.EXIT_ENGINE_ERROR
    print(f"# {result.get('kind')} plan ({result.get('source')})")
    if result.get("placeholders"):
        print(f"fill: {', '.join(result['placeholders'])}")
    print(json.dumps(result.get("steps") or [], indent=2))
    return core.EXIT_OK


def cmd_record(args) -> int:
    """Start/stop recording a session's actions into a reusable flow."""
    session_id = args.session
    if args.stop:
        result = core.api_request("POST", f"/browser/sessions/{session_id}/record",
                             {"active": False})
        if result is None or "error" in result:
            print(f"Stop recording failed: {(result or {}).get('error', 'engine unreachable')}")
            return core.EXIT_ENGINE_ERROR
        steps = result.get("steps") or []
        doc = {"url": args.url or "", "steps": steps}
        if args.out:
            Path(args.out).write_text(json.dumps(doc, indent=2))
            print(f"Wrote {args.out} ({len(steps)} steps)")
        else:
            print(json.dumps(doc, indent=2))
        return core.EXIT_OK

    result = core.api_request("POST", f"/browser/sessions/{session_id}/record",
                         {"active": True})
    if result is None or "error" in result:
        print(f"Start recording failed: {(result or {}).get('error', 'engine unreachable')}")
        return core.EXIT_ENGINE_ERROR
    print(f"Recording session {session_id}. Drive the browser, then save with:")
    print(f"  jambu record --session {session_id} --stop --out flow.json")
    return core.EXIT_OK


def cmd_dev_servers(args) -> int:
    """Scan common ports for a running local dev server."""
    from urllib.parse import quote

    result = core.api_request("GET", f"/browser/dev-servers?host={quote(args.host)}")
    if result is None or "error" in result:
        print(f"Scan failed: {(result or {}).get('error', 'engine unreachable')}")
        return core.EXIT_ENGINE_ERROR
    servers = result.get("servers") or []
    if not servers:
        print(f"No dev servers found on {args.host} (scanned common ports).")
        return core.EXIT_GATE_FAILED
    for server in servers:
        print(f"  :{server.get('port')}  {server.get('framework') or 'unknown'}  "
              f"{server.get('title') or server.get('url')}")
    return core.EXIT_OK


def register(subparsers) -> None:
    """Declare the QA/test surface: managed cases, codegen round-trips,
    datasets and the watch/dev-server loops."""
    p_test = subparsers.add_parser(
        "test", help="Run a browser test flow (local-dev friendly)",
    )
    p_test.add_argument("flow", nargs="?", help="Flow JSON file")
    p_test.add_argument("--url", help="App URL (or from the flow file)")
    p_test.add_argument("--local", action="store_true",
                        help="Allow localhost/private hosts")
    p_test.add_argument("--approve", action="store_true",
                        help="Approve risky/input actions")
    p_test.add_argument("--stop-on-failure", dest="stop_on_failure",
                        action="store_true")
    p_test.add_argument("--trace", action="store_true", help="Capture a trace")
    p_test.add_argument("--har", action="store_true", help="Capture a HAR")
    p_test.add_argument("--video", action="store_true", help="Record video")
    p_test.add_argument("--resolve-sources", dest="resolve_sources",
                        action="store_true", help="Map console errors via source maps")
    p_test.add_argument("--forbid-evaluate", dest="forbid_evaluate",
                        action="store_true",
                        help="Refuse JS-dependent evaluate steps")
    p_test.add_argument("--viewport", metavar="WxH",
                        help="Viewport for this run, e.g. 390x844 (default 1440x900)")
    p_test.add_argument("--device", metavar="NAME",
                        help="Device preset: mobile, tablet, laptop, desktop, "
                             "iphone_13, pixel_5")
    p_test.add_argument("--color-scheme", dest="color_scheme",
                        choices=("light", "dark", "no-preference", "null"),
                        help="Emulate a colour scheme")
    p_test.add_argument("--reduced-motion", dest="reduced_motion",
                        choices=("reduce", "no-preference", "null"),
                        help="Emulate a motion preference")
    p_test.add_argument("--no-scrub", dest="scrub_pii", action="store_false",
                        default=None,
                        help="Return page content unmasked (default for local targets)")
    p_test.add_argument("--scrub", dest="scrub_pii", action="store_true",
                        default=None,
                        help="Mask PII in returned content (default for public hosts)")
    p_test.add_argument("--network-idle", dest="network_idle",
                        action="store_true",
                        help="Wait for requests to go quiet after each step")
    p_test.add_argument("--json", action="store_true", help="Print the full report JSON")
    p_test.add_argument("--sarif", metavar="FILE",
                        help="Write a per-step SARIF 2.1.0 report (use '-' for stdout)")
    p_test.add_argument("--clock", default="",
                        help="JSON clock spec, e.g. '{\"time\":\"2026-01-01T09:00:00Z\",\"rate\":0}'")
    p_test.add_argument("--throttle", default="",
                        help="JSON network shaping, e.g. '{\"offline\":true}' or '{\"download_kbps\":400}'")
    p_test.add_argument("--coverage", action="store_true",
                        help="Capture JS coverage and report used bytes per script")
    p_test.add_argument("--storage-state", dest="storage_state_file",
                        metavar="FILE",
                        help="Playwright storage_state JSON (cookies/localStorage) "
                             "to seed the session; overrides the flow file's key")

    p_export = subparsers.add_parser(
        "export", help="Export a flow to Playwright Test (.spec.ts)",
    )
    p_export.add_argument("flow", help="Flow JSON file")
    p_export.add_argument("--name", help="Test name")
    p_export.add_argument("--url", help="Original entry URL (kept as a comment)")
    p_export.add_argument("--base-url", dest="base_url", help="Playwright baseURL")
    p_export.add_argument("--out", help="Output .spec.ts path")
    p_export.add_argument("--json", action="store_true",
                          help="Emit normalised flow JSON instead")

    p_import = subparsers.add_parser(
        "import", help="Convert a Playwright .spec.ts into a flow JSON",
    )
    p_import.add_argument("spec", help="Playwright .spec.ts file")
    p_import.add_argument("--out", help="Output flow JSON path")
    p_import.add_argument("--url", help="Entry URL to store with the flow")

    p_plan = subparsers.add_parser(
        "plan", help="Propose a test flow from a natural-language goal",
    )
    p_plan.add_argument("goal", nargs="+", help="e.g. test login")
    p_plan.add_argument("--url", required=True, help="App URL")
    p_plan.add_argument("--kind",
                        help="smoke|login|signup|checkout|search|accessibility|performance|responsive")
    p_plan.add_argument("--use-llm", dest="use_llm", action="store_true")

    p_record = subparsers.add_parser(
        "record", help="Record a session's actions into a reusable flow",
    )
    p_record.add_argument("--session", required=True, help="Browser session id")
    p_record.add_argument("--stop", action="store_true",
                          help="Stop recording and save the flow")
    p_record.add_argument("--out", help="Output flow JSON path")
    p_record.add_argument("--url", help="Entry URL to store with the flow")

    p_watch = subparsers.add_parser(
        "watch", help="Rerun a flow whenever watched files change",
    )
    p_watch.add_argument("flow", help="Flow JSON file")
    p_watch.add_argument("--url", help="App URL (or from the flow file)")
    p_watch.add_argument("--watch-dir", dest="watch_dir", action="append",
                         default=[],
                         help="Extra dir to watch (repeatable; default: the flow file)")
    p_watch.add_argument("--interval", type=float, default=2.0,
                         help="Poll interval in seconds (default 2)")
    p_watch.add_argument("--local", action="store_true",
                         help="Allow localhost/private hosts")
    p_watch.add_argument("--approve", action="store_true",
                         help="Approve risky/input actions")
    p_watch.add_argument("--stop-on-failure", dest="stop_on_failure",
                         action="store_true")
    p_watch.add_argument("--trace", action="store_true")
    p_watch.add_argument("--har", action="store_true")
    p_watch.add_argument("--video", action="store_true")
    p_watch.add_argument("--resolve-sources", dest="resolve_sources",
                         action="store_true")
    p_watch.add_argument("--forbid-evaluate", dest="forbid_evaluate",
                         action="store_true",
                         help="Refuse JS-dependent evaluate steps")
    p_watch.add_argument("--json", action="store_true",
                         help="Print the full report JSON each run")
    p_watch.add_argument("--once", action="store_true",
                         help="Run once and exit (no watching)")

    p_devs = subparsers.add_parser(
        "dev-servers", help="Scan for a running local dev server",
    )
    p_devs.add_argument("--host", default="127.0.0.1")

    p_qa = subparsers.add_parser(
        "qa", help="Managed QA cases (create, run, heal)",
    )
    q_sub = p_qa.add_subparsers(dest="qa_command")

    p_q_create = q_sub.add_parser("create", help="Create a case")
    p_q_create.add_argument("--name", required=True)
    p_q_create.add_argument("--url", required=True)
    p_q_create.add_argument("--goal", default="",
                            help="NL goal, e.g. 'test login'")
    p_q_create.add_argument("--flow", help="Flow JSON file")
    p_q_create.add_argument("--kind", default="smoke")
    p_q_create.add_argument("--severity", default="medium",
                            choices=["critical", "high", "medium",
                                     "low", "info"])
    p_q_create.add_argument("--owner", default="")
    p_q_create.add_argument("--local", action="store_true",
                            help="Allow localhost/private hosts")
    p_q_create.add_argument("--dataset-id", dest="dataset_id", type=int,
                            default=None,
                            help="Attach a stored dataset (jambu qa dataset)")

    q_sub.add_parser("list", help="List cases")

    p_q_run = q_sub.add_parser("run", help="Run a case")
    p_q_run.add_argument("case_id", type=int)
    p_q_run.add_argument("--local", action="store_true")
    p_q_run.add_argument("--approve", action="store_true")
    p_q_run.add_argument("--stop-on-failure", dest="stop_on_failure",
                         action="store_true")
    p_q_run.add_argument("--dataset-file", dest="dataset_file",
                         default=None,
                         help="JSON list (or {'rows': [...]}) of datasets")
    p_q_run.add_argument("--junit", dest="junit_out", metavar="FILE",
                         default=None,
                         help="Write JUnit XML for CI ('-' = stdout)")
    p_q_run.add_argument("--sarif", dest="sarif_out", metavar="FILE",
                         default=None,
                         help="Write SARIF 2.1.0 for code scanning")
    p_q_run.add_argument("--viewport", dest="viewports", metavar="NAME=WxH",
                         action="append", default=None,
                         help="Viewport variant, repeatable "
                              "(e.g. --viewport desktop=1280x800 "
                              "--viewport mobile=390x844)")
    p_q_run.add_argument("--force", action="store_true",
                         help="Run even if the case is quarantined")

    q_sub.add_parser("heals", help="Show proposed heals")

    for verb in ("accept", "reject"):
        p_q_decide = q_sub.add_parser(verb, help=f"{verb} a heal")
        p_q_decide.add_argument("heal_id", type=int)
        p_q_decide.add_argument("--actor", default="qa-lead")

    for verb, enabled in (("quarantine", None), ("unquarantine", None)):
        p_q_q = q_sub.add_parser(verb, help=f"{verb} a case")
        p_q_q.add_argument("case_id", type=int)
        if verb == "quarantine":
            p_q_q.add_argument("--reason", default="")
            p_q_q.add_argument("--actor", default="qa-lead")

    p_q_retry = q_sub.add_parser("auto-retry",
                                 help="Toggle the flake auto-retry")
    p_q_retry.add_argument("case_id", type=int)
    p_q_retry.add_argument("--off", dest="enabled", action="store_false")

    p_q_ds = q_sub.add_parser("dataset", help="Manage datasets")
    ds_sub = p_q_ds.add_subparsers(dest="qa_dataset_command")

    p_q_ds_create = ds_sub.add_parser("create", help="Create a dataset")
    p_q_ds_create.add_argument("--name", required=True)
    p_q_ds_create.add_argument("rows_file",
                               help="JSON rows file (list or {'rows': [...]})")
    ds_sub.add_parser("list", help="List datasets")
