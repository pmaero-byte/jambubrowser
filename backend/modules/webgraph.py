"""
Web graph — URL discovery, crawling and fetch receipts
======================================================

The primitives a "map this site" / "crawl this site" surface needs, kept
dependency-free and **fetch-injected** so the graph logic can be tested without
a network or a browser.

Two things this module deliberately does that a plain crawler does not:

* **Respects robots.txt and sitemaps by default.** A crawler that ignores them
  is a liability, not a feature.
* **Emits a receipt per page** — content hash, byte count, timestamp and what
  was stripped on the way in. A fetch you cannot account for is a fetch you
  cannot trust, and no hosted scraping API ships this.

The fetch itself is a caller-supplied ``async (url) -> (status, body)`` so this
module never assumes a transport.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

# ---------------------------------------------------------------------------
# URL handling
# ---------------------------------------------------------------------------

_SKIP_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp", ".avif",
    ".css", ".js", ".mjs", ".woff", ".woff2", ".ttf", ".eot",
    ".zip", ".gz", ".tar", ".rar", ".7z",
    ".mp3", ".mp4", ".avi", ".mov", ".webm", ".wav",
)

_HREF_RE = re.compile(r"""<a\b[^>]*?\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)
_LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.I | re.S)
_SITEMAP_TAG_RE = re.compile(r"<(urlset|sitemapindex)\b", re.I)


