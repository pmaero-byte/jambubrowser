"""Deterministic, LLM-free security checks for the Security Auditor.

These rules run on every audit whether or not an LLM is configured, so a
quick scan never comes back "0 findings" merely because the model was
unreachable. Each rule reads only the collected :class:`AuditData`
(response headers, cookies, network requests and the rendered page source)
and is pure: the same input always yields the same findings, with stable ids.
"""

from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urlsplit

from .base import AuditData, Finding, Severity

EMPLOYEE = "Security Auditor"
_MAX_EVIDENCE = 240


def _finding(severity: Severity, category: str, title: str, description: str,
             fix: str, evidence: str = "") -> Finding:
    digest = hashlib.sha1(f"{category}|{title}|{evidence}".encode("utf-8")).hexdigest()
    return Finding(
        id=f"rule-{digest[:10]}",
        employee=EMPLOYEE,
        severity=severity,
        category=category,
        title=title,
        description=description,
        fix_suggestion=fix,
        evidence_snippet=evidence[:_MAX_EVIDENCE],
    )


class _SourceScan(HTMLParser):
    """Collects the handful of DOM facts the rules need, tolerant of bad HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.inline_handlers: list[str] = []
        self.js_urls: list[str] = []
        self.meta_csp: list[str] = []
        self.meta_referrer: list[str] = []
        self.insecure_resources: list[str] = []
        self.password_forms_http: list[str] = []
        self.blank_targets: list[str] = []
        self._form_action: Optional[str] = None

    def handle_starttag(self, tag: str, attrs) -> None:
        amap = {(k or "").lower(): (v or "") for k, v in attrs}
        for key, value in amap.items():
            if key.startswith("on") and len(key) > 2 and key.isalpha():
                self.inline_handlers.append(f"<{tag} {key}=\"{value[:60]}\">")
            if key in ("href", "src", "action", "formaction") and \
                    value.strip().lower().startswith("javascript:"):
                self.js_urls.append(f"<{tag} {key}=\"{value[:60]}\">")
        if tag == "meta":
            equiv = amap.get("http-equiv", "").lower()
            if equiv == "content-security-policy":
                self.meta_csp.append(amap.get("content", ""))
            if amap.get("name", "").lower() == "referrer":
                self.meta_referrer.append(amap.get("content", ""))
        if tag in ("script", "img", "iframe", "link", "audio", "video", "source", "embed"):
            src = amap.get("src") or (amap.get("href") if tag == "link" else "")
            if src.lower().startswith("http://"):
                self.insecure_resources.append(f"<{tag} {src[:100]}>")
        if tag == "form":
            self._form_action = amap.get("action", "")
        if tag == "input" and amap.get("type", "").lower() == "password":
            action = self._form_action or ""
            if action.lower().startswith("http://"):
                self.password_forms_http.append(action[:100])
        if tag == "a" and amap.get("target", "").lower() == "_blank":
            rel = amap.get("rel", "").lower()
            href = amap.get("href", "")
            if "noopener" not in rel and "noreferrer" not in rel and \
                    href.lower().startswith(("http://", "https://")):
                self.blank_targets.append(href[:100])

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._form_action = None


def _headers(data: AuditData) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in (data.response_headers or {}).items()}


def _script_directive(policy: str) -> str:
    """The directive that governs scripts: script-src, else default-src."""
    directives = {}
    for part in policy.split(";"):
        bits = part.strip().split(None, 1)
        if bits:
            directives[bits[0].lower()] = bits[1] if len(bits) > 1 else ""
    return directives.get("script-src", directives.get("default-src", ""))


def check_headers(data: AuditData, scan: _SourceScan) -> list[Finding]:
    h = _headers(data)
    out: list[Finding] = []
    https = urlsplit(data.url or "").scheme == "https"
    csp = h.get("content-security-policy", "")
    meta_csp = " ".join(scan.meta_csp)

    if not csp and not meta_csp:
        out.append(_finding(
            Severity.MEDIUM, "csp", "Missing Content-Security-Policy",
            "The page sets no Content-Security-Policy (neither header nor <meta>), "
            "so injected script runs with the page's full authority.",
            "Send a Content-Security-Policy header, e.g. "
            "\"default-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'\".",
            "content-security-policy: (absent)"))
    else:
        policy = csp or meta_csp
        if "'unsafe-inline'" in _script_directive(policy):
            out.append(_finding(
                Severity.LOW, "csp", "CSP allows 'unsafe-inline' scripts",
                "The Content-Security-Policy permits inline script, which removes most of its XSS protection.",
                "Move inline scripts to files or use nonces/hashes instead of 'unsafe-inline'.",
                f"content-security-policy: {policy}"))

    if "x-frame-options" not in h and "frame-ancestors" not in csp:
        out.append(_finding(
            Severity.MEDIUM, "clickjacking", "Missing X-Frame-Options / frame-ancestors",
            "Nothing stops another origin from framing this page (clickjacking).",
            "Send \"X-Frame-Options: DENY\" or a CSP \"frame-ancestors 'none'\" directive.",
            "x-frame-options: (absent)"))

    if h.get("x-content-type-options", "").strip().lower() != "nosniff":
        out.append(_finding(
            Severity.LOW, "headers", "Missing X-Content-Type-Options: nosniff",
            "Browsers may MIME-sniff responses, turning an uploaded or reflected file into script.",
            "Send \"X-Content-Type-Options: nosniff\" on every response.",
            f"x-content-type-options: {h.get('x-content-type-options', '(absent)')}"))

    if "referrer-policy" not in h and not scan.meta_referrer:
        out.append(_finding(
            Severity.LOW, "headers", "Missing Referrer-Policy",
            "Without a Referrer-Policy, full URLs (including query strings) can leak to third parties.",
            "Send \"Referrer-Policy: strict-origin-when-cross-origin\" (or stricter).",
            "referrer-policy: (absent)"))

    if "permissions-policy" not in h:
        out.append(_finding(
            Severity.INFO, "headers", "Missing Permissions-Policy",
            "Powerful features (camera, microphone, geolocation) are not explicitly restricted.",
            "Send a Permissions-Policy header disabling features the page does not use.",
            "permissions-policy: (absent)"))

    if https and "strict-transport-security" not in h:
        out.append(_finding(
            Severity.MEDIUM, "transport", "Missing Strict-Transport-Security",
            "The HTTPS page does not set HSTS, so a first visit can be downgraded to HTTP.",
            "Send \"Strict-Transport-Security: max-age=31536000; includeSubDomains\".",
            "strict-transport-security: (absent)"))

    for name in ("server", "x-powered-by", "x-aspnet-version"):
        value = h.get(name, "")
        if value and re.search(r"\d", value):
            out.append(_finding(
                Severity.LOW, "disclosure", f"Version disclosure in {name} header",
                "The response advertises software and version, which helps target known exploits.",
                f"Remove the version from the {name} header.",
                f"{name}: {value}"))
    return out


def check_cookies(data: AuditData) -> list[Finding]:
    out: list[Finding] = []
    https = urlsplit(data.url or "").scheme == "https"
    for cookie in data.cookies or []:
        name = str(cookie.get("name") or "?")
        missing = []
        if https and not cookie.get("secure"):
            missing.append("Secure")
        if not cookie.get("httpOnly"):
            missing.append("HttpOnly")
        same_site = str(cookie.get("sameSite") or "")
        if not same_site or same_site.lower() == "none" and not cookie.get("secure"):
            missing.append("SameSite")
        if missing:
            out.append(_finding(
                Severity.LOW, "cookies", f"Cookie {name!r} lacks {', '.join(missing)}",
                "Cookie flags limit theft via script, plaintext transport and cross-site requests.",
                f"Set {', '.join(missing)} on the {name!r} cookie.",
                f"{name}: secure={bool(cookie.get('secure'))} httpOnly={bool(cookie.get('httpOnly'))} "
                f"sameSite={same_site or '(unset)'}"))
    return out


def check_source(data: AuditData, scan: _SourceScan) -> list[Finding]:
    out: list[Finding] = []
    if scan.inline_handlers:
        out.append(_finding(
            Severity.LOW, "xss", f"{len(scan.inline_handlers)} inline event handler(s)",
            "Inline on*= handlers require 'unsafe-inline' in a CSP and widen the XSS surface.",
            "Attach listeners from script files with addEventListener instead.",
            "; ".join(scan.inline_handlers[:3])))
    if scan.js_urls:
        out.append(_finding(
            Severity.MEDIUM, "xss", f"{len(scan.js_urls)} javascript: URL(s)",
            "javascript: URLs execute script on navigation and defeat CSP protections.",
            "Replace javascript: links with buttons wired to event listeners.",
            "; ".join(scan.js_urls[:3])))
    https = urlsplit(data.url or "").scheme == "https"
    if https:
        insecure = list(scan.insecure_resources)
        for req in data.network_requests or []:
            url = str(req.get("url") or "")
            if url.startswith("http://"):
                insecure.append(url[:100])
        if insecure:
            out.append(_finding(
                Severity.MEDIUM, "transport", f"{len(insecure)} mixed-content resource(s)",
                "An HTTPS page loads resources over plain HTTP, which an attacker can modify.",
                "Load every subresource over https://.",
                "; ".join(sorted(set(insecure))[:3])))
    if scan.password_forms_http:
        out.append(_finding(
            Severity.HIGH, "transport", "Password form submits over HTTP",
            "Credentials are sent in plaintext.",
            "Point the form action at an https:// endpoint.",
            "; ".join(scan.password_forms_http[:3])))
    if scan.blank_targets:
        out.append(_finding(
            Severity.INFO, "tabnabbing", f"{len(scan.blank_targets)} target=_blank link(s) without rel=noopener",
            "Older browsers give the opened page a handle on window.opener.",
            "Add rel=\"noopener noreferrer\" to external target=_blank links.",
            "; ".join(scan.blank_targets[:3])))
    return out


def run_security_rules(data: AuditData) -> list[Finding]:
    """All deterministic security findings for one page, in a stable order."""
    scan = _SourceScan()
    if data.page_source:
        try:
            scan.feed(data.page_source)
            scan.close()
        except Exception:  # malformed markup must not kill the rules
            pass
    return check_headers(data, scan) + check_cookies(data) + check_source(data, scan)
