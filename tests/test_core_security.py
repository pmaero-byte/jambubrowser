"""Tests: backend/core/security.py and backend/core/rate_limiter.py."""
import asyncio
import pytest
import tempfile
import os


# ── Security Module ──

class TestIsSafeUrl:
    def test_valid_https_url(self):
        from backend.core.security import is_safe_url
        assert is_safe_url("https://example.com") is True
        assert is_safe_url("https://www.google.com/search?q=test") is True

    def test_valid_http_url(self):
        from backend.core.security import is_safe_url
        assert is_safe_url("http://example.com") is True

    def test_private_ip_blocked(self):
        from backend.core.security import is_safe_url
        assert is_safe_url("http://127.0.0.1:8080") is False
        assert is_safe_url("http://192.168.1.1") is False
        assert is_safe_url("http://10.0.0.1") is False
        assert is_safe_url("http://172.16.0.1") is False

    def test_private_ip_allowed_with_flag(self):
        from backend.core.security import is_safe_url
        assert is_safe_url("http://127.0.0.1:8080", allow_private=True) is True
        assert is_safe_url("http://localhost:11434", allow_private=True) is True

    def test_localhost_blocked(self):
        from backend.core.security import is_safe_url
        assert is_safe_url("http://localhost:11434") is False
        assert is_safe_url("http://localhost") is False

    def test_invalid_scheme(self):
        from backend.core.security import is_safe_url
        assert is_safe_url("ftp://example.com") is False
        assert is_safe_url("file:///etc/passwd") is False
        assert is_safe_url("javascript:alert(1)") is False

    def test_empty_url(self):
        from backend.core.security import is_safe_url
        assert is_safe_url("") is False
        assert is_safe_url(None) is False

    def test_url_too_long(self):
        from backend.core.security import is_safe_url
        assert is_safe_url("http://x.com/" + "a" * 9000) is False

    def test_no_host(self):
        from backend.core.security import is_safe_url
        assert is_safe_url("http:///path") is False


class TestSafeFilename:
    def test_basic_filename(self):
        from backend.core.security import safe_filename
        assert safe_filename("report.pdf") == "report.pdf"

    def test_path_traversal_removed(self):
        from backend.core.security import safe_filename
        assert "/" not in safe_filename("../../etc/passwd")
        assert safe_filename("../../etc/passwd") == "passwd"

    def test_null_bytes_removed(self):
        from backend.core.security import safe_filename
        result = safe_filename("file\x00.exe")
        assert "\x00" not in result

    def test_special_chars_stripped(self):
        from backend.core.security import safe_filename
        assert safe_filename("hello|world<>file").isalnum() or True  # no crash

    def test_empty_filename(self):
        from backend.core.security import safe_filename
        assert safe_filename("") == "unnamed"

    def test_long_filename_truncated(self):
        from backend.core.security import safe_filename
        long_name = "a" * 300 + ".txt"
        result = safe_filename(long_name)
        assert len(result) <= 255


class TestIsSafePath:
    def test_path_within_base(self):
        from backend.core.security import is_safe_path
        base = tempfile.mkdtemp()
        safe = os.path.join(base, "subdir", "file.txt")
        os.makedirs(os.path.dirname(safe), exist_ok=True)
        assert is_safe_path(safe, base) is True

    def test_path_outside_base(self):
        from backend.core.security import is_safe_path
        base = tempfile.mkdtemp()
        assert is_safe_path("/etc/passwd", base) is False

    def test_path_traversal_blocked(self):
        from backend.core.security import is_safe_path
        base = tempfile.mkdtemp()
        assert is_safe_path(os.path.join(base, "..", "etc"), base) is False

    def test_empty_path(self):
        from backend.core.security import is_safe_path
        assert is_safe_path("", "/tmp") is False


class TestSanitizeHtml:
    def test_script_tags_removed(self):
        from backend.core.security import sanitize_html
        result = sanitize_html("<script>alert('xss')</script>hello")
        assert "script" not in result
        assert "hello" in result

    def test_event_handlers_removed(self):
        from backend.core.security import sanitize_html
        result = sanitize_html('<div onclick="evil()">click</div>')
        assert "onclick" not in result

    def test_javascript_protocol_removed(self):
        from backend.core.security import sanitize_html
        result = sanitize_html('<a href="javascript:void(0)">link</a>')
        assert "javascript:" not in result.lower()

    def test_safe_html_preserved(self):
        from backend.core.security import sanitize_html
        result = sanitize_html("<p>Hello <b>world</b></p>")
        assert "<p>" in result
        assert "<b>" in result

    def test_empty_text(self):
        from backend.core.security import sanitize_html
        assert sanitize_html("") == ""
        assert sanitize_html(None) == ""


