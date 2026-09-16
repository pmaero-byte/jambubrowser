"""
Browser sessions as a service — the agent loop, hardened.

Exposes the perception pattern that won the category's bake-offs
(accessibility/DOM snapshot → typed element catalog with refs → deterministic
dispatch by ref) with the safety rails browser agents need in production:

- **Domain allowlist** per session. Navigations outside it are refused; link
  clicks are pre-checked against the catalog ``href`` and post-checked after
  the action (a disallowed landing page is reverted to ``about:blank`` and
  recorded as a violation).
- **Approval gates**. Sessions can require ``approve=true`` for input
  actions, and a *risk classifier* (buy/pay/delete/send/transfer/…) always
  requires explicit approval — prompt injection cannot click "Delete
  account" through the agent.
- **PII scrubbing**. Snapshot text, element names and hrefs pass through the
  shared :class:`PIIDetector` before an agent sees them.
- **Per-step receipts**. Every step is hash-chained (MeshPay's JS-faithful
  canonical serializer); the session receipt log has a Merkle root and can be
  signed into an evidence bundle.

The page is abstracted behind a small adapter (:class:`PlaywrightPage` in
production, a scripted fake in tests), so the safety semantics are unit
tested without a browser.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol
from urllib.parse import urlparse

from backend.core.security import is_safe_url
from backend.modules.meshpay import js_dumps, merkle_root

log = logging.getLogger("jambu.browser_agent")

MAX_SESSIONS = 4
SESSION_TTL_SECONDS = 900
MAX_STEPS = 200
MAX_ELEMENTS = 200
MAX_TEXT_CHARS = 4000

# Words that mark an action as irreversible/high-stakes regardless of session
# settings. Matched case-insensitively against element name/role/href.
RISKY_PATTERNS = (
    "buy", "purchase", "checkout", "pay ", "payment", "delete", "remove",
    "send", "transfer", "withdraw", "subscribe", "unsubscribe", "confirm",
    "order", "book", "publish", "post ", "submit", "sign up", "sign-up",
    "donate", "upgrade", "cancel plan", "close account",
)

SNAPSHOT_JS = """
() => {
  const out = {url: location.href, title: document.title, elements: [], text: ""};
  const sel = 'a, button, input, select, textarea, [role="button"], [role="link"], [role="checkbox"], [role="tab"]';
  let i = 0;
  for (const el of document.querySelectorAll(sel)) {
    const ref = `@e${++i}`;
    el.setAttribute('data-jambu-ref', ref);
    const name = (el.innerText || el.value || el.getAttribute('aria-label')
                  || el.getAttribute('placeholder') || '').trim().slice(0, 120);
    out.elements.push({
      ref, tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role') || '', type: el.getAttribute('type') || '',
      name, href: el.href || '',
    });
    if (out.elements.length >= %d) break;
  }
  out.text = (document.body ? document.body.innerText : '').slice(0, %d);
  return out;
}
""" % (MAX_ELEMENTS, MAX_TEXT_CHARS)


# ---------------------------------------------------------------------------
# Page adapters
# ---------------------------------------------------------------------------

class PageAdapter(Protocol):
    async def goto(self, url: str) -> None: ...
    async def snapshot(self) -> dict: ...
    async def click(self, ref: str) -> None: ...
    async def type_text(self, ref: str, text: str) -> None: ...
    async def current_url(self) -> str: ...


class PlaywrightPage:
    """Adapter over a Playwright page (created by ``BrowserSession``)."""

    def __init__(self, page):
        self._page = page

    async def goto(self, url: str) -> None:
        await self._page.goto(url, wait_until="domcontentloaded", timeout=20000)

    async def snapshot(self) -> dict:
        return await self._page.evaluate(SNAPSHOT_JS)

    async def click(self, ref: str) -> None:
        await self._page.click(f'[data-jambu-ref="{ref}"]', timeout=10000)

    async def type_text(self, ref: str, text: str) -> None:
        await self._page.fill(f'[data-jambu-ref="{ref}"]', text, timeout=10000)

    async def current_url(self) -> str:
        return self._page.url


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

@dataclass
class Step:
    seq: int
    ts: float
    action: str
    outcome: str            # ok | blocked | reverted
    url: str
    ref: Optional[str] = None
    detail: str = ""
    prev_hash: Optional[str] = None
    step_hash: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "seq": self.seq, "ts": self.ts, "action": self.action,
            "outcome": self.outcome, "url": self.url, "ref": self.ref,
            "detail": self.detail, "prev_hash": self.prev_hash,
            "step_hash": self.step_hash,
        }


class SessionRefused(Exception):
    """A safety rail refused the action (allowlist, approval, SSRF)."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(detail or reason)


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def host_allowed(host: str, allow_domains: list[str]) -> bool:
    """Exact host or subdomain match against the session allowlist."""
    host = (host or "").lower()
    if not host:
        return False
    for domain in allow_domains:
        domain = domain.lower().lstrip(".")
        if host == domain or host.endswith("." + domain):
            return True
    return False


