"""The `/proxy` header policy and body rewriting.

`web_proxy` strips `X-Frame-Options` and CSP from the target site so the page
can render inside our iframe — that is the endpoint's entire reason to exist.
The same code then sets permissive values, because the app's
`SecurityHeadersMiddleware` only adds headers that are *absent* and would
otherwise re-add `X-Frame-Options: DENY`.

Both halves look like a mistake in isolation, so both are pinned here:

* the iframe blockers from upstream must be gone;
* the permissive values must be **present**, not merely absent;
* `content-encoding` and `content-length` must be dropped, because the body may
  be rewritten and a stale length truncates it;
* non-HTML bodies are returned untouched, because rewriting them corrupts them
  for no benefit.
"""
from __future__ import annotations

import pytest

from backend.routes.proxy import (
    build_proxy_headers,
    rewrite_body,
    _PASSTHROUGH_PREFIXES,
)


class TestHeaderPolicy:
    def test_upstream_iframe_blockers_are_removed(self):
        headers = build_proxy_headers({
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
            "Content-Type": "text/html",
        })
        # The upstream values must not survive.
        assert headers.get("X-Frame-Options") != "DENY"
        assert "frame-ancestors 'none'" not in headers.get(
            "Content-Security-Policy", "")

    def test_permissive_values_are_set_not_just_absent(self):
        """SecurityHeadersMiddleware only adds *missing* headers."""
        headers = build_proxy_headers({})
        assert headers["X-Frame-Options"] == "SAMEORIGIN"
        csp = headers["Content-Security-Policy"]
        assert "frame-ancestors *" in csp
        assert "default-src *" in csp

    def test_body_describing_headers_are_dropped(self):
        """The body gets rewritten, so the upstream length is a lie."""
        headers = build_proxy_headers({
            "Content-Encoding": "gzip",
            "Content-Length": "12345",
        })
        assert "Content-Encoding" not in headers
        assert "Content-Length" not in headers

    def test_other_headers_pass_through(self):
        headers = build_proxy_headers({
            "Content-Type": "text/html; charset=utf-8",
            "X-Powered-By": "something",
        })
        assert headers["Content-Type"] == "text/html; charset=utf-8"
        assert headers["X-Powered-By"] == "something"

    def test_blocking_is_case_insensitive(self):
        """Upstream header casing varies; the blocklist must not."""
        headers = build_proxy_headers({"x-FRAME-options": "DENY",
                                       "CONTENT-ENCODING": "br"})
        assert not any(k.lower() == "x-frame-options" and v == "DENY"
                       for k, v in headers.items())
        assert not any(k.lower() == "content-encoding" for k in headers)

    def test_nothing_is_mutated(self):
        """The upstream dict is a snapshot; mutating it would corrupt the cache."""
        upstream = {"Content-Type": "text/html"}
        build_proxy_headers(upstream)
        assert upstream == {"Content-Type": "text/html"}

    def test_caching_is_disabled_on_the_proxied_response(self):
        headers = build_proxy_headers({})
        assert headers["Cache-Control"] == "no-cache, no-store, must-revalidate"
        assert headers["Pragma"] == "no-cache"
        assert headers["Expires"] == "0"


class TestRewriteBody:
    def test_html_gets_a_base_tag(self):
        out = rewrite_body(b"<html><body>hi</body></html>", "text/html",
                           "https://example.com/page")
        assert b"<base" in out
        assert isinstance(out, bytes)

    def test_css_is_rewritten(self):
        out = rewrite_body(b"body { background: url(/img.png); }", "text/css",
                           "https://example.com/a/style.css")
        assert b"proxy" in out

    def test_binary_types_are_returned_untouched(self):
        for prefix in _PASSTHROUGH_PREFIXES:
            body = f"\x00\x01{prefix}-payload".encode()
            assert rewrite_body(body, f"{prefix}whatever",
                                "https://example.com/") == body

    def test_unknown_content_type_is_passed_through_byte_for_byte(self):
        body = b'{"not": "html"}'
        assert rewrite_body(body, "application/json",
                            "https://example.com/api") == body

    def test_undecodable_bytes_do_not_raise(self):
        """A latin-1 page must come back, not blow up the response."""
        out = rewrite_body(b"<html>caf\xe9</html>", "text/html",
                           "https://example.com/")
        assert b"caf" in out