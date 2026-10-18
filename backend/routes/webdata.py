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

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field as PField

from backend.core.security import is_public_url
from backend.modules.webgraph import (
    CrawlConfig,
    crawl,
    discover_urls,
    normalize_url,
)

log = logging.getLogger("jambu.webdata")

router = APIRouter(tags=["webdata"])

_USER_AGENT = "JambuBrowser/3.4 (+webgraph; local)"


async def _http_fetch(url: str):
    """Plain-HTTP transport for map/crawl. Returns ``(status, body)``."""
    try:
        from backend.core.socks import make_async_client
    except ImportError:  # pragma: no cover
        import httpx
        make_async_client = httpx.AsyncClient

    async with make_async_client(timeout=20.0, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": _USER_AGENT})
        return resp.status_code, resp.text


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


@router.post("/v1/map")
async def map_site(req: MapRequest):
    """Discover the URLs of a site (sitemaps + the seed's own links)."""
    seed = _guard(req.url)
    out = await discover_urls(
        seed,
        _http_fetch,
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


@router.post("/v1/crawl")
async def crawl_site(req: CrawlRequest):
    """Breadth-first crawl, bounded by depth, page count, robots and patterns."""
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
    result = await crawl(seed, _http_fetch, cfg)
    return {
        "success": True,
        "url": result.seed,
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


# ---------------------------------------------------------------------------
# scrape
# ---------------------------------------------------------------------------

class ScrapeRequest(BaseModel):
    url: str
    formats: List[str] = ["markdown"]
    max_chars: int = PField(20000, ge=0, le=200000)


@router.post("/v1/scrape")
async def scrape_one(req: ScrapeRequest):
    """Fetch one URL. Always returns a receipt, even on a non-200."""
    seed = _guard(req.url)
    status, body = await _http_fetch(seed)

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

    status, body = await _http_fetch(seed)

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
