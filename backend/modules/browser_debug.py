"""
Debugging helpers for agent test flows.

Pure, dependency-free utilities used by the browser flow runner:

- **Network policy** — compile ``mocks`` / ``fail`` / ``delay`` / ``offline``
  rules and match them against requests (Playwright routes the page through
  them; tests exercise the matcher directly).
- **DOM deltas** — summarise what changed between two element catalogs.
- **Token-efficient observation** — project a full page state down to the
  smallest view a model can still act on (column rows, relevance filter,
  delta-only, hard token ceiling).
- **Source maps** — a real Base64-VLQ decoder and mapping lookup, so console
  errors can be reported against original source instead of bundle offsets.
- **Page probes** — self-contained JavaScript for accessibility and
  performance auditing (no external dependency; runs in the page).
"""
from __future__ import annotations

import asyncio
import fnmatch
import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse


_ALLOWED_REQUEST_SCHEMES = {"http", "https", "ws", "wss"}
_BROWSER_INTERNAL_SCHEMES = {"about", "data", "blob"}


@dataclass(frozen=True)
class NetworkDecision:
    allowed: bool
    reason: str
    host: str = ""
    scheme: str = ""
    detail: str = ""


def host_matches_allowlist(host: str, allow_domains: list[str]) -> bool:
    """Match a host against exact domains/subdomains, never a wildcard."""
    host = (host or "").lower().rstrip(".")
    if not host:
        return False
    for domain in allow_domains or []:
        domain = str(domain).strip().lower().lstrip(".")
        if domain and domain != "*" and (host == domain or host.endswith("." + domain)):
            return True
    return False


def _ip_is_non_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return bool(
        not ip.is_global
    )


