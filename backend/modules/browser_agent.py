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

import asyncio
import glob
import hashlib
import inspect
import json
import logging
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol
from urllib.parse import urljoin

from backend.core.security import is_safe_url
from backend.modules.browser_debug import (
    A11Y_JS,
    PERF_JS,
    PERF_OBSERVER_JS,
    compact_observation,
    compile_network,
    diff_elements,
    map_url_for,
    match_network,
    NetworkDecision,
    NetworkPolicy,
    parse_stack_frames,
    serialize_body,
    SourceMapData,
)
from backend.decentralized.meshpay import js_dumps, merkle_root

log = logging.getLogger("jambu.browser_agent")

MAX_SESSIONS = 4
SESSION_TTL_SECONDS = 900
MAX_STEPS = 200
MAX_TEXT_CHARS = 4000

# File uploads / downloads: bounded so a flow cannot exfiltrate or hoard disks.
MAX_UPLOAD_FILES = 10
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# An assertion reads at most this much text before it truncates.
MAX_ASSERT_TEXT = 2000
# Options ``snapshot(compact=…)`` forwards to the pure observation projector.
_OBSERVE_OPTIONS = frozenset({
    "query", "roles", "fields", "match", "changed_since",
    "include_hidden", "limit", "text", "max_tokens",
})
# How many times a flow may silently re-observe to rescue a stale target
# before it gives up and asks the agent for a fresh observation.
DEFAULT_REOBSERVE_BUDGET = 3

# Actions the single-primitive verb (``act``) accepts; everything else in the
# vocabulary is reachable through the flow runner / batch verb.
_ACT_ACTIONS = ("click", "type", "upload")

# Loopback / private hosts that a *local* test session is allowed to reach.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"}

# Injected before flows so transitions/animations don't cause flaky reads.
DISABLE_ANIM_CSS = (
    "*,*::before,*::after{transition:none!important;animation:none!important;"
    "animation-duration:0s!important;caret-color:transparent!important}"
)

# Words that mark an action as irreversible/high-stakes regardless of session
# settings. Matched case-insensitively against element name/role/href.
RISKY_PATTERNS = (
    "buy", "purchase", "checkout", "pay ", "payment", "delete", "remove",
    "send", "transfer", "withdraw", "subscribe", "unsubscribe", "confirm",
    "order", "book", "publish", "post ", "submit", "sign up", "sign-up",
    "donate", "upgrade", "cancel plan", "close account",
)




# ---------------------------------------------------------------------------
# Page adapters
# ---------------------------------------------------------------------------

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


# SessionRefused now lives in browser_agent_errors so extracted subsystems can
# raise it without importing this module (see that file's docstring).
from backend.modules.browser_agent_errors import SessionRefused  # noqa: E402
# Re-exported so existing importers keep working while the implementation
# lives with the assertion engine that owns it.
from backend.modules.browser_assertions import json_path as _json_path  # noqa: E402,F401
from backend.modules.browser_step_actions import (  # noqa: E402
    run_api_step,
    run_dialog_and_navigation,
    run_file_step,
    run_interaction,
)
# The page adapter (PlaywrightPage, Telemetry, the catalog JS and the coverage
# arithmetic) lives in browser_page: session logic and driver logic change for
# different reasons and are read from different tracebacks. These names are
# re-exported because this module is the entry point browser-agent code imports,
# and because MAX_ELEMENTS is used below.
from backend.modules.browser_page import (  # noqa: E402,F401
    DEFAULT_STEP_TIMEOUT_MS,
    MAX_ELEMENTS,
    PageAdapter,
    PlaywrightPage,
    summarise_coverage,
    Telemetry,
)
# The flow/report helpers live in browser_flow (they are pure functions over
# JSON, so they can be tested without a session). Imported here because
# run_flow/act_many are their only callers and they are part of this module's
# contract.
from backend.modules.browser_flow import (  # noqa: E402,F401
    FLOW_BLOCKED_REASONS,
    MAX_BATCH_ACTS,
    MAX_FLOW_STEPS,
    MAX_TARGET_CANDIDATES,
    MUTATING_ACTIONS,
    classify_flow_status,
    classify_step_failure,
    estimate_primitive_cost,
    estimate_tokens,
    host_of,
    normalize_acts,
    normalize_flow_steps,
    parse_dialog_spec,
    record_flow_savings,
    render_flow_report,
    summarize_flow_diagnostics,
    token_savings,
)
# Old private spellings, kept so any importer of them still resolves.
_host = host_of  # noqa: E305,F401
_FLOW_BLOCKED_REASONS = FLOW_BLOCKED_REASONS  # noqa: E305,F401


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

# ---------------------------------------------------------------------------
# Local file access: uploads in, downloads out
# ---------------------------------------------------------------------------

UPLOAD_ROOTS_ENV = "JAMBU_UPLOAD_ROOTS"
DOWNLOAD_DIR_ENV = "JAMBU_DOWNLOAD_DIR"


def _roots(raw: str) -> list[str]:
    parts = [p for p in (raw or "").split(os.pathsep) if p.strip()]
    return [os.path.realpath(os.path.expanduser(p)) for p in parts]


def upload_roots() -> list[str]:
    """Directories an agent may read files *from* (default: the working dir).

    An upload step reads from the machine running the engine, so without a root
    fence a flow could ship ``~/.ssh/id_rsa`` through a contact form.
    """
    return _roots(os.environ.get(UPLOAD_ROOTS_ENV, "")) or [os.path.realpath(os.getcwd())]


