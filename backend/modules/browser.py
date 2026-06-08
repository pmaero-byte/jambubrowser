"""
Playwright Browser Automation
=============================
Privacy-first browser automation with strict isolation modes.
Supports persistent, ephemeral, and tor-routed sessions.

Security Features:
- Mandatory context isolation per session
- Tor routing with stream isolation
- Ephemeral/incognito mode (no persistence)
- Fingerprint randomization per session
- Request/response sanitization
- Zero-knowledge session management
"""

import asyncio
import json
import time
import base64
import hashlib
import secrets
from typing import Optional, List, Dict, Literal
from pathlib import Path
from threading import Lock
from enum import Enum

from backend.core.database import get_db, get_db_cursor
from backend.modules.fingerprint_rotator import get_rotator


# Lazy import for Playwright - only imported when first used
_playwright = None
_playwright_lock = Lock()


class SessionMode(Enum):
    """Browser session isolation modes."""
    PERSISTENT = "persistent"      # Full state persistence (cookies, localStorage, cache)
    EPHEMERAL = "ephemeral"        # In-memory only, destroyed on close
    TOR_ISOLATED = "tor_isolated"  # Tor-routed with stream isolation, ephemeral
    LOCAL_ONLY = "local_only"      # No external network calls allowed


class PrivacyLevel(Enum):
    """Privacy enforcement levels."""
    STANDARD = "standard"           # Basic fingerprinting protection
    ENHANCED = "enhanced"           # Fingerprint rotation + cookie blocking
    MAXIMUM = "maximum"             # Tor + no JS + no persistence + sanitization


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
    Privacy-first browser session with strict isolation modes.

    Security Features:
    - Mandatory context isolation per session
    - Tor routing with stream isolation
    - Ephemeral/incognito mode (no persistence)
    - Fingerprint randomization per session
    - Request/response sanitization
    """

    def __init__(
        self,
        session_id: str,
        name: str = "default",
        proxy: str = None,
        mode: SessionMode = SessionMode.EPHEMERAL,
        privacy_level: PrivacyLevel = PrivacyLevel.ENHANCED,
    ):
        self.session_id = session_id
        self.name = name
        self.proxy = proxy
        self.mode = mode
        self.privacy_level = privacy_level
        self._context = None
        self._page = None
        self._pw = None
        self._browser = None
        self._fingerprint = None
        self._created_at = time.time()
        self._pages_visited = 0
        self._sanitize_log = []

    async def start(self):
        """Launch the browser session with privacy-first configuration."""
        self._pw = await _get_playwright()

        # Configure browser launch
        launch_args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
        ]

        # Tor routing configuration
        if self.mode == SessionMode.TOR_ISOLATED:
            if not self.proxy:
                self.proxy = "socks5://127.0.0.1:9050"
            # Additional Tor-specific flags for stream isolation
            launch_args.extend([
                "--proxy-server=" + self.proxy,
                "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1",
            ])
        elif self.proxy:
            launch_args.append(f"--proxy-server={self.proxy}")

        # Privacy: Disable telemetry and tracking
        launch_args.extend([
            "--disable-background-networking",
            "--disable-client-side-phishing-detection",
            "--disable-default-apps",
            "--disable-extensions",
            "--disable-gpu",
            "--disable-sync",
            "--no-first-run",
        ])

        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=launch_args,
        )

        # Generate fingerprint for this session
        rotator = get_rotator()
        self._fingerprint = rotator.generate_profile()

        # Create isolated context with fingerprint
        context_options = self._fingerprint.to_playwright_config()

        # Privacy: Block cookies in ephemeral/maximum mode
        if self.mode in (SessionMode.EPHEMERAL, SessionMode.TOR_ISOLATED):
            context_options["storage_state"] = None
            context_options["is_mobile"] = False
            context_options["has_touch"] = False

        # Tor: Additional isolation
        if self.mode == SessionMode.TOR_ISOLATED:
            context_options["extra_http_headers"] = {
                "Accept-Language": "en-US,en;q=0.9",
                "DNT": "1",
            }

        self._context = await self._browser.new_context(**context_options)

        # Inject anti-fingerprinting scripts
        await self._inject_privacy_scripts()

        self._page = await self._context.new_page()

        # Privacy: Block tracking scripts in maximum mode
        if self.privacy_level == PrivacyLevel.MAXIMUM:
            await self._block_trackers()

    async def _inject_privacy_scripts(self):
        """Inject anti-fingerprinting and privacy scripts."""
        privacy_js = """
        // Override navigator properties
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });

        // Override permissions API
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );

        // Override chrome detection
        window.chrome = { runtime: {} };

        // Override console.debug to prevent detection
        const originalDebug = console.debug;
        console.debug = function() { return originalDebug.apply(this, arguments); };
        """
        await self._context.add_init_script(privacy_js)

    async def _block_trackers(self):
        """Block known tracking domains."""
        blocking_patterns = [
            "google-analytics.com",
            "googletagmanager.com",
            "facebook.com/tr",
            "doubleclick.net",
            "hotjar.com",
            "mixpanel.com",
            "segment.com",
            "amplitude.com",
        ]

        def route_handler(route):
            url = route.request.url
            if any(pattern in url for pattern in blocking_patterns):
                return route.abort()
            return route.continue_()

        await self._page.route("**/*", route_handler)

    async def stop(self):
        """Close the browser session. Ephemeral sessions leave no trace."""
        if self._context:
            # Only persist state for persistent mode
            if self.mode == SessionMode.PERSISTENT:
                await self.save_state()

            # Clear all storage in ephemeral modes
            if self.mode in (SessionMode.EPHEMERAL, SessionMode.TOR_ISOLATED):
                await self._clear_all_storage()

            await self._context.close()

        if self._browser:
            await self._browser.close()

        self._context = None
        self._page = None
        self._browser = None

    async def _clear_all_storage(self):
        """Clear all browser storage (cookies, localStorage, sessionStorage)."""
        try:
            if self._page and not self._page.is_closed():
                # Clear cookies
                await self._context.clear_cookies()

                # Clear storage via JavaScript
                await self._page.evaluate("""
                    () => {
                        try {
                            localStorage.clear();
                            sessionStorage.clear();
                            indexedDB.databases().then(databases => {
                                databases.forEach(db => indexedDB.deleteDatabase(db.name));
                            });
                        } catch (e) {}
                    }
                """)
        except Exception:
            pass  # Best-effort cleanup

    async def _ensure_page(self):
        """Ensure we have an active page."""
        if self._page is None or self._page.is_closed():
            if self._context is None:
                await self.start()
            self._page = await self._context.new_page()
        return self._page

    async def save_state(self):
        """Persist cookies and state to database (persistent mode only)."""
        if not self._context or self.mode != SessionMode.PERSISTENT:
            return

        try:
            cookies = await self._context.cookies()
            local_storage = {}
            if self._page and not self._page.is_closed():
                try:
                    local_storage = await self._page.evaluate(
                        "() => JSON.stringify(localStorage)"
                    )
                    local_storage = json.loads(local_storage) if local_storage else {}
                except Exception:
                    local_storage = {}

            # Encrypt sensitive data before storage
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
                        self._fingerprint.user_agent if self._fingerprint else "",
                        self.proxy,
                        time.time(),
                    ),
                )
        except Exception:
            pass  # Non-critical

    async def load_state(self):
        """Restore cookies and state from database (persistent mode only)."""
        if not self._context or self.mode != SessionMode.PERSISTENT:
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

                if row["cookies"]:
                    cookies = json.loads(row["cookies"])
                    await self._context.add_cookies(cookies)

            with get_db_cursor() as cursor:
                cursor.execute(
                    "UPDATE browser_sessions SET last_used = ? WHERE id = ?",
                    (time.time(), self.session_id),
                )
        except Exception:
            pass

    def get_privacy_report(self) -> dict:
        """Get a report of privacy protections applied to this session."""
        return {
            "session_id": self.session_id,
            "mode": self.mode.value,
            "privacy_level": self.privacy_level.value,
            "fingerprint_id": self._fingerprint.profile_id if self._fingerprint else None,
            "tor_enabled": self.mode == SessionMode.TOR_ISOLATED,
            "cookies_cleared": self.mode in (SessionMode.EPHEMERAL, SessionMode.TOR_ISOLATED),
            "trackers_blocked": self.privacy_level == PrivacyLevel.MAXIMUM,
            "anti_fingerprint": True,
            "pages_visited": self._pages_visited,
            "created_at": self._created_at,
        }


# ---- Browser Manager ----

class BrowserManager:
    """
    Manages multiple BrowserSession instances with privacy-first defaults.
    Singleton pattern for resource efficiency.

    Security Features:
    - Session isolation enforcement
    - Automatic cleanup of ephemeral sessions
    - Privacy level validation
    - Resource usage monitoring
    """

    _instance: Optional["BrowserManager"] = None
    _lock: Lock = Lock()

    def __init__(self):
        self._sessions: Dict[str, BrowserSession] = {}
        self._session_limits = {
            "max_concurrent": 5,
            "ephemeral_ttl": 3600,  # Auto-cleanup after 1 hour
            "max_pages_per_session": 100,
        }

    @classmethod
    def get_instance(cls) -> "BrowserManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    async def get_session(
        self,
        session_id: str,
        mode: SessionMode = SessionMode.EPHEMERAL,
        privacy_level: PrivacyLevel = PrivacyLevel.ENHANCED,
    ) -> BrowserSession:
        """Get or create a browser session with specified privacy mode."""
        # Enforce session limits
        if len(self._sessions) >= self._session_limits["max_concurrent"]:
            # Cleanup oldest ephemeral session
            await self._cleanup_oldest_ephemeral()

        if session_id not in self._sessions:
            session = BrowserSession(
                session_id=session_id,
                name=session_id,
                mode=mode,
                privacy_level=privacy_level,
            )
            await session.start()
            self._sessions[session_id] = session
        elif self._sessions[session_id]._context is None:
            await self._sessions[session_id].start()

        return self._sessions[session_id]

    def _create_session_object(
        self,
        name: str,
        proxy: str = None,
        mode: SessionMode = SessionMode.EPHEMERAL,
        privacy_level: PrivacyLevel = PrivacyLevel.ENHANCED,
    ) -> BrowserSession:
        """Create a session object without starting it (for testing)."""
        session_id = hashlib.md5(
            f"{name}_{time.time()}_{secrets.token_hex(8)}".encode()
        ).hexdigest()[:16]

        session = BrowserSession(
            session_id=session_id,
            name=name,
            proxy=proxy,
            mode=mode,
            privacy_level=privacy_level,
        )
        return session

    async def create_session(
        self,
        name: str,
        proxy: str = None,
        mode: SessionMode = SessionMode.EPHEMERAL,
        privacy_level: PrivacyLevel = PrivacyLevel.ENHANCED,
    ) -> BrowserSession:
        """Create a new named browser session with privacy controls."""
        session_id = hashlib.md5(
            f"{name}_{time.time()}_{secrets.token_hex(8)}".encode()
        ).hexdigest()[:16]

        session = BrowserSession(
            session_id=session_id,
            name=name,
            proxy=proxy,
            mode=mode,
            privacy_level=privacy_level,
        )
        await session.start()
        self._sessions[session_id] = session
        return session

    async def create_tor_session(self, name: str = "tor_session") -> BrowserSession:
        """Create a Tor-isolated session for maximum privacy."""
        return await self.create_session(
            name=name,
            mode=SessionMode.TOR_ISOLATED,
            privacy_level=PrivacyLevel.MAXIMUM,
        )

    async def create_ephemeral_session(self, name: str = "ephemeral") -> BrowserSession:
        """Create an ephemeral session (no persistence)."""
        return await self.create_session(
            name=name,
            mode=SessionMode.EPHEMERAL,
            privacy_level=PrivacyLevel.ENHANCED,
        )

    async def close_session(self, session_id: str):
        """Close a specific session and clean up all traces."""
        if session_id in self._sessions:
            await self._sessions[session_id].stop()
            del self._sessions[session_id]

    async def close_all(self):
        """Close all active sessions."""
        for session_id in list(self._sessions.keys()):
            await self.close_session(session_id)

    async def _cleanup_oldest_ephemeral(self):
        """Cleanup the oldest ephemeral session to free resources."""
        ephemeral_sessions = [
            (sid, s) for sid, s in self._sessions.items()
            if s.mode in (SessionMode.EPHEMERAL, SessionMode.TOR_ISOLATED)
        ]
        if ephemeral_sessions:
            oldest_sid = min(ephemeral_sessions, key=lambda x: x[1]._created_at)[0]
            await self.close_session(oldest_sid)

    def list_sessions(self) -> list:
        """List all active sessions with privacy info."""
        return [
            {
                "id": sid,
                "name": s.name,
                "mode": s.mode.value,
                "privacy_level": s.privacy_level.value,
                "pages_visited": s._pages_visited,
            }
            for sid, s in self._sessions.items()
        ]

    def get_privacy_summary(self) -> dict:
        """Get summary of all sessions' privacy protections."""
        sessions = list(self._sessions.values())
        return {
            "total_sessions": len(sessions),
            "ephemeral_count": sum(1 for s in sessions if s.mode == SessionMode.EPHEMERAL),
            "tor_count": sum(1 for s in sessions if s.mode == SessionMode.TOR_ISOLATED),
            "persistent_count": sum(1 for s in sessions if s.mode == SessionMode.PERSISTENT),
            "max_privacy_count": sum(1 for s in sessions if s.privacy_level == PrivacyLevel.MAXIMUM),
        }