class NetworkPolicy:
    """Fail-closed policy applied to every browser-originated request.

    The domain check is independent of navigation checks: page resources,
    ``fetch``/XHR, redirects and WebSockets all pass through this object.
    Hostnames are resolved for public-host requests to prevent DNS rebinding
    into RFC1918/loopback/link-local space. A resolver can be injected for
    deterministic tests.
    """

    def __init__(self, allow_domains: list[str], *, allow_private: bool = False,
                 resolver=None, max_events: int = 100):
        self.allow_domains = [str(d).strip().lower() for d in allow_domains if str(d).strip()]
        self.allow_private = bool(allow_private)
        self._resolver = resolver or self._default_resolver
        self.max_events = max_events
        self.seen: list[dict] = []
        self.blocked: list[dict] = []
        self._websocket_supported: Optional[bool] = None
        self._routing_installed = False

    @staticmethod
    def _default_resolver(host: str) -> list[str]:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        return [str(info[4][0]) for info in infos]

    def _record(self, decision: NetworkDecision, method: str, kind: str) -> None:
        event = {
            "allowed": decision.allowed, "reason": decision.reason,
            "method": method, "kind": kind, "scheme": decision.scheme,
            "host": decision.host,
            "url": f"{decision.scheme}://{decision.host}" if decision.host else decision.scheme,
            "detail": decision.detail,
        }
        self.seen.append(event)
        if not decision.allowed:
            self.blocked.append(event)
        if len(self.seen) > self.max_events:
            del self.seen[: len(self.seen) - self.max_events]
        if len(self.blocked) > self.max_events:
            del self.blocked[: len(self.blocked) - self.max_events]

    def decide(self, url: str, *, method: str = "GET", kind: str = "http") -> NetworkDecision:
        """Return an allow/deny decision without performing network I/O itself."""
        raw = str(url or "")
        try:
            parsed = urlparse(raw)
            scheme = (parsed.scheme or "").lower()
            host = (parsed.hostname or "").lower().rstrip(".")
        except Exception:
            return NetworkDecision(False, "invalid_url", detail="URL could not be parsed")

        if not raw or len(raw) > 8192:
            decision = NetworkDecision(False, "invalid_url", scheme=scheme, host=host,
                                       detail="URL is empty or exceeds 8192 characters")
            self._record(decision, method, kind)
            return decision
        if scheme in _BROWSER_INTERNAL_SCHEMES and not host:
            decision = NetworkDecision(True, "browser_internal", scheme=scheme, host=host)
            self._record(decision, method, kind)
            return decision
        if scheme not in _ALLOWED_REQUEST_SCHEMES:
            decision = NetworkDecision(False, "blocked_protocol", scheme=scheme, host=host,
                                       detail=f"protocol {scheme or '(none)'} is not allowed")
            self._record(decision, method, kind)
            return decision
        if not host:
            decision = NetworkDecision(False, "invalid_url", scheme=scheme, host=host,
                                       detail="request URL has no hostname")
            self._record(decision, method, kind)
            return decision
        if not host_matches_allowlist(host, self.allow_domains):
            decision = NetworkDecision(False, "blocked_domain", scheme=scheme, host=host,
                                       detail="host is outside the session allowlist")
            self._record(decision, method, kind)
            return decision

        try:
            literal_ip = ipaddress.ip_address(host)
        except ValueError:
            literal_ip = None
        if literal_ip is not None:
            if literal_ip.is_unspecified:
                decision = NetworkDecision(False, "private_address", scheme=scheme, host=host,
                                           detail="unspecified address is never routable")
                self._record(decision, method, kind)
                return decision
            if _ip_is_non_public(host) and not self.allow_private:
                decision = NetworkDecision(False, "private_address", scheme=scheme, host=host,
                                           detail="private, loopback, or reserved address")
                self._record(decision, method, kind)
                return decision
        else:
            try:
                addresses = self._resolver(host)
            except Exception as exc:
                decision = NetworkDecision(False, "dns_resolution_failed", scheme=scheme,
                                           host=host, detail=f"DNS lookup failed: {exc}"[:160])
                self._record(decision, method, kind)
                return decision
            if not addresses or (any(_ip_is_non_public(str(address)) for address in addresses)
                                 and not self.allow_private):
                decision = NetworkDecision(False, "private_address", scheme=scheme, host=host,
                                           detail="hostname resolves to a non-public address")
                self._record(decision, method, kind)
                return decision

        decision = NetworkDecision(True, "allowed", scheme=scheme, host=host)
        self._record(decision, method, kind)
        return decision

    def report(self) -> dict:
        return {
            "enforced": self._routing_installed,
            "allow_domains": list(self.allow_domains),
            "allow_private": self.allow_private,
            "allowed_protocols": sorted(_ALLOWED_REQUEST_SCHEMES),
            "browser_internal_protocols": sorted(_BROWSER_INTERNAL_SCHEMES),
            "websocket_supported": self._websocket_supported,
            "requests_seen": list(self.seen),
            "blocked_requests": list(self.blocked),
        }


# ---------------------------------------------------------------------------
# Network mocks/fails/delays (flow-level test controls)
# ---------------------------------------------------------------------------

_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


class NetworkRule:
    def __init__(self, pattern: str, action: str, *, status: int = 200,
                 body: Any = None, content_type: str = "application/json",
                 ms: int = 0, method: str = ""):
        self.pattern = pattern
        self.action = action          # fulfill | abort | delay
        self.status = status
        self.body = body
        self.content_type = content_type
        self.ms = ms
        self.method = (method or "").upper()

    def matches(self, url: str, method: str) -> bool:
        if self.method and self.method != method.upper():
            return False
        if self.pattern in ("*", "**/*", "**"):
            return True
        if fnmatch.fnmatch(url, self.pattern):
            return True
        # Bare substring patterns ("/api/user") are the common authoring shape.
        return self.pattern in url

    def to_dict(self) -> dict:
        return {"pattern": self.pattern, "action": self.action,
                "status": self.status, "method": self.method}


