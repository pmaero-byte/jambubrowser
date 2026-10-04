"""MCP server for Jambubrowser — 52 tools over the engine API.

This module is the *registry*: it builds the FastMCP server, hands it to every
tool family in `backend/mcp_tools/`, applies the configured tool profile, and
runs. The tools themselves live in the family modules, one per domain, because
52 tools plus their renderers did not fit in a file anyone could read.

Tool profiles (``JAMBU_MCP_PROFILE``):
- ``full`` (default for stdio installs) — every tool.
- ``curated`` — drops arbitrary-execution tools. Remote deployments
  (`backend/mcp_http.py`) should use this.
- ``developer`` — only the high-level browser-testing verbs, so an agent
  choosing among tools pays the smallest possible selection cost.
"""
from __future__ import annotations

import logging
import os

from mcp.server.fastmcp import FastMCP

from backend import __version__
from backend.mcp_tools import register_all

log = logging.getLogger("jambu.mcp_server")

mcp = FastMCP(f"Jambubrowser Sovereign Engine v{__version__}")

register_all(mcp)

# `curated` removes arbitrary execution (execute_tool) — it is the default for
# the remote transport, where an agent must not be able to run host code.
CURATED_EXCLUDES = ("execute_tool",)

DEVELOPER_TOOLS = {
    "browser_task",
    "browser_test_flow",
    "browser_test_plan",
    "browser_test_matrix",
    "browser_session_run",
    "browser_export_playwright",
    "browser_import_playwright",
    "check_engine_health",
}


def apply_tool_profile(profile: str) -> list[str]:
    """Remove non-profile tools from the live registry. Returns removals."""
    if profile == "curated":
        removed = []
        for name in CURATED_EXCLUDES:
            try:
                mcp.remove_tool(name)
                removed.append(name)
            except Exception:  # tool already absent
                log.debug(f"mcp tool {name} already absent from the registry",
                                      exc_info=True)
        return removed
    if profile == "developer":
        try:
            names = [t.name for t in mcp._tool_manager.list_tools()]
        except Exception:
            # No tool manager to enumerate: fall back to keeping the developer
            # verbs by removing everything we know is not one of them.
            log.debug("tool manager unavailable; using static developer list",
                      exc_info=True)
            names = list(DEVELOPER_TOOLS)
        removed = []
        for name in names:
            if name in DEVELOPER_TOOLS:
                continue
            try:
                mcp.remove_tool(name)
                removed.append(name)
            except Exception:
                log.debug(f"mcp tool {name} already absent from the registry",
                                      exc_info=True)
        return removed
    return []


apply_tool_profile(os.environ.get("JAMBU_MCP_PROFILE", "full"))


if __name__ == "__main__":
    mcp.run()
