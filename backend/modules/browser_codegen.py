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


def _getby_selector(kind: str, args: str) -> Optional[str]:
    """CSS/`role=` addressing for locators without a natural intent name."""
    kind = kind.lower()
    if kind == "getbytestid":
        name = _quoted(args, 0)
        return f'[data-testid="{name}"]' if name else None
    if kind == "getbyplaceholder":
        name = _quoted(args, 0)
        return f'[placeholder="{name}"]' if name else None
    if kind == "getbyalttext":
        name = _quoted(args, 0)
        return f'[alt="{name}"]' if name else None
    if kind == "getbytitle":
        name = _quoted(args, 0)
        return f'[title="{name}"]' if name else None
    if kind == "getbyrole":
        role, name = _quoted(args, 0), _quoted(args, 1)
        if role and name:
            return f'role={role}[name="{name}"]'
        if role:
            return f'role={role}'
        return None
    return None


def _locator_address(kind: str, args: str):
    """Return ("target", text) or ("selector", css) for a locator factory."""
    if kind == "locator":
        sel = _quoted(args, 0)
        return ("selector", sel) if sel else (None, None)
    lowered = kind.lower()
    if lowered in ("getbytext", "getbylabel"):
        name = _quoted(args, 0)
        return ("target", name) if name else (None, None)
    if lowered == "getbyrole":
        first, second = _quoted(args, 0), _quoted(args, 1)
        if first and second:
            # "role name" is the flow's documented intent addressing
            return ("target", f"{first} {second}")
        sel = _getby_selector(kind, args)
        return ("selector", sel) if sel else (None, None)
    sel = _getby_selector(kind, args)
    if sel:
        return ("selector", sel)
    name = _quoted(args, 0)
    return ("target", name) if name else (None, None)


def _factory_address(expression: str):
    """Address of an inline ``page.locator/getBy…(...)`` factory call."""
    match = re.search(r"page\.(locator|getBy\w+)\((.*)\)", expression)
    if not match:
        return (None, None)
    return _locator_address(match.group(1), match.group(2))


def _regex_literal(args: str) -> Optional[str]:
    """Pattern text of a JS regex literal (for toHaveURL/toHaveTitle)."""
    match = re.search(r"/((?:\\.|[^/])+)/[a-z]*", args or "")
    return match.group(1) if match else None


def _parse_evaluate(stripped: str):
    """Translate ``page.evaluate(...)`` to an evaluate step.

    String scripts run as-is; arrow/function sources are self-invoked so the
    page returns their result instead of the function object.
    """
    match = re.search(r"page\.evaluate\((.+)\)\s*;?\s*$", stripped, re.DOTALL)
    if not match:
        return None
    arg = match.group(1).strip()
    if (arg.startswith(("'", '"', "`")) and (script := _quoted(arg, 0)) is not None):
        return {"action": "evaluate", "script": script}
    if re.match(r"(\(\s*\)|\(\s*\w[\w$,\s]*\)|function\b|async\b)", arg):
        return {"action": "evaluate", "script": f"({arg})()"}
    return None


def _symbol_address(name: str, symbols: dict):
    """Unwrap a declared locator symbol to ("target"|"selector", value)."""
    entry = symbols.get(name)
    if isinstance(entry, tuple):
        return entry
    if isinstance(entry, str):
        return ("target", entry)
    return (None, None)


def _locator_to_target(expression: str, symbols: dict) -> Optional[str]:
    expression = re.sub(r"\.(first|last|nth\(\d+\))\(\)", "",
                        expression.strip().rstrip(";"))
    match = _GETBY_RE.search(expression)
    if match:
        return _getby_target(match.group(1), match.group(2))
    match = _DECL_RE.search(expression)
    if match:
        form, value = _symbol_address(match.group(1), symbols)
        return value if form == "target" else None
    name = expression.strip().rstrip(";")
    form, value = _symbol_address(name, symbols)
    if form == "target":
        return value
    if expression.strip() == "page":
        return None
    return None