def compile_network(network: Optional[dict]) -> list[NetworkRule]:
    """Turn a flow ``network`` block into ordered rules.

    Shape::

        {
          "offline": false,
          "mocks": [{"url": "**/api/user", "status": 200, "json": {...}}],
          "fail":  ["**/analytics/**"],
          "delay": [{"url": "**/api/slow", "ms": 3000}]
        }
    """
    if not network:
        return []
    rules: list[NetworkRule] = []
    for mock in network.get("mocks") or []:
        if isinstance(mock, str):
            mock = {"url": mock}
        body = mock.get("json", mock.get("body"))
        content_type = mock.get("content_type") or (
            "application/json" if "json" in mock else "text/plain"
        )
        rules.append(NetworkRule(
            mock.get("url") or mock.get("pattern") or "*",
            "fulfill",
            status=int(mock.get("status", 200)),
            body=body,
            content_type=content_type,
            method=mock.get("method", ""),
        ))
    for pattern in network.get("fail") or []:
        if isinstance(pattern, dict):
            rules.append(NetworkRule(
                pattern.get("url") or pattern.get("pattern") or "*",
                "abort", method=pattern.get("method", ""),
            ))
        else:
            rules.append(NetworkRule(pattern, "abort"))
    for delay in network.get("delay") or []:
        if isinstance(delay, str):
            delay = {"url": delay}
        rules.append(NetworkRule(
            delay.get("url") or delay.get("pattern") or "*",
            "delay", ms=int(delay.get("ms", 1000)), method=delay.get("method", ""),
        ))
    return rules


def match_network(rules: list[NetworkRule], url: str,
                  method: str = "GET") -> Optional[NetworkRule]:
    for rule in rules:  # first match wins (author order is precedence)
        if rule.matches(url, method):
            return rule
    return None


def serialize_body(body: Any, content_type: str):
    import json

    if body is None:
        return ""
    if isinstance(body, str):
        return body
    return json.dumps(body)


# ---------------------------------------------------------------------------
# DOM deltas
# ---------------------------------------------------------------------------

def _signature(element: dict) -> tuple:
    return (
        element.get("name") or "",
        element.get("value") or "",
        bool(element.get("checked")),
        bool(element.get("disabled")),
        element.get("visible", True),
    )


def diff_elements(before: list[dict], after: list[dict]) -> dict:
    """Return added / removed / changed counts and a bounded sample."""
    b = {e.get("ref"): e for e in before if e.get("ref")}
    a = {e.get("ref"): e for e in after if e.get("ref")}
    added = [r for r in a if r not in b]
    removed = [r for r in b if r not in a]
    changed = [r for r in a if r in b and _signature(a[r]) != _signature(b[r])]
    return {
        "added": len(added),
        "removed": len(removed),
        "changed": len(changed),
        "added_names": [(a[r].get("name") or "")[:40] for r in added[:5]],
        "removed_names": [(b[r].get("name") or "")[:40] for r in removed[:5]],
        "changed_names": [(a[r].get("name") or "")[:40] for r in changed[:5]],
    }


# ---------------------------------------------------------------------------
# Token-efficient observation
# ---------------------------------------------------------------------------
# A raw snapshot ships every interactive element with eight fields plus the
# body text, on every observation. That is the largest single line item in an
# agent's context and nothing in the pipeline budgets for it. The helpers below
# turn a full state payload into the smallest view that still lets a model act
# and assert. Pure functions, no I/O — the session applies them.

# Fields an agent may project an observation down to.
OBSERVE_FIELDS = (
    "ref", "role", "name", "value", "checked", "disabled", "visible", "href",
)
# The default projection: enough to click, type, and read a control's label.
DEFAULT_OBSERVE_FIELDS = ("ref", "role", "name")
MAX_OBSERVE_ROWS = 60
MAX_OBSERVE_MATCHES = 12
MAX_OBSERVE_TEXT = 600
_VALUE_FIELDS = ("value", "checked")


def element_identity(element: dict) -> str:
    """Content identity for an element, stable across observations.

    ``SNAPSHOT_JS`` hands out positional refs (``@e1``, ``@e2``, …) on every
    read, so a ref only means anything inside a single observation. Anything
    that compares two catalogs has to key on content instead — otherwise one
    inserted node renumbers every later ref and the diff reports the whole
    page as churn.
    """
    href = (element.get("href") or "").split("?", 1)[0].rstrip("/")
    return "|".join([
        (element.get("tag") or "").lower(),
        (element.get("role") or "").lower(),
        (element.get("name") or "").strip().lower(),
        href.lower(),
    ])