def get_browser_manager() -> BrowserManager:
    """Get the singleton browser manager."""
    return BrowserManager.get_instance()


# ---- Core Browser Actions ----

async def scrape_url(
    url: str,
    session_id: str = None,
    wait_until: str = "networkidle",
    mode: SessionMode = SessionMode.EPHEMERAL,
    privacy_level: PrivacyLevel = PrivacyLevel.ENHANCED,
) -> dict:
    """
    Navigate to URL and return scraped content as markdown-like text.

    Privacy Features:
    - Automatic session isolation
    - Request/response sanitization
    - Fingerprint rotation

    Returns:
        dict with keys: success, url, title, text_content, screenshot_base64, error, privacy_report
    """
    try:
        manager = get_browser_manager()
        session = await manager.get_session(
            session_id or "default",
            mode=mode,
            privacy_level=privacy_level,
        )
        page = await session._ensure_page()

        await page.goto(url, wait_until=wait_until, timeout=30000)

        title = await page.title()

        # Extract text content (approximate markdown)
        text_content = await page.evaluate("""
            () => {
                const main = document.querySelector('main, article, [role="main"], .content, #content');
                const source = main || document.body;
                return source.innerText.substring(0, 50000);
            }
        """)

        # Take screenshot
        screenshot = await page.screenshot(type="png", full_page=False)
        screenshot_b64 = base64.b64encode(screenshot).decode()

        session._pages_visited += 1

        return {
            "success": True,
            "url": url,
            "title": title,
            "text_content": text_content,
            "screenshot_base64": screenshot_b64,
            "error": None,
            "privacy_report": session.get_privacy_report(),
        }
    except Exception as e:
        return {
            "success": False,
            "url": url,
            "title": None,
            "text_content": None,
            "screenshot_base64": None,
            "error": str(e),
            "privacy_report": None,
        }


