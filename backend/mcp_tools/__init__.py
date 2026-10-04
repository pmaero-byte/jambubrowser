"""MCP tool families.

`backend/mcp_server.py` used to hold all 52 tools in one 1,894-line file. They
are split by domain here so that "where is the VPN tool" has one answer, and so
a family can be read without scrolling past eight unrelated ones.

Each module exposes ``register(mcp)``. Registration order is preserved from the
original file, which keeps the live registry — and therefore
``docs/MCP_TOOLS.md`` — byte-identical across the split.
"""
from __future__ import annotations

# Import order is registration order; do not sort this alphabetically.
TOOL_MODULES = (
    "research",
    "browser_actions",
    "vision",
    "memory_tools",
    "skills",
    "system",
    "mesh_dcm",
    "simulation",
    "vpn",
    "meshpay",
    "browser_sessions",
    "browser_testing",
    "agent_eval",
)


def register_all(mcp) -> int:
    """Register every tool family with ``mcp`` and return the tool count."""
    from importlib import import_module

    count = 0
    for name in TOOL_MODULES:
        module = import_module(f"backend.mcp_tools.{name}")
        module.register(mcp)
        manager = getattr(mcp, "_tool_manager", None)
        if manager is not None:
            count = len(list(manager.list_tools()))
    return count


__all__ = ["TOOL_MODULES", "register_all"]
