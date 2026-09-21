"""Tests for API-step codegen export (Milestone 2)."""
from backend.modules.browser_codegen import flow_to_playwright

API_FLOW = [
    {"action": "navigate", "url": "https://example.com/"},
    {"action": "api", "method": "GET",
     "url": "https://example.com/api/health", "expect_status": "2xx"},
    {"action": "assert_status", "value": "200"},
    {"action": "assert_json", "path": "data.items[0].id", "expected": 7},
    {"action": "assert_header", "header": "content-type", "value": "json"},
    {"action": "assert_latency", "value": 250},
    {"action": "assert_schema", "required": ["ok"]},
]


class TestApiCodegen:
    def test_uses_request_fixture(self):
        code = flow_to_playwright(API_FLOW, name="api flow")
        assert "async ({ page, request }) =>" in code
        assert "await request.get('https://example.com/api/health');" in code

    def test_expect_status_becomes_expectation(self):
        code = flow_to_playwright(API_FLOW)
        assert "expect(res1.ok()).toBeTruthy();" in code

    def test_asserts_reference_the_response_var(self):
        code = flow_to_playwright(API_FLOW)
        assert "expect(res1.status()).toBe(200);" in code
        assert ("expect((await res1.json())['data']['items'][0]['id'])"
                ".toEqual(7);" in code)
        assert ("expect(res1.headers()['content-type'])"
                ".toContain('json');" in code)
        assert "// Budget: api latency <= 250ms" in code
        assert "// Schema check: required keys [\"ok\"]" in code

    def test_post_body_is_serialised(self):
        code = flow_to_playwright([
            {"action": "api", "method": "POST",
             "url": "https://example.com/api/orders",
             "json": {"sku": "A"}, "expect_status": 201},
        ])
        assert "await request.post(" in code
        assert "data: {\"sku\": \"A\"}" in code
        assert "expect(res1.status()).toBe(201);" in code

    def test_api_assert_without_api_step_is_annotated(self):
        code = flow_to_playwright([{"action": "assert_status",
                                    "value": "200"}])
        assert "no preceding api step" in code
