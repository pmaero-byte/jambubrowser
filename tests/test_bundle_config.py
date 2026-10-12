"""Regression tests for the bundled-app engine sidecar contract.

The macOS ``.app`` used to ship UI-only: on a machine with no checkout the
orchestrator found no engine and every panel showed "engine unreachable".
The bundle now embeds a PyInstaller-frozen ``jambubrowser-engine`` sidecar
(``scripts/build_engine_sidecar.sh``) via Tauri ``externalBin``. These tests
pin the three-file contract that must stay in sync:

1. ``tauri.conf.json`` declares the sidecar in ``externalBin`` (Tauri fails
   the bundle build if it is missing, and never ships the binary without it).
2. The spawn permission in ``capabilities/default.json`` allows it.
3. The frozen entry point parses the flags the orchestrator passes
   (``--host``, ``--port``).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TAURI_CONF = ROOT / "browser-app" / "src-tauri" / "tauri.conf.json"
CAPABILITIES = ROOT / "browser-app" / "src-tauri" / "capabilities" / "default.json"
SIDECAR_ENTRY = ROOT / "bin" / "engine_sidecar.py"
BUILD_SCRIPT = ROOT / "scripts" / "build_engine_sidecar.sh"


def test_tauri_bundle_declares_engine_sidecar() -> None:
    conf = json.loads(TAURI_CONF.read_text())
    external = conf["bundle"].get("externalBin", [])
    assert "binaries/jambubrowser-engine" in external, external


def test_capabilities_allow_sidecar_spawn() -> None:
    caps = json.loads(CAPABILITIES.read_text())
    spawn = next(
        p for p in caps["permissions"]
        if isinstance(p, dict) and p.get("identifier") == "shell:allow-spawn"
    )
    names = {entry["name"] for entry in spawn["allow"]}
    assert "jambubrowser-engine" in names, names


def test_frozen_entry_parses_orchestrator_flags() -> None:
    """The Rust orchestrator spawns `jambubrowser-engine --host … --port …`;
    the entry point must accept exactly those flags."""
    result = subprocess.run(
        [sys.executable, str(SIDECAR_ENTRY), "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "--host" in result.stdout
    assert "--port" in result.stdout


def test_build_script_exists_and_is_executable() -> None:
    assert BUILD_SCRIPT.is_file()
    assert BUILD_SCRIPT.stat().st_mode & 0o111, "build script must be executable"