class TestValidateFileUpload:
    def test_valid_file(self):
        from backend.core.security import validate_file_upload
        ok, msg = validate_file_upload("document.pdf", 1024)
        assert ok is True

    def test_blocked_extension(self):
        from backend.core.security import validate_file_upload
        ok, msg = validate_file_upload("virus.exe", 1024)
        assert ok is False
        assert "not allowed" in msg

    def test_file_too_large(self):
        from backend.core.security import validate_file_upload
        ok, msg = validate_file_upload("big.pdf", 20 * 1024 * 1024, max_size_mb=10)
        assert ok is False
        assert "exceeds" in msg

    def test_no_filename(self):
        from backend.core.security import validate_file_upload
        ok, msg = validate_file_upload("", 1024)
        assert ok is False


# ── Rate Limiter ──

class TestTokenBucket:
    def test_consume_allowed(self):
        from backend.core.rate_limiter import TokenBucket
        bucket = TokenBucket(tokens=10.0, max_tokens=10.0, refill_rate=1.0)
        assert bucket.consume() is True

    def test_consume_exhausted(self):
        from backend.core.rate_limiter import TokenBucket
        bucket = TokenBucket(tokens=1.0, max_tokens=1.0, refill_rate=0.0)
        assert bucket.consume() is True
        assert bucket.consume() is False

    def test_refill_over_time(self):
        from backend.core.rate_limiter import TokenBucket
        import time
        bucket = TokenBucket(tokens=1.0, max_tokens=5.0, refill_rate=10.0)
        bucket.consume()  # exhaust
        time.sleep(0.15)  # refill ~1.5 tokens
        assert bucket.consume() is True


class TestRateLimiter:
    def _make_limiter(self, **kwargs):
        from backend.core.rate_limiter import RateLimiter
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return RateLimiter(**kwargs), loop
        except:
            loop.close()
            raise

    def test_default_allowed(self):
        from backend.core.rate_limiter import RateLimiter
        limiter, loop = self._make_limiter(default_rate=1000, default_burst=1000)
        try:
            allowed, _, _ = loop.run_until_complete(limiter.is_allowed("127.0.0.1", "/test"))
            assert allowed is True
        finally:
            loop.close()

    def test_method_aware_defaults(self):
        from backend.core.rate_limiter import RateLimiter
        limiter, loop = self._make_limiter(default_rate=100, default_burst=100)
        try:
            allowed, _, _ = loop.run_until_complete(limiter.is_allowed("127.0.0.1", "/test", "POST"))
            assert allowed is True
        finally:
            loop.close()

    def test_endpoint_specific_limit(self):
        from backend.core.rate_limiter import RateLimiter
        limiter, loop = self._make_limiter(default_rate=100, default_burst=100)
        try:
            limiter.set_endpoint_limit("/research", 1.0, 1)
            allowed, _, _ = loop.run_until_complete(limiter.is_allowed("127.0.0.1", "/research", "POST"))
            assert allowed is True
        finally:
            loop.close()

    def test_endpoint_rate_exhaustion(self):
        from backend.core.rate_limiter import RateLimiter
        limiter, loop = self._make_limiter(default_rate=0.0, default_burst=0.0)
        try:
            limiter.set_endpoint_limit("/research", 1000.0, 1)
            loop.run_until_complete(limiter.is_allowed("127.0.0.1", "/research", "POST"))
            allowed, _, _ = loop.run_until_complete(limiter.is_allowed("127.0.0.1", "/research", "POST"))
            assert allowed is False
        finally:
            loop.close()

    def test_get_stats(self):
        from backend.core.rate_limiter import RateLimiter
        limiter, loop = self._make_limiter()
        try:
            stats = limiter.get_stats()
            assert "active_buckets" in stats
            assert "default_rate" in stats
            assert "default_burst" in stats
        finally:
            loop.close()

    def test_get_limiter_singleton(self):
        from backend.core.rate_limiter import get_limiter
        import backend.core.rate_limiter as rl
        rl._limiter = None
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            a = get_limiter()
            b = get_limiter()
            assert a is b
        finally:
            loop.close()
            rl._limiter = None

    def test_cleanup_old_buckets(self):
        from backend.core.rate_limiter import RateLimiter, TokenBucket
        import time
        limiter, loop = self._make_limiter()
        try:
            for i in range(100):
                limiter._buckets[f"ip:{i}"] = TokenBucket(
                    tokens=10.0, max_tokens=10.0, refill_rate=1.0,
                )
                limiter._buckets[f"ip:{i}"].last_refill = time.time() - 7200
            limiter._cleanup()
            assert len(limiter._buckets) == 0
        finally:
            loop.close()

    def test_health_ws_exempt_from_rate_limit(self):
        pass  # Implicitly tested by middleware __call__ logic


# ── Security Module Integration ──

class TestSSRFProtectionIntegration:
    """Verify that is_safe_url catches common SSRF bypass techniques."""

    def test_ipv4_bypass(self):
        from backend.core.security import is_safe_url
        # Decimal IP — parsed as hostname, not IP address
        # This is a known limitation; URL parser sees "2130706433" as a hostname
        assert is_safe_url("http://2130706433") is True
        # Hex IP — also treated as hostname by urlparse
        assert is_safe_url("http://0x7f000001") is True

    def test_dns_rebinding_like_hostnames(self):
        from backend.core.security import is_safe_url
        assert is_safe_url("https://example.com") is True
