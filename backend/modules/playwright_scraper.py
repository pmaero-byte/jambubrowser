"""
Playwright Browser Scraper
==========================
Direct Playwright-based scraping when crawl4ai is not available.
Provides fallback browser automation capabilities.
"""

import asyncio
import logging
from typing import Optional, Dict, Any, List
from markdownify import markdownify as md

log = logging.getLogger("jambu.playwright_scraper")


async def scrape_with_playwright(
    url: str,
    wait_until: str = "networkidle",
    timeout: int = 30000,
    js_code: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Scrape a webpage using Playwright directly.
    
    Returns:
        Dict with 'success', 'content', 'title', 'url' keys
    """
    try:
        from playwright.async_api import async_playwright
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            
            try:
                await page.goto(url, wait_until=wait_until, timeout=timeout)
                
                # Execute custom JavaScript if provided
                if js_code:
                    await page.evaluate(js_code)
                
                # Get page content
                content = await page.content()
                title = await page.title()
                
                # Convert HTML to markdown
                markdown_content = md(
                    content,
                    strip=["img", "script", "style"],
                    heading_style="ATX",
                )
                
                await browser.close()
                
                return {
                    "success": True,
                    "content": markdown_content[:50000],
                    "title": title,
                    "url": url,
                }
            except Exception as e:
                await browser.close()
                return {
                    "success": False,
                    "content": "",
                    "title": "",
                    "url": url,
                    "error": str(e),
                }
    except ImportError:
        return {
            "success": False,
            "content": "",
            "title": "",
            "url": url,
            "error": "Playwright not installed",
        }
    except Exception as e:
        return {
            "success": False,
            "content": "",
            "title": "",
            "url": url,
            "error": str(e),
        }


class ActionError(ValueError):
    """An /act action was malformed or unsupported (never silently skipped)."""


SUPPORTED_ACTIONS = ("click", "type", "scroll", "click_xy", "wait", "goto")


async def apply_action(page, action: Dict[str, Any], *, wait_until: str = "networkidle",
                       timeout: int = 30000) -> str:
    """Run one action on ``page``; returns a short detail string.

    Raises :class:`ActionError` for an unknown action or missing arguments,
    and lets Playwright errors (selector not found, timeout) propagate, so
    the caller records a failure instead of pretending the step ran.
    """
    action_type = action.get("action")
    selector = action.get("selector")
    value = action.get("value")
    x = action.get("x")
    y = action.get("y")
    if action_type not in SUPPORTED_ACTIONS:
        raise ActionError(f"unsupported action {action_type!r}; "
                          f"supported: {', '.join(SUPPORTED_ACTIONS)}")
    if action_type == "click":
        if not selector:
            raise ActionError("click needs a selector")
        await page.click(selector, timeout=timeout)
        return f"clicked {selector}"
    if action_type == "type":
        if not selector or value is None:
            raise ActionError("type needs a selector and a value")
        await page.fill(selector, str(value), timeout=timeout)
        return f"filled {selector}"
    if action_type == "scroll":
        try:
            dy = float(value)
        except (TypeError, ValueError):
            raise ActionError(f"scroll needs a numeric value, got {value!r}")
        await page.evaluate("dy => window.scrollBy(0, dy)", dy)
        return f"scrolled {dy:g}px"
    if action_type == "click_xy":
        if x is None or y is None:
            raise ActionError("click_xy needs x and y (viewport percent)")
        hit = await page.evaluate(
            "([px, py]) => { const el = document.elementFromPoint("
            "window.innerWidth * px / 100, window.innerHeight * py / 100);"
            " if (!el) return false; el.click(); return true; }",
            [float(x), float(y)],
        )
        if not hit:
            raise ActionError(f"click_xy found no element at ({x}%, {y}%)")
        return f"clicked at ({x}%, {y}%)"
    if action_type == "wait":
        try:
            ms = int(float(value))
        except (TypeError, ValueError):
            raise ActionError(f"wait needs a numeric value in ms, got {value!r}")
        if ms < 0:
            raise ActionError("wait value must be >= 0")
        await page.wait_for_timeout(ms)
        return f"waited {ms}ms"
    # goto
    if not value:
        raise ActionError("goto needs a URL value")
    await page.goto(value, wait_until=wait_until, timeout=timeout)
    return f"navigated to {value}"


async def run_actions(page, actions: List[Dict[str, Any]], *, wait_until: str = "networkidle",
                      timeout: int = 30000) -> List[Dict[str, Any]]:
    """Run every action, recording an outcome for each (ok or error)."""
    results: List[Dict[str, Any]] = []
    for index, action in enumerate(actions):
        entry: Dict[str, Any] = {"index": index, "action": action.get("action")}
        try:
            entry["detail"] = await apply_action(page, action, wait_until=wait_until,
                                                 timeout=timeout)
            entry["ok"] = True
        except Exception as e:
            log.error("Action %s failed: %s", action.get("action"), e)
            entry["ok"] = False
            entry["error"] = f"{type(e).__name__}: {e}"
        results.append(entry)
    return results


def summarize_action_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    failed = [r for r in results if not r.get("ok")]
    if not failed:
        status = "ok"
    elif len(failed) == len(results):
        status = "failed"
    else:
        status = "partial"
    return {"status": status, "action_results": results, "failed_actions": failed,
            "actions_failed": len(failed), "actions_total": len(results)}


async def perform_actions_with_playwright(
    url: str,
    actions: List[Dict[str, Any]],
    wait_until: str = "networkidle",
    timeout: int = 30000,
) -> Dict[str, Any]:
    """
    Perform browser actions using Playwright directly.
    
    Args:
        url: Target URL
        actions: List of action dicts with 'action', 'selector', 'value', 'x', 'y' keys
        wait_until: When to consider page loaded
        timeout: Navigation timeout in ms
    
    Returns:
        Dict with 'success', 'content', 'url' keys
    """
    try:
        from playwright.async_api import async_playwright
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            
            try:
                await page.goto(url, wait_until=wait_until, timeout=timeout)
                
                # Execute each action; a failure is recorded, never skipped.
                results = await run_actions(page, actions, wait_until=wait_until,
                                            timeout=timeout)
                summary = summarize_action_results(results)

                # Get final page content
                content = await page.content()
                title = await page.title()
                
                # Convert HTML to markdown
                markdown_content = md(
                    content,
                    strip=["img", "script", "style"],
                    heading_style="ATX",
                )
                
                await browser.close()
                
                result = {
                    "success": summary["status"] == "ok",
                    "content": markdown_content[:10000],
                    "title": title,
                    "url": url,
                    **summary,
                }
                if summary["failed_actions"]:
                    first = summary["failed_actions"][0]
                    result["error"] = (
                        f"{summary['actions_failed']}/{summary['actions_total']} action(s) failed; "
                        f"first: #{first['index']} {first['action']}: {first['error']}")
                return result
            except Exception as e:
                await browser.close()
                return {
                    "success": False,
                    "content": "",
                    "title": "",
                    "url": url,
                    "error": str(e),
                }
    except ImportError:
        return {
            "success": False,
            "content": "",
            "title": "",
            "url": url,
            "error": "Playwright not installed",
        }
    except Exception as e:
        return {
            "success": False,
            "content": "",
            "title": "",
            "url": url,
            "error": str(e),
        }
