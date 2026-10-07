"""The browser-agent session registry.

`BrowserAgentService` owns the lifecycle of sessions — open, list, close, TTL
eviction, the max-concurrent cap — and nothing about what happens inside one.
It is the only surface the HTTP routes, the MCP tools and the flow/QA monitors
use, so keeping it apart from the session means the callers have one small
thing to read.

The session class itself is imported from `browser_agent`; that module reaches
this one lazily inside its accessor functions, so the dependency runs one way.
"""
from __future__ import annotations

import asyncio
import glob
import logging
import os
import tempfile
import time
import uuid
from typing import Any, Optional

from backend.core.privacy import default_scrub_pii
from backend.modules.browser_agent import (
    MAX_SESSIONS,
    SESSION_TTL_SECONDS,
    BrowserAgentSession,
)
from backend.modules.browser_agent_errors import SessionRefused
from backend.modules.browser_context_options import (
    DEFAULT_VIEWPORT,
    normalize_context_options,
)
from backend.modules.browser_flow import host_of, normalize_flow_steps
from backend.modules.browser_page import NetworkPolicy, PlaywrightPage

log = logging.getLogger("jambu.browser_agent_service")

# ---------------------------------------------------------------------------

class BrowserAgentService:
    """Manages agent sessions with caps, TTL, and real-browser creation."""

    def __init__(self, max_sessions: int = MAX_SESSIONS,
                 ttl_seconds: int = SESSION_TTL_SECONDS):
        self.max_sessions = max_sessions
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, BrowserAgentSession] = {}
        self._browser_sessions: dict[str, Any] = {}
        self._artifacts: dict[str, dict] = {}

    def _prune(self) -> None:
        now = time.time()
        for sid in list(self._sessions):
            session = self._sessions[sid]
            if session.closed or now - session.created_at > self.ttl_seconds:
                self._sessions.pop(sid, None)
                self._browser_sessions.pop(sid, None)
                self._artifacts.pop(sid, None)

    async def open(
        self, *, allow_domains: list[str], require_approval: bool = True,
        scrub_pii: Optional[bool] = None, privacy_level: Optional[str] = None,
        allow_private: bool = False, storage_state: Any = None,
        context_options: Optional[dict] = None, trace: bool = False,
        har: bool = False, video: bool = False,
        artifacts_dir: Optional[str] = None,
    ) -> BrowserAgentSession:
        self._prune()
        if len(self._sessions) >= self.max_sessions:
            raise SessionRefused(
                "session_limit",
                f"max {self.max_sessions} concurrent sessions — close one first",
            )
        session_id = f"bs-{uuid.uuid4().hex[:10]}"

        from backend.modules.browser import (
            BrowserManager,
            PrivacyLevel,
            SessionMode,
        )

        # Scrubbing stays opt-in for local targets: an explicit scrub_pii from
        # the caller wins, otherwise the policy decides from the allowlist.
        effective_scrub = (
            default_scrub_pii(allow_private=allow_private, allow_domains=allow_domains)
            if scrub_pii is None else bool(scrub_pii)
        )

        opts = dict(context_options or {})
        # Geometry must always be explicit: without this the viewport comes from
        # the rotated fingerprint and changes run to run, which is what made
        # layout assertions irreproducible. Callers that want a device pass
        # their own viewport and win.
        opts.setdefault("viewport", dict(DEFAULT_VIEWPORT))
        if storage_state:
            opts["storage_state"] = storage_state
        # Prevent service workers from creating an out-of-band network path.
        opts.setdefault("service_workers", "block")
        # Downloads are a first-class flow step ("export the CSV, then assert on
        # it"), so the context has to be allowed to accept them in the first place.
        opts.setdefault("accept_downloads", True)
        if (trace or har or video) and not artifacts_dir:
            artifacts_dir = tempfile.mkdtemp(prefix="jambu-artifacts-")
        if artifacts_dir:
            os.makedirs(artifacts_dir, exist_ok=True)
            if har:
                opts["record_har_path"] = os.path.join(artifacts_dir, "network.har")
            if video:
                opts["record_video_dir"] = artifacts_dir

        privacy = PrivacyLevel[privacy_level.upper()] if privacy_level else PrivacyLevel.ENHANCED
        browser_session = await BrowserManager.get_instance().get_session(
            session_id, mode=SessionMode.EPHEMERAL, privacy_level=privacy,
            context_options=opts or None,
        )
        page = await browser_session.get_page()
        if trace:
            starter = getattr(browser_session, "start_trace", None)
            if starter is not None:
                try:
                    await starter()
                except Exception:
                    log.warning("failed to start trace for %s", session_id, exc_info=True)
        policy = NetworkPolicy(allow_domains, allow_private=allow_private)
        adapter = PlaywrightPage(page, network_policy=policy,
                                artifacts_dir=artifacts_dir)
        try:
            network_info = await adapter.setup_network({})
            if callable(getattr(page, "route", None)):
                if not network_info.get("supported"):
                    raise SessionRefused(
                        "network_policy_unavailable",
                        "Playwright request routing could not be installed; session refused",
                    )
                if not network_info.get("websocket_supported"):
                    raise SessionRefused(
                        "network_policy_unavailable",
                        "Playwright WebSocket routing is unavailable; session refused",
                    )
        except Exception:
            try:
                await browser_session.stop()
            except Exception:
                log.warning("failed to close browser after policy setup failure",
                            exc_info=True)
            raise
        agent = BrowserAgentSession(
            session_id, adapter, allow_domains=allow_domains,
            require_approval=require_approval, scrub_pii=effective_scrub,
            allow_private=allow_private, artifacts_dir=artifacts_dir,
        )
        self._sessions[session_id] = agent
        self._browser_sessions[session_id] = browser_session
        self._artifacts[session_id] = {
            "dir": artifacts_dir, "trace": trace, "har": har, "video": video,
        }
        return agent

    def get(self, session_id: str) -> BrowserAgentSession:
        self._prune()
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionRefused("not_found", f"no such session: {session_id}")
        return session

    async def run_test(
        self, *, url: str, steps=None, allow_domains: Optional[list[str]] = None,
        local: bool = False, approve: bool = False, stop_on_failure: bool = False,
        privacy_level: Optional[str] = None, scrub_pii: Optional[bool] = None,
        network: Optional[dict] = None, resolve_sources: bool = False,
        freeze_animations: bool = True, storage_state: Any = None,
        context_options: Optional[dict] = None, trace: bool = False,
        har: bool = False, video: bool = False,
        artifacts_dir: Optional[str] = None, settle_ms: int = 0,
        detect_dev_server: bool = False, forbid_evaluate: bool = False,
        clock: Optional[dict] = None, throttle: Optional[dict] = None,
        coverage: bool = False, network_idle: bool = False,
        wait_network_idle_ms: int = 15000,
    ) -> dict:
        """One-shot: open an ephemeral session, run a flow, close, return report.

        The single-call entry point for agents testing a local app. The
        allowlist defaults to the target URL's host; ``local=True`` additionally
        permits loopback/private hosts (the dev server case). ``trace``/``har``/
        ``video`` capture debugging artifacts whose paths are returned.

        ``scrub_pii`` unset means "policy": off for a local target, so the
        numbers a solver reports survive to the assertion. ``context_options``
        carries the viewport/device the caller wants; the routes fill in the
        fixed 1440x900 default when they are not supplied.
        """
        host = host_of(url).lower()
        if not host:
            raise SessionRefused("invalid_url", f"could not parse a host from {url!r}")
        domains = [d.strip().lower() for d in (allow_domains or []) if d and d.strip()]
        if local or not domains:
            if host not in domains:
                domains.append(host)
        effective_scrub = (
            default_scrub_pii(host=host, allow_private=local, allow_domains=domains)
            if scrub_pii is None else bool(scrub_pii)
        )
        session = await self.open(
            allow_domains=domains, require_approval=False, scrub_pii=effective_scrub,
            privacy_level=privacy_level, allow_private=local,
            storage_state=storage_state, context_options=context_options,
            trace=trace, har=har, video=video, artifacts_dir=artifacts_dir,
        )
        closed: dict = {}
        try:
            flow = normalize_flow_steps(steps) if steps else []
            if not any((s.get("action") or "").lower() == "navigate" for s in flow):
                flow = [{"action": "navigate", "url": url}] + flow
            report = await session.run_flow(
                flow, approve=approve, stop_on_failure=stop_on_failure,
                network=network, resolve_sources=resolve_sources,
                freeze_animations=freeze_animations, settle_ms=settle_ms,
                forbid_evaluate=forbid_evaluate,
                clock=clock, throttle=throttle, coverage=coverage,
                network_idle=network_idle, wait_network_idle_ms=wait_network_idle_ms,
            )
            receipts = session.receipts()
        finally:
            closed = await self.close(session.id)
        report["scrub_pii"] = effective_scrub
        report["session_id"] = session.id
        report["allow_domains"] = domains
        report["receipts"] = {
            "count": receipts["count"], "merkle_root": receipts["merkle_root"],
        }
        if closed.get("artifacts"):
            report["artifacts"] = closed["artifacts"]
        if detect_dev_server:
            from backend.modules.dev_server import is_loopback, probe_url

            if is_loopback(url):
                try:
                    report["dev_server"] = await probe_url(url)
                except Exception:
                    report["dev_server"] = {"url": url, "reachable": False}
        return report

    def list(self) -> list[dict]:
        self._prune()
        return [s.info() for s in self._sessions.values()]

    async def run_matrix(
        self, *, url: str, steps=None, matrix: Optional[list[dict]] = None,
        local: bool = False, approve: bool = False, network: Optional[dict] = None,
        stop_on_failure: bool = False, resolve_sources: bool = False,
        trace: bool = False, har: bool = False, video: bool = False,
    ) -> dict:
        """Run the same flow across viewports/locales concurrently.

        Each matrix entry may set ``name``, a ``device`` preset, plus any
        Playwright context option (``viewport``, ``locale``, ``user_agent``,
        ``device_scale_factor``, ``timezone_id``, ``color_scheme``,
        ``reduced_motion``, ``is_mobile``, ``has_touch``). Concurrency is
        capped at the session limit.

        Each variant returns a compact digest *and* the full ``report``, plus
        ``evaluated`` (the values every ``evaluate`` step produced) and
        ``screenshots``. The digest alone was not enough to write a responsive
        assertion -- it carried pass/fail and console text but neither the
        numbers a step read nor the image a human needed, so anyone checking
        "does this break at 390px" had to re-run the variant by hand.
        """
        variants = matrix or [
            {"name": "desktop", "viewport": {"width": 1280, "height": 800}},
            {"name": "mobile", "viewport": {"width": 390, "height": 844}},
        ]
        semaphore = asyncio.Semaphore(max(1, self.max_sessions))
        option_keys = ("viewport", "locale", "user_agent", "device_scale_factor",
                       "timezone_id", "color_scheme", "is_mobile", "has_touch",
                       "reduced_motion", "screen")

        async def one(index: int, variant: dict) -> dict:
            name = variant.get("name") or f"variant-{index}"
            base_options = {
                k: variant[k] for k in option_keys if variant.get(k) is not None
            }
            try:
                context_options = normalize_context_options(
                    base_options, device=variant.get("device"),
                    viewport_matrix=True,
                )
            except ValueError as exc:
                return {"variant": name, "ok": False, "error": str(exc)[:300],
                        "context_options": base_options}
            async with semaphore:
                try:
                    report = await self.run_test(
                        url=url, steps=steps, local=local, approve=approve,
                        stop_on_failure=stop_on_failure, network=network,
                        resolve_sources=resolve_sources, context_options=context_options,
                        trace=trace, har=har, video=video,
                    )
                except Exception as exc:
                    return {"variant": name, "ok": False, "error": str(exc)[:300],
                            "context_options": context_options}
            failed_steps = [
                {"i": s.get("i"), "action": s.get("action"),
                 "reason": s.get("reason"), "error": s.get("error"),
                 "cause": s.get("cause")}
                for s in report.get("steps") or [] if s.get("status") == "failed"
            ]
            return {
                "variant": name,
                "context_options": context_options,
                "ok": report.get("ok"),
                "passed": report.get("passed"),
                "failed": report.get("failed"),
                "total": report.get("total"),
                "failed_steps": failed_steps[:5],
                "console_errors": (report.get("console_errors") or [])[:5],
                "evaluated": report.get("evaluated") or [],
                "screenshots": report.get("screenshots") or [],
                "artifacts": report.get("artifacts") or {},
                "report": report,
            }

        results = await asyncio.gather(
            *(one(i, v) for i, v in enumerate(variants, 1)), return_exceptions=False,
        )
        ok = all(r.get("ok") for r in results)
        return {
            "ok": ok,
            "url": url,
            "variants": results,
            "summary": {
                "variants": len(results),
                "passed": sum(1 for r in results if r.get("ok")),
                "failed": sum(1 for r in results if not r.get("ok")),
            },
        }

    async def close(self, session_id: str) -> dict:
        session = self._sessions.pop(session_id, None)
        if session is None:
            raise SessionRefused("not_found", f"no such session: {session_id}")
        session.closed = True
        artifacts: dict = {}
        art = self._artifacts.pop(session_id, None)
        browser_session = self._browser_sessions.pop(session_id, None)
        if browser_session is not None:
            if art and art.get("trace"):
                stop_trace = getattr(browser_session, "stop_trace", None)
                path = os.path.join(art["dir"], "trace.zip")
                if stop_trace is not None:
                    try:
                        if await stop_trace(path):
                            artifacts["trace"] = path
                    except Exception:
                        log.warning("failed to write trace for %s", session_id, exc_info=True)
            try:
                # BrowserSession exposes stop(); tolerate a close() alias.
                teardown = getattr(browser_session, "stop", None) or getattr(
                    browser_session, "close", None,
                )
                if teardown is not None:
                    await teardown()
            except Exception:
                log.warning("failed to close browser session %s", session_id, exc_info=True)
            if art:
                if art.get("har"):
                    har_path = os.path.join(art["dir"], "network.har")
                    if os.path.exists(har_path):
                        artifacts["har"] = har_path
                if art.get("video"):
                    videos = sorted(glob.glob(os.path.join(art["dir"], "*.webm")))
                    if videos:
                        artifacts["video"] = videos[-1]
        else:
            # Artifacts requested but the browser session was faked/absent.
            if art:
                for name, fname in (("har", "network.har"),):
                    candidate = os.path.join(art["dir"], fname)
                    if os.path.exists(candidate):
                        artifacts[name] = candidate
        return {
            "session_id": session_id, "closed": True,
            "steps": len(session.steps), "artifacts": artifacts,
        }


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