def normalize_url(url: str, base: Optional[str] = None) -> Optional[str]:
    """Resolve, clean and validate a URL. Returns None if unusable.

    Drops fragments (they are not distinct resources), lowercases the host,
    removes the default port, and rejects non-HTTP schemes and asset URLs.
    """
    if not url:
        return None
    url = url.strip()
    if not url or url.startswith(("mailto:", "javascript:", "tel:", "data:", "#")):
        return None
    if base:
        url = urljoin(base, url)
    try:
        parts = urlparse(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None

    host = parts.netloc.lower()
    if host.endswith(":80") and parts.scheme == "http":
        host = host[:-3]
    if host.endswith(":443") and parts.scheme == "https":
        host = host[:-4]

    path = parts.path or "/"
    if path.lower().endswith(_SKIP_EXTENSIONS):
        return None
    return urlunparse((parts.scheme, host, path, parts.params, parts.query, ""))


def host_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except ValueError:
        return ""


def same_site(a: str, b: str, *, allow_subdomains: bool = False) -> bool:
    """Whether two URLs belong to the same site."""
    ha, hb = host_of(a), host_of(b)
    if not ha or not hb:
        return False
    if ha == hb:
        return True
    if not allow_subdomains:
        return False
    # Strip the port before comparing registrable-ish suffixes.
    return ha.endswith("." + hb) or hb.endswith("." + ha)


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------

def _rule_matches(rule: str, path: str) -> bool:
    """Match a robots.txt path pattern.

    robots.txt is not plain prefix matching: ``*`` matches any sequence and a
    trailing ``$`` anchors the end (RFC 9309 §2.2.2). Treating ``/*.json$`` as a
    literal prefix silently allows everything the rule was meant to block.
    """
    anchored = rule.endswith("$")
    pattern = rule[:-1] if anchored else rule
    regex = re.escape(pattern).replace(r"\*", ".*")
    if anchored:
        return re.fullmatch(regex, path) is not None
    return re.match(regex, path) is not None


@dataclass
class RobotsRules:
    """Parsed robots.txt for one user-agent, with longest-match precedence."""

    rules: List[Tuple[str, bool]] = field(default_factory=list)  # (path, allowed)
    crawl_delay: Optional[float] = None
    sitemaps: List[str] = field(default_factory=list)

    def allowed(self, path: str) -> bool:
        if not path:
            path = "/"
        best_len = -1
        best_allow = True
        for rule_path, allow in self.rules:
            if not rule_path:
                continue
            if not _rule_matches(rule_path, path):
                continue
            # The most specific (longest) matching rule decides.
            if len(rule_path) > best_len:
                best_len = len(rule_path)
                best_allow = allow
        # No matching rule means allowed (RFC 9309).
        return best_allow if best_len >= 0 else True


def parse_robots(text: str, user_agent: str = "*") -> RobotsRules:
    """Parse robots.txt for ``user_agent``.

    Handles the common case correctly: groups are matched by user-agent token
    (longest matching token wins), and within a group the longest matching path
    rule decides. ``Allow`` beats ``Disallow`` only where it is more specific.
    """
    rules = RobotsRules()
    if not text:
        return rules

    ua = (user_agent or "*").lower()
    groups: List[Tuple[List[str], List[Tuple[str, bool]], Optional[float]]] = []
    current_agents: List[str] = []
    current_rules: List[Tuple[str, bool]] = []
    current_delay: Optional[float] = None

    def flush() -> None:
        if current_agents:
            groups.append((list(current_agents), list(current_rules), current_delay))

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        directive, _, value = line.partition(":")
        directive = directive.strip().lower()
        value = value.strip()

        if directive == "user-agent":
            if current_rules or current_delay is not None:
                # A new group starts after rules were already seen.
                flush()
                current_agents, current_rules, current_delay = [], [], None
            current_agents.append(value.lower())
        elif directive in ("allow", "disallow"):
            if directive == "disallow" and value == "":
                continue  # "Disallow:" (empty) means allow everything
            current_rules.append((value, directive == "allow"))
        elif directive == "crawl-delay":
            try:
                current_delay = float(value)
            except ValueError:
                pass
        elif directive == "sitemap":
            rules.sitemaps.append(value)
    flush()

    # Pick the most specific matching agent group; fall back to "*".
    best_score = -1
    for agents, group_rules, delay in groups:
        for agent in agents:
            if agent == "*":
                score = 0
            elif ua == agent or ua.startswith(agent) or agent in ua:
                score = len(agent)
            else:
                continue
            if score > best_score:
                best_score = score
                rules.rules = group_rules
                rules.crawl_delay = delay
    return rules


# ---------------------------------------------------------------------------
# sitemaps
# ---------------------------------------------------------------------------

@dataclass
class SitemapParse:
    urls: List[str] = field(default_factory=list)
    sitemaps: List[str] = field(default_factory=list)  # nested sitemap indexes
    is_index: bool = False


def parse_sitemap(xml: str, base: Optional[str] = None) -> SitemapParse:
    """Parse a sitemap or sitemap index (small, dependency-free XML scan).

    Not a general XML processor: it reads ``<loc>`` values and decides
    index-vs-urlset from the root element, which is what sitemaps actually use.
    """
    out = SitemapParse()
    if not xml or "<" not in xml:
        return out
    match = _SITEMAP_TAG_RE.search(xml)
    out.is_index = bool(match and match.group(1).lower() == "sitemapindex")

    seen = set()
    for raw in _LOC_RE.findall(xml):
        url = normalize_url(raw, base)
        if url and url not in seen:
            seen.add(url)
            (out.sitemaps if out.is_index else out.urls).append(url)
    return out


def default_sitemap_urls(seed: str) -> List[str]:
    """The sitemap locations to try for a seed, most standard first."""
    parts = urlparse(seed)
    root = f"{parts.scheme}://{parts.netloc}"
    return [f"{root}/sitemap.xml", f"{root}/sitemap_index.xml"]


# ---------------------------------------------------------------------------
# link extraction
# ---------------------------------------------------------------------------

def extract_links(html: str, base: Optional[str] = None) -> List[str]:
    """Extract and normalise the links from an HTML document, in page order."""
    if not html:
        return []
    seen = set()
    out: List[str] = []
    for m in _HREF_RE.finditer(html):
        raw = m.group(1) or m.group(2) or m.group(3) or ""
        url = normalize_url(raw, base)
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


# ---------------------------------------------------------------------------
# Fetch receipts
# ---------------------------------------------------------------------------

@dataclass
class FetchReceipt:
    """An auditable record of one fetch.

    The content hash is what makes this worth having: it lets a third party
    verify that the text they were shown is the text that was fetched.
    """

    url: str
    fetched_at: float
    status: int
    content_sha256: str
    bytes_in: int
    bytes_out: int
    removed_invisible: int = 0
    injection_markers: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "url": self.url,
            "fetched_at": self.fetched_at,
            "status": self.status,
            "content_sha256": self.content_sha256,
            "bytes_in": self.bytes_in,
            "bytes_out": self.bytes_out,
            "removed_invisible": self.removed_invisible,
            "injection_markers": list(self.injection_markers),
        }


def make_receipt(url: str, status: int, raw: str, cleaned, *, fetched_at: Optional[float] = None) -> FetchReceipt:
    """Build a receipt from the raw body and its sanitised form."""
    raw = raw or ""
    text = getattr(cleaned, "text", "") or ""
    return FetchReceipt(
        url=url,
        fetched_at=fetched_at if fetched_at is not None else time.time(),
        status=status,
        content_sha256=hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest(),
        bytes_in=len(raw.encode("utf-8", "replace")),
        bytes_out=len(text.encode("utf-8", "replace")),
        removed_invisible=getattr(cleaned, "removed_invisible", 0),
        injection_markers=list(getattr(cleaned, "injection_markers", []) or []),
    )


# ---------------------------------------------------------------------------
# Crawl planning and execution
# ---------------------------------------------------------------------------

FetchFn = Callable[[str], Awaitable[Tuple[int, str]]]


