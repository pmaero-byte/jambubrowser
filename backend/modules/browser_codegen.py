"""
Export declarative test flows to Playwright Test (.spec.ts) source.

Pure string generation — no browser, no engine. Lets a developer take a flow
authored/generated in Jambubrowser and graduate it into their own CI without
being locked in.
"""
from __future__ import annotations

import json
from typing import Any


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
