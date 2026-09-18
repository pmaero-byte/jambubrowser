"""
Debugging helpers for agent test flows.

Pure, dependency-free utilities used by the browser flow runner:

- **Network policy** — compile ``mocks`` / ``fail`` / ``delay`` / ``offline``
  rules and match them against requests (Playwright routes the page through
  them; tests exercise the matcher directly).
- **DOM deltas** — summarise what changed between two element catalogs.
- **Source maps** — a real Base64-VLQ decoder and mapping lookup, so console
  errors can be reported against original source instead of bundle offsets.
- **Page probes** — self-contained JavaScript for accessibility and
  performance auditing (no external dependency; runs in the page).
"""
from __future__ import annotations

import fnmatch
import re
from typing import Any, Optional
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Network policy
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