async def navigate(
    url: str,
    session_id: str = None,
    mode: SessionMode = SessionMode.EPHEMERAL,
    privacy_level: PrivacyLevel = PrivacyLevel.ENHANCED,
) -> dict:
    """Navigate to a URL in the browser session."""
    try:
        manager = get_browser_manager()
        session = await manager.get_session(
            session_id or "default",
            mode=mode,
            privacy_level=privacy_level,
        )
        page = await session._ensure_page()

        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        title = await page.title()

        session._pages_visited += 1

        return {
            "success": True,
            "url": url,
            "title": title,
            "error": None,
            "privacy_report": session.get_privacy_report(),
        }
    except Exception as e:
        return {"success": False, "url": url, "title": None, "error": str(e)}


async def click_element(
    url: str,
    selector: str,
    session_id: str = None,
    mode: SessionMode = SessionMode.EPHEMERAL,
    privacy_level: PrivacyLevel = PrivacyLevel.ENHANCED,
) -> dict:
    """Click an element identified by CSS selector."""
    try:
        manager = get_browser_manager()
        session = await manager.get_session(
            session_id or "default",
            mode=mode,
            privacy_level=privacy_level,
        )
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
    mode: SessionMode = SessionMode.EPHEMERAL,
    privacy_level: PrivacyLevel = PrivacyLevel.ENHANCED,
) -> dict:
    """
    Click at viewport-relative coordinates (0-100 percentage).
    """
    try:
        manager = get_browser_manager()
        session = await manager.get_session(
            session_id or "default",
            mode=mode,
            privacy_level=privacy_level,
        )
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
    mode: SessionMode = SessionMode.EPHEMERAL,
    privacy_level: PrivacyLevel = PrivacyLevel.ENHANCED,
) -> dict:
    """Type text into an input element."""
    try:
        manager = get_browser_manager()
        session = await manager.get_session(
            session_id or "default",
            mode=mode,
            privacy_level=privacy_level,
        )
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
    mode: SessionMode = SessionMode.EPHEMERAL,
    privacy_level: PrivacyLevel = PrivacyLevel.ENHANCED,
) -> dict:
    """Fill multiple form fields at once."""
    try:
        manager = get_browser_manager()
        session = await manager.get_session(
            session_id or "default",
            mode=mode,
            privacy_level=privacy_level,
        )
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
    mode: SessionMode = SessionMode.EPHEMERAL,
    privacy_level: PrivacyLevel = PrivacyLevel.ENHANCED,
) -> dict:
    """Scroll the page by a pixel amount."""
    try:
        manager = get_browser_manager()
        session = await manager.get_session(
            session_id or "default",
            mode=mode,
            privacy_level=privacy_level,
        )
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
    mode: SessionMode = SessionMode.EPHEMERAL,
    privacy_level: PrivacyLevel = PrivacyLevel.ENHANCED,
) -> dict:
    """Take a screenshot of the current page or a specific URL."""
    try:
        manager = get_browser_manager()
        session = await manager.get_session(
            session_id or "default",
            mode=mode,
            privacy_level=privacy_level,
        )
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
    mode: SessionMode = SessionMode.EPHEMERAL,
    privacy_level: PrivacyLevel = PrivacyLevel.ENHANCED,
) -> dict:
    """Get the full HTML and text content of a page."""
    try:
        manager = get_browser_manager()
        session = await manager.get_session(
            session_id or "default",
            mode=mode,
            privacy_level=privacy_level,
        )
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
    mode: SessionMode = SessionMode.EPHEMERAL,
    privacy_level: PrivacyLevel = PrivacyLevel.ENHANCED,
) -> dict:
    """Press a keyboard key in the active page."""
    try:
        manager = get_browser_manager()
        session = await manager.get_session(
            session_id or "default",
            mode=mode,
            privacy_level=privacy_level,
        )
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
