"""Tests: backend/core/supply_chain.py — dependency integrity verifier."""
import pytest
from unittest.mock import patch, MagicMock, mock_open


class TestSupplyChainVerifier:
    def test_init_with_no_hash_file(self):
        from backend.core.supply_chain import SupplyChainVerifier
        verifier = SupplyChainVerifier(base_dir="/tmp/nonexistent_path_xyz")
        assert verifier._known_hashes == {}

    def test_init_loads_existing_hashes(self):
        from backend.core.supply_chain import SupplyChainVerifier
        import json

        mock_data = json.dumps({"requests": "abc123", "flask": "def456"})
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.read_text", return_value=mock_data):
                verifier = SupplyChainVerifier(base_dir="/tmp")
                assert verifier._known_hashes.get("requests") == "abc123"
                assert verifier._known_hashes.get("flask") == "def456"

    def test_init_handles_corrupt_hash_file(self):
        from backend.core.supply_chain import SupplyChainVerifier

        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.read_text", return_value="not valid json"):
                verifier = SupplyChainVerifier(base_dir="/tmp")
                assert verifier._known_hashes == {}

    def test_verify_binary_nonexistent(self):
        from backend.core.supply_chain import SupplyChainVerifier
        verifier = SupplyChainVerifier(base_dir="/tmp")
        ok, msg = verifier.verify_binary("/this/path/does/not/exist")
        assert ok is False
        assert "not found" in msg.lower()

    def test_verify_binary_matches_known_hash(self, tmp_path):
        from backend.core.supply_chain import SupplyChainVerifier
        import hashlib, os, stat

        binary = tmp_path / "mybin"
        binary.write_bytes(b"#!/bin/sh\necho hi\n")
        os.chmod(binary, 0o755)

        known_hash = hashlib.sha256(b"#!/bin/sh\necho hi\n").hexdigest()
        verifier = SupplyChainVerifier(base_dir="/tmp")
        verifier._known_hashes[str(binary)] = known_hash

        ok, msg = verifier.verify_binary(str(binary))
        assert ok is True
        assert "verified" in msg.lower()

    def test_verify_binary_hash_mismatch(self, tmp_path):
        from backend.core.supply_chain import SupplyChainVerifier
        import os

        binary = tmp_path / "tampered"
        binary.write_bytes(b"#!/bin/sh\necho evil\n")
        os.chmod(binary, 0o755)

        verifier = SupplyChainVerifier(base_dir="/tmp")
        verifier._known_hashes[str(binary)] = "0" * 64

        ok, msg = verifier.verify_binary(str(binary))
        assert ok is False
        assert "mismatch" in msg.lower()

    def test_verify_binary_not_executable(self, tmp_path):
        from backend.core.supply_chain import SupplyChainVerifier

        binary = tmp_path / "noexec"
        binary.write_bytes(b"data")
        os_mode = 0o644

        import os
        os.chmod(binary, os_mode)

        verifier = SupplyChainVerifier(base_dir="/tmp")
        ok, msg = verifier.verify_binary(str(binary))
        assert ok is False
        assert "executable" in msg.lower() or "not found" in msg.lower() or "permission" in msg.lower() or "verified" in msg.lower()


class TestSystemComponentVerification:
    def test_verify_python_succeeds(self):
        from backend.core.supply_chain import SupplyChainVerifier
        verifier = SupplyChainVerifier(base_dir="/tmp")
        assert verifier._verify_python() is True

    def test_verify_pip_succeeds(self):
        from backend.core.supply_chain import SupplyChainVerifier
        verifier = SupplyChainVerifier(base_dir="/tmp")
        assert verifier._verify_pip() is True

    def test_verify_playwright(self):
        from backend.core.supply_chain import SupplyChainVerifier
        verifier = SupplyChainVerifier(base_dir="/tmp")
        result = verifier._verify_playwright()
        assert isinstance(result, bool)

    def test_verify_sqlite_vec(self):
        from backend.core.supply_chain import SupplyChainVerifier
        verifier = SupplyChainVerifier(base_dir="/tmp")
        result = verifier._verify_sqlite_vec()
        assert isinstance(result, bool)

    def test_verify_system_components_returns_dict(self):
        from backend.core.supply_chain import SupplyChainVerifier
        verifier = SupplyChainVerifier(base_dir="/tmp")
        components = verifier.verify_system_components()
        assert "python" in components
        assert "pip" in components
        assert "playwright" in components
        assert "sqlite_vec" in components


class TestPackageVerification:
    def test_verify_nonexistent_package(self):
        from backend.core.supply_chain import SupplyChainVerifier
        verifier = SupplyChainVerifier(base_dir="/tmp")

        with patch("importlib.metadata.distribution") as mock_dist:
            from importlib.metadata import PackageNotFoundError
            mock_dist.side_effect = PackageNotFoundError("notfound")
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1, stdout="")
                info = verifier.verify_package("notfound")
        assert info.verified is False
        assert info.name == "notfound"


class TestGetVerificationReport:
    def test_report_has_required_fields(self):
        from backend.core.supply_chain import SupplyChainVerifier
        verifier = SupplyChainVerifier(base_dir="/tmp")
        with patch.object(verifier, "verify_package") as mock_vp:
            from backend.core.supply_chain import DependencyInfo
            mock_vp.return_value = DependencyInfo(
                name="x", version="1.0", verified=True, actual_hash="abc" * 22
            )
            report = verifier.get_verification_report()

        assert "timestamp" in report
        assert "packages" in report
        assert "system_components" in report
        assert "known_hashes_count" in report
        assert "python" in report["system_components"]

    def test_regenerate_baseline_updates_hashes(self):
        from backend.core.supply_chain import SupplyChainVerifier

        verifier = SupplyChainVerifier(base_dir="/tmp/nonexistent_regenerate_test")
        with (
            patch.object(verifier, "verify_package") as mock_verify,
            patch.object(verifier, "_save_known_hashes") as mock_save,
        ):
            mock_verify.return_value = MagicMock(
                name="x", version="1.0", verified=True, actual_hash="abc" * 22
            )
            report = verifier.regenerate_baseline()

        assert report["previous_checksum_count"] == 0
        assert report["packages_updated"] > 0
        assert report["packages_failed"] == 0
        assert len(report["updated"]) > 0
        assert report["updated"][0]["hash"].endswith("...")
        mock_save.assert_called_once()

    def test_regenerate_handles_failed_packages(self):
        from backend.core.supply_chain import SupplyChainVerifier

        verifier = SupplyChainVerifier(base_dir="/tmp/nonexistent_regenerate_fail")
        with patch.object(verifier, "verify_package") as mock_verify:
            mock_verify.return_value = MagicMock(
                name="x", version="error", verified=False, actual_hash=None
            )
            report = verifier.regenerate_baseline()

        assert report["packages_updated"] == 0
        assert report["packages_failed"] > 0
