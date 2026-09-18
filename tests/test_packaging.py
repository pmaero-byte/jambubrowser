"""Packaging guards: versions stay in sync and the CLI entry point exists."""
from __future__ import annotations

import json
import re
from importlib.metadata import distribution
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    text = (REPO_ROOT / "pyproject.toml").read_text()
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "pyproject.toml must declare a version"
    return match.group(1)


class TestVersions:
    def test_backend_matches_pyproject(self):
        from backend import __version__

        assert __version__ == _pyproject_version()

    def test_frontend_matches_pyproject(self):
        package = json.loads((REPO_ROOT / "browser-app" / "package.json").read_text())
        assert package["version"] == _pyproject_version()

    def test_jambu_entry_point_registered(self):
        try:
            dist = distribution("jambubrowser")
        except Exception:
            import pytest

            pytest.skip("jambubrowser is not installed in this environment")
        entry_points = [f"{ep.name} = {ep.value}" for ep in dist.entry_points]
        assert any(ep.startswith("jambu = ") for ep in entry_points)


class TestManifest:
    def test_pyproject_has_pypi_metadata(self):
        text = (REPO_ROOT / "pyproject.toml").read_text()
        for marker in ("classifiers", "keywords", "[project.urls]"):
            assert marker in text, f"pyproject.toml is missing {marker}"