def _project_fields(fields) -> list[str]:
    if not fields:
        return list(DEFAULT_OBSERVE_FIELDS)
    keep = [f for f in fields if f in OBSERVE_FIELDS]
    if "ref" not in keep:
        keep.insert(0, "ref")  # the agent always needs a handle to act on
    return keep


def _element_row(element: dict, fields: list[str]) -> list:
    """One element as a positional row, dropping default-valued cells.

    ``{"ref": "@e1", "role": "button", "name": "Ok", "value": "",
    "disabled": false, "visible": true}`` is ~90 characters of mostly noise;
    the same control as three cells is ~25. Defaults are omitted so the agent
    only pays for what deviates from a normal enabled control.
    """
    row: list = []
    for field in fields:
        value = element.get(field)
        if field in _VALUE_FIELDS and value in ("", None, False):
            continue
        if field in ("disabled", "visible") and value is True:
            continue
        row.append(value)
    return row


def _term_hit(term: str, haystack: str, words: set[str]) -> bool:
    """Match one query term against an element.

    Short terms must land on a whole word, otherwise a query like
    "sign in email" matches every link containing "link" (L-**in**-k) and
    the agent gets the whole page back. Longer terms fall back to substring
    so "email" still finds ``EmailAddress``.
    """
    if term in words:
        return True
    return len(term) > 3 and term in haystack


def _matches(element: dict, query: str, roles: list[str], match: str = "all") -> bool:
    if roles:
        tag = (element.get("role") or element.get("tag") or "").lower()
        if tag not in roles:
            return False
    if not query:
        return True
    haystack = " ".join(
        str(element.get(field) or "")
        for field in ("name", "value", "href", "role", "type")
    ).lower()
    words = set(re.split(r"[^a-z0-9@._-]+", haystack))
    terms = query.lower().split()
    hits = [_term_hit(term, haystack, words) for term in terms]
    return any(hits) if match == "any" else all(hits)


def diff_view(before: list[dict], after: list[dict], *,
              fields=None, limit: int = MAX_OBSERVE_MATCHES) -> dict:
    """Identity-keyed added/removed/changed rows, sized for a model to read.

    ``diff_elements`` answers "how much churn?" for cause attribution. This
    answers the question an agent actually has after every mutating step —
    "what do I need to re-assert?" — with before/after values attached to
    each change so no extra observation is required to interpret it.
    """
    keep = _project_fields(fields)
    b: dict[str, dict] = {}
    a: dict[str, dict] = {}
    for element in before or []:
        b.setdefault(element_identity(element), element)
    for element in after or []:
        a.setdefault(element_identity(element), element)

    added = [_element_row(a[k], keep) for k in a if k not in b]
    removed = [
        (b[k].get("name") or b[k].get("tag") or "?")[:40] for k in b if k not in a
    ]
    changed = [
        {"before": _element_row(b[k], keep), "after": _element_row(a[k], keep)}
        for k in a if k in b and _signature(a[k]) != _signature(b[k])
    ]
    return {
        "columns": keep,
        "added": added[:limit],
        "changed": changed[:limit],
        "removed": removed[:limit],
        "counts": {
            "added": len(added),
            "changed": len(changed),
            "removed": len(removed),
        },
    }


def search_text(text: str, query: str, *, context: int = 1,
                limit: int = MAX_OBSERVE_MATCHES,
                max_chars: int = MAX_OBSERVE_TEXT) -> dict:
    """Matching page-text lines with a little surrounding context.

    Replaces "here are 4000 characters, tell me whether the order
    confirmation is on screen" with the two lines that answer it. The
    payload is capped at ``max_chars`` so a page with the same phrase
    repeated 400 times cannot blow the budget; the cap is reported in
    ``truncated`` rather than silently applied.
    """
    if not query or not text:
        return {}
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    terms = query.lower().split()
    hits = [
        i for i, line in enumerate(lines)
        if all(term in line.lower() for term in terms)
    ]
    if not hits:
        return {"hit": False, "matches": []}
    picked: list[str] = []
    seen: set[int] = set()
    deduped: set[str] = set()
    truncated = False
    spent = 0
    for i in hits[:limit]:
        for j in range(max(0, i - context), min(len(lines), i + context + 1)):
            if j in seen:
                continue
            seen.add(j)
            line = lines[j][:200]
            # The same sentence repeated down a page (nav footers, repeated
            # rows) costs a full line each time and adds no information.
            if line in deduped:
                continue
            if spent + len(line) > max_chars:
                truncated = True
                continue
            deduped.add(line)
            spent += len(line)
            picked.append(line)
    return {
        "hit": True, "matches": picked, "lines": len(lines),
        "truncated": truncated, "total_hits": len(hits),
    }