def resolve_upload_paths(files: Any, *, roots: Optional[list[str]] = None) -> list[str]:
    """Validate agent-supplied upload paths; raise :class:`SessionRefused`.

    Every path must resolve inside a configured root, exist, and be a regular
    file under :data:`MAX_UPLOAD_BYTES`. Count is capped by MAX_UPLOAD_FILES.
    """
    if isinstance(files, str):
        files = [p for p in files.split(os.pathsep) if p.strip()]
    if not isinstance(files, list) or not files:
        raise SessionRefused("invalid_step", "upload requires a non-empty 'files' list")
    if len(files) > MAX_UPLOAD_FILES:
        raise SessionRefused(
            "upload_limit", f"at most {MAX_UPLOAD_FILES} files per upload step",
        )
    allowed = roots if roots is not None else upload_roots()
    out: list[str] = []
    for item in files:
        if not isinstance(item, str) or not item.strip():
            raise SessionRefused("invalid_step", "upload file entries must be strings")
        candidate = os.path.realpath(os.path.expanduser(item.strip()))
        if not any(
            candidate == root or candidate.startswith(root + os.sep)
            for root in allowed
        ):
            raise SessionRefused(
                "upload_path_denied",
                f"{item!r} is outside the upload roots "
                f"({UPLOAD_ROOTS_ENV}={os.pathsep.join(allowed)})",
            )
        if not os.path.isfile(candidate):
            raise SessionRefused("upload_path_denied", f"not a readable file: {item!r}")
        size = os.path.getsize(candidate)
        if size > MAX_UPLOAD_BYTES:
            raise SessionRefused(
                "upload_limit", f"{item!r} is {size} bytes (cap {MAX_UPLOAD_BYTES})",
            )
        out.append(candidate)
    return out


