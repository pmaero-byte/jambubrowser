"""Tests: backend/core/security_events.py."""
import pytest
from unittest.mock import MagicMock, patch


class TestExtractClientIp:
    def test_from_scope_client(self):
        from backend.core.security_events import extract_client_ip
        assert extract_client_ip({"client": ("192.168.1.1", 50000)}) == "192.168.1.1"

    def test_from_x_forwarded_for(self):
        from backend.core.security_events import extract_client_ip
        scope = {
            "client": None,
            "headers": [(b"x-forwarded-for", b"203.0.113.5, 10.0.0.1")],
        }
        assert extract_client_ip(scope) == "203.0.113.5"

    def test_no_client_no_header(self):
        from backend.core.security_events import extract_client_ip
        scope = {"client": None, "headers": []}
        assert extract_client_ip(scope) == "unknown"

    def test_empty_scope(self):
        from backend.core.security_events import extract_client_ip
        assert extract_client_ip({}) == "unknown"

    def test_string_client(self):
        from backend.core.security_events import extract_client_ip
        assert extract_client_ip({"client": "10.0.0.5"}) == "10.0.0.5"


_log_security_event = None


def _get_log_security_event():
    global _log_security_event
    if _log_security_event is None:
        from backend.core.security_events import log_security_event
        _log_security_event = log_security_event
    return _log_security_event


class TestLogSecurityEvent:
    def test_calls_audit_log(self):
        from backend.core.security_events import log_security_event

        with patch("backend.core.audit.get_audit_logger") as mock_get:
            mock_audit = MagicMock()
            mock_get.return_value = mock_audit
            log_security_event(
                action="rate_limit_exceeded",
                client_ip="1.2.3.4",
                path="/api/test",
                method="POST",
                request_id="req-123",
            )

            mock_get.assert_called_once()
            mock_audit.log.assert_called_once()
            kwargs = mock_audit.log.call_args.kwargs
            assert kwargs["action"] == "rate_limit_exceeded"
            assert kwargs["details"]["client_ip"] == "1.2.3.4"
            assert kwargs["details"]["path"] == "/api/test"
            assert kwargs["details"]["method"] == "POST"
            assert kwargs["details"]["request_id"] == "req-123"

    def test_includes_extra_details(self):
        from backend.core.security_events import log_security_event

        with patch("backend.core.audit.get_audit_logger") as mock_get:
            mock_audit = MagicMock()
            mock_get.return_value = mock_audit
            log_security_event(
                action="body_too_large",
                client_ip="1.2.3.4",
                path="/api/upload",
                method="POST",
                request_id="req-456",
                details={"size": 5242880, "limit": 2097152},
            )

            details = mock_audit.log.call_args.kwargs["details"]
            assert details["size"] == 5242880
            assert details["limit"] == 2097152

    def test_swallows_audit_failures(self):
        from backend.core.security_events import log_security_event

        with patch("backend.core.audit.get_audit_logger") as mock_get:
            mock_get.side_effect = Exception("Audit DB is down")
            log_security_event(
                action="untrusted_host",
                client_ip="1.2.3.4",
                path="/",
                method="GET",
            )
            assert True

    def test_handles_missing_audit_module(self):
        from backend.core import security_events
        from backend.core.security_events import log_security_event

        original = security_events.log
        try:
            security_events.log = MagicMock()
            log_security_event(
                action="rate_limit_exceeded",
                client_ip="1.2.3.4",
                path="/",
                method="GET",
            )
            assert True
        finally:
            security_events.log = original

    def test_known_actions_have_descriptions(self):
        from backend.core.security_events import _SECURITY_ACTIONS
        assert "rate_limit_exceeded" in _SECURITY_ACTIONS
        assert "body_too_large" in _SECURITY_ACTIONS
        assert "untrusted_host" in _SECURITY_ACTIONS
        assert "request_timeout" in _SECURITY_ACTIONS
