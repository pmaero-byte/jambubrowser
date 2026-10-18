"""
Web-data API (v1) — map, crawl, scrape, extract
===============================================

A Firecrawl-shaped surface for agents, with two deliberate differences:

* **Every page carries a fetch receipt** — content hash, timestamp, status, and
  what was stripped on the way in. A hosted scraping API returns text; this
  returns text *and* a verifiable record of what was fetched.
* **Extraction goes through the quarantine boundary.** A caller-supplied schema
  is validated and enforced, so the returned data is typed fields rather than
  prose that could carry an instruction into the caller's own agent.

Runs entirely locally: the transport is the engine's own HTTP client, subject to
the same SSRF guard as the rest of the product.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field as PField

from backend.core.api_guard import require_api_key
from backend.core.security import is_public_url
from backend.modules.webgraph import (
    CrawlConfig,
    crawl,
    discover_urls,
    normalize_url,
)

log = logging.getLogger("jambu.webdata")

# Every route on this surface is behind the key guard: it fetches arbitrary
# URLs and can spend model budget. See backend/core/api_guard.py for the policy.
router = APIRouter(tags=["webdata"], dependencies=[Depends(require_api_key)])

_USER_AGENT = "JambuBrowser/3.4 (+webgraph; local)"


def _webdata_cache():
    """Process-wide response cache for fetched pages (bounded, TTL'd)."""
    global _CACHE
    if _CACHE is None:
        from backend.core.response_cache import ResponseCache

        _CACHE = ResponseCache(max_size_bytes=32 * 1024 * 1024, default_ttl=300.0)
    return _CACHE


_CACHE = None


async def _http_fetch(url: str, max_age: float = 0.0):
    """Plain-HTTP transport for map/crawl. Returns ``(status, body)``.

    ``max_age`` > 0 serves a cached copy when one is fresh enough, which is what
    makes repeated crawls of the same site cheap. ``0`` always goes to the
    network — stale data is worse than a slow answer when the point is to
    observe the current state.
    """
    cache = _webdata_cache()
    if max_age > 0:
        hit = cache.get(url)
        if hit is not None:
            return hit.status_code, hit.body.decode("utf-8", "replace")

    try:
        from backend.core.socks import make_async_client
    except ImportError:  # pragma: no cover
        import httpx
        make_async_client = httpx.AsyncClient

    async with make_async_client(timeout=20.0, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": _USER_AGENT})
        status, text = resp.status_code, resp.text

    if status == 200 and max_age > 0:
        try:
            from backend.core.response_cache import CachedResponse

            cache.set(
                url,
                CachedResponse(
                    body=text.encode("utf-8", "replace"),
                    content_type=resp.headers.get("content-type", "text/html"),
                    status_code=status,
                ),
                ttl=max_age,
            )
        except Exception:  # a cache failure must never fail the fetch
            log.debug("web-data cache set failed", exc_info=True)
    return status, text


def _make_fetch(max_age: float):
    """Bind a cache policy to the transport for one request."""

    async def fetch(url: str):
        return await _http_fetch(url, max_age)

    return fetch


def _cache_stats():
    s = _webdata_cache().stats()
    return {
        "entries": s.entries,
        "bytes_used": s.bytes_used,
        "bytes_budget": s.bytes_budget,
        "hits": s.hits,
        "misses": s.misses,
        "hit_rate": round(s.hit_rate, 4),
        "evictions": s.evictions,
        "expirations": s.expirations,
    }


@router.get("/v1/cache/stats")
async def cache_stats():
    """Fetch-cache health. `max_age > 0` on the other routes uses this cache."""
    return _cache_stats()


# ---------------------------------------------------------------------------
# batch
# ---------------------------------------------------------------------------

class BatchRequest(BaseModel):
    urls: List[str] = PField(..., min_length=1, max_length=500)
    max_age: float = PField(0, ge=0, le=604800)
    max_chars: int = PField(20000, ge=0, le=200000)


@router.post("/v1/batch")
async def batch_scrape(req: BatchRequest):
    """Scrape a fixed list of URLs concurrently. Returns a job id immediately."""
    from backend.modules.crawl_jobs import get_crawl_jobs

    seeds = []
    for raw in req.urls:
        seeds.append(_guard(raw))

    job = get_crawl_jobs().submit_batch(
        seeds, _make_fetch(req.max_age), max_chars=req.max_chars
    )
    return {
        "success": True,
        "id": job.id,
        "total": job.total,
        "status": job.status.value,
        "poll": f"/v1/batch/{job.id}",
    }


@router.get("/v1/batch/{job_id}")
async def batch_status(job_id: str, include_pages: bool = True):
    from backend.modules.crawl_jobs import get_crawl_jobs

    job = get_crawl_jobs().get(job_id)
    if job is None or getattr(job, "urls", None) is None:
        raise HTTPException(status_code=404, detail="unknown or expired batch job")
    return {"success": True, **job.to_dict(include_pages=include_pages)}


@router.delete("/v1/batch/{job_id}")
async def batch_cancel(job_id: str):
    from backend.modules.crawl_jobs import get_crawl_jobs

    job = get_crawl_jobs().cancel(job_id)
    if job is None or getattr(job, "urls", None) is None:
        raise HTTPException(status_code=404, detail="unknown or expired batch job")
    return {"success": True, "id": job.id, "status": job.status.value}


# ---------------------------------------------------------------------------
# change tracking
# ---------------------------------------------------------------------------

class CheckRequest(BaseModel):
    url: str
    max_age: float = PField(0, ge=0, le=604800)
    include_diff: bool = True


@router.post("/v1/changes/check")
async def check_change(req: CheckRequest):
    """Fetch a URL and report whether it changed since the last check.

    The first check for a URL is recorded but not reported as a change — there
    is nothing to compare against, and alerting on it would fire once per new URL.
    """
    seed = _guard(req.url)
    status, body = await _http_fetch(seed, req.max_age)

    from backend.core.untrusted import sanitize_untrusted
    from backend.modules.changes import record_check

    cleaned = sanitize_untrusted(body or "", max_chars=200000)
    decision = record_check(seed, cleaned.text)
    return {
        "success": status == 200,
        "http_status": status,
        "markers": cleaned.injection_markers,
        **decision.to_dict(include_diff=req.include_diff),
    }


@router.get("/v1/changes")
async def list_changes(url: Optional[str] = None, limit: int = 50):
    """Watched URLs (newest checked first), or the change log for one URL."""
    from backend.modules.changes import list_watched, recent_changes

    if url:
        return {"success": True, "url": url, "changes": recent_changes(url, limit=limit)}
    return {"success": True, "watched": list_watched(limit=limit)}


class ForgetRequest(BaseModel):
    url: str


@router.delete("/v1/changes")
async def forget_change(req: ForgetRequest):
    """Stop tracking a URL and drop its snapshot and history."""
    from backend.modules.changes import forget
    from backend.modules.webgraph import normalize_url

    seed = normalize_url(req.url) or req.url
    return {"success": True, "url": seed, "forgotten": forget(seed)}


@router.delete("/v1/cache")
async def cache_clear():
    """Drop every cached page."""
    _webdata_cache().clear()
    return {"success": True, "cleared": True}


def _guard(url: str) -> str:
    """SSRF-check and normalise an inbound URL.

    Uses :func:`is_public_url`, not ``is_safe_url``: the local-workspace
    relaxation (which permits loopback so Greenwich can preview local EPUB/PDF
    output) must not let a scraping endpoint reach this engine's own services.
    """
    clean = normalize_url(url)
    if not clean or not is_public_url(clean):
        raise HTTPException(
            status_code=400,
            detail="unsafe or unsupported URL (private, loopback and non-HTTP targets are refused)",
        )
    return clean


# ---------------------------------------------------------------------------
# map
# ---------------------------------------------------------------------------

class MapRequest(BaseModel):
    url: str
    limit: int = PField(500, ge=1, le=10000)
    use_sitemap: bool = True
    same_domain: bool = True
    allow_subdomains: bool = False
    max_age: float = PField(0, ge=0, le=604800)


@router.post("/v1/map")
async def map_site(req: MapRequest):
    """Discover the URLs of a site (sitemaps + the seed's own links)."""
    seed = _guard(req.url)
    out = await discover_urls(
        seed,
        _make_fetch(req.max_age),
        limit=req.limit,
        use_sitemap=req.use_sitemap,
        same_domain=req.same_domain,
        allow_subdomains=req.allow_subdomains,
    )
    out["success"] = "error" not in out
    return out


# ---------------------------------------------------------------------------
# crawl
# ---------------------------------------------------------------------------

class CrawlRequest(BaseModel):
    url: str
    max_depth: int = PField(2, ge=0, le=5)
    page_limit: int = PField(25, ge=1, le=500)
    same_domain: bool = True
    allow_subdomains: bool = False
    include_patterns: List[str] = []
    exclude_patterns: List[str] = []
    respect_robots: bool = True
    use_sitemap: bool = False
    max_age: float = PField(0, ge=0, le=604800)
    sync: bool = False


@router.post("/v1/crawl")
async def crawl_site(req: CrawlRequest):
    """Start a crawl.

    Returns a job id immediately and runs the crawl in the background — at a few
    hundred pages this takes minutes, which is not a request/response shape. Poll
    `GET /v1/crawl/{id}`. Set `sync=true` only for small jobs where blocking is
    acceptable (the result is returned inline).
    """
    seed = _guard(req.url)
    cfg = CrawlConfig(
        max_depth=req.max_depth,
        page_limit=req.page_limit,
        same_domain=req.same_domain,
        allow_subdomains=req.allow_subdomains,
        include_patterns=tuple(req.include_patterns),
        exclude_patterns=tuple(req.exclude_patterns),
        respect_robots=req.respect_robots,
        use_sitemap=req.use_sitemap,
    )
    fetch = _make_fetch(req.max_age)

    if req.sync:
        result = await crawl(seed, fetch, cfg)
        return {
            "success": True,
            "sync": True,
            "url": result.seed,
            "status": "cancelled" if result.cancelled else "completed",
            "count": result.count,
            "truncated": result.truncated,
            "skipped_robots": result.skipped_robots,
            "errors": result.errors,
            "data": [
                {
                    "url": p.url,
                    "depth": p.depth,
                    "status": p.status,
                    "markdown": p.text,
                    "receipt": p.receipt.to_dict(),
                }
                for p in result.pages
            ],
        }

    from backend.modules.crawl_jobs import get_crawl_jobs

    job = get_crawl_jobs().submit(seed, fetch, cfg)
    return {
        "success": True,
        "sync": False,
        "id": job.id,
        "url": job.seed,
        "status": job.status.value,
        "poll": f"/v1/crawl/{job.id}",
    }


@router.get("/v1/crawl/{job_id}")
async def crawl_status(job_id: str, include_pages: bool = True):
    """Poll a crawl job. Results expire `JAMBU_CRAWL_RESULT_TTL` after it ends."""
    from backend.modules.crawl_jobs import get_crawl_jobs

    job = get_crawl_jobs().get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="unknown or expired crawl job (results are not kept forever)",
        )
    return {"success": True, **job.to_dict(include_pages=include_pages)}


@router.delete("/v1/crawl/{job_id}")
async def crawl_cancel(job_id: str):
    """Request cancellation. Cooperative — it takes effect between pages."""
    from backend.modules.crawl_jobs import get_crawl_jobs

    job = get_crawl_jobs().cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown or expired crawl job")
    return {"success": True, "id": job.id, "status": job.status.value}


@router.get("/v1/crawl")
async def crawl_list():
    """Recent crawl jobs (newest first)."""
    from backend.modules.crawl_jobs import get_crawl_jobs

    jobs = get_crawl_jobs().list_jobs()
    return {
        "success": True,
        "count": len(jobs),
        "jobs": [j.to_dict(include_pages=False) for j in jobs],
    }


# ---------------------------------------------------------------------------
# scrape
# ---------------------------------------------------------------------------

class ScrapeRequest(BaseModel):
    url: str
    formats: List[str] = ["markdown"]
    max_chars: int = PField(20000, ge=0, le=200000)
    max_age: float = PField(0, ge=0, le=604800)


@router.post("/v1/scrape")
async def scrape_one(req: ScrapeRequest):
    """Fetch one URL. Always returns a receipt, even on a non-200."""
    seed = _guard(req.url)
    status, body = await _http_fetch(seed, req.max_age)

    from backend.core.untrusted import sanitize_untrusted
    from backend.modules.webgraph import extract_links, make_receipt

    cleaned = sanitize_untrusted(body or "", max_chars=req.max_chars)
    receipt = make_receipt(seed, status, body or "", cleaned)

    out: Dict[str, Any] = {
        "success": status == 200,
        "url": seed,
        "receipt": receipt.to_dict(),
    }
    if "markdown" in req.formats:
        out["markdown"] = cleaned.text
    if "html" in req.formats:
        out["html"] = body or ""
    if "links" in req.formats:
        out["links"] = extract_links(body or "", seed)
    return out


# ---------------------------------------------------------------------------
# extract — caller-supplied schema, quarantined
# ---------------------------------------------------------------------------

_ALLOWED_FIELD_TYPES = {"str", "int", "float", "bool", "list[str]", "enum"}


class FieldSpec(BaseModel):
    name: str
    type: str = "str"
    role: str = "content"
    max_chars: int = PField(300, ge=1, le=5000)
    max_items: int = PField(20, ge=1, le=100)
    choices: List[str] = []
    description: str = ""


class ExtractRequest(BaseModel):
    url: str
    fields: List[FieldSpec]
    max_chars: int = PField(8000, ge=100, le=100000)
    max_age: float = PField(0, ge=0, le=604800)


def build_schema(name: str, specs: List[FieldSpec]):
    """Build a validated ExtractionSchema from caller input.

    Every rule is enforced by ``Field``/``validate_structure`` — a caller cannot
    smuggle a free-text control field or a recursive structure through the API.
    """
    from backend.core.quarantine import ExtractionSchema, Field

    if not specs:
        raise HTTPException(status_code=400, detail="at least one field is required")
    if len(specs) > 25:
        raise HTTPException(status_code=400, detail="too many fields (max 25)")
    fields = []
    for s in specs:
        if s.type not in _ALLOWED_FIELD_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"field {s.name!r}: unsupported type {s.type!r}",
            )
        try:
            fields.append(
                Field(
                    name=s.name,
                    type=s.type,
                    max_chars=s.max_chars,
                    max_items=s.max_items,
                    choices=tuple(s.choices),
                    description=s.description,
                    role=s.role,
                )
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"field {s.name!r}: {e}") from e
    schema = ExtractionSchema(name, fields)
    try:
        schema.validate_structure()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return schema


@router.post("/v1/extract")
async def extract_structured(req: ExtractRequest):
    """Extract a caller-defined schema from one URL, through the quarantine.

    The page text never reaches the caller as prose: it is extracted into typed
    fields by a tool-less model, and undeclared fields are rejected.
    """
    seed = _guard(req.url)
    schema = build_schema("caller_extract", req.fields)

    status, body = await _http_fetch(seed, req.max_age)

    from backend.core.observations import registry_chat
    from backend.core.quarantine import quarantine_extract
    from backend.modules.webgraph import make_receipt
    from backend.core.untrusted import sanitize_untrusted

    cleaned = sanitize_untrusted(body or "", max_chars=50000)
    receipt = make_receipt(seed, status, body or "", cleaned)

    result = await quarantine_extract(
        cleaned.text, schema, registry_chat, max_chars=req.max_chars
    )
    return {
        "success": result.ok,
        "url": seed,
        "data": result.data,
        "schema": schema.name,
        "problems": result.problems,
        "injection_markers": result.injection_markers,
        "receipt": receipt.to_dict(),
    }