def download_dir_for(session_id: str, artifacts_dir: Optional[str] = None) -> str:
    """Where saved downloads land: the session's artifact dir when it has one."""
    if artifacts_dir:
        return artifacts_dir
    configured = _roots(os.environ.get(DOWNLOAD_DIR_ENV, ""))
    base = configured[0] if configured else os.path.join(
        tempfile.gettempdir(), f"jambu-downloads-{session_id}",
    )
    os.makedirs(base, exist_ok=True)
    return base




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
        allow_private: bool = False,
        created_at: Optional[float] = None,
        artifacts_dir: Optional[str] = None,
    ):
        if not allow_domains:
            raise ValueError("allow_domains must be non-empty (fail closed)")
        self.id = session_id
        self.page = page
        self.network_policy = getattr(page, "network_policy", None)
        self.allow_domains = [d.strip() for d in allow_domains if d.strip()]
        self.require_approval = require_approval
        self.scrub_pii = scrub_pii
        # Local dev testing: permit loopback/private hosts, but only for hosts
        # that also appear in the explicit allowlist (both gates must agree).
        self.allow_private = allow_private
        self.created_at = created_at or time.time()
        self.steps: list[Step] = []
        self.catalog: dict[str, dict] = {}
        self.last_url = ""
        self.closed = False
        self._source_maps: dict[str, Optional[SourceMapData]] = {}
        # Human-in-the-loop hook: interactive sessions can pause for a human
        # (CAPTCHA/2FA) and resume. The desktop UI drives this; the backend
        # exposes the state and frame.
        self.human_takeover = False
        # Action recording: when on, performed actions are captured as a flow.
        self.recording = False
        self.recorded_steps: list[dict] = []
        self._settle_ms = 0
        self._forbid_evaluate = False
        # Last API-step response, read by assert_status/json/latency/schema.
        self._last_api: Optional[dict] = None
        # Where saved downloads land (artifact dir when the session has one).
        self.artifacts_dir = artifacts_dir

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

    def _require_agent_control(self, action: str) -> None:
        """Refuse mutating actions while a human has taken over the session."""
        if self.human_takeover:
            raise SessionRefused(
                "human_takeover",
                f"session is under human control; '{action}' refused",
            )

    def _check_navigation(self, url: str) -> None:
        if not is_safe_url(url, allow_private=self.allow_private):
            raise SessionRefused("unsafe_url", f"URL failed safety checks: {url}")
        if not host_allowed(host_of(url), self.allow_domains):
            raise SessionRefused(
                "blocked_domain",
                f"{host_of(url) or url} is outside the allowlist: {self.allow_domains}",
            )

    def _guard_click_target(self, ref: str) -> dict:
        element = self.catalog.get(ref)
        if element is None:
            raise SessionRefused(
                "unknown_ref", f"{ref} is not in the current snapshot — snapshot again",
            )
        href = element.get("href") or ""
        if href and href.startswith("http"):
            host = host_of(href)
            if host and not host_allowed(host, self.allow_domains):
                raise SessionRefused(
                    "blocked_domain",
                    f"link target {host} is outside the allowlist: {self.allow_domains}",
                )
        return element

    # -- API -----------------------------------------------------------------

    @property
    def download_dir(self) -> str:
        """Directory saved downloads land in (created on the first download)."""
        return download_dir_for(self.id, self.artifacts_dir)

    def info(self) -> dict:
        return {
            "session_id": self.id,
            "allow_domains": self.allow_domains,
            "require_approval": self.require_approval,
            "scrub_pii": self.scrub_pii,
            "allow_private": self.allow_private,
            "network_policy": (
                self.network_policy.report() if self.network_policy is not None
                else {"enforced": False, "reason": "adapter_without_request_policy"}
            ),
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
        if not host_allowed(host_of(self.last_url), self.allow_domains):
            await self.page.goto("about:blank")
            self._record(
                "navigate", "reverted", url=self.last_url,
                detail=f"redirected outside allowlist: {host_of(self.last_url)}",
            )
            raise SessionRefused(
                "blocked_domain",
                f"redirect landed on {host_of(self.last_url)} (outside allowlist); reverted",
            )
        step = self._record("navigate", "ok", url=self.last_url)
        self._capture_step({"action": "navigate", "url": url})
        return {"url": self.last_url, "step": step.to_dict()}

    async def _read_state(self) -> dict:
        """Fetch + scrub the current page state and refresh the catalog.

        Does *not* append a receipt — internal re-observations between flow
        steps should not pollute the audited step log.
        """
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
                "value": self._scrub(element.get("value") or ""),
                "visible": element.get("visible", True),
                "disabled": bool(element.get("disabled", False)),
                "checked": element.get("checked"),
                # Only present when the element is not in the top document:
                # an agent must know a ref lives in an iframe (or a shadow root)
                # before it decides a click "did nothing".
                **({"frame": element["frame"], "frame_url": element.get("frame_url", "")}
                   if element.get("frame") else {}),
                **({"shadow": True} if element.get("shadow") else {}),
                "risk": classify_risk(
                    element.get("name") or "", element.get("href") or "",
                ),
            })
        self.catalog = {e["ref"]: e for e in elements if e.get("ref")}
        text = self._scrub((raw.get("text") or "")[:MAX_TEXT_CHARS])
        self.last_url = raw.get("url") or self.last_url
        state = {
            "url": self.last_url,
            "title": self._scrub(raw.get("title") or ""),
            "text": text,
            "elements": elements,
            "count": len(elements),
        }
        if raw.get("frames"):
            state["frames"] = raw["frames"]
        return state

    async def snapshot(self, *, compact: bool = False, delta: bool = False,
                       observe: Optional[dict] = None) -> dict:
        """Perceive the page, optionally projected down to a token budget.

        The full state is what ``act`` resolves refs against and what the
        evidence bundle records, so it stays the default. ``compact``
        returns the same page through :func:`compact_observation` — column
        rows instead of dicts, a relevance filter and a hard token ceiling
        — for a caller that only needs to *read* the page rather than act
        on every row. ``delta`` additionally reports only what moved since
        the previous observation, which is the question a caller actually
        has right after a mutating step.
        """
        previous = list(self.catalog.values())
        state = await self._read_state()
        self._record("snapshot", "ok", url=self.last_url,
                     detail=f"{state['count']} elements")
        if not compact and not delta:
            return state
        options = dict(observe or {})
        unknown = sorted(set(options) - _OBSERVE_OPTIONS)
        if unknown:
            raise SessionRefused(
                "invalid_observation",
                f"unknown observe option(s): {', '.join(unknown)}; "
                f"expected any of {', '.join(sorted(_OBSERVE_OPTIONS))}",
            )
        if delta:
            # An absent previous catalog must stay None. Diffing against an
            # empty list would report the whole page as "added" instead of
            # listing its rows, which is the opposite of a delta.
            options.setdefault("changed_since", previous or None)
        return compact_observation(state, **options)

    async def act(self, action: str, ref: str, *, text: str = "",
                  approve: bool = False, selector: str = "",
                  files: Optional[list] = None, dialog: Any = "") -> dict:
        """One primitive action, with the rails.

        ``dialog`` answers the native dialog this action raises (``"accept"`` /
        ``"dismiss"`` / ``{"accept": true, "text": "…"}`` for ``prompt()``),
        which is what makes a ``confirm()``-guarded button testable in one call
        instead of an arm-then-click race.
        """
        if action not in _ACT_ACTIONS:
            raise SessionRefused("unknown_action", f"unsupported action: {action}")
        self._require_agent_control(action)
        if dialog:
            await self.arm_dialog(dialog)
        if action == "upload":
            return await self.upload_files(
                ref, files or [], approve=approve, selector=selector,
            )
        if selector and not ref:
            return await self.act_selector(action, selector, text=text, approve=approve)

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
        await self._settle_navigation(action, ref, before_url)

        step = self._record(action, "ok", ref=ref,
                            detail=(text[:40] if action == "type" and text else ""))
        if action == "click":
            self._capture_step({"action": "click",
                                "target": element.get("name") or ref, "ref": ref})
        else:
            eltype = (element.get("type") or "").lower()
            name = (element.get("name") or "").lower()
            if eltype == "password" or "password" in name:
                recorded_value = "{{password}}"
            elif eltype == "email" or "email" in name:
                recorded_value = "{{email}}"
            else:
                recorded_value = self._scrub(text)
            self._capture_step({
                "action": "type", "target": element.get("name") or ref,
                "value": recorded_value,
            })
        return {"outcome": "ok", "url": self.last_url, "step": step.to_dict()}

    async def _settle_navigation(self, action: str, ref: str, before_url: str) -> None:
        """Post-action URL check: revert disallowed landings, else adopt."""
        after_url = await self.page.current_url()
        if after_url == before_url:
            return
        if not host_allowed(host_of(after_url), self.allow_domains):
            await self.page.goto("about:blank")
            self.last_url = "about:blank"
            self._record(action, "reverted", ref=ref, url=after_url,
                         detail=f"navigation to {host_of(after_url)} blocked; reverted")
            raise SessionRefused(
                "blocked_domain",
                f"action navigated to {host_of(after_url)} (outside allowlist); reverted",
            )
        self.last_url = after_url

    async def act_selector(self, action: str, selector: str, *, text: str = "",
                           approve: bool = False) -> dict:
        """Dispatch click/type by CSS/XPath selector (bypasses the catalog).

        Selectors address elements the snapshot never catalogs, so the risk
        classifier cannot see them — they always require explicit approval.
        """
        if action not in ("click", "type"):
            raise SessionRefused("unknown_action", f"unsupported action: {action}")
        self._require_agent_control(action)
        if not (selector or "").strip():
            raise SessionRefused("invalid_step", "selector is empty")
        if not approve:
            self._record(action, "blocked", detail="selector actions require approve=true")
            raise SessionRefused(
                "approval_required",
                "selector actions address unclassified elements; re-send with approve=true",
            )
        if not self.last_url:
            try:
                self.last_url = await self.page.current_url()
            except Exception:
                # A page that cannot report its URL (closed/crashed) is
                # handled by the step itself; keep going.
                log.debug("current_url unavailable; using last known url",
                          exc_info=True)
        before_url = self.last_url
        if action == "click":
            await self._call_optional("click_selector", selector)
        else:
            await self._call_optional("fill_selector", selector, text)
        await self._settle_navigation(action, None, before_url)
        step = self._record(action, "ok",
                            detail=(f"{selector[:60]} ← {text[:40]}"
                                    if action == "type" and text else selector[:60]))
        self._capture_step(
            {"action": action, "selector": selector, **({"value": self._scrub(text)} if action == "type" else {})}
        )
        return {"outcome": "ok", "url": self.last_url, "step": step.to_dict()}

    # -- dialogs --------------------------------------------------------------

    async def arm_dialog(self, spec: Any) -> dict:
        """Decide how the *next* dialog raised by an action is answered.

        Nothing is armed by default, which means dialogs get dismissed (the
        Playwright default) and still land in telemetry — an agent that ignores
        ``confirm()`` learns it changed nothing instead of guessing.
        """
        accept, text = parse_dialog_spec(spec)
        await self._call_optional("arm_dialog", accept, text)
        self._record("dialog", "ok", detail=f"armed {'accept' if accept else 'dismiss'}")
        return {"armed": "accept" if accept else "dismiss",
                **({"text": text} if text else {})}

    # -- uploads --------------------------------------------------------------

    async def upload_files(self, ref: str, files: Any, *, approve: bool = False,
                           selector: str = "", chooser: Optional[bool] = None) -> dict:
        """Attach local files to a file input, or feed the picker a button opens.

        Uploads read from the engine's own disk and the risk classifier cannot
        see file contents, so they always need ``approve=true`` plus a path that
        resolves inside the configured upload roots.
        """
        self._require_agent_control("upload")
        if not approve:
            self._record("upload", "blocked", ref=ref or None,
                         detail="uploads require approve=true")
            raise SessionRefused(
                "approval_required",
                "upload reads local files; re-send with approve=true",
            )
        paths = resolve_upload_paths(files)
        element = self.catalog.get(ref) if ref else None
        if element is None and not selector:
            raise SessionRefused("target_required", "upload needs a 'ref' or 'selector'")
        wants_input = chooser is False or (
            chooser is None and element is not None
            and (element.get("tag") or "").lower() == "input"
            and (element.get("type") or "").lower() == "file"
        )
        if selector and not ref:
            info = await self._call_optional("set_input_files_selector", selector, paths)
        elif wants_input:
            info = await self._call_optional("set_input_files", ref, paths)
        else:
            info = await self._call_optional("upload_via_chooser", ref, paths)
        info = info or {}
        names = [os.path.basename(p) for p in paths]
        mode = str(info.get("mode") or ("input" if wants_input else "chooser"))
        step = self._record("upload", "ok", ref=ref or None,
                            detail=f"{len(paths)} file(s) via {mode}")
        self._capture_step({
            "action": "upload",
            "target": (element or {}).get("name") or ref or selector,
            "files": names,
            **({"chooser": True} if mode == "chooser" else {}),
        })
        return {"outcome": "ok", "url": self.last_url, "uploaded": names,
                "mode": mode, "step": step.to_dict()}

    # -- batched primitives ---------------------------------------------------

    async def act_many(self, actions: Any, *, approve: bool = False,
                       stop_on_error: bool = True) -> dict:
        """Run up to :data:`MAX_BATCH_ACTS` primitives in one call.

        The primitive loop's cost is round trips, not the actions: N acts means
        N tool calls and, because refs go stale after any mutation, up to N
        re-observations. This keeps every per-action receipt and refusal (each
        act is still recorded and hash-chained) but pays one tool call, one
        response, and zero re-snapshots while refs stay valid. When the page
        navigates mid-batch, the result says ``reobserve: true`` so the agent
        re-observes once instead of guessing.
        """
        items = normalize_acts(actions)
        before_tel = self._telemetry_counts()
        before_url = self.last_url
        results: list[dict] = []
        failed = 0
        stopped_at: Optional[int] = None
        for index, item in enumerate(items):
            action = str(item.get("action") or "").strip().lower()
            ref = str(item.get("ref") or "")
            entry: dict = {"i": index, "action": action or "?"}
            if ref:
                entry["ref"] = ref
            wants_approve = bool(item.get("approve", approve))
            try:
                if action in _ACT_ACTIONS:
                    extra = await self.act(
                        action, ref,
                        text=str(item.get("text", item.get("value", "")) or ""),
                        approve=wants_approve,
                        selector=str(item.get("selector") or ""),
                        files=item.get("files"),
                        dialog=item.get("dialog", ""),
                    )
                else:
                    # Everything the flow runner understands (press, select,
                    # check, hover, wait, assert_*, navigate, download, …) is
                    # fair game in a batch too — with observe=False so the batch
                    # pays for one observation, not one per act.
                    summary, extra = await self._run_step(
                        dict(item), approve=wants_approve, observe=False,
                    )
                    entry["detail"] = summary
                entry["ok"] = True
                if extra.get("uploaded"):
                    entry["uploaded"] = extra["uploaded"]
                if extra.get("download"):
                    entry["download"] = extra["download"]
                if self.last_url != before_url:
                    entry["url"] = self.last_url
            except SessionRefused as refusal:
                entry["ok"] = False
                entry["reason"] = refusal.reason
                entry["error"] = (refusal.detail or refusal.reason)[:200]
                if refusal.candidates:
                    entry["candidates"] = refusal.candidates[:MAX_TARGET_CANDIDATES]
                failed += 1
            except Exception as exc:  # harness/page error, not a safety refusal
                entry["ok"] = False
                entry["reason"] = "harness_error"
                entry["error"] = str(exc)[:200]
                failed += 1
            results.append(entry)
            if failed and stop_on_error:
                stopped_at = index
                break
        deltas = {
            key: value - before_tel.get(key, 0)
            for key, value in self._telemetry_counts().items()
        }
        deltas = {key: value for key, value in deltas.items() if value}
        self._record(
            "act_batch", "ok" if not failed else "failed",
            detail=f"{len(results) - failed}/{len(items)} acts ok",
        )
        out: dict = {
            "ok": failed == 0,
            "count": len(results),
            "passed": len(results) - failed,
            "url": self.last_url,
            "results": results,
        }
        if stopped_at is not None:
            out["stopped_at"] = stopped_at
        if self.last_url != before_url:
            out["reobserve"] = True
        if deltas:
            out["telemetry"] = deltas
        return out

    def telemetry_report(self, *, drain: bool = False) -> dict:
        """Read standing console/network/dialog telemetry — no re-snapshot.

        Lets a primitive loop answer "did anything break?" for a few dozen
        tokens instead of re-snapshotting the page to find out.
        """
        data = self._drain_telemetry() if drain else self._peek_telemetry()
        data.setdefault("dialogs", [])
        out: dict = {
            "session_id": self.id, "url": self.last_url,
            "supported": bool(data), **data,
        }
        made = getattr(self.page, "requests", None)
        if isinstance(made, list):
            out["requests_made"] = len(made)
        if drain:
            self._record("telemetry", "ok", detail="drained")
        return out

    def resolve_target(self, target: str) -> str:
        """Resolve a ref or a human-readable target to a catalog ref.

        Accepts ``@e3`` refs, exact names, role-prefixed names
        ("button Sign in"), and unique substrings. Ambiguity raises with a
        bounded candidate list so the agent can retry in one fewer round trip.
        """
        if not target or not str(target).strip():
            raise SessionRefused("target_required", "step needs a 'ref' or 'target'")
        t = str(target).strip()
        if t in self.catalog:
            return t
        if t.startswith("@e"):
            raise SessionRefused(
                "unknown_ref", f"{t} is not in the current snapshot — snapshot again",
            )
        low = t.lower()

        def match(pred) -> list[str]:
            return [r for r, e in self.catalog.items() if pred(e)]

        exact = match(lambda e: (e.get("name") or "").strip().lower() == low)
        if len(exact) == 1:
            return exact[0]

        parts = low.split(None, 1)
        if len(parts) == 2 and parts[0] in {
            "button", "link", "input", "select", "textarea", "tab", "checkbox", "a",
        }:
            role, rest = parts
            role_hits = match(
                lambda e: (e.get("role") or e.get("tag") or "").lower() == role
                and rest in (e.get("name") or "").lower()
            )
            if len(role_hits) == 1:
                return role_hits[0]

        contains = match(lambda e: low in (e.get("name") or "").lower())
        if len(contains) == 1:
            return contains[0]

        pool = exact or contains
        candidates = [
            {
                "ref": r, "tag": self.catalog[r].get("tag"),
                "role": self.catalog[r].get("role"),
                "name": (self.catalog[r].get("name") or "")[:60],
            }
            for r in pool[:MAX_TARGET_CANDIDATES]
        ]
        if candidates:
            raise SessionRefused(
                "target_ambiguous",
                f"{target!r} matched {len(pool)} elements; use a ref",
                candidates=candidates,
            )
        raise SessionRefused(
            "target_not_found",
            f"no element matches {target!r}",
            candidates=[
                {"ref": r, "name": (e.get("name") or "")[:50]}
                for r, e in list(self.catalog.items())[:MAX_TARGET_CANDIDATES]
            ],
        )

    async def _target(self, step: dict) -> str:
        if not self.catalog:
            await self._read_state()
        target = step.get("ref") or step.get("target") or step.get("name") or ""
        return self.resolve_target(target)

    # -- optional adapter capabilities --------------------------------------

    async def _call_optional(self, name: str, *args, **kwargs):
        fn = getattr(self.page, name, None)
        if fn is None:
            raise SessionRefused(
                "unsupported_action", f"page adapter does not support {name!r}",
            )
        result = fn(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    def _drain_telemetry(self) -> dict:
        drain = getattr(self.page, "drain_telemetry", None)
        if callable(drain):
            try:
                return drain() or {}
            except Exception:
                return {}
        return {}

    def _peek_telemetry(self) -> dict:
        peek = getattr(self.page, "peek_telemetry", None)
        if callable(peek):
            try:
                return peek() or {}
            except Exception:
                return {}
        return {}

    async def _safe_state(self) -> dict:
        try:
            return await self._read_state()
        except Exception:
            return {"url": self.last_url, "title": "", "text": "", "elements": [], "count": 0}

    # -- declarative flow runner --------------------------------------------

    async def run_flow(
        self, steps, *, approve: bool = False, stop_on_failure: bool = False,
        observe: bool = True, network: Optional[dict] = None,
        freeze_animations: bool = True, resolve_sources: bool = False,
        settle_ms: int = 0, forbid_evaluate: bool = False,
        clock: Optional[dict] = None, throttle: Optional[dict] = None,
        coverage: bool = False,
    ) -> dict:
        """Execute a declarative list of steps and return one compact report.

        This is the token-efficiency centrepiece: the agent describes the whole
        test once; the runner performs navigation, intent-based clicks/types,
        waits, assertions and re-observation internally, and hands back a
        pass/fail digest plus auto-collected console/network telemetry — no
        snapshot/act round trips per step.

        ``network`` installs request interception (mocks/fail/delay/offline),
        ``resolve_sources`` maps console errors through source maps, and each
        step carries the telemetry + DOM delta it caused.

        Determinism knobs (all optional, all reported back so a run states
        the conditions it executed under):

        ``clock``     — ``{"time": ..., "rate": 0}`` freezes/advances the
                        page clock (Playwright ``page.clock``).
        ``throttle``  — CDP network shaping (``{"offline": true}`` or
                        kbps/latency) so 3G / offline paths are testable.
        ``coverage``  — capture JS coverage and summarise used bytes per
                        script in the report (Chromium).
        """
        normalized = normalize_flow_steps(steps)
        results: list[dict] = []
        passed = failed = 0
        started = time.time()
        self._settle_ms = max(0, int(settle_ms or 0))
        self._forbid_evaluate = bool(forbid_evaluate)

        network_info: dict = {
            "rules": 0,
            "offline": False,
            "policy": bool(self.network_policy),
        }
        if network or self.network_policy is not None:
            network_info = await self._call_optional("setup_network", network or {}) or network_info
        determinism: dict = {}
        if clock:
            try:
                determinism["clock"] = await self._call_optional("install_clock", clock)
            except SessionRefused as exc:
                determinism["clock"] = {"skipped": exc.detail or "unsupported"}
        if throttle:
            try:
                determinism["throttle"] = await self._call_optional("set_throttle", throttle)
            except SessionRefused as exc:
                determinism["throttle"] = {"skipped": exc.detail or "unsupported"}
        coverage_info: dict = {}
        if coverage:
            try:
                await self._call_optional("start_coverage")
            except SessionRefused as exc:
                coverage_info = {"supported": False, "reason": exc.detail or "unsupported"}
        if freeze_animations:
            try:
                await self._call_optional("inject_css", DISABLE_ANIM_CSS)
            except SessionRefused:
                pass
        try:
            await self._call_optional("init_perf_observers")
        except SessionRefused:
            pass

        for i, step in enumerate(normalized, 1):
            t0 = time.time()
            action = (step.get("action") or "?").strip().lower()
            result: dict = {"i": i, "action": action, "status": "passed"}
            before_elements = list(self.catalog.values())
            before_tel = self._telemetry_counts()
            try:
                step_approve = bool(step.get("approve", approve))
                detail, evidence = await self._run_step(
                    step, approve=step_approve, observe=observe,
                )
                if detail:
                    result["detail"] = detail
                if evidence:
                    result.update(evidence)
                passed += 1
            except SessionRefused as refusal:
                result["status"] = classify_step_failure(refusal.reason)
                result["reason"] = refusal.reason
                result["error"] = refusal.detail or refusal.reason
                if refusal.candidates:
                    result["candidates"] = refusal.candidates
                failed += 1
            except Exception as exc:  # unexpected page/tool error
                result["status"] = "inconclusive"
                result["reason"] = "harness_error"
                result["error"] = str(exc)[:300]
                failed += 1

            # Cause attribution: what did this step change / emit?
            cause = self._attribute(before_elements, before_tel)
            if cause:
                result["cause"] = cause
            result["ms"] = int((time.time() - t0) * 1000)
            results.append(result)
            if result["status"] != "passed" and stop_on_failure:
                break

        telemetry = self._drain_telemetry()
        state = await self._safe_state()
        report = {
            "ok": failed == 0,
            "status": classify_flow_status(results),
            "passed": passed,
            "failed": failed,
            "total": len(results),
            "steps": results,
            "console_errors": telemetry.get("console_errors", []),
            "console_warnings": telemetry.get("console_warnings", []),
            "failed_requests": telemetry.get("failed_requests", []),
            "bad_responses": telemetry.get("bad_responses", []),
            "dialogs": telemetry.get("dialogs", []),
            "final_url": state.get("url", self.last_url),
            "title": state.get("title", ""),
            "duration_ms": int((time.time() - started) * 1000),
        }
        if network:
            report["network"] = network_info
        if determinism:
            report["determinism"] = determinism
        if coverage:
            try:
                coverage_info = await self._call_optional("stop_coverage") or {}
            except SessionRefused as exc:
                coverage_info = {"supported": False, "reason": exc.detail or "unsupported"}
        if coverage_info:
            report["coverage"] = coverage_info
        report["network_policy"] = (
            self.network_policy.report() if self.network_policy is not None
            else {"enforced": False, "reason": "adapter_without_request_policy"}
        )
        report["diagnostics"] = summarize_flow_diagnostics(results, telemetry)
        if resolve_sources:
            report["console_errors_source"] = await self._resolve_sources(
                telemetry.get("console_errors_detail") or [],
            )
        report["uses_evaluate"] = any(
            (s.get("action") or "").lower() == "evaluate" for s in normalized
        )
        report["tokens_estimate"] = estimate_tokens(report)
        # The answer to "is this actually cheaper?": the same script driven as
        # MCP primitives costs one call per action plus a fresh page state
        # after every mutation that moves the DOM.
        primitive = estimate_primitive_cost(
            normalized,
            elements=len(self.catalog) or int(state.get("count") or 0),
            text_chars=len(str(state.get("text") or "")),
        )
        report["savings"] = {
            "steps": len(normalized),
            "flow_calls": 1,
            "primitive_calls": primitive["calls"],
            "calls_avoided": max(0, primitive["calls"] - 1),
            "flow_tokens": report["tokens_estimate"],
            "primitive_tokens": primitive["tokens"],
            "saved_tokens": max(0, primitive["tokens"] - report["tokens_estimate"]),
        }
        record_flow_savings(report["savings"])
        self._record(
            "run_flow", "ok" if report["ok"] else "failed",
            detail=f"{passed}/{len(results)} steps passed",
        )
        return report

    def _telemetry_counts(self) -> dict:
        t = self._peek_telemetry()
        return {
            "console_errors": len(t.get("console_errors") or []),
            "failed_requests": len(t.get("failed_requests") or []),
            "bad_responses": len(t.get("bad_responses") or []),
            "dialogs": len(t.get("dialogs") or []),
        }

    def _attribute(self, before_elements: list[dict], before_tel: dict) -> dict:
        """Summarise the telemetry + DOM changes a step caused."""
        cause: dict = {}
        tel = self._peek_telemetry()
        new_errors = (tel.get("console_errors") or [])[before_tel["console_errors"]:]
        new_failed = (tel.get("failed_requests") or [])[before_tel["failed_requests"]:]
        new_bad = (tel.get("bad_responses") or [])[before_tel["bad_responses"]:]
        new_dialogs = (tel.get("dialogs") or [])[before_tel.get("dialogs", 0):]
        if new_errors:
            cause["console_errors"] = [e[:160] for e in new_errors[:3]]
        if new_failed:
            cause["failed_requests"] = new_failed[:3]
        if new_bad:
            cause["bad_responses"] = new_bad[:3]
        if new_dialogs:
            cause["dialogs"] = [
                f"{d.get('type')}:{d.get('message', '')[:60]}"
                f"{'(accepted)' if d.get('accepted') else '(dismissed)'}"
                for d in new_dialogs[:3]
            ]
        dom = diff_elements(before_elements, list(self.catalog.values()))
        dom_small = {k: dom[k] for k in ("added", "removed", "changed") if dom.get(k)}
        if dom_small:
            dom_small["added_names"] = dom["added_names"]
            dom_small["removed_names"] = dom["removed_names"]
            cause["dom"] = dom_small
        return cause

    async def _resolve_sources(self, detail: list[dict]) -> list[dict]:
        """Map console-error locations through the page's source maps."""
        out: list[dict] = []
        for item in detail:
            location = item.get("location") or {}
            resolved = None
            url = location.get("url") or ""
            if url.startswith(("http://", "https://")):
                data = await self._get_source_map(url)
                if data is not None:
                    try:
                        resolved = data.lookup(
                            int(location.get("lineNumber", 0)) + 1,
                            int(location.get("columnNumber", 0)),
                        )
                    except Exception:
                        resolved = None
            entry = {"text": item.get("text", "")[:200], "location": location}
            if resolved:
                entry["source"] = resolved.get("source")
                entry["source_line"] = resolved.get("line")
            out.append(entry)
        return out

    async def _get_source_map(self, script_url: str) -> Optional[SourceMapData]:
        map_url = map_url_for(script_url)
        if map_url in self._source_maps:
            return self._source_maps[map_url]
        data: Optional[SourceMapData] = None
        try:
            raw = await self._call_optional("fetch_text", map_url)
            if raw:
                import json

                data = SourceMapData(json.loads(raw))
        except Exception:
            data = None
        self._source_maps[map_url] = data
        return data

    async def _run_step(self, step: dict, *, approve: bool, observe: bool):
        action = (step.get("action") or "").strip().lower()
        if not action:
            raise SessionRefused("invalid_step", "step is missing 'action'")
        if action in MUTATING_ACTIONS:
            self._require_agent_control(action)
        timeout = int(step.get("timeout", DEFAULT_STEP_TIMEOUT_MS))

        # A step can answer the dialog *it* raises. Arming has to happen before
        # the dispatch, and the answer is one-shot so it cannot leak downstream.
        if step.get("dialog") and action != "dialog":
            await self.arm_dialog(step["dialog"])

        result = await run_dialog_and_navigation(self, action, step, observe)
        if result is not None:
            return result
        result = await run_interaction(self, action, step, approve, observe)
        if result is not None:
            return result
        result = await run_api_step(self, action, step, approve)
        if result is not None:
            return result
        result = await run_file_step(self, action, step, approve, observe)
        if result is not None:
            return result

        if action == "evaluate":
            script = step.get("script") or step.get("value") or ""
            if not script.strip():
                raise SessionRefused("invalid_step", "evaluate requires 'script'")
            if self._forbid_evaluate:
                raise SessionRefused(
                    "evaluate_forbidden",
                    "this flow forbids JS-dependent steps (forbid_evaluate)",
                )
            if not approve:
                raise SessionRefused(
                    "approval_required",
                    "evaluate runs arbitrary JS; re-send with approve=true",
                )
            result = await self._call_optional("eval_js", script)
            if observe:
                await self._read_state()
            text = self._scrub(str(result if result is not None else ""))[:MAX_ASSERT_TEXT]
            self._capture_step({"action": "evaluate", "script": script[:200]})
            return "evaluated", {"evaluated": text}

        if action in ("wait", "wait_for"):
            await self._run_wait(step, timeout, approve)
            await self._read_state()
            return "waited", {}

        if action == "screenshot":
            b64 = await self._call_optional("screenshot", bool(step.get("full_page", False)))
            return "screenshot captured", {
                "screenshot_base64": b64,
                "screenshot_bytes": len(b64) * 3 // 4,
            }

        if action == "assert" or action.startswith("assert_"):
            state = await self._read_state()
            # The assertion vocabulary lives in browser_assertions; this class
            # keeps only the plumbing (read state, raise on failure).
            from backend.modules.browser_assertions import evaluate_assert

            passed, message = await evaluate_assert(self, step, state)
            if not passed:
                raise SessionRefused("assertion_failed", message)
            return message, {}

        raise SessionRefused("unknown_action", f"unsupported action: {action}")

    async def _run_wait(self, step: dict, timeout: int, approve: bool = False) -> None:
        if step.get("js"):
            # A JS predicate is the escape hatch for "wait until the app is
            # actually ready" (a flag, a promise, a component state) — but it
            # runs script, so it carries evaluate's approval gate.
            if not approve:
                raise SessionRefused(
                    "approval_required",
                    "wait with a 'js' predicate runs script on the page; "
                    "re-send with approve=true",
                )
            await self._call_optional("wait_for_function", str(step["js"]), timeout)
            return
        if step.get("selector"):
            await self._call_optional("wait_for_selector", step["selector"], timeout)
            return
        if step.get("text"):
            await self._call_optional("wait_for_text", step["text"], timeout)
            return
        if step.get("url_contains"):
            deadline = time.time() + timeout / 1000
            while time.time() < deadline:
                if step["url_contains"] in await self.page.current_url():
                    return
                await asyncio.sleep(0.1)
            raise SessionRefused(
                "wait_timeout", f"url never contained {step['url_contains']!r}",
            )
        await self._call_optional("wait_for", timeout_ms=timeout)

    async def capture_screenshot(self, full_page: bool = False) -> Optional[str]:
        """Current frame as base64 PNG (live-view / takeover source)."""
        try:
            return await self._call_optional("screenshot", full_page)
        except SessionRefused:
            return None

    # -- action recording ----------------------------------------------------

    def start_recording(self) -> dict:
        self.recording = True
        self.recorded_steps = []
        return {"recording": True, "steps": 0}

    def stop_recording(self) -> dict:
        self.recording = False
        return {"recording": False, "steps": self.recorded_steps,
                "count": len(self.recorded_steps)}

    def _capture_step(self, step: dict) -> None:
        if self.recording:
            self.recorded_steps.append(step)

    async def _wait_network_quiet(self, quiet_ms: int = 600,
                                  timeout_ms: int = 15000) -> None:
        """Wait until the resource count stops changing for ``quiet_ms``.

        This is the hot-reload settle: a dev server injecting HMR modules
        keeps adding resources; waiting for quiet avoids reading a
        half-rebuilt page.
        """
        deadline = time.time() + timeout_ms / 1000
        last = -1
        stable_since = time.time()
        while time.time() < deadline:
            try:
                count = await self._call_optional("resource_count")
            except SessionRefused:
                return
            if count != last:
                last = count
                stable_since = time.time()
            elif (time.time() - stable_since) * 1000 >= quiet_ms:
                return
            await asyncio.sleep(0.1)

    def receipts(self) -> dict:
        hashes = [s.step_hash for s in self.steps if s.step_hash]
        return {
            "session_id": self.id,
            "steps": [s.to_dict() for s in self.steps],
            "count": len(self.steps),
            "merkle_root": merkle_root(hashes),
            "chain_head": hashes[-1] if hashes else None,
            "allow_domains": self.allow_domains,
            "human_takeover": self.human_takeover,
        }


# ---------------------------------------------------------------------------
# Service (session lifecycle)


# ---------------------------------------------------------------------------
# Service (session lifecycle)
# ---------------------------------------------------------------------------

# The session registry lives in browser_agent_service, which needs
# BrowserAgentSession from here — so it cannot be imported at module level.
# PEP 562 module __getattr__ resolves it on first use instead, which keeps
# `from backend.modules.browser_agent import BrowserAgentService` (used by the
# routes, the flow monitor, qa_cases and the tests) working without a cycle.
_LAZY_FROM_SERVICE = frozenset({
    "BrowserAgentService",
    "get_browser_agent_service",
    "reset_browser_agent_service",
})


def __getattr__(name: str):
    """Resolve the session-registry names from browser_agent_service."""
    if name in _LAZY_FROM_SERVICE:
        from backend.modules import browser_agent_service
        return getattr(browser_agent_service, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