def classify_risk(*parts: str) -> Optional[str]:
    """Return the matched risky phrase, or None for ordinary elements."""
    blob = " ".join(p or "" for p in parts).lower()
    for pattern in RISKY_PATTERNS:
        if pattern in blob:
            return pattern.strip()
    return None


class BrowserAgentSession:
    """One agent-facing session: catalog snapshots, gated actions, receipts."""

    def __init__(
        self,
        session_id: str,
        page: PageAdapter,
        *,
        allow_domains: list[str],
        require_approval: bool = True,
        scrub_pii: bool = True,
        created_at: Optional[float] = None,
    ):
        if not allow_domains:
            raise ValueError("allow_domains must be non-empty (fail closed)")
        self.id = session_id
        self.page = page
        self.allow_domains = [d.strip() for d in allow_domains if d.strip()]
        self.require_approval = require_approval
        self.scrub_pii = scrub_pii
        self.created_at = created_at or time.time()
        self.steps: list[Step] = []
        self.catalog: dict[str, dict] = {}
        self.last_url = ""
        self.closed = False

    # -- helpers -------------------------------------------------------------

    def _scrub(self, text: str) -> str:
        if not self.scrub_pii or not text:
            return text
        from backend.core.privacy import PIIDetector

        masked = text
        for pii_type in PIIDetector.PATTERNS:
            masked = PIIDetector.mask_pii(masked, pii_type)
        return masked

    def _record(self, action: str, outcome: str, *, url: str = "",
                ref: Optional[str] = None, detail: str = "") -> Step:
        prev = self.steps[-1].step_hash if self.steps else None
        step = Step(
            seq=len(self.steps) + 1, ts=time.time(), action=action,
            outcome=outcome, url=url or self.last_url, ref=ref, detail=detail,
            prev_hash=prev,
        )
        payload = {
            "seq": step.seq, "ts": step.ts, "action": step.action,
            "outcome": step.outcome, "url": step.url, "ref": step.ref,
            "detail": step.detail, "prev_hash": step.prev_hash,
        }
        step.step_hash = hashlib.sha256(js_dumps(payload).encode("utf-8")).hexdigest()
        self.steps.append(step)
        if len(self.steps) > MAX_STEPS:
            self.steps = self.steps[-MAX_STEPS:]
        return step

    def _check_navigation(self, url: str) -> None:
        if not is_safe_url(url):
            raise SessionRefused("unsafe_url", f"URL failed safety checks: {url}")
        if not host_allowed(_host(url), self.allow_domains):
            raise SessionRefused(
                "blocked_domain",
                f"{_host(url) or url} is outside the allowlist: {self.allow_domains}",
            )

    def _guard_click_target(self, ref: str) -> dict:
        element = self.catalog.get(ref)
        if element is None:
            raise SessionRefused(
                "unknown_ref", f"{ref} is not in the current snapshot — snapshot again",
            )
        href = element.get("href") or ""
        if href and href.startswith("http"):
            host = _host(href)
            if host and not host_allowed(host, self.allow_domains):
                raise SessionRefused(
                    "blocked_domain",
                    f"link target {host} is outside the allowlist: {self.allow_domains}",
                )
        return element

    # -- API -----------------------------------------------------------------

    def info(self) -> dict:
        return {
            "session_id": self.id,
            "allow_domains": self.allow_domains,
            "require_approval": self.require_approval,
            "scrub_pii": self.scrub_pii,
            "created_at": self.created_at,
            "age_seconds": round(time.time() - self.created_at, 1),
            "steps": len(self.steps),
            "url": self.last_url,
            "closed": self.closed,
        }

    async def navigate(self, url: str) -> dict:
        try:
            self._check_navigation(url)
        except SessionRefused as refusal:
            self._record("navigate", "blocked", url=url, detail=refusal.detail)
            raise
        await self.page.goto(url)
        self.last_url = await self.page.current_url()
        # Post-check: redirects can land outside the allowlist.
        if not host_allowed(_host(self.last_url), self.allow_domains):
            await self.page.goto("about:blank")
            self._record(
                "navigate", "reverted", url=self.last_url,
                detail=f"redirected outside allowlist: {_host(self.last_url)}",
            )
            raise SessionRefused(
                "blocked_domain",
                f"redirect landed on {_host(self.last_url)} (outside allowlist); reverted",
            )
        step = self._record("navigate", "ok", url=self.last_url)
        return {"url": self.last_url, "step": step.to_dict()}

    async def snapshot(self) -> dict:
        raw = await self.page.snapshot()
        elements = []
        for element in (raw.get("elements") or [])[:MAX_ELEMENTS]:
            elements.append({
                "ref": element.get("ref"),
                "tag": element.get("tag"),
                "role": element.get("role"),
                "type": element.get("type"),
                "name": self._scrub(element.get("name") or ""),
                "href": self._scrub(element.get("href") or ""),
                "risk": classify_risk(
                    element.get("name") or "", element.get("href") or "",
                ),
            })
        self.catalog = {e["ref"]: e for e in elements if e.get("ref")}
        text = self._scrub((raw.get("text") or "")[:MAX_TEXT_CHARS])
        self.last_url = raw.get("url") or self.last_url
        self._record("snapshot", "ok", url=self.last_url,
                     detail=f"{len(elements)} elements")
        return {
            "url": self.last_url,
            "title": self._scrub(raw.get("title") or ""),
            "text": text,
            "elements": elements,
            "count": len(elements),
        }

    async def act(self, action: str, ref: str, *, text: str = "",
                  approve: bool = False) -> dict:
        if action not in ("click", "type"):
            raise SessionRefused("unknown_action", f"unsupported action: {action}")

        element = self.catalog.get(ref)
        if element is None:
            raise SessionRefused(
                "unknown_ref", f"{ref} is not in the current snapshot — snapshot again",
            )
        risk = classify_risk(element.get("name") or "", element.get("href") or "")
        if risk and not approve:
            self._record(action, "blocked", ref=ref,
                         detail=f"approval required (risky element: {risk!r})")
            raise SessionRefused(
                "approval_required",
                f"element looks irreversible ({risk!r}); re-send with approve=true",
            )
        if self.require_approval and not approve:
            self._record(action, "blocked", ref=ref, detail="session requires approval")
            raise SessionRefused(
                "approval_required", "session requires approve=true for input actions",
            )

        if action == "click":
            self._guard_click_target(ref)  # may raise blocked_domain / unknown_ref

        before_url = self.last_url
        if action == "click":
            await self.page.click(ref)
        else:
            await self.page.type_text(ref, text)

        after_url = await self.page.current_url()
        if after_url != before_url:
            if not host_allowed(_host(after_url), self.allow_domains):
                await self.page.goto("about:blank")
                self.last_url = "about:blank"
                self._record(action, "reverted", ref=ref, url=after_url,
                             detail=f"navigation to {_host(after_url)} blocked; reverted")
                raise SessionRefused(
                    "blocked_domain",
                    f"action navigated to {_host(after_url)} (outside allowlist); reverted",
                )
            self.last_url = after_url

        step = self._record(action, "ok", ref=ref,
                            detail=(text[:40] if action == "type" and text else ""))
        return {"outcome": "ok", "url": self.last_url, "step": step.to_dict()}

    def receipts(self) -> dict:
        hashes = [s.step_hash for s in self.steps if s.step_hash]
        return {
            "session_id": self.id,
            "steps": [s.to_dict() for s in self.steps],
            "count": len(self.steps),
            "merkle_root": merkle_root(hashes),
            "chain_head": hashes[-1] if hashes else None,
            "allow_domains": self.allow_domains,
        }


