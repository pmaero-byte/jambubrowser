"""Assertion engine for browser test flows.

Extracted from ``browser_agent.py`` so the assertion vocabulary has one
home. The dispatch below is a linear chain because assertion *ordering is
semantics*: selector probes beat page-state probes beat API probes, and a
new kind must not shadow an existing one by accident.

Contract for every handler: return ``(ok, detail)``. ``detail`` is written
into the step result and shown to the caller, so it must explain *what was
compared*, not just pass/fail — a failing assertion nobody can debug is
worse than no assertion.

To add a kind: extend the appropriate helper (``assert_selector`` for CSS /
XPath probes, ``evaluate_assert`` for page/API state) and keep the
"unsupported assertion kind" refusal as the fall-through.
"""

from __future__ import annotations

from typing import Any, Optional

from backend.modules.browser_agent_errors import SessionRefused


def json_path(payload: Any, path: str) -> Any:
    """Tiny JSON-path reader for API assertions: ``a.b[0].c`` style.

    Deliberately minimal (no wildcards/filters): QA assertions should be
    readable, and anything fancier belongs in a real schema check.
    """
    if not path:
        return payload
    node = payload
    token = ""
    i = 0
    parts: list[Any] = []
    while i < len(path):
        ch = path[i]
        if ch == ".":
            if token:
                parts.append(token)
                token = ""
        elif ch == "[":
            if token:
                parts.append(token)
                token = ""
            end = path.find("]", i)
            if end < 0:
                return None
            index = path[i + 1:end].strip().strip("'\"")
            parts.append(int(index) if index.isdigit() else index)
            i = end
        else:
            token += ch
        i += 1
    if token:
        parts.append(token)
    for part in parts:
        if isinstance(part, int):
            if not isinstance(node, list) or part >= len(node):
                return None
            node = node[part]
        else:
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
    return node

async def evaluate_assert(session, step: dict, state: dict) -> tuple[bool, str]:
    """Evaluate one ``assert_*`` step against the session's live page state.

    Returns ``(ok, detail)``. Raises :class:`SessionRefused` for a kind the
    vocabulary does not know, so typos surface as refusals instead of a
    silent pass.
    """
    action = (step.get("action") or "").strip().lower()
    kind = (step.get("kind") or
            (action[len("assert_"):] if action.startswith("assert_") else "")).strip().lower()
    kind = kind or "visible"
    target = step.get("target") or step.get("name") or ""
    value = str(step.get("value", step.get("expected", "")))

    element = None
    if target:
        try:
            element = session.catalog.get(session.resolve_target(target))
        except SessionRefused:
            element = None

    selector = (step.get("selector") or "").strip()
    if selector and kind in (
        "visible", "not_visible", "hidden", "text", "text_contains",
        "text_equals", "value", "count", "checked", "unchecked",
        "not_checked", "enabled", "disabled",
    ):
        return await assert_selector(session, kind, selector, value)

    handled = await assert_page_health(session, kind, step, value, state)
    if handled is not None:
        return handled
    handled = await assert_api_response(session, kind, step, value)
    if handled is not None:
        return handled
    if kind in ("no_request", "request_absent"):
        made = await session._call_optional("made_request", value)
        return (not made), (f"request absent: {value}" if not made
                            else f"unexpected request: {value}")

    if kind == "visible":
        if element is not None:
            ok = element.get("visible") is not False
            return ok, (f"visible: {target}" if ok else f"not visible: {target}")
        # Non-interactive content (headings, panels, text) is not in the
        # element catalog; fall back to rendered page text (innerText
        # respects display:none, so hidden content stays hidden).
        text_hit = (target or "").lower() in (state.get("text") or "").lower()
        return text_hit, (f"visible text: {target}" if text_hit
                          else f"element/text missing: {target}")
    if kind in ("not_visible", "hidden"):
        if element is not None:
            ok = element.get("visible") is False
            return ok, (f"not visible: {target}" if ok else f"still visible: {target}")
        text_hit = (target or "").lower() in (state.get("text") or "").lower()
        return (not text_hit), (f"not visible: {target}" if not text_hit
                                else f"text still present: {target}")
    if kind in ("text", "text_contains"):
        blob = (element or {}).get("name") if element else state.get("text", "")
        blob = blob or (state.get("text", "") if not element else "")
        ok = value.lower() in (blob or "").lower()
        return ok, (f"text contains {value!r}" if ok else f"text missing {value!r}")
    if kind == "text_equals":
        blob = (element or {}).get("name") if element else state.get("text", "")
        ok = (blob or "").strip() == value.strip()
        return ok, (f"text == {value!r}" if ok else f"text != {value!r}")
    if kind == "value":
        ok = value.lower() in ((element or {}).get("value") or "").lower()
        return ok, (f"value contains {value!r}" if ok else f"value missing {value!r}")
    if kind == "url":
        ok = value in state.get("url", "")
        return ok, (f"url contains {value!r}" if ok else f"url does not contain {value!r}")
    if kind == "title":
        ok = value.lower() in (state.get("title", "") or "").lower()
        return ok, (f"title contains {value!r}" if ok else f"title missing {value!r}")
    if kind == "count":
        if target:
            n = sum(1 for e in session.catalog.values()
                    if target.lower() in (e.get("name") or "").lower())
        else:
            n = len(session.catalog)
        ok = n == int(value or 0)
        return ok, (f"count == {n}" if ok else f"count {n} != {value}")
    if kind == "checked":
        ok = bool(element) and element.get("checked") is True
        return ok, ("checked" if ok else f"not checked: {target}")
    if kind in ("unchecked", "not_checked"):
        ok = element is None or element.get("checked") is not True
        return ok, ("unchecked" if ok else f"checked: {target}")
    if kind == "enabled":
        ok = bool(element) and not element.get("disabled")
        return ok, ("enabled" if ok else f"disabled/missing: {target}")
    if kind == "disabled":
        ok = bool(element) and element.get("disabled")
        return ok, ("disabled" if ok else f"enabled/missing: {target}")
    if kind in ("console_clean", "no_console_errors"):
        errors = session._peek_telemetry().get("console_errors", [])
        return (not errors), ("console clean" if not errors else f"{len(errors)} console error(s)")
    if kind in ("no_failed_requests", "network_clean"):
        failed_reqs = session._peek_telemetry().get("failed_requests", [])
        return (not failed_reqs), ("no failed requests" if not failed_reqs
                                   else f"{len(failed_reqs)} failed request(s)")
    raise SessionRefused("unknown_assertion", f"unsupported assertion kind: {kind}")

