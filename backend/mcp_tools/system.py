"""System and mission-control MCP tools.

Health, stats and mission control.

Tools are plain async functions here; ``register(mcp)`` applies FastMCP's
decorator to each one, so a tool can be read, imported and unit-tested
without an MCP server in the loop.
"""
from __future__ import annotations

from backend.mcp_tools import _shared


async def check_engine_health() -> str:
    """
    Check if the Jambubrowser engine is running and healthy.
    Returns engine status and system metrics.
    """
    result = await _shared.call_engine("GET", "/health")
    if "error" in result:
        return f"Engine Offline: {result['error']}"

    return (
        f"Engine Status: {result.get('status', 'unknown')}\n"
        f"Message: {result.get('message', 'No message')}\n"
        f"RAM Used: {result.get('ram_used_gb', 'N/A')} GB\n"
        f"CPU: {result.get('cpu_percent', 'N/A')}%"
    )


async def get_system_stats() -> str:
    """
    Get detailed system statistics: CPU usage, RAM, document count,
    active missions, and database size.
    """
    health = await _shared.call_engine("GET", "/health")
    stats = await _shared.call_engine("GET", "/stats")

    lines = ["# System Statistics\n"]
    if "error" not in health:
        lines.append(f"- Status: {health.get('status', 'unknown')}")
        lines.append(f"- RAM: {health.get('ram_used_gb', 'N/A')}/{health.get('ram_total_gb', 'N/A')} GB")
        lines.append(f"- CPU: {health.get('cpu_percent', 'N/A')}%")
    if "error" not in stats:
        lines.append(f"- Documents: {stats.get('doc_count', 0)}")
    return "\n".join(lines)


async def start_mission(query: str, schedule: str = None) -> str:
    """
    Register a long-running background research mission.
    The engine will periodically research this topic and report findings.

    Args:
        query: The research topic to monitor
        schedule: Cron-style schedule (e.g., '0 */6 * * *' for every 6 hours)
    """
    result = await _shared.call_engine("POST", "/mission", {
        "query": query,
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Mission registration failed: {result['error']}"
    return (
        f"Mission registered!\n"
        f"Mission ID: {result.get('mission_id', 'unknown')}\n"
        f"Query: {query}\n"
        f"Status: Active"
    )


async def stop_mission(mission_id: str) -> str:
    """
    Stop a running background research mission.

    Args:
        mission_id: The mission ID to stop (from start_mission)
    """
    result = await _shared.call_engine("POST", "/mission/stop", {
        "mission_id": mission_id,
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Mission stop failed: {result['error']}"
    return f"Mission {mission_id} stopped."



def register(mcp) -> None:
    """Register all 4 system tools with the server."""
    mcp.tool()(check_engine_health)
    mcp.tool()(get_system_stats)
    mcp.tool()(start_mission)
    mcp.tool()(stop_mission)
