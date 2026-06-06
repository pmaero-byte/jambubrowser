"""
Playwright Browser Automation
=============================
Persistent browser sessions with cookie management and stateful
navigation. Replaces Crawl4AI for transactional workflows.

Features:
- Persistent browser contexts with cookie storage
- Session management (create, load, save, close)
- Core actions: navigate, click, type, fill, scroll, screenshot
- Session persistence via database
"""

import asyncio
import json
import time
import base64
import hashlib
from typing import Optional, List, Dict
from pathlib import Path
from threading import Lock

from backend.core.database import get_db, get_db_cursor


# Lazy import for Playwright - only imported when first used
_playwright = None
_playwright_lock = Lock()


async def _get_playwright():
    """Lazy-load Playwright with error handling."""
    global _playwright
    if _playwright is not None:
        return _playwright

    with _playwright_lock:
        if _playwright is not None:
            return _playwright

        try:
            from playwright.async_api import async_playwright
            _pw = await async_playwright().start()
            _playwright = _pw
            return _playwright
        except ImportError:
            raise ImportError(
                "Playwright is not installed. Install with:\n"
                "  pip install playwright\n"
                "  playwright install chromium\n"
            )
        except Exception as e:
            raise RuntimeError(f"Failed to start Playwright: {e}")


# ---- Browser Session ----

