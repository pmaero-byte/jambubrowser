"""Custom tool & skill MCP tools.

Custom tool listing and execution.

Tools are plain async functions here; ``register(mcp)`` applies FastMCP's
decorator to each one, so a tool can be read, imported and unit-tested
without an MCP server in the loop.
"""
from __future__ import annotations

import json
from backend.mcp_tools import _shared


async def list_custom_tools() -> str:
    """
    List all saved agent-generated tools and skills stored
    in the toolbox.
    """
    result = await _shared.call_engine("GET", "/tools")
    if "error" in result:
        return f"Tool listing failed: {result['error']}"

    tools = result.get("tools", [])
    if not tools:
        return "No custom tools in the toolbox yet."

    lines = ["# Custom Toolbox\n"]
    for i, t in enumerate(tools, 1):
        lines.append(f"{i}. **{t.get('name', 'unknown')}**: {t.get('description', 'No description')}")
    return "\n".join(lines)


async def execute_tool(name: str, kwargs: str = "{}") -> str:
    """
    Execute a previously saved custom tool/script.

    Args:
        name: Name of the tool to execute
        kwargs: JSON string of keyword arguments to pass to the tool
    """
    try:
        parsed_kwargs = json.loads(kwargs)
    except json.JSONDecodeError:
        return f"Invalid kwargs JSON: {kwargs}"

    result = await _shared.call_engine("POST", "/tool/exec", {
        "name": name,
        "kwargs": parsed_kwargs,
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Tool execution failed: {result['error']}"
    return f"Tool '{name}' executed. Output: {result.get('output', 'No output.')}"



def register(mcp) -> None:
    """Register all 2 skills tools with the server."""
    mcp.tool()(list_custom_tools)
    mcp.tool()(execute_tool)
