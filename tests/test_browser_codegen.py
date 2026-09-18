"""Tests for Playwright flow export."""
from __future__ import annotations

from backend.modules.browser_codegen import flow_to_dict, flow_to_playwright, playwright_to_flow


FLOW = [
    {"action": "navigate", "url": "http://localhost:3000/"},
    {"action": "type", "target": "Email", "value": "dev@example.com"},
    {"action": "click", "target": "Sign in"},
    {"action": "assert_visible", "target": "Dashboard"},
    {"action": "assert_url", "value": "/dashboard"},
    {"action": "assert_console_clean"},
    {"action": "screenshot", "path": "shot.png"},
]


class TestCodegen:
    def test_header_and_imports(self):
        code = flow_to_playwright(FLOW, name="login flow")
        assert "from '@playwright/test'" in code
        assert "test('login flow'" in code

    def test_navigate_and_interactions(self):
        code = flow_to_playwright(FLOW)
        assert "await page.goto('http://localhost:3000/');" in code
        assert "getByLabel('Email')" in code and ".fill('dev@example.com')" in code
        assert "getByText('Sign in'" in code and ".click();" in code

    def test_assertions(self):
        code = flow_to_playwright(FLOW)
        assert "toBeVisible()" in code
        assert "toHaveURL(new RegExp('/dashboard'))" in code
        assert "Jambubrowser telemetry" in code  # console_clean note

    def test_screenshot(self):
        code = flow_to_playwright(FLOW)
        assert "page.screenshot({ path: 'shot.png'" in code

    def test_accepts_json_string_and_wrapper(self):
        import json
        assert "page.goto" in flow_to_playwright(json.dumps(FLOW))
        assert "page.goto" in flow_to_playwright({"steps": FLOW})

    def test_base_url_prelude(self):
        code = flow_to_playwright(FLOW, base_url="http://localhost:3000")
        assert "test.use({ baseURL: 'http://localhost:3000' })" in code

    def test_string_escaping(self):
        code = flow_to_playwright([{"action": "assert_text", "value": "it's fine"}])
        assert "it\\'s fine" in code

    def test_unknown_action_is_commented(self):
        code = flow_to_playwright([{"action": "warp"}])
        assert "TODO unsupported action: warp" in code

    def test_flow_to_dict(self):
        assert flow_to_dict(FLOW)["steps"] == FLOW
        assert flow_to_dict({"steps": FLOW})["steps"] == FLOW


SPEC = """
import { test, expect } from '@playwright/test';

test('login', async ({ page }) => {
  await page.goto('http://localhost:3000/login');
  await page.getByLabel('Email').fill('dev@example.com');
  await page.getByLabel('Password').fill('secret');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.keyboard.press('Enter');
  await page.waitForURL('**/dashboard');
  await expect(page.getByText('Dashboard')).toBeVisible();
  await expect(page).toHaveURL(new RegExp('/dashboard'));
  const save = page.getByRole('button', { name: 'Save' });
  await save.click();
  await expect(save).toBeEnabled();
});
"""


class TestImport:
    def test_core_flow(self):
        doc = playwright_to_flow(SPEC)
        actions = [s["action"] for s in doc["steps"]]
        assert actions == ["navigate", "type", "type", "click", "press", "wait",
                           "assert_visible", "assert_url", "click", "assert_enabled"]

    def test_step_shapes(self):
        doc = playwright_to_flow(SPEC)
        steps = doc["steps"]
        assert steps[0] == {"action": "navigate", "url": "http://localhost:3000/login"}
        assert steps[1] == {"action": "type", "target": "Email", "value": "dev@example.com"}
        # Named roles import as "role name" — the flow's documented addressing.
        assert steps[3] == {"action": "click", "target": "button Sign in"}
        assert steps[4] == {"action": "press", "key": "Enter"}
        assert steps[5] == {"action": "wait", "url_contains": "**/dashboard"}
        assert steps[6] == {"action": "assert_visible", "target": "Dashboard"}
        assert steps[7] == {"action": "assert_url", "value": "/dashboard"}
        assert steps[9] == {"action": "assert_enabled", "target": "button Save"}

    def test_negations_and_checks(self):
        doc = playwright_to_flow("""
          await expect(page.getByText('Old')).not.toBeVisible();
          await expect(page.getByLabel('Remember')).toBeChecked();
          await page.getByLabel('Country').selectOption('US');
          await page.getByText('Avatar').hover();
        """)
        assert doc["steps"][0] == {"action": "assert_not_visible", "target": "Old"}
        assert doc["steps"][1] == {"action": "assert_checked", "target": "Remember"}
        assert doc["steps"][2] == {"action": "select", "target": "Country", "value": "US"}
        assert doc["steps"][3] == {"action": "hover", "target": "Avatar"}

    def test_unparsed_is_reported(self):
        doc = playwright_to_flow("""
          await page.goto('http://x');
          await page.locator('.fancy > div').click();
          await page.getByTestId('avatar').setInputFiles('a.png');
        """)
        assert doc["steps"][0]["action"] == "navigate"
        # CSS locators now import as selector steps; only setInputFiles is lost.
        assert doc["steps"][1] == {"action": "click", "selector": ".fancy > div"}
        assert len(doc["unparsed"]) == 1
        assert doc["unparsed"][0]["reason"] == "unsupported-action"

    def test_round_trip(self):
        exported = flow_to_playwright(FLOW, name="login flow")
        back = playwright_to_flow(exported)
        assert [s["action"] for s in back["steps"]] == [
            "navigate", "type", "click", "assert_visible", "assert_url", "screenshot",
        ]
        assert back["unparsed"] == []


