"""Agent bridge — launcher scripts + Tauri agent commands + docs.

Pins the local-AI-agent surface so refactors cannot silently drop it:

- ``bin/jambubrowser-mcp`` / ``bin/jambubrowser-engine`` exist, are
  executable, and pass ``bash -n``.
- ``examples/claude-desktop-config.json`` is valid JSON with an
  ``mcpServers.jambubrowser.command`` entry.
- ``docs/LOCAL_AGENTS.md`` documents all three paths (stdio / invoke /
  HTTP) and every ``agent_*`` Tauri command is both defined in
  ``commands/agent.rs`` and registered in ``lib.rs``'s invoke handler.
- ``AGENT_USE_CASES`` covers the ten use-case ids the guide promises.
"""

from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_MCP = REPO_ROOT / "bin" / "jambubrowser-mcp"
BIN_ENGINE = REPO_ROOT / "bin" / "jambubrowser-engine"
AGENT_RS = REPO_ROOT / "browser-app" / "src-tauri" / "src" / "commands" / "agent.rs"
LIB_RS = REPO_ROOT / "browser-app" / "src-tauri" / "src" / "lib.rs"
CLAUDE_CFG = REPO_ROOT / "examples" / "claude-desktop-config.json"
GUIDE = REPO_ROOT / "docs" / "LOCAL_AGENTS.md"

AGENT_COMMANDS = (
    "agent_bridge_status",
    "agent_session_open",
    "agent_session_act",
    "agent_session_close",
    "agent_run_audit",
    "agent_run_research",
)

USE_CASE_IDS = (
    "audit",
    "browse",
    "research",
    "memory",
    "mesh",
    "simulate",
    "privacy",
    "qa",
    "missions",
    "eval",
)


def test_launchers_exist_executable_and_parse():
    for path in (BIN_MCP, BIN_ENGINE):
        assert path.exists(), f"missing {path}"
        mode = path.stat().st_mode
        assert mode & stat.S_IXUSR, f"{path} is not executable"
        rc = subprocess.run(["bash", "-n", str(path)]).returncode
        assert rc == 0, f"{path} fails bash -n"


def test_launcher_never_prints_to_stdout():
    # MCP frames own stdout: the launcher's own diagnostics must go to
    # stderr, or every client handshake breaks.
    text = BIN_MCP.read_text()
    assert ">&2" in text, "launcher must redirect diagnostics to stderr"
    assert "JAMBUBROWSER_MCP_LOG" in text


def test_claude_config_valid_and_points_at_launcher():
    payload = json.loads(CLAUDE_CFG.read_text())
    server = payload["mcpServers"]["jambubrowser"]
    assert server["command"].endswith("bin/jambubrowser-mcp"), server["command"]
    assert "env" in server


def test_agent_commands_defined_and_registered():
    agent_src = AGENT_RS.read_text()
    lib_src = LIB_RS.read_text()
    assert (
        "pub mod agent;"
        in (REPO_ROOT / "browser-app" / "src-tauri" / "src" / "commands" / "mod.rs").read_text()
    )
    for cmd in AGENT_COMMANDS:
        assert f"pub async fn {cmd}(" in agent_src, f"{cmd} not defined in agent.rs"
        assert f"commands::agent::{cmd}" in lib_src, f"{cmd} not in invoke_handler"


def test_use_case_catalog_covers_guide():
    agent_src = AGENT_RS.read_text()
    guide = GUIDE.read_text()
    for uid in USE_CASE_IDS:
        assert f'"{uid}"' in agent_src, f"use case {uid!r} missing from AGENT_USE_CASES"
        assert uid in guide, f"use case {uid!r} not documented in LOCAL_AGENTS.md"


def test_guide_documents_all_three_paths():
    guide = GUIDE.read_text()
    for marker in (
        "jambubrowser-mcp",
        "agent_bridge_status",
        "agent_session_open",
        "/mcp/tools/docs",
        "jambubrowser-engine",
        ".well-known/mcp-server-card.json",
    ):
        assert marker in guide, f"LOCAL_AGENTS.md never mentions {marker}"
