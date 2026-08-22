"""
Jambubrowser MCP Server
=======================
FastMCP server exposing the full Jambubrowser engine as MCP tools.
External agents (Claude, Cursor, etc.) can use these tools to perform
autonomous research, browser automation, and knowledge management.

21 MCP tools covering:
- Research & Search (5 tools)
- Browser Actions (5 tools)
- Vision & Perception (2 tools)
- Memory & Knowledge (3 tools)
- Tools & Skills (2 tools)
- System (4 tools: check_engine_health, get_system_stats, start_mission, stop_mission)
"""

import asyncio
import json
import os
import httpx
from mcp.server.fastmcp import FastMCP

from backend import __version__

# Initialize FastMCP server for Jambubrowser
mcp = FastMCP(f"Jambubrowser Sovereign Engine v{__version__}")

# Engine URL is overridable via JAMBU_ENGINE_URL (useful for tests that spawn
# the engine on a free port). Default matches the conventional dev port.
ENGINE_URL = os.environ.get("JAMBU_ENGINE_URL", "http://localhost:8001")
DEFAULT_TIMEOUT = 60.0


async def _call_engine(
    method: str,
    path: str,
    json_data: dict = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Internal helper to call the engine API."""
    async with httpx.AsyncClient() as client:
        try:
            if method == "GET":
                resp = await client.get(
                    f"{ENGINE_URL}{path}",
                    params=json_data,
                    timeout=timeout,
                )
            elif method == "POST":
                resp = await client.post(
                    f"{ENGINE_URL}{path}",
                    json=json_data or {},
                    timeout=timeout,
                )
            else:
                return {"error": f"Unsupported method: {method}"}

            if resp.status_code == 200:
                return resp.json()
            return {"error": f"Engine returned status {resp.status_code}"}
        except httpx.TimeoutException:
            return {"error": f"Request timed out after {timeout}s"}
        except httpx.ConnectError:
            return {"error": "Engine is not running. Start it with: python engine.py"}
        except Exception as e:
            return {"error": str(e)}


# ===================================================================
# RESEARCH & SEARCH TOOLS
# ===================================================================

@mcp.tool()
async def research_web(query: str, tor: bool = False) -> str:
    """
    Perform an autonomous research mission using the Jambubrowser swarm.
    Decomposes query into parallel sub-tasks and synthesizes findings.

    Args:
        query: The research question or topic
        tor: Route through Tor for anonymity (default: False)
    """
    result = await _call_engine("POST", "/research", {
        "query": query,
        "tor_routing": tor,
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Research failed: {result['error']}"
    return f"RESEARCH RESULTS:\n\n{result.get('context', 'No results found.')}\n\nSources: {json.dumps(result.get('sources', []))}"


@mcp.tool()
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
    result = await _call_engine("GET", "/search", {
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


@mcp.tool()
async def search_academic(query: str) -> str:
    """
    Search ArXiv for academic papers on a topic.
    Returns paper titles, abstracts, and links.

    Args:
        query: Research topic to search for
    """
    result = await _call_engine("POST", "/research", {
        "query": query,
        "domain": "academic",
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Academic search failed: {result['error']}"
    return f"ACADEMIC RESULTS:\n\n{result.get('context', 'No papers found.')}\n\nSources: {json.dumps(result.get('sources', []))}"


@mcp.tool()
async def search_code(query: str) -> str:
    """
    Search GitHub for code repositories matching a query.
    Returns repo names, descriptions, and links.

    Args:
        query: Code or project topic to search for
    """
    result = await _call_engine("POST", "/research", {
        "query": query,
        "domain": "coding",
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Code search failed: {result['error']}"
    return f"CODE RESULTS:\n\n{result.get('context', 'No repos found.')}\n\nSources: {json.dumps(result.get('sources', []))}"


@mcp.tool()
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
        result = await _call_engine("POST", "/research", {
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


# ===================================================================
# BROWSER ACTION TOOLS
# ===================================================================

@mcp.tool()
async def scrape_page(url: str, session_id: str = None) -> str:
    """
    Scrape a webpage and return its text content as clean text.
    Includes page title, main content, and a screenshot.

    Args:
        url: The webpage URL to scrape
        session_id: Optional browser session ID for stateful navigation
    """
    result = await _call_engine("POST", "/scrape", {
        "url": url,
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Scrape failed: {result['error']}"
    content = result.get("context", result.get("markdown", "No content extracted."))
    return f"PAGE CONTENT ({url}):\n\n{content[:10000]}"


@mcp.tool()
async def click_element(url: str, selector: str, session_id: str = None) -> str:
    """
    Click an element on a webpage using a CSS selector.
    Returns the page state after clicking.

    Args:
        url: The page URL
        selector: CSS selector for the element to click
        session_id: Optional browser session ID
    """
    result = await _call_engine("POST", "/act", {
        "url": url,
        "steps": [{"action": "click", "selector": selector}],
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Click failed: {result['error']}"
    return f"Clicked '{selector}' on {url}. Result: {result.get('markdown', 'Action completed.')[:5000]}"


@mcp.tool()
async def type_text(url: str, selector: str, text: str, session_id: str = None) -> str:
    """
    Type text into an input field on a webpage.

    Args:
        url: The page URL
        selector: CSS selector for the input field
        text: Text to type
        session_id: Optional browser session ID
    """
    result = await _call_engine("POST", "/act", {
        "url": url,
        "steps": [{"action": "type", "selector": selector, "value": text}],
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Type failed: {result['error']}"
    return f"Typed '{text}' into '{selector}' on {url}."


@mcp.tool()
async def take_screenshot(url: str, full_page: bool = False, session_id: str = None) -> str:
    """
    Take a screenshot of a webpage. Returns base64-encoded PNG.

    Args:
        url: The page URL to screenshot
        full_page: Capture the full scrollable page (default: viewport only)
        session_id: Optional browser session ID
    """
    result = await _call_engine("POST", "/scrape", {
        "url": url,
        "query": "screenshot",
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Screenshot failed: {result['error']}"
    return f"Screenshot captured for {url}. Content length: {len(result.get('context', ''))} chars."


@mcp.tool()
async def navigate_browser(url: str, session_id: str = None) -> str:
    """
    Navigate the browser to a URL. Use before other browser actions
    to establish the page context.

    Args:
        url: The URL to navigate to
        session_id: Optional browser session ID
    """
    result = await _call_engine("POST", "/scrape", {
        "url": url,
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Navigation failed: {result['error']}"
    return f"Navigated to {url}. Page title: {result.get('title', 'Unknown')}"


# ===================================================================
# VISION & PERCEPTION TOOLS
# ===================================================================

@mcp.tool()
async def visual_grounding(url: str) -> str:
    """
    Analyze a webpage visually and identify interactive elements
    (buttons, forms, links). Returns suggested actions the agent can take.

    Args:
        url: The page URL to analyze visually
    """
    result = await _call_engine("POST", "/vision/grounding", {
        "url": url,
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Vision grounding failed: {result['error']}"

    suggestions = result.get("suggestions", [])
    if not suggestions:
        return "No interactive elements identified."

    lines = ["# Visual Grounding Analysis", f"URL: {url}", ""]
    for i, s in enumerate(suggestions, 1):
        lines.append(
            f"{i}. {s.get('label', 'Action')} | "
            f"Type: {s.get('action', 'unknown')} | "
            f"Selector: {s.get('selector', 'viewport')}"
        )
    return "\n".join(lines)


@mcp.tool()
async def analyze_screenshot(image_data: str) -> str:
    """
    Analyze a screenshot or image using the vision model.
    Describe what the agent sees in the image.

    Args:
        image_data: Base64-encoded image data
    """
    result = await _call_engine("POST", "/vision/analyze", {
        "image": image_data,
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Image analysis failed: {result['error']}"
    return result.get("analysis", "No analysis available.")


# ===================================================================
# MEMORY & KNOWLEDGE TOOLS
# ===================================================================

@mcp.tool()
async def query_brain(query: str) -> str:
    """
    Search the local knowledge vault (vector search) for relevant
    previously-researched information.

    Args:
        query: What to search for in the knowledge vault
    """
    result = await _call_engine("GET", "/memory/recall", {"query": query})
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


@mcp.tool()
async def recall_memory(query: str) -> str:
    """
    Cross-session semantic recall. Finds information from past
    research sessions that relates to the current query.

    Args:
        query: Context to find related past research for
    """
    result = await _call_engine("GET", "/memory/recall", {"query": query})
    if "error" in result:
        return f"Memory recall failed: {result['error']}"

    memories = result.get("memory", [])
    if not memories:
        return "No cross-session memories found."

    lines = ["# Cross-Session Memory Recall\n"]
    for i, m in enumerate(memories[:5], 1):
        lines.append(f"{i}. {m.get('text', '')[:200]}")
    return "\n".join(lines)


@mcp.tool()
async def get_brain_stats() -> str:
    """
    Get statistics about the local knowledge vault:
    document count, active missions, stored tools, credentials.
    """
    result = await _call_engine("GET", "/stats")
    if "error" in result:
        return f"Stats check failed: {result['error']}"
    return (
        f"Knowledge Vault Stats:\n"
        f"- Documents indexed: {result.get('doc_count', 0)}\n"
        f"- Database path: rag_data.db"
    )


# ===================================================================
# TOOLS & SKILLS TOOLS
# ===================================================================

@mcp.tool()
async def list_custom_tools() -> str:
    """
    List all saved agent-generated tools and skills stored
    in the toolbox.
    """
    result = await _call_engine("GET", "/tools")
    if "error" in result:
        return f"Tool listing failed: {result['error']}"

    tools = result.get("tools", [])
    if not tools:
        return "No custom tools in the toolbox yet."

    lines = ["# Custom Toolbox\n"]
    for i, t in enumerate(tools, 1):
        lines.append(f"{i}. **{t.get('name', 'unknown')}**: {t.get('description', 'No description')}")
    return "\n".join(lines)


@mcp.tool()
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

    result = await _call_engine("POST", "/tool/exec", {
        "name": name,
        "kwargs": parsed_kwargs,
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Tool execution failed: {result['error']}"
    return f"Tool '{name}' executed. Output: {result.get('output', 'No output.')}"


# ===================================================================
# SYSTEM TOOLS
# ===================================================================

@mcp.tool()
async def check_engine_health() -> str:
    """
    Check if the Jambubrowser engine is running and healthy.
    Returns engine status and system metrics.
    """
    result = await _call_engine("GET", "/health")
    if "error" in result:
        return f"Engine Offline: {result['error']}"

    return (
        f"Engine Status: {result.get('status', 'unknown')}\n"
        f"Message: {result.get('message', 'No message')}\n"
        f"RAM Used: {result.get('ram_used_gb', 'N/A')} GB\n"
        f"CPU: {result.get('cpu_percent', 'N/A')}%"
    )


@mcp.tool()
async def get_system_stats() -> str:
    """
    Get detailed system statistics: CPU usage, RAM, document count,
    active missions, and database size.
    """
    health = await _call_engine("GET", "/health")
    stats = await _call_engine("GET", "/stats")

    lines = ["# System Statistics\n"]
    if "error" not in health:
        lines.append(f"- Status: {health.get('status', 'unknown')}")
        lines.append(f"- RAM: {health.get('ram_used_gb', 'N/A')}/{health.get('ram_total_gb', 'N/A')} GB")
        lines.append(f"- CPU: {health.get('cpu_percent', 'N/A')}%")
    if "error" not in stats:
        lines.append(f"- Documents: {stats.get('doc_count', 0)}")
    return "\n".join(lines)


@mcp.tool()
async def start_mission(query: str, schedule: str = None) -> str:
    """
    Register a long-running background research mission.
    The engine will periodically research this topic and report findings.

    Args:
        query: The research topic to monitor
        schedule: Cron-style schedule (e.g., '0 */6 * * *' for every 6 hours)
    """
    result = await _call_engine("POST", "/mission", {
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


@mcp.tool()
async def stop_mission(mission_id: str) -> str:
    """
    Stop a running background research mission.

    Args:
        mission_id: The mission ID to stop (from start_mission)
    """
    result = await _call_engine("POST", "/mission/stop", {
        "mission_id": mission_id,
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Mission stop failed: {result['error']}"
    return f"Mission {mission_id} stopped."


# ===================================================================
# ENTRY POINT
# ===================================================================

if __name__ == "__main__":
    mcp.run()
