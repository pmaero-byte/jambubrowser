"""One-shot browser action MCP tools.

One-shot browser actions that open their own session.

Tools are plain async functions here; ``register(mcp)`` applies FastMCP's
decorator to each one, so a tool can be read, imported and unit-tested
without an MCP server in the loop.
"""
from __future__ import annotations

from backend.mcp_tools import _shared


async def scrape_page(url: str, session_id: str = None) -> str:
    """
    Scrape a webpage and return its text content as clean text.
    Includes page title, main content, and a screenshot.

    Args:
        url: The webpage URL to scrape
        session_id: Optional browser session ID for stateful navigation
    """
    result = await _shared.call_engine("POST", "/scrape", {
        "url": url,
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Scrape failed: {result['error']}"
    content = result.get("context", result.get("markdown", "No content extracted."))
    return f"PAGE CONTENT ({url}):\n\n{content[:10000]}"


async def click_element(url: str, selector: str, session_id: str = None) -> str:
    """
    Click an element on a webpage using a CSS selector.
    Returns the page state after clicking.

    Args:
        url: The page URL
        selector: CSS selector for the element to click
        session_id: Optional browser session ID
    """
    result = await _shared.call_engine("POST", "/act", {
        "url": url,
        "steps": [{"action": "click", "selector": selector}],
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Click failed: {result['error']}"
    return f"Clicked '{selector}' on {url}. Result: {result.get('markdown', 'Action completed.')[:5000]}"


async def type_text(url: str, selector: str, text: str, session_id: str = None) -> str:
    """
    Type text into an input field on a webpage.

    Args:
        url: The page URL
        selector: CSS selector for the input field
        text: Text to type
        session_id: Optional browser session ID
    """
    result = await _shared.call_engine("POST", "/act", {
        "url": url,
        "steps": [{"action": "type", "selector": selector, "value": text}],
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Type failed: {result['error']}"
    return f"Typed '{text}' into '{selector}' on {url}."


async def take_screenshot(url: str, full_page: bool = False, session_id: str = None) -> str:
    """
    Take a screenshot of a webpage. Returns base64-encoded PNG.

    Args:
        url: The page URL to screenshot
        full_page: Capture the full scrollable page (default: viewport only)
        session_id: Optional browser session ID
    """
    result = await _shared.call_engine("POST", "/scrape", {
        "url": url,
        "query": "screenshot",
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Screenshot failed: {result['error']}"
    return f"Screenshot captured for {url}. Content length: {len(result.get('context', ''))} chars."


async def navigate_browser(url: str, session_id: str = None) -> str:
    """
    Navigate the browser to a URL. Use before other browser actions
    to establish the page context.

    Args:
        url: The URL to navigate to
        session_id: Optional browser session ID
    """
    result = await _shared.call_engine("POST", "/scrape", {
        "url": url,
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Navigation failed: {result['error']}"
    return f"Navigated to {url}. Page title: {result.get('title', 'Unknown')}"



def register(mcp) -> None:
    """Register all 5 browser_actions tools with the server."""
    mcp.tool()(scrape_page)
    mcp.tool()(click_element)
    mcp.tool()(type_text)
    mcp.tool()(take_screenshot)
    mcp.tool()(navigate_browser)
