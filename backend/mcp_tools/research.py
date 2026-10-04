"""Research & search MCP tools.

Research and multi-engine search.

Tools are plain async functions here; ``register(mcp)`` applies FastMCP's
decorator to each one, so a tool can be read, imported and unit-tested
without an MCP server in the loop.
"""
from __future__ import annotations

import json
from backend.mcp_tools import _shared


async def research_web(query: str, tor: bool = False) -> str:
    """
    Perform an autonomous research mission using the Jambubrowser swarm.
    Decomposes query into parallel sub-tasks and synthesizes findings.

    Args:
        query: The research question or topic
        tor: Route through Tor for anonymity (default: False)
    """
    result = await _shared.call_engine("POST", "/research", {
        "query": query,
        "tor_routing": tor,
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Research failed: {result['error']}"
    return f"RESEARCH RESULTS:\n\n{result.get('context', 'No results found.')}\n\nSources: {json.dumps(result.get('sources', []))}"


async def search_multi_engine(
    query: str,
    engines: str = "google,bing,duckduckgo",
) -> str:
    """
    Search across multiple engines without scraping pages.
    Returns raw search results with URLs and snippets.

    Args:
        query: Search query
        engines: Comma-separated engine list (default: google,bing,duckduckgo)
    """
    result = await _shared.call_engine("GET", "/search", {
        "q": query,
        "engines": engines,
    })
    if "error" in result:
        return f"Search failed: {result['error']}"
    results = result.get("results", [])
    if not results:
        return "No results found."
    lines = [f"# Search results for: {query}\n"]
    for i, r in enumerate(results[:10], 1):
        lines.append(f"{i}. **{r.get('title', 'Untitled')}**\n   {r.get('url', '')}\n   {r.get('content', '')[:200]}\n")
    return "\n".join(lines)


async def search_academic(query: str) -> str:
    """
    Search ArXiv for academic papers on a topic.
    Returns paper titles, abstracts, and links.

    Args:
        query: Research topic to search for
    """
    result = await _shared.call_engine("POST", "/research", {
        "query": query,
        "domain": "academic",
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Academic search failed: {result['error']}"
    return f"ACADEMIC RESULTS:\n\n{result.get('context', 'No papers found.')}\n\nSources: {json.dumps(result.get('sources', []))}"


async def search_code(query: str) -> str:
    """
    Search GitHub for code repositories matching a query.
    Returns repo names, descriptions, and links.

    Args:
        query: Code or project topic to search for
    """
    result = await _shared.call_engine("POST", "/research", {
        "query": query,
        "domain": "coding",
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Code search failed: {result['error']}"
    return f"CODE RESULTS:\n\n{result.get('context', 'No repos found.')}\n\nSources: {json.dumps(result.get('sources', []))}"


async def deep_research(query: str, rounds: int = 3) -> str:
    """
    Perform multi-round recursive research that builds on previous findings.
    More thorough than single-pass research.

    Args:
        query: The research topic
        rounds: Number of recursive research rounds (default: 3, max: 5)
    """
    rounds = min(rounds, 5)
    all_context = []
    current_query = query

    for r in range(rounds):
        result = await _shared.call_engine("POST", "/research", {
            "query": current_query,
            "persist": True,
            "client_id": "mcp",
        })
        if "error" not in result:
            all_context.append(f"--- Round {r + 1} ---\n{result.get('context', '')}")
            # Refine query for next round based on findings
            if result.get("context"):
                current_query = f"{query} additional details: {result['context'][:200]}"

    return "DEEP RESEARCH RESULTS:\n\n" + "\n\n".join(all_context)



def register(mcp) -> None:
    """Register all 5 research tools with the server."""
    mcp.tool()(research_web)
    mcp.tool()(search_multi_engine)
    mcp.tool()(search_academic)
    mcp.tool()(search_code)
    mcp.tool()(deep_research)