class TestImportRoute:
    def test_import_route(self):
        from fastapi.testclient import TestClient
        from backend.engine import app

        with TestClient(app) as client:
            resp = client.post("/browser/sessions/import", json={
                "code": "await page.goto('http://x');\nawait page.getByText('Go').click();\n",
            })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["count"] == 2
        assert body["steps"][1] == {"action": "click", "target": "Go"}


class TestImportV2:
    def test_multiline_chain_joins(self):
        doc = playwright_to_flow("""
          await page.getByRole('button', {
            name: 'Sign in'
          }).click();
        """)
        assert doc["steps"] == [{"action": "click", "target": "button Sign in"}]
        assert doc["unparsed"] == []

    def test_locator_and_testid_become_selectors(self):
        doc = playwright_to_flow("""
          await page.locator('#login-form input[name="q"]').fill('hi');
          await page.getByTestId('avatar').click();
          await page.getByPlaceholder('Search').fill('x');
        """)
        assert doc["steps"][0] == {"action": "type",
                                   "selector": '#login-form input[name="q"]',
                                   "value": "hi"}
        assert doc["steps"][1] == {"action": "click", "selector": '[data-testid="avatar"]'}
        assert doc["steps"][2] == {"action": "type", "selector": '[placeholder="Search"]',
                                   "value": "x"}

    def test_evaluate_steps(self):
        doc = playwright_to_flow("""
          await page.evaluate("document.title");
          await page.evaluate(() => window.__state);
        """)
        assert doc["steps"][0] == {"action": "evaluate", "script": "document.title"}
        assert doc["steps"][1]["action"] == "evaluate"
        assert doc["steps"][1]["script"].endswith("()")

    def test_wait_for_response_becomes_assertion(self):
        doc = playwright_to_flow("await page.waitForResponse('**/api/order');")
        assert doc["steps"] == [{"action": "assert_made_request", "value": "**/api/order"}]

    def test_route_becomes_network_policy(self):
        doc = playwright_to_flow("""
          await page.route('**/api/user', async (route) => {
            await route.fulfill({ status: 200, json: {"name": "Dev"} });
          });
          await page.route('**/analytics/**', async (route) => {
            await route.abort();
          });
          await page.goto('http://x');
        """)
        assert doc["steps"] == [{"action": "navigate", "url": "http://x"}]
        assert doc["network"]["mocks"] == [
            {"url": "**/api/user", "status": 200, "json": {"name": "Dev"}},
        ]
        assert doc["network"]["fail"] == [{"url": "**/analytics/**"}]
        assert doc["unparsed"] == []

    def test_control_flow_and_fixtures_are_reported(self):
        doc = playwright_to_flow("""
          import { test } from './fixtures';
          test('x', async ({ page, orderPage }) => {
            for (const item of items) {
              await item.add();
            }
            await orderPage.checkout();
            await page.goto('http://x');
          });
        """)
        assert doc["steps"] == [{"action": "navigate", "url": "http://x"}]
        reasons = {u.get("reason") for u in doc["unparsed"]}
        assert "control-flow" in reasons
        assert "page-object/fixture" in reasons or "custom-helper" in reasons

    def test_single_line_test_step_unwrap(self):
        doc = playwright_to_flow(
            "await test.step('go', async () => { await page.goto('http://x'); });"
        )
        assert doc["steps"] == [{"action": "navigate", "url": "http://x"}]

    def test_each_expands_positional_rows(self):
        doc = playwright_to_flow("""
          test.each([['alice', 'a1'], ['bob', 'b2']])('login %s', async (user, pw) => {
            await page.getByLabel('Email').fill(user);
            await page.getByLabel('Password').fill(pw);
          });
        """)
        assert "variants" in doc
        assert len(doc["variants"]) == 2
        first = doc["variants"][0]["steps"]
        assert first[0] == {"action": "type", "target": "Email", "value": "alice"}
        assert first[1] == {"action": "type", "target": "Password", "value": "a1"}
        assert doc["variants"][1]["steps"][0]["value"] == "bob"
        assert doc["unparsed"] == []

    def test_each_expands_object_rows(self):
        doc = playwright_to_flow("""
          test.each([{ user: 'alice' }])('login', async ({ user }) => {
            await page.getByText(user).click();
          });
        """)
        assert doc["variants"][0]["steps"] == [
            {"action": "click", "target": "alice"}
        ]

    def test_each_with_setup_prepended(self):
        doc = playwright_to_flow("""
          test.beforeEach(async ({ page }) => {
            await page.goto('http://x');
          });
          test.each([['a']])('t', async (v) => {
            await page.getByText(v).click();
          });
        """)
        variant = doc["variants"][0]
        assert variant["steps"][0] == {"action": "navigate", "url": "http://x"}
        assert variant["steps"][1] == {"action": "click", "target": "a"}

    def test_before_each_setup_prepended(self):
        doc = playwright_to_flow("""
          test.beforeEach(async ({ page }) => {
            await page.goto('http://x/login');
          });
          test('a', async ({ page }) => {
            await page.getByText('Go').click();
          });
        """)
        assert doc["steps"][0] == {"action": "navigate", "url": "http://x/login"}
        assert doc["steps"][1] == {"action": "click", "target": "Go"}

    def test_use_base_url_resolves_relative_goto(self):
        doc = playwright_to_flow("""
          test.use({ baseURL: 'http://localhost:3000' });
          test('a', async ({ page }) => {
            await page.goto('/dashboard');
          });
        """)
        assert doc["base_url"] == "http://localhost:3000"
        assert doc["steps"] == [
            {"action": "navigate", "url": "http://localhost:3000/dashboard"}
        ]

    def test_use_storage_state_path_extracted(self):
        doc = playwright_to_flow("""
          test.use({ storageState: 'auth.json' });
          test('a', async ({ page }) => { await page.goto('http://x'); });
        """)
        assert doc["storage_state_path"] == "auth.json"
