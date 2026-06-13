"""Tests: backend/core/privacy.py — PII detection, network isolation, content sanitization."""
import pytest


class TestPIIDetector:
    def test_detect_email(self):
        from backend.core.privacy import PIIDetector
        findings = PIIDetector.detect_pii("Contact me at jane@example.com")
        assert "email" in findings
        assert "jane@example.com" in findings["email"]

    def test_detect_us_phone(self):
        from backend.core.privacy import PIIDetector
        findings = PIIDetector.detect_pii("Call 415-555-1234")
        assert "phone_us" in findings

    def test_detect_ssn(self):
        from backend.core.privacy import PIIDetector
        findings = PIIDetector.detect_pii("SSN: 123-45-6789")
        assert "ssn" in findings
        assert "123-45-6789" in findings["ssn"]

    def test_detect_credit_card(self):
        from backend.core.privacy import PIIDetector
        findings = PIIDetector.detect_pii("Card 4532-1234-5678-9012 on file")
        assert "credit_card" in findings

    def test_detect_ip_address(self):
        from backend.core.privacy import PIIDetector
        findings = PIIDetector.detect_pii("Server at 192.168.1.1")
        assert "ip_address" in findings

    def test_detect_mac_address(self):
        from backend.core.privacy import PIIDetector
        findings = PIIDetector.detect_pii("MAC: AA:BB:CC:DD:EE:FF")
        assert "mac_address" in findings

    def test_detect_no_pii_in_clean_text(self):
        from backend.core.privacy import PIIDetector
        findings = PIIDetector.detect_pii("The quick brown fox jumps over the lazy dog")
        assert findings == {}

    def test_mask_email_replaces_value(self):
        from backend.core.privacy import PIIDetector
        masked = PIIDetector.mask_pii("Email: test@x.com for info", "email")
        assert "test@x.com" not in masked
        assert "REDACTED_EMAIL" in masked
        assert "for info" in masked

    def test_mask_ssn(self):
        from backend.core.privacy import PIIDetector
        masked = PIIDetector.mask_pii("SSN 123-45-6789 verified", "ssn")
        assert "123-45-6789" not in masked
        assert "verified" in masked

    def test_mask_unknown_type_returns_input(self):
        from backend.core.privacy import PIIDetector
        text = "unchanged"
        assert PIIDetector.mask_pii(text, "nonexistent") == text

    def test_detect_tracking_ga(self):
        from backend.core.privacy import PIIDetector
        findings = PIIDetector.detect_tracking("UA-12345-6 tracking")
        assert "google_analytics" in findings

    def test_detect_tracking_fbq(self):
        from backend.core.privacy import PIIDetector
        findings = PIIDetector.detect_tracking("fbq(12345, 'PageView')")
        assert "facebook_pixel" in findings