class BrowserSession:
    """
    A persistent browser session with cookie and state management.
    Each session maintains its own browser context with isolated
    storage, cookies, and fingerprint.
    """

    def __init__(
        self,
        session_id: str,
        name: str = "default",
        proxy: str = None,
        user_agent: str = None,
    ):
        self.session_id = session_id
        self.name = name
        self.proxy = proxy
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        )
        self._context = None
        self._page = None
        self._pw = None
        self._browser = None

    async def start(self):
        """Launch the browser session."""
        self._pw = await _get_playwright()

        # Configure browser launch
        launch_args = ["--no-sandbox", "--disable-setuid-sandbox"]
        if self.proxy:
            launch_args.append(f"--proxy-server={self.proxy}")

        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=launch_args,
        )

        # Create isolated context
        context_options = {
            "user_agent": self.user_agent,
            "viewport": {"width": 1440, "height": 900},
            "locale": "en-US",
        }

        if self.proxy:
            context_options["proxy"] = {"server": self.proxy}

        self._context = await self._browser.new_context(**context_options)
        self._page = await self._context.new_page()

        # Try to restore saved state from DB
        await self.load_state()

    async def stop(self):
        """Close the browser session and persist state."""
        if self._context:
            await self.save_state()
            await self._context.close()
        if self._browser:
            await self._browser.close()
        self._context = None
        self._page = None
        self._browser = None

    async def _ensure_page(self):
        """Ensure we have an active page."""
        if self._page is None or self._page.is_closed():
            if self._context is None:
                await self.start()
            self._page = await self._context.new_page()
        return self._page

    async def save_state(self):
        """Persist cookies and state to database."""
        if not self._context:
            return

        try:
            cookies = await self._context.cookies()
            # Also try to get localStorage from current page
            local_storage = {}
            if self._page and not self._page.is_closed():
                try:
                    local_storage = await self._page.evaluate(
                        "() => JSON.stringify(localStorage)"
                    )
                    local_storage = json.loads(local_storage) if local_storage else {}
                except Exception:
                    local_storage = {}

            with get_db_cursor() as cursor:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO browser_sessions
                    (id, name, cookies, local_storage, user_agent, proxy, last_used)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.session_id,
                        self.name,
                        json.dumps(cookies),
                        json.dumps(local_storage),
                        self.user_agent,
                        self.proxy,
                        time.time(),
                    ),
                )
        except Exception:
            pass  # Non-critical - state persistence is best-effort

    async def load_state(self):
        """Restore cookies and state from database."""
        if not self._context:
            return

        try:
            with get_db_cursor() as cursor:
                cursor.execute(
                    "SELECT cookies, local_storage FROM browser_sessions WHERE id = ?",
                    (self.session_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return

                # Restore cookies
                if row["cookies"]:
                    cookies = json.loads(row["cookies"])
                    await self._context.add_cookies(cookies)

            # Update last_used
            with get_db_cursor() as cursor:
                cursor.execute(
                    "UPDATE browser_sessions SET last_used = ? WHERE id = ?",
                    (time.time(), self.session_id),
                )
        except Exception:
            pass  # Non-critical


# ---- Browser Manager ----

class BrowserManager:
    """
    Manages multiple BrowserSession instances.
    Singleton pattern for resource efficiency.
    """

    _instance: Optional["BrowserManager"] = None
    _lock: Lock = Lock()

    def __init__(self):
        self._sessions: Dict[str, BrowserSession] = {}

    @classmethod
    def get_instance(cls) -> "BrowserManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    async def get_session(self, session_id: str) -> BrowserSession:
        """Get or create a browser session."""
        if session_id not in self._sessions:
            session = BrowserSession(session_id=session_id, name=session_id)
            await session.start()
            self._sessions[session_id] = session
        elif self._sessions[session_id]._context is None:
            await self._sessions[session_id].start()
        return self._sessions[session_id]

    async def create_session(
        self,
        name: str,
        proxy: str = None,
        user_agent: str = None,
    ) -> BrowserSession:
        """Create a new named browser session."""
        session_id = hashlib.md5(
            f"{name}_{time.time()}".encode()
        ).hexdigest()[:16]
        session = BrowserSession(
            session_id=session_id,
            name=name,
            proxy=proxy,
            user_agent=user_agent,
        )
        await session.start()
        self._sessions[session_id] = session
        return session

    async def close_session(self, session_id: str):
        """Close a specific session."""
        if session_id in self._sessions:
            await self._sessions[session_id].stop()
            del self._sessions[session_id]

    async def close_all(self):
        """Close all active sessions."""
        for session_id in list(self._sessions.keys()):
            await self.close_session(session_id)

    def list_sessions(self) -> list:
        """List all active session IDs."""
        return list(self._sessions.keys())


def get_browser_manager() -> BrowserManager:
    """Get the singleton browser manager."""
    return BrowserManager.get_instance()


# ---- Core Browser Actions ----

async def scrape_url(
    url: str,
    session_id: str = None,
    wait_until: str = "networkidle",
) -> dict:
    """
    Navigate to URL and return scraped content as markdown-like text.

    Returns:
        dict with keys: success, url, title, text_content, screenshot_base64, error
    """
    try:
        manager = get_browser_manager()
        session = await manager.get_session(session_id or "default")
        page = await session._ensure_page()

        await page.goto(url, wait_until=wait_until, timeout=30000)

        title = await page.title()

        # Extract text content (approximate markdown)
        text_content = await page.evaluate("""
            () => {
                // Get main content, skip nav/footer
                const main = document.querySelector('main, article, [role="main"], .content, #content');
                const source = main || document.body;
                return source.innerText.substring(0, 50000);
            }
        """)

        # Take screenshot
        screenshot = await page.screenshot(type="png", full_page=False)
        screenshot_b64 = base64.b64encode(screenshot).decode()

        return {
            "success": True,
            "url": url,
            "title": title,
            "text_content": text_content,
            "screenshot_base64": screenshot_b64,
            "error": None,
        }
    except Exception as e:
        return {
            "success": False,
            "url": url,
            "title": None,
            "text_content": None,
            "screenshot_base64": None,
            "error": str(e),
        }


async def navigate(url: str, session_id: str = None) -> dict:
    """Navigate to a URL in the browser session."""
    try:
        manager = get_browser_manager()
        session = await manager.get_session(session_id or "default")
        page = await session._ensure_page()

        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        title = await page.title()

        return {
            "success": True,
            "url": url,
            "title": title,
            "error": None,
        }
    except Exception as e:
        return {"success": False, "url": url, "title": None, "error": str(e)}


async def click_element(
    url: str,
    selector: str,
    session_id: str = None,
) -> dict:
    """Click an element identified by CSS selector."""
    try:
        manager = get_browser_manager()
        session = await manager.get_session(session_id or "default")
        page = await session._ensure_page()

        if page.url != url:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        await page.click(selector, timeout=10000)

        return {
            "success": True,
            "action": "click",
            "selector": selector,
            "url": page.url,
            "error": None,
        }
    except Exception as e:
        return {"success": False, "action": "click", "selector": selector, "error": str(e)}


async def click_coordinates(
    url: str,
    x: float,
    y: float,
    session_id: str = None,
) -> dict:
    """
    Click at viewport-relative coordinates (0-100 percentage).

    Args:
        x: Horizontal position as percentage (0-100)
        y: Vertical position as percentage (0-100)
    """
    try:
        manager = get_browser_manager()
        session = await manager.get_session(session_id or "default")
        page = await session._ensure_page()

        if page.url != url:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        viewport = page.viewport_size
        px = int(viewport["width"] * x / 100)
        py = int(viewport["height"] * y / 100)

        await page.mouse.click(px, py)

        return {
            "success": True,
            "action": "click_xy",
            "x": x,
            "y": y,
            "pixel_x": px,
            "pixel_y": py,
            "error": None,
        }
    except Exception as e:
        return {"success": False, "action": "click_xy", "error": str(e)}


async def type_text(
    url: str,
    selector: str,
    text: str,
    session_id: str = None,
) -> dict:
    """Type text into an input element."""
    try:
        manager = get_browser_manager()
        session = await manager.get_session(session_id or "default")
        page = await session._ensure_page()

        if page.url != url:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        await page.fill(selector, text, timeout=10000)

        return {
            "success": True,
            "action": "type",
            "selector": selector,
            "text_length": len(text),
            "error": None,
        }
    except Exception as e:
        return {"success": False, "action": "type", "selector": selector, "error": str(e)}


async def fill_form(
    url: str,
    fields: List[Dict[str, str]],
    session_id: str = None,
) -> dict:
    """
    Fill multiple form fields at once.

    Args:
        fields: List of dicts with 'selector' and 'value' keys
    """
    try:
        manager = get_browser_manager()
        session = await manager.get_session(session_id or "default")
        page = await session._ensure_page()

        if page.url != url:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        filled = []
        for field in fields:
            selector = field.get("selector", "")
            value = field.get("value", "")
            if selector:
                await page.fill(selector, value, timeout=10000)
                filled.append(selector)

        return {
            "success": True,
            "action": "fill_form",
            "fields_filled": len(filled),
            "selectors": filled,
            "error": None,
        }
    except Exception as e:
        return {"success": False, "action": "fill_form", "error": str(e)}


async def scroll_page(
    url: str,
    amount: int = 500,
    session_id: str = None,
) -> dict:
    """Scroll the page by a pixel amount."""
    try:
        manager = get_browser_manager()
        session = await manager.get_session(session_id or "default")
        page = await session._ensure_page()

        if page.url != url:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        await page.evaluate(f"window.scrollBy(0, {amount})")

        scroll_y = await page.evaluate("window.scrollY")

        return {
            "success": True,
            "action": "scroll",
            "amount": amount,
            "scroll_y": scroll_y,
            "error": None,
        }
    except Exception as e:
        return {"success": False, "action": "scroll", "error": str(e)}


async def take_screenshot(
    url: str = None,
    session_id: str = None,
    full_page: bool = False,
) -> dict:
    """
    Take a screenshot of the current page or a specific URL.

    Returns:
        dict with keys: success, screenshot_base64, url, error
    """
    try:
        manager = get_browser_manager()
        session = await manager.get_session(session_id or "default")
        page = await session._ensure_page()

        if url and page.url != url:
            await page.goto(url, wait_until="networkidle", timeout=30000)

        screenshot = await page.screenshot(type="png", full_page=full_page)
        screenshot_b64 = base64.b64encode(screenshot).decode()

        return {
            "success": True,
            "screenshot_base64": screenshot_b64,
            "url": page.url,
            "error": None,
        }
    except Exception as e:
        return {
            "success": False,
            "screenshot_base64": None,
            "url": url,
            "error": str(e),
        }


async def get_page_content(
    url: str,
    session_id: str = None,
) -> dict:
    """Get the full HTML and text content of a page."""
    try:
        manager = get_browser_manager()
        session = await manager.get_session(session_id or "default")
        page = await session._ensure_page()

        await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        title = await page.title()
        html = await page.content()
        text = await page.evaluate("() => document.body.innerText")

        return {
            "success": True,
            "url": url,
            "title": title,
            "html": html[:100000],  # Truncate for safety
            "text_content": text[:50000],
            "error": None,
        }
    except Exception as e:
        return {
            "success": False,
            "url": url,
            "title": None,
            "html": None,
            "text_content": None,
            "error": str(e),
        }


async def press_key(
    key: str,
    session_id: str = None,
) -> dict:
    """Press a keyboard key in the active page."""
    try:
        manager = get_browser_manager()
        session = await manager.get_session(session_id or "default")
        page = await session._ensure_page()

        await page.keyboard.press(key)

        return {
            "success": True,
            "action": "press_key",
            "key": key,
            "error": None,
        }
    except Exception as e:
        return {"success": False, "action": "press_key", "error": str(e)}


# ---- Cleanup ----

async def cleanup_browser():
    """Close all browser sessions. Call on application shutdown."""
    try:
        manager = get_browser_manager()
        await manager.close_all()
    except Exception:
        pass
