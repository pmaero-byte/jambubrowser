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
        assert steps[3] == {"action": "click", "target": "Sign in"}
        assert steps[4] == {"action": "press", "key": "Enter"}
        assert steps[5] == {"action": "wait", "url_contains": "**/dashboard"}
        assert steps[6] == {"action": "assert_visible", "target": "Dashboard"}
        assert steps[7] == {"action": "assert_url", "value": "/dashboard"}
        assert steps[9] == {"action": "assert_enabled", "target": "Save"}

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
        assert len(doc["unparsed"]) == 2
        assert {u["line"] for u in doc["unparsed"]} == {3, 4}

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
