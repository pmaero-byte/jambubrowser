"""Vision & perception MCP tools.

Visual grounding and screenshot analysis.

Tools are plain async functions here; ``register(mcp)`` applies FastMCP's
decorator to each one, so a tool can be read, imported and unit-tested
without an MCP server in the loop.
"""
from __future__ import annotations

from backend.mcp_tools import _shared


async def visual_grounding(url: str) -> str:
    """
    Analyze a webpage visually and identify interactive elements
    (buttons, forms, links). Returns suggested actions the agent can take.

    Args:
        url: The page URL to analyze visually
    """
    result = await _shared.call_engine("POST", "/vision/grounding", {
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


async def analyze_screenshot(image_data: str) -> str:
    """
    Analyze a screenshot or image using the vision model.
    Describe what the agent sees in the image.

    Args:
        image_data: Base64-encoded image data
    """
    result = await _shared.call_engine("POST", "/vision/analyze", {
        "image": image_data,
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Image analysis failed: {result['error']}"
    return result.get("analysis", "No analysis available.")



def register(mcp) -> None:
    """Register all 2 vision tools with the server."""
    mcp.tool()(visual_grounding)
    mcp.tool()(analyze_screenshot)
