"""Regression: the network log recorded only ``scheme://host``.

Every request to ``127.0.0.1:7878`` looked identical, so a flow report could
not show which API endpoint failed. The log now keeps the path and redacts
query *values* (keys stay visible).
"""
from __future__ import annotations

from backend.modules.browser_debug import NetworkPolicy, redact_url


def test_redact_keeps_path_and_query_keys():
    assert redact_url("https://api.test/v1/render?q=secret&token=abc#frag") == \
        "https://api.test/v1/render?q=<redacted>&token=<redacted>"


def test_redact_keeps_port_drops_userinfo():
    assert redact_url("http://user:pw@127.0.0.1:7878/api/scene/svg?id=p1") == \
        "http://127.0.0.1:7878/api/scene/svg?id=<redacted>"


def test_redact_bare_query_and_length_bound():
    assert redact_url("https://a.test/x?opaque") == "https://a.test/x?opaque=<redacted>"
    long = redact_url("https://a.test/" + "p" * 2000)
    assert len(long) == 512 and long.endswith("...")


def test_policy_log_has_path_not_query_values():
    policy = NetworkPolicy(["example.com"], resolver=lambda host: ["93.184.216.34"])
    policy.decide("https://example.com/api/render?spec=hero&key=s3cr3t")
    policy.decide("https://evil.test/steal?c=cookie")
    seen = policy.report()["requests_seen"]
    assert seen[0]["url"] == "https://example.com/api/render?spec=<redacted>&key=<redacted>"
    assert seen[1]["url"] == "https://evil.test/steal?c=<redacted>"
    assert "s3cr3t" not in str(seen) and "cookie" not in str(seen)
    assert policy.report()["blocked_requests"][0]["host"] == "evil.test"
