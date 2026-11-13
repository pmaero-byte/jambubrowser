"""Regression: ``python cli/jambu.py`` died with "No module named 'cli'".

Both the script form and the module form must work, from any working
directory.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(args, cwd):
    return subprocess.run([sys.executable, *args, "--help"], cwd=cwd,
                          capture_output=True, text=True, timeout=60)


def test_script_invocation_from_repo_root():
    proc = _run(["cli/jambu.py"], ROOT)
    assert proc.returncode == 0, proc.stderr
    assert "No module named" not in proc.stderr
    assert "usage" in proc.stdout.lower()


def test_script_invocation_from_elsewhere(tmp_path):
    proc = _run([str(ROOT / "cli" / "jambu.py")], tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "usage" in proc.stdout.lower()


def test_module_invocation():
    proc = _run(["-m", "cli.jambu"], ROOT)
    assert proc.returncode == 0, proc.stderr
    assert "usage" in proc.stdout.lower()