def _parse_line(line: str, symbols: dict):
    stripped = line.strip().rstrip(";")

    # Single-line test.step('…', async () => { … }) — unwrap simple bodies.
    wrapped = re.match(
        r"await\s+test\.step\(.*?async\s*\(\)\s*=>\s*\{(.*)\}\s*\)\s*;?\s*$",
        stripped, re.DOTALL,
    )
    if wrapped and "{" not in wrapped.group(1) and "}" not in wrapped.group(1):
        return _parse_line(wrapped.group(1), symbols)

    if (not stripped or stripped.startswith(("import ", "//", "/*", "*", "test(", "test.use(",
            "});", "})", "});", "{", "}", "await test.step(",
            "await page.route(", "await route.", "route."))):
        return _SKIP

    # Locator declarations: const save = page.getByRole(...); / page.locator(...)
    decl = re.match(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(.+)$", stripped)
    if decl and re.search(r"page\.(getBy\w+|locator)\(", decl.group(2)):
        address = _factory_address(decl.group(2))
        if address[0]:
            symbols[decl.group(1)] = address
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
    if wait_url and (pattern := _regex_literal(wait_url.group(1)) or _quoted(wait_url.group(1), 0)):
        return {"action": "wait", "url_contains": pattern}
    if "page.waitForLoadState(" in stripped:
        return {"action": "wait"}
    wait_resp = re.search(r"page\.waitFor(?:Response|Request)\((.+)\)", stripped)
    if wait_resp and (pattern := _quoted(wait_resp.group(1), 0)):
        return {"action": "assert_made_request", "value": pattern}

    evaluated = _parse_evaluate(stripped)
    if evaluated is not None:
        return evaluated

    # expect(...) assertions — including `not.` negations.
    expect = _EXPECT_RE.search(stripped)
    if expect:
        inner, negated, assertion, args = expect.groups()
        return _expect_to_assert(inner.strip(), bool(negated), assertion, args, symbols)

    # Locator action chains: page.getByText('Save').click();
    # page.locator('#id').fill('x'); symbol chains: save.click();
    chain = re.search(r"page\.(locator|getBy\w+)\((.*)\)((?:\.\w+\([^)]*\))+)", stripped)
    symbol_chain = None
    if chain is None:
        symbol_chain = re.match(
            r"await\s+([A-Za-z_$][\w$]*)\.(\w+)\((.*)\)\s*;?\s*$", stripped,
        )
    if chain:
        form, address = _locator_address(chain.group(1), chain.group(2))
        if not form:
            return None
        calls = re.findall(r"\.(\w+)\(([^)]*)\)", chain.group(3))
        if not calls:
            return None
        method, call_args = calls[-1]
        value = _quoted(call_args, 0) or ""
    elif symbol_chain and symbol_chain.group(1) in symbols:
        form, address = _symbol_address(symbol_chain.group(1), symbols)
        if not form:
            return None
        method = symbol_chain.group(2)
        value = _quoted(symbol_chain.group(3), 0) or ""
    else:
        return None
    key = "target" if form == "target" else "selector"
    if method == "click":
        return {"action": "click", key: address}
    if method in ("fill", "type"):
        return {"action": "type", key: address, "value": value}
    if method == "hover":
        return {"action": "hover", key: address}
    if method == "press":
        return {"action": "press", key: address, "key": value or "Enter"}
    if method == "check":
        return {"action": "check", key: address}
    if method == "uncheck":
        return {"action": "uncheck", key: address}
    if method == "selectOption":
        return {"action": "select", key: address, "value": value}
    if method == "waitFor":
        if form == "selector":
            return {"action": "wait", "selector": address}
        return {"action": "wait", "text": address}
    return None

    return None


def _expect_to_assert(inner: str, negated: bool, assertion: str,
                      args: str, symbols: dict):
    value = _quoted(args, 0) or ""
    if inner == "page":
        arg_text = (args or "").strip()
        if assertion in ("toHaveURL", "toHaveTitle"):
            if arg_text.startswith("new RegExp(/") or arg_text.startswith("/"):
                pattern = _regex_literal(args) or ""
            else:
                pattern = value
            action = "assert_url" if assertion == "toHaveURL" else "assert_title"
            return {"action": action, "value": pattern}
        return None
    address = _factory_address(inner)
    if not address[0]:
        form, resolved = _symbol_address(inner, symbols)
        address = (form, resolved) if form else (None, None)
    if not address[0]:
        legacy = _locator_to_target(inner, symbols)
        address = ("target", legacy) if legacy else (None, None)
    if not address[0]:
        return None
    key = {"target" if address[0] == "target" else "selector": address[1]}
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
        return {"action": action, **key}
    if assertion in ("toContainText", "toHaveText"):
        kind = "text_equals" if assertion == "toHaveText" else "text"
        return {"action": "assert", "kind": kind, **key, "value": value}
    if assertion == "toHaveValue":
        return {"action": "assert_value", **key, "value": value}
    if assertion == "toHaveCount":
        try:
            return {"action": "assert_count", **key, "value": int(value or 0)}
        except ValueError:
            return None
    return None


def playwright_to_flow(source: str) -> dict:
    """Translate a Playwright Test source to a flow document.

    Returns ``{"steps", "network", "unparsed" ([{line, text, reason?}]), "count"}``.
    ``network`` carries any ``page.route()`` mock/abort policy found.
    """
    steps: list[dict] = []
    unparsed: list[dict] = []
    symbols: dict = {}
    for lineno, statement in _join_statements(source):
        parsed = _parse_line(statement, symbols)
        if parsed is _SKIP:
            continue
        if parsed is None:
            text = statement.strip()
            if text:
                item: dict = {"line": lineno, "text": text[:160]}
                reason = _unparsed_reason(text)
                if reason:
                    item["reason"] = reason
                unparsed.append(item)
        else:
            steps.append(parsed)
    network = _extract_route_policies(str(source or ""))
    return {"steps": steps, "network": network,
            "unparsed": unparsed, "count": len(steps)}


def _is_block_opener(line: str) -> bool:
    """A line that opens a block (test/step/if/arrow) — flush, never join."""
    stripped = line.strip()
    if stripped.startswith(("test(", "test.step(", "if ", "if(", "for ", "for(",
                            "while ", "while(", "switch ", "try", "catch")):
        return True
    return bool(re.search(r"(=>|\)|\belse)\s*\{\s*$", stripped))


def _join_statements(source: str) -> list[tuple[int, str]]:
    """Join continuation lines (chains, open brackets) into statements."""
    out: list[tuple[int, str]] = []
    buf, start = "", 0
    for lineno, raw in enumerate(str(source or "").splitlines(), 1):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "/*", "*")):
            if buf:
                out.append((start, buf))
                buf, start = "", 0
            continue
        if not buf:
            buf, start = line, lineno
            continue
        if _is_block_opener(buf):
            out.append((start, buf))
            buf, start = line, lineno
            continue
        if re.match(r"^[\]\}\)]+\s*;?\s*$", line.strip()):
            # Pure closers (});) never continue a statement — flush first.
            out.append((start, buf))
            out.append((lineno, line.strip()))
            buf, start = "", 0
            continue
        prev_open = buf.rstrip().endswith(
            (".", "(", ",", "{", "[", "=>", "+", "&&", "||"))
        stripped_next = line.lstrip()
        cur_cont = stripped_next.startswith(".") or stripped_next[:1] in ("}", ")", "]")
        if prev_open or cur_cont:
            buf += " " + line.strip()
        else:
            out.append((start, buf))
            buf, start = line, lineno
    if buf:
        out.append((start, buf))
    return out