# ---------------------------------------------------------------------------
# Service (session lifecycle)
# ---------------------------------------------------------------------------

class BrowserAgentService:
    """Manages agent sessions with caps, TTL, and real-browser creation."""

    def __init__(self, max_sessions: int = MAX_SESSIONS,
                 ttl_seconds: int = SESSION_TTL_SECONDS):
        self.max_sessions = max_sessions
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, BrowserAgentSession] = {}
        self._browser_sessions: dict[str, Any] = {}

    def _prune(self) -> None:
        now = time.time()
        for sid in list(self._sessions):
            session = self._sessions[sid]
            if session.closed or now - session.created_at > self.ttl_seconds:
                self._sessions.pop(sid, None)
                self._browser_sessions.pop(sid, None)

    async def open(
        self, *, allow_domains: list[str], require_approval: bool = True,
        scrub_pii: bool = True, privacy_level: Optional[str] = None,
    ) -> BrowserAgentSession:
        self._prune()
        if len(self._sessions) >= self.max_sessions:
            raise SessionRefused(
                "session_limit",
                f"max {self.max_sessions} concurrent sessions — close one first",
            )
        session_id = f"bs-{uuid.uuid4().hex[:10]}"

        from backend.modules.browser import (
            BrowserManager, PrivacyLevel, SessionMode,
        )

        privacy = PrivacyLevel[privacy_level.upper()] if privacy_level else PrivacyLevel.ENHANCED
        browser_session = await BrowserManager.get_instance().get_session(
            session_id, mode=SessionMode.EPHEMERAL, privacy_level=privacy,
        )
        page = await browser_session.get_page()
        agent = BrowserAgentSession(
            session_id, PlaywrightPage(page), allow_domains=allow_domains,
            require_approval=require_approval, scrub_pii=scrub_pii,
        )
        self._sessions[session_id] = agent
        self._browser_sessions[session_id] = browser_session
        return agent

    def get(self, session_id: str) -> BrowserAgentSession:
        self._prune()
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionRefused("not_found", f"no such session: {session_id}")
        return session

    def list(self) -> list[dict]:
        self._prune()
        return [s.info() for s in self._sessions.values()]

    async def close(self, session_id: str) -> dict:
        session = self._sessions.pop(session_id, None)
        if session is None:
            raise SessionRefused("not_found", f"no such session: {session_id}")
        session.closed = True
        browser_session = self._browser_sessions.pop(session_id, None)
        if browser_session is not None:
            try:
                await browser_session.close()
            except Exception:
                log.warning("failed to close browser session %s", session_id, exc_info=True)
        return {"session_id": session_id, "closed": True, "steps": len(session.steps)}


_service: Optional[BrowserAgentService] = None


def get_browser_agent_service() -> BrowserAgentService:
    global _service
    if _service is None:
        _service = BrowserAgentService()
    return _service


def reset_browser_agent_service() -> None:
    """Test hook: drop the singleton (sessions are in-memory)."""
    global _service
    _service = None