@dataclass
class CrawlConfig:
    max_depth: int = 2
    page_limit: int = 25
    same_domain: bool = True
    allow_subdomains: bool = False
    include_patterns: Sequence[str] = ()
    exclude_patterns: Sequence[str] = ()
    respect_robots: bool = True
    use_sitemap: bool = True

    def permits(self, url: str, seed: str) -> bool:
        if self.same_domain and not same_site(url, seed, allow_subdomains=self.allow_subdomains):
            return False
        path = urlparse(url).path or "/"
        if self.include_patterns and not any(p in path for p in self.include_patterns):
            return False
        if any(p in path for p in self.exclude_patterns):
            return False
        return True


@dataclass
class CrawlPage:
    url: str
    depth: int
    status: int
    text: str
    receipt: FetchReceipt
    links_found: int = 0


@dataclass
class CrawlResult:
    seed: str
    pages: List[CrawlPage] = field(default_factory=list)
    errors: List[Dict[str, str]] = field(default_factory=list)
    skipped_robots: List[str] = field(default_factory=list)
    truncated: bool = False

    @property
    def count(self) -> int:
        return len(self.pages)


async def _load_robots(seed: str, fetch: FetchFn) -> RobotsRules:
    parts = urlparse(seed)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        status, body = await fetch(robots_url)
    except Exception:
        return RobotsRules()
    if status != 200 or not body:
        return RobotsRules()
    return parse_robots(body)


async def discover_urls(
    seed: str,
    fetch: FetchFn,
    *,
    limit: int = 500,
    use_sitemap: bool = True,
    same_domain: bool = True,
    allow_subdomains: bool = False,
) -> Dict[str, object]:
    """Discover the URLs of a site: sitemaps plus the seed's own links.

    This is the "map" operation: fast, shallow, no page extraction.
    """
    seed = normalize_url(seed) or seed
    found: List[str] = []
    seen = set()

    def add(url: Optional[str]) -> None:
        if url and url not in seen and len(found) < limit:
            if not same_domain or same_site(url, seed, allow_subdomains=allow_subdomains):
                seen.add(url)
                found.append(url)

    sources: List[str] = []

    if use_sitemap:
        sitemap_targets = list(default_sitemap_urls(seed))
        visited_sitemaps = set()
        for _ in range(5):  # bounded sitemap-index walking
            if not sitemap_targets:
                break
            target = sitemap_targets.pop(0)
            if target in visited_sitemaps or len(found) >= limit:
                continue
            visited_sitemaps.add(target)
            try:
                status, body = await fetch(target)
            except Exception:
                continue
            if status != 200 or not body:
                continue
            parsed = parse_sitemap(body, target)
            sources.append(target)
            for url in parsed.urls:
                add(url)
            sitemap_targets.extend(parsed.sitemaps)

    try:
        status, body = await fetch(seed)
        if status == 200 and body:
            for url in extract_links(body, seed):
                add(url)
    except Exception as e:
        return {
            "url": seed, "urls": found, "count": len(found),
            "sources": sources, "error": str(e),
        }

    return {
        "url": seed, "urls": found, "count": len(found),
        "sources": sources, "truncated": len(found) >= limit,
    }


async def crawl(
    seed: str,
    fetch: FetchFn,
    config: Optional[CrawlConfig] = None,
    *,
    sanitize=None,
) -> CrawlResult:
    """Breadth-first crawl, bounded by depth, page count, robots and patterns.

    ``fetch`` must return ``(status, body)``. ``sanitize`` defaults to the
    untrusted-content guard so every stored page carries its injection markers
    and stripped-character count in its receipt.
    """
    cfg = config or CrawlConfig()
    if sanitize is None:
        from backend.core.untrusted import sanitize_untrusted
        sanitize = lambda t: sanitize_untrusted(t, max_chars=0)  # noqa: E731

    seed = normalize_url(seed)
    if not seed:
        return CrawlResult(seed="", errors=[{"url": "", "error": "invalid seed URL"}])

    result = CrawlResult(seed=seed)
    robots = await _load_robots(seed, fetch) if cfg.respect_robots else RobotsRules()

    visited = {seed}
    queue: List[Tuple[str, int]] = [(seed, 0)]

    while queue and len(result.pages) < cfg.page_limit:
        url, depth = queue.pop(0)

        if cfg.respect_robots and not robots.allowed(urlparse(url).path or "/"):
            result.skipped_robots.append(url)
            continue

        try:
            status, body = await fetch(url)
        except Exception as e:
            result.errors.append({"url": url, "error": str(e)})
            continue

        cleaned = sanitize(body or "")
        receipt = make_receipt(url, status, body or "", cleaned)
        links = extract_links(body or "", url)
        result.pages.append(
            CrawlPage(
                url=url, depth=depth, status=status,
                text=cleaned.text, receipt=receipt, links_found=len(links),
            )
        )

        if depth < cfg.max_depth:
            for link in links:
                if link in visited or not cfg.permits(link, seed):
                    continue
                visited.add(link)
                queue.append((link, depth + 1))

    if queue:
        result.truncated = True
    return result