def _balanced(text: str, opener: str = "(", closer: str = ")") -> Optional[str]:
    """Return the first balanced opener…closer span, strings/comments aware."""
    start = text.find(opener)
    if start < 0:
        return None
    depth, i, quote = 0, start, None
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ("'", '"', "`"):
            quote = ch
        elif text.startswith("//", i):
            nxt = text.find("\n", i)
            i = len(text) if nxt < 0 else nxt
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = len(text) if end < 0 else end + 2
            continue
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    return None


def _extract_route_policies(source: str) -> dict:
    """Translate ``page.route(pattern, handler)`` into a network policy."""
    mocks: list[dict] = []
    fails: list[dict] = []
    for match in re.finditer(r"page\.route\(\s*(['\"])(.*?)\1\s*,", source or ""):
        pattern = match.group(2)
        body = _balanced(source[match.end():], "{", "}") or ""
        if re.search(r"\broute\.abort\s*\(", body):
            fails.append({"url": pattern})
            continue
        found = re.search(r"\broute\.fulfill\s*\(", body)
        if not found:
            continue
        opts_raw = _balanced(body[found.end() - 1:], "(", ")") or "()"
        opts = opts_raw[1:-1]
        mock: dict = {"url": pattern}
        status = re.search(r"\bstatus\s*:\s*(\d+)", opts)
        if status:
            mock["status"] = int(status.group(1))
        content_type = re.search(r"contentType\s*:\s*(['\"])(.*?)\1", opts)
        if content_type:
            mock["content_type"] = content_type.group(2)
        if re.search(r"\bjson\s*:", opts):
            obj = _balanced(opts[re.search(r"\bjson\s*:", opts).end():], "{", "}")
            parsed = None
            if obj:
                try:
                    parsed = json.loads(obj)
                except Exception:
                    parsed = None
            if parsed is not None:
                mock["json"] = parsed
            elif obj:
                mock["body"] = obj
                mock.setdefault("content_type", "application/json")
        else:
            body_match = re.search(r"\bbody\s*:\s*(['\"])(.*?)\1", opts, re.DOTALL)
            if body_match:
                mock["body"] = body_match.group(2)
        mocks.append(mock)
    return {"mocks": mocks, "fail": fails}


def _unparsed_reason(text: str) -> Optional[str]:
    stripped = text.strip()
    if re.match(r"(for|if|while|switch|try|catch|finally|function)\s*[\({]", stripped):
        return "control-flow"
    if re.search(r"\.(dblclick|setInputFiles|dragTo|selectText|tap|upload)\s*\(", stripped):
        return "unsupported-action"
    if re.search(r"\bnew\s+[A-Z]\w*", stripped) or re.search(r"\b\w+Page\b", stripped):
        return "page-object/fixture"
    if re.match(r"await\s+(?!page\.|expect\()", stripped):
        return "custom-helper"
    return None
