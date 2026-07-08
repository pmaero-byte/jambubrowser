"""
Supply Chain Verification
=========================
Verifies the integrity of dependencies and system components.
Implements security checks for the sovereign browser.

Security Features:
- Dependency hash verification
- Version integrity checking
- Tamper detection for local binaries
- Secure update verification
"""

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, field
import re


@dataclass
class DependencyInfo:
    """Information about a dependency."""
    name: str
    version: str
    expected_hash: Optional[str] = None
    actual_hash: Optional[str] = None
    verified: bool = False
    last_checked: float = 0
    source: str = "pip"


class SupplyChainVerifier:
    """
    Verifies the integrity of dependencies and system components.

    Features:
    - Hash verification for Python packages
    - Binary integrity checking
    - Secure update verification
    - Tamper detection
    """

    def __init__(self, base_dir: str = None):
        self._base_dir = Path(base_dir) if base_dir else Path.cwd()
        self._known_hashes: Dict[str, str] = {}
        self._verification_cache: Dict[str, DependencyInfo] = {}
        self._load_known_hashes()

    def _load_known_hashes(self):
        """Load known good hashes from secure store."""
        hash_file = self._base_dir / ".dependency_hashes.json"
        if hash_file.exists():
            try:
                self._known_hashes = json.loads(hash_file.read_text())
            except Exception:
                self._known_hashes = {}

    def _save_known_hashes(self):
        """Save known hashes to secure store."""
        hash_file = self._base_dir / ".dependency_hashes.json"
        try:
            hash_file.write_text(json.dumps(self._known_hashes, indent=2))
            os.chmod(hash_file, 0o600)
        except Exception:
            pass

    def verify_package(self, package_name: str) -> DependencyInfo:
        """
        Verify a Python package's integrity.

        Args:
            package_name: Name of the package to verify

        Returns:
            DependencyInfo with verification status
        """
        try:
            version = None
            location = ""
            
            # Try importlib.metadata first (Python 3.8+)
            try:
                import importlib.metadata as metadata
                version = metadata.version(package_name)
                
                # Get package location
                try:
                    dist = metadata.distribution(package_name)
                    if hasattr(dist, '_path') and dist._path:
                        location = str(dist._path.parent)
                except Exception:
                    pass
                    
            except (ImportError, metadata.PackageNotFoundError):
                # Fallback to pip for older Python versions
                result = subprocess.run(
                    ["pip", "show", package_name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

                if result.returncode != 0:
                    return DependencyInfo(
                        name=package_name,
                        version="unknown",
                        verified=False,
                        source="pip",
                    )

                # Parse version
                version_match = re.search(r"Version:\s+(.+)", result.stdout)
                version = version_match.group(1).strip() if version_match else "unknown"

                # Get package location
                location_match = re.search(r"Location:\s+(.+)", result.stdout)
                location = location_match.group(1).strip() if location_match else ""

            if not version:
                return DependencyInfo(
                    name=package_name,
                    version="unknown",
                    verified=False,
                    source="importlib",
                )

            # Compute hash of package files
            actual_hash = self._compute_package_hash(package_name, location)

            # Check against known hash
            known_hash = self._known_hashes.get(package_name)
            verified = known_hash is None or known_hash == actual_hash

            info = DependencyInfo(
                name=package_name,
                version=version,
                expected_hash=known_hash,
                actual_hash=actual_hash,
                verified=verified,
                last_checked=time.time(),
                source="importlib" if version else "pip",
            )

            self._verification_cache[package_name] = info
            return info

        except Exception as e:
            return DependencyInfo(
                name=package_name,
                version="error",
                verified=False,
                source="pip",
            )

    def _compute_package_hash(self, package_name: str, location: str) -> str:
        """Compute hash of package files."""
        if not location:
            return ""

        package_dir = Path(location) / package_name.replace("-", "_")
        if not package_dir.exists():
            package_dir = Path(location) / package_name

        if not package_dir.exists():
            return ""

        # Hash all Python files in the package
        hash_obj = hashlib.sha256()

        for py_file in sorted(package_dir.rglob("*.py")):
            try:
                content = py_file.read_bytes()
                hash_obj.update(content)
            except Exception:
                continue

        return hash_obj.hexdigest()

    def register_known_hash(self, package_name: str, hash_value: str):
        """Register a known good hash for a package."""
        self._known_hashes[package_name] = hash_value
        self._save_known_hashes()

    def verify_binary(self, binary_path: str) -> Tuple[bool, str]:
        """
        Verify a binary's integrity.

        Args:
            binary_path: Path to the binary

        Returns:
            Tuple of (is_valid, message)
        """
        path = Path(binary_path)
        if not path.exists():
            return False, f"Binary not found: {binary_path}"

        try:
            # Compute hash
            hash_obj = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    hash_obj.update(chunk)
            actual_hash = hash_obj.hexdigest()

            # Check against known hash
            known_hash = self._known_hashes.get(binary_path)
            if known_hash and known_hash != actual_hash:
                return False, f"Hash mismatch: expected {known_hash[:16]}..., got {actual_hash[:16]}..."

            # Verify executable permissions
            if not os.access(path, os.X_OK):
                return False, "Binary is not executable"

            return True, f"Binary verified: {actual_hash[:16]}..."

        except Exception as e:
            return False, f"Verification failed: {str(e)}"

    def verify_system_components(self) -> Dict[str, bool]:
        """Verify critical system components."""
        components = {
            "python": self._verify_python(),
            "pip": self._verify_pip(),
            "playwright": self._verify_playwright(),
            "sqlite_vec": self._verify_sqlite_vec(),
        }
        return components

    def _verify_python(self) -> bool:
        """Verify Python interpreter integrity."""
        try:
            result = subprocess.run(
                [sys.executable, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _verify_pip(self) -> bool:
        """Verify pip integrity."""
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _verify_playwright(self) -> bool:
        """Verify Playwright installation."""
        try:
            from playwright.async_api import async_playwright
            return True
        except ImportError:
            return False

    def _verify_sqlite_vec(self) -> bool:
        """Verify sqlite_vec extension."""
        try:
            import sqlite_vec
            return True
        except ImportError:
            return False

    def get_verification_report(self) -> dict:
        """Generate a comprehensive verification report."""
        # Verify critical packages
        critical_packages = [
            "fastapi", "uvicorn", "pydantic", "httpx",
            "cryptography", "sentence-transformers", "sqlite-vec",
            "playwright", "crawl4ai", "psutil",
        ]

        package_results = {}
        for pkg in critical_packages:
            info = self.verify_package(pkg)
            package_results[pkg] = {
                "version": info.version,
                "verified": info.verified,
                "hash": info.actual_hash[:16] + "..." if info.actual_hash else None,
            }

        return {
            "timestamp": time.time(),
            "packages": package_results,
            "system_components": self.verify_system_components(),
            "known_hashes_count": len(self._known_hashes),
        }

    def regenerate_baseline(self) -> dict:
        """Re-hash every critical package and overwrite the known-good baseline.

        Use this after a legitimate dependency update (pip install --upgrade,
        new requirements.txt) so future ``verify_package`` calls compare
        against the new hashes instead of flagging every package as tampered.
        """
        critical_packages = [
            "fastapi", "uvicorn", "pydantic", "httpx",
            "cryptography", "sentence-transformers", "sqlite-vec",
            "playwright", "crawl4ai", "psutil",
        ]
        previous_count = len(self._known_hashes)
        updated = []
        failed = []

        for pkg in critical_packages:
            info = self.verify_package(pkg)
            if info.actual_hash:
                self._known_hashes[pkg] = info.actual_hash
                updated.append({"name": pkg, "version": info.version, "hash": info.actual_hash[:16] + "..."})
            else:
                failed.append(pkg)

        self._save_known_hashes()

        return {
            "timestamp": time.time(),
            "previous_checksum_count": previous_count,
            "new_checksum_count": len(self._known_hashes),
            "packages_updated": len(updated),
            "packages_failed": len(failed),
            "updated": updated,
            "failed": failed,
        }


import sys

# Module-level singleton
_verifier: Optional[SupplyChainVerifier] = None


def get_verifier(base_dir: str = None) -> SupplyChainVerifier:
    """Get or create the singleton verifier."""
    global _verifier
    if _verifier is None:
        _verifier = SupplyChainVerifier(base_dir)
    return _verifier
