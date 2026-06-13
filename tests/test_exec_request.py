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
