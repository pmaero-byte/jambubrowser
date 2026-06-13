"""Tests: audit PII redaction."""
import pytest
from unittest.mock import patch


class TestAuditPiiRedaction:
    def _redact(self, data):
        from backend.core.audit import AuditLogger
        with patch("backend.core.audit.get_db_cursor"):
            logger = AuditLogger.__new__(AuditLogger)
        return logger._redact_pii(data)

    def test_email_address_masked(self):
        result = self._redact({"contact": "Reach me at jane.doe@example.com please"})
        assert "jane.doe@example.com" not in result["contact"]
        assert "REDACTED_EMAIL" in result["contact"]

    def test_phone_number_masked(self):
        result = self._redact({"note": "Call 415-555-1234 anytime"})
        assert "415-555-1234" not in result["note"]
        assert "REDACTED" in result["note"]

    def test_ssn_masked(self):
        result = self._redact({"info": "SSN is 123-45-6789"})
        assert "123-45-6789" not in result["info"]

    def test_credit_card_masked(self):
        result = self._redact({"card": "Card: 4532-1234-5678-9012"})
        assert "4532-1234-5678-9012" not in result["card"]

    def test_ip_address_masked(self):
        result = self._redact({"origin": "From 192.168.1.100 today"})
        assert "192.168.1.100" not in result["origin"]

    def test_password_key_fully_redacted(self):
        result = self._redact({"password": "hunter2", "user": "alice"})
        assert result["password"] == "[REDACTED_SECRET]"
        assert result["user"] == "alice"

    def test_api_key_key_fully_redacted(self):
        result = self._redact({"api_key": "sk-abc-123", "data": "ok"})
        assert result["api_key"] == "[REDACTED_SECRET]"

    def test_token_key_fully_redacted(self):
        result = self._redact({"token": "abc123"})
        assert result["token"] == "[REDACTED_SECRET]"

    def test_nested_dict_redacted(self):
        data = {"outer": {"email": "a@b.com", "note": "hello"}}
        result = self._redact(data)
        assert "a@b.com" not in result["outer"]["email"]
        assert result["outer"]["note"] == "hello"

    def test_list_of_strings_redacted(self):
        data = {"logins": ["alice@x.com", "bob@y.com", "plain text"]}
        result = self._redact(data)
        assert "@x.com" not in result["logins"][0]
        assert "@y.com" not in result["logins"][1]
        assert result["logins"][2] == "plain text"

    def test_list_of_dicts_redacted(self):
        data = {"users": [{"email": "a@b.com"}, {"name": "alice"}]}
        result = self._redact(data)
        assert "a@b.com" not in result["users"][0]["email"]
        assert result["users"][1]["name"] == "alice"

    def test_non_string_values_pass_through(self):
        data = {"count": 42, "ratio": 3.14, "active": True, "tags": None}
        result = self._redact(data)
        assert result["count"] == 42
        assert result["ratio"] == 3.14
        assert result["active"] is True
        assert result["tags"] is None

    def test_plain_text_unchanged(self):
        data = {"message": "the quick brown fox jumps over the lazy dog"}
        result = self._redact(data)
        assert result["message"] == "the quick brown fox jumps over the lazy dog"

    def test_empty_dict(self):
        assert self._redact({}) == {}

    def test_redaction_preserves_remaining_text(self):
        result = self._redact({"body": "Email me at test@x.com for details"})
        assert "Email me at" in result["body"]
        assert "for details" in result["body"]
        assert "test@x.com" not in result["body"]
