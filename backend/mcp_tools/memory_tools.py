"""Memory & knowledge MCP tools.

Semantic memory queries over the brain store.

Tools are plain async functions here; ``register(mcp)`` applies FastMCP's
decorator to each one, so a tool can be read, imported and unit-tested
without an MCP server in the loop.
"""
from __future__ import annotations

from backend.mcp_tools import _shared


async def query_brain(query: str) -> str:
    """
    Search the local knowledge vault (vector search) for relevant
    previously-researched information.

    Args:
        query: What to search for in the knowledge vault
    """
    result = await _shared.call_engine("GET", "/memory/recall", {"query": query})
    if "error" in result:
        return f"Brain query failed: {result['error']}"

    memories = result.get("memory", [])
    if not memories:
        return "No relevant memories found in the knowledge vault."

    lines = [f"# Knowledge Vault Results for: {query}\n"]
    for i, m in enumerate(memories[:10], 1):
        lines.append(f"{i}. {m.get('text', '')[:300]}")
        if m.get("url"):
            lines.append(f"   Source: {m['url']}")
        lines.append("")
    return "\n".join(lines)


async def recall_memory(query: str) -> str:
    """
    Cross-session semantic recall. Finds information from past
    research sessions that relates to the current query.

    Args:
        query: Context to find related past research for
    """
    result = await _shared.call_engine("GET", "/memory/recall", {"query": query})
    if "error" in result:
        return f"Memory recall failed: {result['error']}"

    memories = result.get("memory", [])
    if not memories:
        return "No cross-session memories found."

    lines = ["# Cross-Session Memory Recall\n"]
    for i, m in enumerate(memories[:5], 1):
        lines.append(f"{i}. {m.get('text', '')[:200]}")
    return "\n".join(lines)


async def get_brain_stats() -> str:
    """
    Get statistics about the local knowledge vault:
    document count, active missions, stored tools, credentials.
    """
    result = await _shared.call_engine("GET", "/stats")
    if "error" in result:
        return f"Stats check failed: {result['error']}"
    return (
        f"Knowledge Vault Stats:\n"
        f"- Documents indexed: {result.get('doc_count', 0)}\n"
        f"- Database path: rag_data.db"
    )



def register(mcp) -> None:
    """Register all 3 memory_tools tools with the server."""
    mcp.tool()(query_brain)
    mcp.tool()(recall_memory)
    mcp.tool()(get_brain_stats)
