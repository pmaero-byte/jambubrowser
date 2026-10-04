"""Browser flow testing & codegen MCP tools.

Token-efficient flow execution, planning and codegen.

Tools are plain async functions here; ``register(mcp)`` applies FastMCP's
decorator to each one, so a tool can be read, imported and unit-tested
without an MCP server in the loop.
"""
from __future__ import annotations

import json
from backend.mcp_tools import _shared


def _render_flow(result: dict) -> str:
    """Compact pass/fail digest of a flow report (token-lean)."""
    import json as _json

    icon = "PASS" if result.get("ok") else "FAIL"
    tokens = result.get("tokens_estimate")
    if tokens is None:
        try:
            tokens = max(1, len(_json.dumps(result, separators=(",", ":"))) // 4)
        except Exception:
            tokens = None
    lines = [
        f"# Browser test {icon} — {result.get('passed', 0)}/{result.get('total', 0)} steps "
        f"in {result.get('duration_ms', 0)}ms",
        f"final: {result.get('title', '') or '(untitled)'} — {result.get('final_url', '')}",
    ]
    if tokens is not None:
        lines[1] += f"\n~{tokens} tokens" + (" · uses JS evaluate" if result.get("uses_evaluate") else "")
    det = result.get("determinism") or {}
    if det:
        bits = []
        if det.get("clock"):
            bits.append(f"clock={_json.dumps(det['clock'])}")
        if det.get("throttle"):
            bits.append(f"throttle={_json.dumps(det['throttle'])}")
        if bits:
            lines[2] = lines[2] + "\ndeterminism: " + " · ".join(bits)
    cov = result.get("coverage") or {}
    if cov.get("supported") and cov.get("script_count"):
        lines.append(
            f"coverage: {cov.get('pct', 0)}% of {cov.get('total_bytes', 0)} JS bytes "
            f"across {cov['script_count']} script(s)"
        )
    for step in result.get("steps") or []:
        mark = "ok " if step.get("status") == "passed" else "FAIL"
        bit = f"{mark} #{step.get('i')} {step.get('action')}"
        if step.get("detail"):
            bit += f" — {step['detail']}"
        if step.get("status") == "failed":
            bit += f" — {step.get('reason')}: {step.get('error')}"
        lines.append(bit)
        for cand in step.get("candidates") or []:
            lines.append(f"      candidate {cand.get('ref')}: {cand.get('name')}")
        cause = step.get("cause") or {}
        if cause:
            parts = []
            if cause.get("dom"):
                d = cause["dom"]
                parts.append(f"dom +{d.get('added', 0)}/-{d.get('removed', 0)}/~{d.get('changed', 0)}")
            if cause.get("failed_requests"):
                parts.append(f"{len(cause['failed_requests'])} failed req")
            if cause.get("console_errors"):
                parts.append(f"{len(cause['console_errors'])} console error")
            if parts:
                lines.append(f"      cause: {'; '.join(parts)}")
    mapped = result.get("console_errors_source") or []
    for item in mapped[:5]:
        if item.get("source"):
            lines.append(f"console {item['source']}:{item.get('source_line')} — {item.get('text', '')[:120]}")
    errors = result.get("console_errors") or []
    if errors:
        lines.append(f"console errors ({len(errors)}):")
        lines.extend(f"  - {e[:160]}" for e in errors[:5])
    failed = result.get("failed_requests") or []
    if failed:
        lines.append(f"failed requests ({len(failed)}):")
        lines.extend(f"  - {r.get('method')} {r.get('url', '')[:110]} — {r.get('failure', '')[:70]}"
                     for r in failed[:5])
    bad = result.get("bad_responses") or []
    if bad:
        lines.append(f"HTTP >=400 ({len(bad)}):")
        lines.extend(f"  - {r.get('status')} {r.get('method')} {r.get('url', '')[:110]}"
                     for r in bad[:5])
    artifacts = result.get("artifacts") or {}
    if artifacts:
        lines.append("artifacts: " + ", ".join(f"{k}={v}" for k, v in artifacts.items()))
    return "\n".join(lines)


async def browser_test_flow(url: str, steps: str = "[]", allow_domains: str = "",
                            local: bool = False, approve: bool = False,
                            stop_on_failure: bool = False,
                            network: str = "", trace: bool = False,
                            har: bool = False, video: bool = False,
                            resolve_sources: bool = False,
                            storage_state: str = "",
                            forbid_evaluate: bool = False,
                            clock: str = "", throttle: str = "",
                            coverage: bool = False) -> str:
    """
    Test a web app end-to-end in ONE call: opens a browser session, runs a
    declarative step list (navigate / click / type / press / wait / assert_*),
    and returns a compact pass/fail report with console errors and failed
    requests already attached. Prefer this over open→snapshot→act loops to
    save tool calls.

    Set local=true for localhost / private dev servers (e.g. http://localhost:3000).

    Args:
        url: Starting URL (also the default allowlist host), e.g. http://localhost:3000
        steps: JSON array of step objects. Actions: navigate{url}, click{target|ref},
            type{target|ref,value}, press{key,target?}, hover, select{value},
            check/uncheck, reload, back, forward, wait{selector|text|url_contains},
            screenshot, assert_visible/assert_not_visible/assert_text{value}/
            assert_text_equals/assert_value/assert_url/assert_title/assert_count/
            assert_checked/assert_unchecked/assert_enabled/assert_disabled/
            assert_console_clean/assert_no_failed_requests/assert_no_a11y_violations/
            assert_lcp/assert_fcp/assert_load/assert_dom_nodes/assert_transfer_kb/
            assert_made_request{value}/assert_no_request{value}.
            'target' matches element text by exact name, unique substring, or "role name".
        allow_domains: Optional comma-separated allowlist (defaults to url host)
        local: Allow loopback/private hosts (local dev testing)
        approve: Approve risky/input actions for every step (delete/pay/send…)
        stop_on_failure: Stop at the first failed step
        network: Optional JSON request-interception policy:
            {"mocks":[{"url":"**/api/user","json":{...},"status":200}],
             "fail":["**/analytics/**"],
             "delay":[{"url":"**/slow","ms":3000}],"offline":false}
        trace: Capture a Playwright trace artifact (screenshots+snapshots)
        har: Capture a HAR network archive
        video: Capture a video recording
        resolve_sources: Map console errors through source maps to original files
        storage_state: Optional JSON storage state ({cookies,origins}) to seed auth
        forbid_evaluate: Refuse JS-dependent evaluate steps (evaluate-free coverage)
        clock: Optional JSON to make time deterministic, e.g.
            {"time":"2026-01-01T09:00:00Z","rate":0} freezes the page clock
        throttle: Optional JSON network shaping (Chromium/CDP), e.g.
            {"offline":true} or {"download_kbps":400,"latency_ms":300}
        coverage: Capture JS coverage and summarise used bytes per script
    """
    import json as _json

    def _load(raw, default):
        if not raw:
            return default
        try:
            return _json.loads(raw) if isinstance(raw, str) else raw
        except _json.JSONDecodeError:
            return default

    try:
        parsed = _json.loads(steps) if isinstance(steps, str) else steps
    except _json.JSONDecodeError as exc:
        return f"steps is not valid JSON: {exc}"
    domains = [d.strip() for d in (allow_domains or "").split(",") if d.strip()]
    result = await _shared.call_engine("POST", "/browser/sessions/run", {
        "url": url, "steps": parsed or [], "allow_domains": domains,
        "local": local, "approve": approve, "stop_on_failure": stop_on_failure,
        "network": _load(network, None), "trace": trace, "har": har, "video": video,
        "resolve_sources": resolve_sources, "storage_state": _load(storage_state, None),
        "forbid_evaluate": forbid_evaluate,
        "clock": _load(clock, None), "throttle": _load(throttle, None),
        "coverage": coverage,
    }, timeout=300.0)
    if "error" in result:
        return f"Test flow failed: {result['error']}"
    return _render_flow(result)


async def browser_session_run(session_id: str, steps: str,
                              approve: bool = False,
                              stop_on_failure: bool = False,
                              network: str = "",
                              resolve_sources: bool = False,
                              forbid_evaluate: bool = False) -> str:
    """
    Run a declarative step flow against an existing browser session and return
    a compact pass/fail report (one call instead of many snapshot/act calls).

    Args:
        session_id: Session from browser_session_open
        steps: JSON array of step objects (see browser_test_flow for actions)
        approve: Approve risky/input actions for every step
        stop_on_failure: Stop at the first failed step
        network: Optional JSON request-interception policy (see browser_test_flow)
        resolve_sources: Map console errors through source maps
        forbid_evaluate: Refuse JS-dependent evaluate steps
    """
    import json as _json

    try:
        parsed = _json.loads(steps) if isinstance(steps, str) else steps
        net = _json.loads(network) if network else None
    except _json.JSONDecodeError as exc:
        return f"steps/network is not valid JSON: {exc}"
    result = await _shared.call_engine("POST", f"/browser/sessions/{session_id}/run", {
        "steps": parsed or [], "approve": approve,
        "stop_on_failure": stop_on_failure, "network": net,
        "resolve_sources": resolve_sources, "forbid_evaluate": forbid_evaluate,
    }, timeout=300.0)
    if "error" in result:
        return f"Flow failed: {result['error']}"
    return _render_flow(result)


async def browser_test_plan(url: str, goal: str, kind: str = "",
                            use_llm: bool = False) -> str:
    """
    Author a browser test flow from a natural-language goal (does NOT run it).
    Returns ready-to-run steps for browser_test_flow. Use this to turn
    "test login and the dashboard" into a concrete flow, then run it.

    Args:
        url: App URL, e.g. http://localhost:3000
        goal: What to test, e.g. "test login with a valid user"
        kind: Force a template: smoke|login|signup|checkout|search|accessibility|performance|responsive
        use_llm: Refine the plan with the configured LLM (default: template only)
    """
    result = await _shared.call_engine("POST", "/browser/sessions/plan", {
        "url": url, "goal": goal, "kind": kind or None, "use_llm": use_llm,
    }, timeout=120.0)
    if "error" in result:
        return f"Plan failed: {result['error']}"
    lines = [
        f"# Test plan — {result.get('kind')} ({result.get('source')})",
        f"goal: {goal or '(none)'}",
    ]
    if result.get("placeholders"):
        lines.append(f"placeholders to fill: {', '.join(result['placeholders'])}")
    lines.append("steps:")
    lines.append(json.dumps(result.get("steps") or [], indent=1))
    lines.append(f"note: {result.get('notes', '')}")
    return "\n".join(lines)


async def browser_test_matrix(url: str, steps: str, matrix: str = "",
                              local: bool = False, approve: bool = False,
                              network: str = "") -> str:
    """
    Run the same test flow across viewports/locales concurrently (responsive
    and cross-locale checks) in ONE call, returning a per-variant digest.

    Args:
        url: App URL
        steps: JSON array of step objects (see browser_test_flow)
        matrix: Optional JSON array of variants, e.g.
            [{"name":"desktop","viewport":{"width":1280,"height":800}},
             {"name":"mobile","viewport":{"width":390,"height":844},"locale":"en-GB"}]
            Defaults to desktop + mobile.
        local: Allow loopback/private hosts
        approve: Approve risky/input actions
        network: Optional JSON request-interception policy
    """
    import json as _json

    try:
        parsed = _json.loads(steps) if isinstance(steps, str) else steps
        variants = _json.loads(matrix) if matrix else None
        net = _json.loads(network) if network else None
    except _json.JSONDecodeError as exc:
        return f"steps/matrix/network is not valid JSON: {exc}"
    result = await _shared.call_engine("POST", "/browser/sessions/matrix", {
        "url": url, "steps": parsed or [], "matrix": variants,
        "local": local, "approve": approve, "network": net,
    }, timeout=600.0)
    if "error" in result:
        return f"Matrix run failed: {result['error']}"
    lines = [
        f"# Matrix {'PASS' if result.get('ok') else 'FAIL'} — "
        f"{result.get('summary', {}).get('passed')}/{result.get('summary', {}).get('variants')} variants",
    ]
    for v in result.get("variants") or []:
        mark = "ok " if v.get("ok") else "FAIL"
        bit = f"{mark} {v.get('variant')}: {v.get('passed')}/{v.get('total')} steps"
        if v.get("error"):
            bit += f" — {v['error']}"
        lines.append(bit)
        for fs in v.get("failed_steps") or []:
            lines.append(f"      #{fs.get('i')} {fs.get('action')}: {fs.get('reason')} {fs.get('error', '')[:80]}")
        if v.get("console_errors"):
            lines.append(f"      console errors: {len(v['console_errors'])}")
    return "\n".join(lines)


async def browser_export_playwright(steps: str, name: str = "jambubrowser flow",
                                    base_url: str = "") -> str:
    """
    Export a declarative flow as a Playwright Test (.spec.ts) file, so it can
    run in the developer's own CI without lock-in.

    Args:
        steps: JSON array of step objects (as used by browser_test_flow)
        name: Test name
        base_url: Optional Playwright baseURL
    """
    import json as _json

    try:
        parsed = _json.loads(steps) if isinstance(steps, str) else steps
    except _json.JSONDecodeError as exc:
        return f"steps is not valid JSON: {exc}"
    result = await _shared.call_engine("POST", "/browser/sessions/export", {
        "steps": parsed or [], "name": name, "base_url": base_url,
    }, timeout=60.0)
    if "error" in result:
        return f"Export failed: {result['error']}"
    return result.get("code", "")


async def browser_import_playwright(code: str) -> str:
    """
    Convert a Playwright Test (.spec.ts) source into a declarative flow for
    browser_test_flow. Translates the common getBy/keyboard/expect subset,
    beforeEach setup, test.use baseURL/storageState, test.each data variants,
    and page.route() policies; every line it cannot translate is reported so
    you know what needs a hand.

    Args:
        code: Playwright Test source text
    """
    result = await _shared.call_engine("POST", "/browser/sessions/import", {
        "code": code,
    }, timeout=60.0)
    if "error" in result:
        return f"Import failed: {result['error']}"
    lines = [f"# Imported {result.get('count', 0)} step(s)"]
    variants = result.get("variants") or []
    if variants:
        lines.append(f"data variants ({len(variants)}): " + ", ".join(
            str(v.get("case")) for v in variants[:6]))
        lines.append("run a variant's own steps array to execute that case")
    network = result.get("network") or {}
    if network.get("mocks") or network.get("fail"):
        lines.append(
            f"network policy: {len(network.get('mocks', []))} mock(s), "
            f"{len(network.get('fail', []))} abort(s) "
            "(pass as `network` to browser_test_flow)"
        )
    unparsed = result.get("unparsed") or []
    if unparsed:
        lines.append(f"unparsed lines ({len(unparsed)}):")
        lines.extend(f"  - line {u.get('line')}: {u.get('text', '')[:120]}" for u in unparsed[:8])
    lines.append(json.dumps(result.get("steps") or [], indent=1))
    return "\n".join(lines)


async def browser_task(url: str, goal: str, inputs: str = "{}",
                       local: bool = True, approve: bool = False) -> str:
    """
    One-call meta-tool: turn a goal into a test flow and run it. Plans the
    flow (template/LLM), substitutes any {{placeholders}} from `inputs`, then
    executes it and returns the pass/fail report. Use this when you don't
    want to author steps yourself.

    Args:
        url: App URL, e.g. http://localhost:3000
        goal: What to test, e.g. "test login with a valid user"
        inputs: JSON object filling placeholders, e.g. {"email":"a@b.com","password":"..."}
        local: Allow loopback/private hosts (default true)
        approve: Approve risky/input actions for every step
    """
    import json as _json

    try:
        values = _json.loads(inputs) if isinstance(inputs, str) else (inputs or {})
    except _json.JSONDecodeError as exc:
        return f"inputs is not valid JSON: {exc}"

    plan = await _shared.call_engine("POST", "/browser/sessions/plan", {
        "url": url, "goal": goal,
    }, timeout=120.0)
    if "error" in plan:
        return f"Plan failed: {plan['error']}"

    steps = plan.get("steps") or []
    missing = []
    for step in steps:
        for key, val in list(step.items()):
            if isinstance(val, str) and "{{" in val:
                for name, replacement in values.items():
                    val = val.replace("{{" + name + "}}", str(replacement))
                step[key] = val
                for ph in plan.get("placeholders") or []:
                    if "{{" + ph + "}}" in val:
                        missing.append(ph)
    missing = sorted(set(missing))
    if missing:
        return (
            f"# Plan ready ({plan.get('kind')}) — needs inputs: {', '.join(missing)}\n"
            f"Call browser_task again with inputs={{\"{missing[0]}\": \"...\"}}\n\n"
            + _json.dumps(steps, indent=1)
        )

    result = await _shared.call_engine("POST", "/browser/sessions/run", {
        "url": url, "steps": steps, "local": local, "approve": approve,
    }, timeout=300.0)
    if "error" in result:
        return f"Run failed: {result['error']}"
    return f"(planned: {plan.get('kind')})\n" + _render_flow(result)



def register(mcp) -> None:
    """Register all 7 browser_testing tools with the server."""
    mcp.tool()(browser_test_flow)
    mcp.tool()(browser_session_run)
    mcp.tool()(browser_test_plan)
    mcp.tool()(browser_test_matrix)
    mcp.tool()(browser_export_playwright)
    mcp.tool()(browser_import_playwright)
    mcp.tool()(browser_task)

    # Helpers in this module are not tools: they render engine
    # output for a tool body and must stay undecorated.
