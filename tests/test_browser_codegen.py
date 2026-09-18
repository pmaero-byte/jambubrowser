"""Tests for Playwright flow export."""
from __future__ import annotations

from backend.modules.browser_codegen import flow_to_dict, flow_to_playwright


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