async def assert_selector(session, kind: str, selector: str, value: str) -> tuple[bool, str]:
    """Assertion kinds answered by direct CSS/XPath probes."""
    label = selector[:60]
    if kind == "visible":
        ok = await session._call_optional("is_visible_selector", selector)
        return bool(ok), (f"visible: {label}" if ok else f"not visible: {label}")
    if kind in ("not_visible", "hidden"):
        ok = await session._call_optional("is_visible_selector", selector)
        return (not ok), (f"not visible: {label}" if not ok else f"still visible: {label}")
    if kind in ("text", "text_contains"):
        blob = await session._call_optional("text_of_selector", selector) or ""
        ok = value.lower() in blob.lower()
        return ok, (f"text contains {value!r}" if ok else f"text missing {value!r}")
    if kind == "text_equals":
        blob = (await session._call_optional("text_of_selector", selector) or "").strip()
        ok = blob == value.strip()
        return ok, (f"text == {value!r}" if ok else f"text != {value!r}")
    if kind == "value":
        current = await session._call_optional("value_of_selector", selector) or ""
        ok = value.lower() in current.lower()
        return ok, (f"value contains {value!r}" if ok else f"value missing {value!r}")
    if kind == "count":
        n = await session._call_optional("count_selector", selector)
        ok = int(n) == int(value or 0)
        return ok, (f"count == {n}" if ok else f"count {n} != {value}")
    if kind == "checked":
        ok = await session._call_optional("is_checked_selector", selector)
        return bool(ok), ("checked" if ok else f"not checked: {label}")
    if kind in ("unchecked", "not_checked"):
        ok = await session._call_optional("is_checked_selector", selector)
        return (not ok), ("unchecked" if not ok else f"checked: {label}")
    if kind == "enabled":
        ok = await session._call_optional("is_enabled_selector", selector)
        return bool(ok), ("enabled" if ok else f"disabled/missing: {label}")
    if kind == "disabled":
        ok = await session._call_optional("is_enabled_selector", selector)
        return (not ok), ("disabled" if not ok else f"enabled/missing: {label}")
    raise SessionRefused("unknown_assertion", f"unsupported assertion kind: {kind}")