class TestNetworkIsolator:
    def test_local_only_blocks_external(self):
        from backend.core.privacy import NetworkIsolator, PrivacyMode
        ni = NetworkIsolator(PrivacyMode.LOCAL_ONLY)
        assert ni.is_url_allowed("http://example.com") is False
        assert ni.is_url_allowed("https://google.com") is False

    def test_local_only_allows_local(self):
        from backend.core.privacy import NetworkIsolator, PrivacyMode
        ni = NetworkIsolator(PrivacyMode.LOCAL_ONLY)
        assert ni.is_url_allowed("http://localhost:8001/api") is True
        assert ni.is_url_allowed("http://127.0.0.1:8001/api") is True

    def test_maximum_blocks_known_trackers(self):
        from backend.core.privacy import NetworkIsolator, PrivacyMode
        ni = NetworkIsolator(PrivacyMode.MAXIMUM)
        assert ni.is_url_allowed("https://google-analytics.com/collect") is False
        assert ni.is_url_allowed("https://facebook.com/tr") is False

    def test_maximum_allows_local(self):
        from backend.core.privacy import NetworkIsolator, PrivacyMode
        ni = NetworkIsolator(PrivacyMode.MAXIMUM)
        assert ni.is_url_allowed("http://localhost:8001/api") is True

    def test_maximum_allows_legitimate(self):
        from backend.core.privacy import NetworkIsolator, PrivacyMode
        ni = NetworkIsolator(PrivacyMode.MAXIMUM)
        assert ni.is_url_allowed("https://wikipedia.org/wiki/Python") is True

    def test_standard_allows_everything(self):
        from backend.core.privacy import NetworkIsolator, PrivacyMode
        ni = NetworkIsolator(PrivacyMode.STANDARD)
        assert ni.is_url_allowed("https://google-analytics.com/collect") is True
        assert ni.is_url_allowed("https://example.com") is True

    def test_blocked_requests_tracked(self):
        from backend.core.privacy import NetworkIsolator, PrivacyMode
        ni = NetworkIsolator(PrivacyMode.MAXIMUM)
        ni.is_url_allowed("https://google-analytics.com/x")
        ni.is_url_allowed("https://facebook.com/y")
        blocked = ni.get_blocked_requests()
        assert len(blocked) == 2
        assert blocked[0]["reason"] == "blocked_domain"

    def test_sanitize_headers_removes_tracking(self):
        from backend.core.privacy import NetworkIsolator, PrivacyMode
        ni = NetworkIsolator(PrivacyMode.MAXIMUM)
        headers = {
            "X-Forwarded-For": "1.2.3.4",
            "X-Real-IP": "5.6.7.8",
            "User-Agent": "test-browser",
            "Content-Type": "application/json",
        }
        clean = ni.sanitize_headers(headers)
        assert "x-forwarded-for" not in clean
        assert "x-real-ip" not in clean
        assert clean["User-Agent"] == "test-browser"
        assert clean["Content-Type"] == "application/json"


class TestContentSanitizer:
    def test_sanitize_for_storage_removes_pii(self):
        from backend.core.privacy import ContentSanitizer, PrivacyMode
        cs = ContentSanitizer(PrivacyMode.ENHANCED)
        content = "Email me at jane@example.com or call 415-555-1234"
        sanitized, result = cs.sanitize_for_storage(content)
        assert "jane@example.com" not in sanitized
        assert "415-555-1234" not in sanitized
        assert result.was_modified is True
        assert any("email" in entry for entry in result.pii_removed)

    def test_sanitize_keeps_clean_content_intact(self):
        from backend.core.privacy import ContentSanitizer, PrivacyMode
        cs = ContentSanitizer(PrivacyMode.ENHANCED)
        content = "Python is a high-level programming language"
        sanitized, result = cs.sanitize_for_storage(content)
        assert sanitized == content
        assert result.was_modified is False

    def test_sanitize_can_skip_pii(self):
        from backend.core.privacy import ContentSanitizer, PrivacyMode
        cs = ContentSanitizer(PrivacyMode.ENHANCED)
        content = "Email: jane@example.com"
        sanitized, result = cs.sanitize_for_storage(content, remove_pii=False)
        assert "jane@example.com" in sanitized

    def test_sanitize_length_tracking(self):
        from backend.core.privacy import ContentSanitizer, PrivacyMode
        cs = ContentSanitizer(PrivacyMode.ENHANCED)
        content = "x" * 1000
        sanitized, result = cs.sanitize_for_storage(content)
        assert result.original_length == 1000
        assert result.sanitized_length == 1000
        assert sanitized == content


class TestPrivacyManager:
    def test_default_mode(self):
        from backend.core.privacy import PrivacyManager
        pm = PrivacyManager()
        assert pm.mode is not None

    def test_init_with_mode(self):
        from backend.core.privacy import PrivacyManager, PrivacyMode
        pm = PrivacyManager(PrivacyMode.LOCAL_ONLY)
        assert pm.mode == PrivacyMode.LOCAL_ONLY

    def test_check_url_allowed_delegates(self):
        from backend.core.privacy import PrivacyManager, PrivacyMode
        pm = PrivacyManager(PrivacyMode.LOCAL_ONLY)
        assert pm.check_url_allowed("https://example.com") is False
        assert pm.check_url_allowed("http://localhost:8001") is True

    def test_get_privacy_report(self):
        from backend.core.privacy import PrivacyManager
        pm = PrivacyManager()
        report = pm.get_privacy_report()
        assert "mode" in report
        assert "blocked_requests" in report
        assert "local_only" in report
