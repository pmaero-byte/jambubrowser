"""
Export declarative test flows to Playwright Test (.spec.ts) source — and a
best-effort *import* back.

The importer covers the common `page.getBy*` / keyboard / `expect` subset and
reports every line it cannot translate (`unparsed` with line numbers), so a
human or agent knows exactly what needs hand-editing. Regex-literal and
multi-line-chain corners are deliberately left to that list rather than
guessed.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional


def _s(value: Any) -> str:
    """A safely-escaped single-quoted TS string literal."""
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"


def _regex(value: str) -> str:
    return f"new RegExp({_s(value)})"


# Playwright locators are intentionally tolerant: the flow's intent targets are
# matched by role/name first, then text — mirroring the engine's resolver.
def _locator(target: str) -> str:
    return f"page.getByText({_s(target)}, {{ exact: false }}).first()"


def _type_locator(target: str) -> str:
    return f"page.getByLabel({_s(target)}).or(page.getByPlaceholder({_s(target)})).first()"


def _step_to_ts(step: dict) -> list[str]:
    action = (step.get("action") or "").strip().lower()
    target = step.get("target") or step.get("name") or ""
    value = step.get("value", step.get("expected", ""))
    approve = "approve=true" if step.get("approve") else ""
    lines: list[str] = []

    if action == "navigate":
        lines.append(f"  await page.goto({_s(step.get('url', ''))});")
    elif action == "reload":
        lines.append("  await page.reload();")
    elif action == "back":
        lines.append("  await page.goBack();")
    elif action == "forward":
        lines.append("  await page.goForward();")
    elif action == "click":
        lines.append(f"  await {_locator(target)}.click();  // {approve}".rstrip())
    elif action == "type":
        lines.append(f"  await {_type_locator(target)}.fill({_s(value)});")
    elif action == "press":
        if target:
            lines.append(f"  await {_locator(target)}.press({_s(step.get('key', 'Enter'))});")
        else:
            lines.append(f"  await page.keyboard.press({_s(step.get('key', 'Enter'))});")
    elif action == "hover":
        lines.append(f"  await {_locator(target)}.hover();")
    elif action == "select":
        lines.append(f"  await page.getByLabel({_s(target)}).selectOption({_s(value)});")
    elif action in ("check", "uncheck"):
        fn = "check" if action == "check" else "uncheck"
        lines.append(f"  await page.getByLabel({_s(target)}).{fn}();")
    elif action in ("wait", "wait_for"):
        if step.get("selector"):
            lines.append(f"  await page.waitForSelector({_s(step['selector'])});")
        elif step.get("text"):
            lines.append(f"  await page.getByText({_s(step['text'])}).first().waitFor();")
        elif step.get("url_contains"):
            lines.append(f"  await page.waitForURL({_regex(step['url_contains'])});")
        else:
            lines.append("  await page.waitForLoadState('networkidle');")
    elif action == "screenshot":
        path = step.get("path") or "screenshot.png"
        lines.append(f"  await page.screenshot({{ path: {_s(path)}, fullPage: "
                     f"{'true' if step.get('full_page') else 'false'} }});")
    elif action == "assert" or action.startswith("assert_"):
        kind = (step.get("kind") or action[len("assert_"):]).strip().lower()
        lines.extend(_assert_to_ts(kind, target, value))
    else:
        lines.append(f"  // TODO unsupported action: {action} {json.dumps(step)}")
    return lines


def _assert_to_ts(kind: str, target: str, value: str) -> list[str]:
    if kind in ("visible", ""):
        return [f"  await expect({_locator(target)}).toBeVisible();"]
    if kind in ("not_visible", "hidden"):
        return [f"  await expect({_locator(target)}).toBeHidden();"]
    if kind in ("text", "text_contains"):
        if target:
            return [f"  await expect({_locator(target)}).toContainText({_s(value)});"]
        return [f"  await expect(page.locator('body')).toContainText({_s(value)});"]
    if kind == "text_equals":
        return [f"  await expect({_locator(target)}).toHaveText({_s(value)});"]
    if kind == "value":
        return [f"  await expect({_type_locator(target)}).toHaveValue({_s(value)});"]
    if kind == "url":
        return [f"  await expect(page).toHaveURL({_regex(value)});"]
    if kind == "title":
        return [f"  await expect(page).toHaveTitle({_regex(value)});"]
    if kind == "count":
        return [f"  await expect(page.getByText({_s(target)})).toHaveCount({int(value or 0)});"]
    if kind == "checked":
        return [f"  await expect(page.getByLabel({_s(target)})).toBeChecked();"]
    if kind in ("unchecked", "not_checked"):
        return [f"  await expect(page.getByLabel({_s(target)})).not.toBeChecked();"]
    if kind == "enabled":
        return [f"  await expect({_locator(target)}).toBeEnabled();"]
    if kind == "disabled":
        return [f"  await expect({_locator(target)}).toBeDisabled();"]
    if kind in ("console_clean", "no_console_errors"):
        return ["  // NOTE: console cleanliness is asserted by the Jambubrowser telemetry collector."]
    if kind in ("no_failed_requests", "network_clean"):
        return ["  // NOTE: failed-request checks are covered by Jambubrowser telemetry."]
    if kind in ("no_a11y_violations", "a11y_clean", "accessible"):
        return [
            "  // Requires: npm i -D @axe-core/playwright",
            "  // const results = await new AxeBuilder({ page }).analyze();",
            "  // expect(results.violations).toEqual([]);",
        ]
    if kind in ("lcp", "fcp", "load", "dom_nodes", "transfer_kb", "resource_count", "navigation_ms", "perf"):
        return [f"  // Budget: {kind} <= {value} (measure with your own performance capture)."]
    if kind == "made_request":
        return [f"  // Expect a request matching {_s(value)}."]
    if kind in ("no_request", "request_absent"):
        return [f"  // Expect no request matching {_s(value)}."]
    return [f"  // TODO unsupported assertion: {kind}"]


def flow_to_playwright(steps, *, name: str = "jambubrowser flow",
                       url: str = "", base_url: str = "") -> str:
    """Render a flow to a Playwright Test TypeScript file."""
    if isinstance(steps, str):
        steps = json.loads(steps)
    if isinstance(steps, dict):
        steps = steps.get("steps") or []
    body: list[str] = []
    for step in steps:
        body.extend(_step_to_ts(step))
    prelude = []
    if url:
        prelude = [f"  // Original entry point: {url}"]
    header = (
        "import { test, expect } from '@playwright/test';\n\n"
        f"test({_s(name)}, async ({{ page }}) => {{\n"
    )
    if base_url:
        header = (
            "import { test, expect } from '@playwright/test';\n\n"
            f"test.use({{ baseURL: {_s(base_url)} }});\n\n"
            f"test({_s(name)}, async ({{ page }}) => {{\n"
        )
    return header + "\n".join(prelude + body) + "\n});\n"


def flow_to_dict(steps) -> dict:
    """Normalise a flow (JSON string / list / wrapper) to a dict document."""
    if isinstance(steps, str):
        steps = json.loads(steps)
    if isinstance(steps, dict):
        return steps
    return {"steps": steps}


# ---------------------------------------------------------------------------
# Playwright → flow (best-effort subset)
# ---------------------------------------------------------------------------

_SKIP = object()

_QUOTED_RE = re.compile(r"""(['"`])((?:\\.|(?!\1).)*)\1""")
_GETBY_RE = re.compile(r"page\.(getBy\w+)\((.*)\)\s*$", re.DOTALL)
_DECL_RE = re.compile(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*page\.(getBy\w+)\((.*)\)")
_EXPECT_RE = re.compile(r"expect\((.+)\)\.(not\.)?(to\w+)\((.*)\)")


def _quoted(text: str, index: int = 0) -> Optional[str]:
    """Nth quoted literal in a fragment, unescaped."""
    found = _QUOTED_RE.findall(text or "")
    if index >= len(found):
        return None
    return found[index][1].replace("\\'", "'").replace('\\"', '"')


def _getby_target(kind: str, args: str) -> Optional[str]:
    kind = kind.lower()
    if kind in ("getbytext", "getbylabel", "getbyplaceholder", "getbytitle", "getbyalttext", "getbytestid"):
        return _quoted(args, 0)
    if kind == "getbyrole":
        first, second = _quoted(args, 0), _quoted(args, 1)
        if second:  # { name: '…' } — a named role resolves by name
            return second
        return first  # bare role ("button") — likely ambiguous, kept anyway
    return None


def _locator_to_target(expression: str, symbols: dict) -> Optional[str]:
    expression = re.sub(r"\.(first|last|nth\(\d+\))\(\)", "",
                        expression.strip().rstrip(";"))
    match = _GETBY_RE.search(expression)
    if match:
        return _getby_target(match.group(1), match.group(2))
    match = _DECL_RE.search(expression)
    if match:
        return symbols.get(match.group(1))
    name = expression.strip().rstrip(";")
    if name in symbols:
        return symbols[name]
    if expression.strip() == "page":
        return None
    return None


def _parse_line(line: str, symbols: dict):
    stripped = line.strip().rstrip(";")
    if (not stripped or stripped.startswith(("import ", "//", "/*", "*", "test(", "test.use(",
            "});", "})", "});", "{", "}", "await test.step("))):
        return _SKIP

    # Locator declarations: const save = page.getByRole('button', { name: 'Save' });
    decl = re.match(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(.+)$", stripped)
    if decl and "page.getBy" in decl.group(2):
        target = _locator_to_target(decl.group(2), symbols)
        if target:
            symbols[decl.group(1)] = target
        return _SKIP

    # await page.goto('…')
    goto = re.search(r"page\.goto\((.+)\)", stripped)
    if goto and (url := _quoted(goto.group(1), 0)):
        return {"action": "navigate", "url": url}

    if "page.reload(" in stripped:
        return {"action": "reload"}
    if "page.goBack(" in stripped:
        return {"action": "back"}
    if "page.goForward(" in stripped:
        return {"action": "forward"}

    shot = re.search(r"page\.screenshot\((.+)\)", stripped)
    if shot and (path := _quoted(shot.group(1), 0)):
        return {"action": "screenshot", "path": path}

    kbd = re.search(r"page\.keyboard\.press\((.+)\)", stripped)
    if kbd and (key := _quoted(kbd.group(1), 0)):
        return {"action": "press", "key": key}

    wait_sel = re.search(r"page\.waitForSelector\((.+)\)", stripped)
    if wait_sel and (sel := _quoted(wait_sel.group(1), 0)):
        return {"action": "wait", "selector": sel}
    wait_url = re.search(r"page\.waitForURL\((.+)\)", stripped)
    if wait_url and (pattern := _quoted(wait_url.group(1), 0)):
        return {"action": "wait", "url_contains": pattern}
    if "page.waitForLoadState(" in stripped:
        return {"action": "wait"}

    # expect(...) assertions — including `not.` negations.
    expect = _EXPECT_RE.search(stripped)
    if expect:
        inner, negated, assertion, args = expect.groups()
        return _expect_to_assert(inner.strip(), bool(negated), assertion, args, symbols)

    # getBy* action chains: await page.getByText('Save').click();
    # (and symbol chains: await save.click(); after a declaration)
    chain = re.search(r"page\.(getBy\w+)\((.*)\)((?:\.\w+\([^)]*\))+)", stripped)
    symbol_chain = None
    if chain is None:
        symbol_chain = re.match(
            r"await\s+([A-Za-z_$][\w$]*)\.(\w+)\((.*)\)\s*;?\s*$", stripped,
        )
    if chain:
        kind, args, tail = chain.group(1), chain.group(2), chain.group(3)
        target = _getby_target(kind, args)
        if target is None:
            return None
        rest = tail.lstrip(".")
        method = rest.split("(", 1)[0].strip()
        value = _quoted(rest, 0) or ""
    elif symbol_chain and symbol_chain.group(1) in symbols:
        target = symbols[symbol_chain.group(1)]
        method = symbol_chain.group(2)
        value = _quoted(symbol_chain.group(3), 0) or ""
    else:
        return None
    if method == "click":
        return {"action": "click", "target": target}
    if method == "fill":
        return {"action": "type", "target": target, "value": value}
    if method == "hover":
        return {"action": "hover", "target": target}
    if method == "press":
        return {"action": "press", "target": target, "key": value or "Enter"}
    if method == "check":
        return {"action": "check", "target": target}
    if method == "uncheck":
        return {"action": "uncheck", "target": target}
    if method == "selectOption":
        return {"action": "select", "target": target, "value": value}
    if method == "waitFor":
        return {"action": "wait", "text": target}
    return None

    return None


def _expect_to_assert(inner: str, negated: bool, assertion: str,
                      args: str, symbols: dict):
    value = _quoted(args, 0) or ""
    if inner == "page":
        if assertion == "toHaveURL":
            return {"action": "assert_url", "value": value}
        if assertion == "toHaveTitle":
            return {"action": "assert_title", "value": value}
        return None
    target = _locator_to_target(inner, symbols)
    if target is None:
        return None
    mapping = {
        "toBeVisible": "assert_visible",
        "toBeHidden": "assert_not_visible",
        "toBeChecked": "assert_checked",
        "toBeEnabled": "assert_enabled",
        "toBeDisabled": "assert_disabled",
    }
    if assertion in mapping:
        action = mapping[assertion]
        if assertion == "toBeChecked" and negated:
            action = "assert_unchecked"
        elif negated and assertion == "toBeVisible":
            action = "assert_not_visible"
        return {"action": action, "target": target}
    if assertion in ("toContainText", "toHaveText"):
        kind = "text_equals" if assertion == "toHaveText" else "text"
        return {"action": "assert", "kind": kind, "target": target, "value": value}
    if assertion == "toHaveValue":
        return {"action": "assert_value", "target": target, "value": value}
    if assertion == "toHaveCount":
        try:
            return {"action": "assert_count", "target": target, "value": int(value or 0)}
        except ValueError:
            return None
    return None


def playwright_to_flow(source: str) -> dict:
    """Translate a Playwright Test source to a flow document.

    Returns ``{"steps": [...], "unparsed": [{"line", "text"}...], "count"}``.
    """
    steps: list[dict] = []
    unparsed: list[dict] = []
    symbols: dict = {}
    for lineno, raw in enumerate(str(source or "").splitlines(), 1):
        parsed = _parse_line(raw, symbols)
        if parsed is _SKIP:
            continue
        if parsed is None:
            text = raw.strip()
            if text:
                unparsed.append({"line": lineno, "text": text[:160]})
        else:
            steps.append(parsed)
    return {"steps": steps, "unparsed": unparsed, "count": len(steps)}
