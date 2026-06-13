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
                
                # Execute each action
                for action in actions:
                    action_type = action.get("action")
                    selector = action.get("selector")
                    value = action.get("value")
                    x = action.get("x")
                    y = action.get("y")
                    
                    try:
                        if action_type == "click" and selector:
                            await page.click(selector)
                        elif action_type == "type" and selector and value:
                            await page.fill(selector, value)
                        elif action_type == "scroll" and value:
                            await page.evaluate(f"window.scrollBy(0, {value})")
                        elif action_type == "click_xy" and x is not None and y is not None:
                            # Calculate viewport coordinates
                            vx = f"window.innerWidth * {x / 100}"
                            vy = f"window.innerHeight * {y / 100}"
                            await page.evaluate(
                                f"{{ const el = document.elementFromPoint({vx}, {vy}); if(el) el.click(); }}"
                            )
                        elif action_type == "wait" and value:
                            await page.wait_for_timeout(int(value))
                        elif action_type == "goto" and value:
                            await page.goto(value, wait_until=wait_until, timeout=timeout)
                    except Exception as e:
                        log.error("Action %s failed: %s", action_type, e)
                        continue
                
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
                
                return {
                    "success": True,
                    "content": markdown_content[:10000],
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