def compact_observation(
    state: dict, *, query: str = "", roles=None, fields=None, match: str = "all",
    changed_since: Optional[list[dict]] = None, include_hidden: bool = False,
    limit: Optional[int] = None, text: Optional[str] = None, max_tokens: int = 0,
) -> dict:
    """Project a full page state into a token-budgeted observation.

    Three levers, in order of payoff:

    - **columns/rows** instead of dicts, with default-valued cells dropped;
    - **relevance** — ``query`` (matched against the element's name/value/
      href/role/type; ``match="any"`` for a candidate set, ``"all"`` for a
      precise one) and ``roles`` narrow a 200-row catalog to the handful the
      agent is about to use;
    - **delta** — ``changed_since`` returns only what moved, reusing the same
      signature the cause attribution already computes.

    ``max_tokens`` is a hard ceiling, not a hint: rows halve, then optional
    text is dropped, until the estimate fits. Everything dropped is reported
    in ``truncated``/``omitted`` alongside a ``hint`` naming the call that
    would retrieve it, so the model can widen its next request deliberately
    instead of guessing and paying for a full snapshot.
    """
    elements = list(state.get("elements") or [])
    keep = _project_fields(fields)
    role_list = [str(r).strip().lower() for r in (roles or []) if str(r).strip()]

    hidden = 0
    candidates: list[dict] = []
    for element in elements:
        if not element.get("visible", True) and not include_hidden:
            hidden += 1
            continue
        if _matches(element, query, role_list, match):
            candidates.append(element)

    view: dict = {
        "url": state.get("url", ""),
        "title": (state.get("title") or "")[:120],
        "columns": keep,
        "total": len(elements),
        "matched": len(candidates),
        "hidden_omitted": hidden,
    }

    if changed_since is not None:
        view["delta"] = diff_view(
            changed_since, candidates, fields=keep, limit=limit or MAX_OBSERVE_MATCHES,
        )
        view["shown"] = len(view["delta"]["added"]) + len(view["delta"]["changed"])
    else:
        cap = max(1, int(limit or MAX_OBSERVE_ROWS))
        view["rows"] = [_element_row(e, keep) for e in candidates[:cap]]
        view["shown"] = len(view["rows"])

    if text:
        found = search_text(state.get("text") or "", text, max_chars=MAX_OBSERVE_TEXT)
        if found:
            view["text"] = found

    omitted: list[str] = []
    if max_tokens:
        def size() -> int:
            probe = {k: v for k, v in view.items()
                     if k not in ("tokens_estimate", "truncated", "omitted", "hint")}
            return max(1, len(json.dumps(probe, separators=(",", ":"), default=str)) // 4)

        while size() > max_tokens:
            if view.get("rows"):
                view["rows"] = view["rows"][:max(1, len(view["rows"]) // 2)]
                view["shown"] = len(view["rows"])
            elif view.get("delta", {}).get("changed"):
                view["delta"]["changed"] = view["delta"]["changed"][:1]
                view["shown"] = 1 + len(view["delta"]["added"])
            elif view.get("delta", {}).get("added"):
                view["delta"]["added"] = view["delta"]["added"][:1]
                view["shown"] = 1
            elif view.get("text"):
                view.pop("text", None)
                omitted.append("text")
            else:
                break
        if "text" in view and view["text"].get("truncated"):
            omitted.append("text truncated")
        if "delta" not in view and view.get("matched", 0) > view.get("shown", 0):
            omitted.append(f"{view['matched'] - view['shown']} further matches")

    view["truncated"] = bool(omitted)
    if omitted:
        view["omitted"] = omitted
        view["hint"] = (
            f"narrow with query=…, roles=…, fields={'/'.join(keep[:2])}, "
            f"or raise limit= (matched {view.get('matched', 0)})"
        )
    view["tokens_estimate"] = max(
        1, len(json.dumps(view, separators=(",", ":"), default=str)) // 4,
    )
    return view


# ---------------------------------------------------------------------------
# Source maps (Base64 VLQ)
# ---------------------------------------------------------------------------

_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_B64_INDEX = {c: i for i, c in enumerate(_B64)}


def decode_vlq(segment: str) -> list[int]:
    """Decode one Base64-VLQ segment into a list of signed integers."""
    values: list[int] = []
    shift = 0
    value = 0
    for ch in segment:
        digit = _B64_INDEX[ch]
        continuation = digit & 32
        digit &= 31
        value += digit << shift
        if continuation:
            shift += 5
        else:
            negative = value & 1
            value >>= 1
            values.append(-value if negative else value)
            value = 0
            shift = 0
    return values


class SourceMapData:
    """Minimal source-map consumer: generated (line, col) -> original."""

    def __init__(self, raw: dict):
        self.sources: list[str] = raw.get("sources") or []
        self.source_root: str = raw.get("sourceRoot") or ""
        self.mappings: str = raw.get("mappings") or ""
        self._lines = [self._decode_line(m) for m in self.mappings.split(";")]

    def _decode_line(self, line: str) -> list[tuple[int, int, int, int]]:
        out: list[tuple[int, int, int, int]] = []
        gen_col = 0
        src_idx = src_line = src_col = 0
        for segment in line.split(","):
            if not segment:
                continue
            vals = decode_vlq(segment)
            if not vals:
                continue
            gen_col += vals[0]
            if len(vals) >= 4:
                src_idx += vals[1]
                src_line += vals[2]
                src_col += vals[3]
                out.append((gen_col, src_idx, src_line, src_col))
        return out

    def lookup(self, line: int, column: int) -> Optional[dict]:
        """line/column are 1-based line, 0-based column (browser convention)."""
        idx = line - 1
        if idx < 0 or idx >= len(self._lines):
            return None
        best = None
        for gen_col, src_idx, src_line, src_col in self._lines[idx]:
            if gen_col <= column:
                best = (src_idx, src_line, src_col)
            else:
                break
        if best is None:
            return None
        src_idx, src_line, src_col = best
        source = self.sources[src_idx] if src_idx < len(self.sources) else None
        if source and self.source_root:
            source = f"{self.source_root.rstrip('/')}/{source.lstrip('/')}"
        return {"source": source, "line": src_line + 1, "column": src_col}


_STACK_RE = re.compile(r"([^\s()]+):(\d+):(\d+)")


def parse_stack_frames(text: str) -> list[dict]:
    """Extract ``url:line:col`` frames from a stack/console message."""
    frames = []
    for match in _STACK_RE.finditer(text or ""):
        url, line, col = match.group(1), int(match.group(2)), int(match.group(3))
        if url.startswith(("http://", "https://", "/", "webpack", "vite")):
            frames.append({"url": url, "line": line, "column": col})
    return frames


def map_url_for(script_url: str) -> str:
    """Where to fetch a script's source map from."""
    parsed = urlparse(script_url)
    if not parsed.scheme:
        return script_url + ".map"
    return script_url.split("?")[0] + ".map"


# ---------------------------------------------------------------------------
# Page probes (self-contained JS)
# ---------------------------------------------------------------------------

A11Y_JS = """
() => {
  const issues = [];
  const add = (id, impact, description, nodes) => {
    if (nodes.length) issues.push({id, impact, description, count: nodes.length, nodes: nodes.slice(0, 5)});
  };
  const name = (el) => (el.getAttribute('aria-label') || el.getAttribute('placeholder')
      || el.getAttribute('title') || (el.innerText || '').trim() || el.getAttribute('alt') || '').trim();

  add('image-alt', 'serious', 'Images must have alternate text',
      [...document.querySelectorAll('img')].filter(i => !i.hasAttribute('alt')).map(i => i.outerHTML.slice(0, 120)));
  add('label', 'serious', 'Form inputs must have a label or accessible name',
      [...document.querySelectorAll('input,select,textarea')]
        .filter(i => i.type !== 'hidden' && !name(i) && !i.labels?.length
          && !i.getAttribute('aria-labelledby')).map(i => i.outerHTML.slice(0, 120)));
  add('button-name', 'critical', 'Buttons must have discernible text',
      [...document.querySelectorAll('button,[role=button]')].filter(b => !name(b)).map(b => b.outerHTML.slice(0, 120)));
  add('link-name', 'serious', 'Links must have discernible text',
      [...document.querySelectorAll('a[href]')].filter(a => !name(a)).map(a => a.outerHTML.slice(0, 120)));
  if (!document.documentElement.getAttribute('lang')) {
    add('html-has-lang', 'serious', 'The <html> element must have a lang attribute', ['<html>']);
  }
  if (!document.title || !document.title.trim()) {
    add('document-title', 'serious', 'The document must have a title', ['<title>']);
  }
  const seen = {}; const dupes = [];
  document.querySelectorAll('[id]').forEach(el => {
    if (seen[el.id]) dupes.push(el.id); else seen[el.id] = true;
  });
  add('duplicate-id', 'minor', 'IDs must be unique', dupes);
  add('tabindex', 'serious', 'Positive tabindex values disrupt focus order',
      [...document.querySelectorAll('[tabindex]')].filter(e => parseInt(e.getAttribute('tabindex'), 10) > 0).map(e => e.outerHTML.slice(0, 120)));
  const levels = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].map(h => parseInt(h.tagName[1], 10));
  const skips = [];
  for (let i = 1; i < levels.length; i++) {
    if (levels[i] - levels[i-1] > 1) skips.push(`h${levels[i-1]} -> h${levels[i]}`);
  }
  add('heading-order', 'moderate', 'Heading levels should not be skipped', skips);
  return {issues, total: issues.reduce((n, i) => n + i.count, 0)};
}
"""

PERF_OBSERVER_JS = """
() => {
  window.__jambuPerf = window.__jambuPerf || {lcp: 0, cls: 0};
  try {
    new PerformanceObserver((list) => {
      const entries = list.getEntries();
      if (entries.length) window.__jambuPerf.lcp = entries[entries.length - 1].startTime;
    }).observe({type: 'largest-contentful-paint', buffered: true});
  } catch (e) {}
  try {
    new PerformanceObserver((list) => {
      for (const e of list.getEntries()) {
        if (!e.hadRecentInput) window.__jambuPerf.cls += e.value;
      }
    }).observe({type: 'layout-shift', buffered: true});
  } catch (e) {}
}
"""

PERF_JS = """
() => {
  const nav = performance.getEntriesByType('navigation')[0] || {};
  const paints = {};
  performance.getEntriesByType('paint').forEach(p => { paints[p.name] = p.startTime; });
  const resources = performance.getEntriesByType('resource') || [];
  let transfer = 0;
  resources.forEach(r => { transfer += (r.transferSize || 0); });
  const lcp = performance.getEntriesByType('largest-contentful-paint').slice(-1)[0];
  const observed = window.__jambuPerf || {};
  return {
    dom_content_loaded_ms: nav.domContentLoadedEventEnd || 0,
    load_ms: nav.loadEventEnd || 0,
    response_ms: nav.responseStart || 0,
    fcp_ms: paints['first-contentful-paint'] || 0,
    fp_ms: paints['first-paint'] || 0,
    lcp_ms: (lcp ? lcp.startTime : 0) || observed.lcp || 0,
    cls: observed.cls || 0,
    dom_nodes: document.getElementsByTagName('*').length,
    resource_count: resources.length,
    transfer_bytes: transfer,
    js_heap_bytes: (performance.memory && performance.memory.usedJSHeapSize) || 0
  };
}
"""