async def assert_page_health(session, kind: str, step: dict, value: str, state: dict) -> tuple[bool, str]:
    """Dialog / accessibility / performance assertions (live page health)."""

    if kind in ("dialog", "no_dialog", "no_dialogs"):
        dialogs = session._peek_telemetry().get("dialogs") or []
        if kind in ("no_dialog", "no_dialogs"):
            if not dialogs:
                return True, "no dialog was raised"
            return False, (
                f"{len(dialogs)} dialog(s) raised; last: "
                f"{dialogs[-1].get('type')}:{str(dialogs[-1].get('message', ''))[:60]}"
            )
        if not dialogs:
            return False, "no dialog was raised"
        last = dialogs[-1]
        want_type = str(step.get("type", "") or "")
        if want_type and last.get("type") != want_type:
            return False, f"last dialog was {last.get('type')}, wanted {want_type}"
        if value and value not in str(last.get("message") or ""):
            return False, (
                f"dialog message {str(last.get('message'))[:80]!r} "
                f"does not contain {value!r}"
            )
        if step.get("accepted") is not None:
            if bool(last.get("accepted")) != bool(step.get("accepted")):
                return False, (
                    "dialog was " + ("accepted" if last.get("accepted") else "dismissed")
                )
        state_bit = ""
        if step.get("accepted") is not None:
            state_bit = " accepted" if last.get("accepted") else " dismissed"
        return True, f"dialog {last.get('type')}{state_bit}"

    if kind in ("no_a11y_violations", "a11y_clean", "accessible"):
        audit = await session._call_optional("a11y_audit")
        issues = (audit or {}).get("issues") or []
        if not issues:
            return True, "no accessibility violations"
        summary = ", ".join(
            f"{i.get('id')}({i.get('count')})" for i in issues[:5]
        )
        return False, f"{len(issues)} a11y issue(s): {summary}"

    if kind in ("perf", "lcp", "fcp", "load", "dom_nodes", "transfer_kb",
                "resource_count", "navigation_ms"):
        metric = kind if kind != "perf" else (step.get("metric") or "lcp").lower()
        metrics = await session._call_optional("perf_metrics")
        metrics = metrics or {}
        source = {
            "lcp": "lcp_ms", "fcp": "fcp_ms", "load": "load_ms",
            "dom_nodes": "dom_nodes", "resource_count": "resource_count",
            "navigation_ms": "response_ms",
        }.get(metric, metric)
        actual = float(metrics.get(source, 0) or 0)
        if metric == "transfer_kb":
            actual = float(metrics.get("transfer_bytes", 0) or 0) / 1024.0
        budget = float(value or 0)
        ok = actual <= budget if budget > 0 else actual > 0
        return ok, (f"{metric}={actual:.1f} (budget {budget:g})" if budget > 0
                    else f"{metric}={actual:.1f}")


    return None  # kind not handled by this family
async def assert_api_response(session, kind: str, step: dict, value: str) -> tuple[bool, str]:
    """Assertions over the last ``api`` step response (status/latency/json/schema/header)."""

    if kind in ("status", "api_status"):
        if session._last_api is None:
            return False, "no api step ran before this assertion"
        actual = int(session._last_api.get("status") or 0)
        expect = value or "2xx"
        if str(expect).isdigit():
            ok = actual == int(expect)
        else:
            e = str(expect).lower()
            ok = (e == "2xx" and 200 <= actual < 300) or \
                 (e == "3xx" and 300 <= actual < 400) or \
                 (e == "4xx" and 400 <= actual < 500) or \
                 (e == "5xx" and 500 <= actual < 600)
        return ok, f"api status {actual} (expected {expect})"

    if kind in ("latency", "api_latency"):
        if session._last_api is None:
            return False, "no api step ran before this assertion"
        actual = int(session._last_api.get("latency_ms") or 0)
        budget = int(float(value or 0))
        ok = actual <= budget if budget > 0 else actual > 0
        return ok, (f"api latency {actual}ms (budget {budget}ms)"
                    if budget > 0 else f"api latency {actual}ms")

    if kind in ("json", "api_json", "json_path"):
        if session._last_api is None:
            return False, "no api step ran before this assertion"
        payload = session._last_api.get("json")
        if payload is None:
            return False, "api response was not JSON"
        path = (step.get("path") or step.get("json_path") or "")
        expected = (step.get("expected") if step.get("expected") is not None
                    else step.get("value"))
        got = json_path(payload, path) if path else payload
        if expected is None or expected == "":
            ok = got is not None
            return ok, (f"json {path or '<root>'} present" if ok
                        else f"json {path or '<root>'} missing")
        ok = str(got) == str(expected)
        return ok, (f"json {path or '<root>'}: {got!r}"
                    + ("" if ok else f" != {expected!r}"))

    if kind in ("schema", "api_schema"):
        if session._last_api is None:
            return False, "no api step ran before this assertion"
        payload = session._last_api.get("json")
        required = step.get("required") or step.get("value") or []
        if isinstance(required, str):
            required = [k.strip() for k in required.split(",") if k.strip()]
        if not isinstance(payload, dict):
            return False, "api response was not a JSON object"
        missing = [k for k in required if k not in payload]
        return (not missing), (
            f"schema ok ({len(required)} keys)" if not missing
            else f"missing keys: {', '.join(missing)}")

    if kind in ("header", "api_header"):
        if session._last_api is None:
            return False, "no api step ran before this assertion"
        headers = {k.lower(): str(v) for k, v in
                   (session._last_api.get("headers") or {}).items()}
        key = (step.get("header") or step.get("name") or "").lower()
        got = headers.get(key)
        expected = str(step.get("value", step.get("expected", "")))
        if not expected:
            ok = got is not None
            return ok, (f"header {key} present" if ok
                        else f"header {key} missing")
        ok = got is not None and expected.lower() in got.lower()
        return ok, (f"header {key}: {got!r}")

    if kind == "made_request":
        made = await session._call_optional("made_request", value)
        return bool(made), (f"request made: {value}" if made
                            else f"no matching request: {value}")

    return None  # kind not handled by this family