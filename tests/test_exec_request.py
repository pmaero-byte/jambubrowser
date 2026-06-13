"""Tests: ExecRequest validation."""
import pytest
from pydantic import ValidationError


class TestExecRequestValidation:
    def _make(self, **kwargs):
        from backend.routes.research import ExecRequest
        defaults = {"code": "print('hi')", "timeout": 30, "client_id": "c1"}
        defaults.update(kwargs)
        return ExecRequest(**defaults)

    def test_valid_request(self):
        req = self._make()
        assert req.timeout == 30
        assert req.code == "print('hi')"

    def test_rejects_zero_timeout(self):
        with pytest.raises(ValidationError):
            self._make(timeout=0)

    def test_rejects_negative_timeout(self):
        with pytest.raises(ValidationError):
            self._make(timeout=-5)

    def test_rejects_excessive_timeout(self):
        with pytest.raises(ValidationError):
            self._make(timeout=999999)

    def test_rejects_huge_code(self):
        with pytest.raises(ValidationError):
            self._make(code="x" * 50001)

    def test_accepts_max_timeout(self):
        req = self._make(timeout=120)
        assert req.timeout == 120

    def test_accepts_max_code_length(self):
        req = self._make(code="x" * 50000)
        assert len(req.code) == 50000


class TestResearchRequestValidation:
    def _make(self, **kwargs):
        from backend.routes.research import ResearchRequest
        defaults = {"query": "what is python"}
        defaults.update(kwargs)
        return ResearchRequest(**defaults)

    def test_valid_request(self):
        req = self._make()
        assert req.query == "what is python"
        assert req.top_n == 8
        assert req.domain == "general"

    def test_rejects_empty_query(self):
        with pytest.raises(ValidationError):
            self._make(query="")

    def test_rejects_whitespace_only_query(self):
        with pytest.raises(ValidationError):
            self._make(query="   ")

    def test_rejects_oversized_query(self):
        with pytest.raises(ValidationError):
            self._make(query="x" * 10001)

    def test_rejects_zero_top_n(self):
        with pytest.raises(ValidationError):
            self._make(top_n=0)

    def test_rejects_excessive_top_n(self):
        with pytest.raises(ValidationError):
            self._make(top_n=1000)

    def test_rejects_invalid_domain(self):
        with pytest.raises(ValidationError):
            self._make(domain="malicious")

    def test_accepts_all_valid_domains(self):
        for d in ("general", "academic", "coding"):
            req = self._make(domain=d)
            assert req.domain == d

    def test_accepts_max_top_n(self):
        req = self._make(top_n=50)
        assert req.top_n == 50
