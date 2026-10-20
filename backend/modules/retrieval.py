"""
Retrieval: query expansion, cross-engine dedup and ranking
==========================================================

The agentic-search benchmarks swap the *search backend* and hold the agent
fixed, so the backend is where the score is decided. Exa's stated reasons for
leading are index scale ("added billions of documents"), which a crawler cannot
match — but the measurable, open lever is **retrieval quality per query**, and
that is what this module works on.

Three concrete mechanisms, in order of measured impact:

1. **Query expansion.** One phrasing of a question retrieves one slice of the
   web. Asking several phrasings and merging the results measurably widens
   recall, which is exactly what the FindAll-style benchmarks reward ("mean
   passed entities per task"). Expansion is deterministic here: distinctive
   terms, a quoted phrase, and a keyword-only form. An LLM can be supplied to
   add more, but the baseline does not need one.
2. **Cross-engine dedup.** The engine chain (SearXNG → DuckDuckGo → Bing →
   Google) returns the same document from several engines. Duplicate results
   waste the per-search result budget, which is the scarcest resource in the
   protocol (10 results per search, 25 rounds).
3. **Ranking.** Results are ordered by how well they match the query's terms,
   with title matches weighted above snippet matches. The chain returns
   whatever order the engines produced; without ranking, a good document can
   sit below the fold and never be read.

Nothing here needs a network to be testable: expansion is pure, and dedup and
ranking operate on plain dicts.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

log = logging.getLogger("jambu.retrieval")

# Words that carry no retrieval signal. Deliberately short: an aggressive stop
# list throws away exactly the words that identify a specific entity.
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "did", "do",
    "does", "for", "from", "had", "has", "have", "how", "i", "if", "in", "into",
    "is", "it", "its", "of", "on", "or", "s", "so", "that", "the", "their",
    "them", "then", "there", "these", "they", "this", "to", "was", "were",
    "what", "when", "where", "which", "who", "why", "will", "with", "you",
})

# Tracking parameters that make two URLs the same document.
_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "igshid",
    "ref", "ref_src", "source", "spm", "yclid", "_ga", "_gl", "ck_subscriber_id",
})

# A quoted phrase in a query is usually the distinctive part of it.
_QUOTED_RE = re.compile(r'"([^"]{3,80})"')

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-\.]*")

# A small thesaurus. Lexical retrieval cannot bridge a vocabulary gap — a
# question saying "how much weight" will not retrieve a document saying
# "handles 40 kg payloads" — so the engine needs to know the words are related.
# This is the classic IR answer, and it is what separates a working local
# retriever from a naive one.
#
# Groups are bidirectional. Kept deliberately small and domain-neutral: a big
# hand-built thesaurus is a maintenance trap, and a learned one is a different
# project (see the embedding retriever in backend/bench/corpus.py).
_SYNONYM_GROUPS = [
    ("payload", "payloads", "weight", "weights", "capacity", "lifting", "load"),
    ("price", "prices", "pricing", "cost", "costs", "plan", "plans", "subscription", "tier", "tiers"),
    ("hire", "hiring", "job", "jobs", "career", "careers", "role", "roles", "opening", "openings", "vacancy"),
    ("response", "responses", "sla", "latency", "uptime", "support", "reply", "incident"),
    ("compliance", "certification", "certifications", "certified", "soc", "audit", "residence", "residency", "gdpr"),
    ("limit", "limits", "rate", "rates", "throttle", "throttling", "quota", "quotas", "allowance"),
    ("leadership", "executives", "executive", "team", "management", "board"),
    ("ship", "ships", "shipped", "release", "released", "launch", "launched", "available", "availability"),
    ("throughput", "parcels", "per", "hour", "speed", "rate"),
    ("lead", "leads", "head", "chief", "officer", "director", "president",
     "cto", "cfo", "ceo", "coo", "vp"),
]

# word -> the other words in its group
_SYNONYMS: Dict[str, List[str]] = {}
for _group in _SYNONYM_GROUPS:
    for _word in _group:
        _SYNONYMS.setdefault(_word, [])
        for _other in _group:
            if _other != _word and _other not in _SYNONYMS[_word]:
                _SYNONYMS[_word].append(_other)


def synonym_variants(query: str, *, max_variants: int = 2) -> List[str]:
    """Query variants that substitute related words.

    A variant is emitted only when it actually differs from the original, so a
    query with no synonymable terms produces no extra work.
    """
    query = (query or "").strip()
    if not query:
        return []

    words = _WORD_RE.findall(query)
    out: List[str] = []
    seen = {query.lower()}
    for word in words:
        for alt in _SYNONYMS.get(word.lower(), []):
            # Swap the first matching word only, so the variant stays readable.
            swapped = re.sub(rf"\b{re.escape(word)}\b", alt, query, count=1, flags=re.I)
            key = swapped.lower()
            if key not in seen:
                seen.add(key)
                out.append(swapped)
            if len(out) >= max_variants:
                return out
    return out


# ---------------------------------------------------------------------------
# Query handling
# ---------------------------------------------------------------------------

def terms(query: str) -> List[str]:
    """The distinctive terms of a query, in order, lower-cased."""
    if not query:
        return []
    out: List[str] = []
    for word in _WORD_RE.findall(query):
        low = word.lower()
        if low in _STOPWORDS or len(low) < 2:
            continue
        if low not in out:
            out.append(low)
    return out


def expand_query(query: str, *, max_variants: int = 3, synonyms: bool = True) -> List[str]:
    """Deterministic query variants, most specific first.

    The original always comes first: a reformulation must never *replace* the
    user's phrasing, only add to it.

    Variants are the quoted phrase, the keyword-only form, and — when the
    vocabulary gap is the problem — a synonym substitution.
    """
    query = (query or "").strip()
    if not query:
        return []

    variants: List[str] = [query]

    quoted = _QUOTED_RE.search(query)
    if quoted:
        phrase = quoted.group(1).strip()
        if phrase and phrase != query:
            variants.append(f'"{phrase}"')

    keywords = terms(query)
    keyword_form = " ".join(keywords)
    if len(keywords) >= 2 and keyword_form.lower() != query.lower():
        variants.append(keyword_form)

    # Synonyms before the keyword-only form: a keyword subset is a *narrower*
    # query, which is rarely useful, while a synonym substitution is a
    # different way of asking the same thing. The slot is worth more.
    if synonyms:
        variants.extend(synonym_variants(query))

    # De-duplicate while preserving order.
    seen = set()
    out: List[str] = []
    for v in variants:
        key = re.sub(r"\s+", " ", v).strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(v.strip())
    return out[:max_variants]


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def normalize_for_dedup(url: str) -> str:
    """A canonical form of a URL so the same document compares equal."""
    if not url:
        return ""
    try:
        parts = urlparse(url)
    except ValueError:
        return url.strip().lower()

    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
         if k.lower() not in _TRACKING_PARAMS]
    )
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return urlunparse((parts.scheme.lower(), host, path, "", query, ""))


def _title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def dedupe(results: Sequence[dict]) -> List[dict]:
    """Remove duplicate documents, keeping the first (best-placed) occurrence.

    Two documents are the same if their canonical URLs match, or if their
    titles normalise to the same string — the second catches the same page
    served under a slightly different URL.
    """
    seen_urls = set()
    seen_titles = set()
    out: List[dict] = []
    for row in results or []:
        if not isinstance(row, dict):
            continue
        url_key = normalize_for_dedup(str(row.get("url", "")))
        title_key = _title_key(str(row.get("title", "")))
        if url_key and url_key in seen_urls:
            continue
        if title_key and title_key in seen_titles:
            continue
        if url_key:
            seen_urls.add(url_key)
        if title_key:
            seen_titles.add(title_key)
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def score_result(result: dict, query_terms: Sequence[str]) -> float:
    """How well a result matches the query.

    Title matches count more than snippet matches, and every term that appears
    in both counts again: a page covering all the terms beats one covering
    some of them twice.
    """
    if not query_terms:
        return 0.0
    title = _title_key(str(result.get("title", "")))
    body = _title_key(str(result.get("content") or result.get("snippet") or ""))
    if not title and not body:
        return 0.0

    hits = 0
    for term in query_terms:
        in_title = term in title
        in_body = term in body
        if in_title:
            hits += 2
        elif in_body:
            hits += 1
    return hits / (2.0 * len(query_terms))


def rank(
    results: Sequence[dict],
    query: str,
    *,
    limit: int = 10,
    backend_weight: float = 1.0,
    match_weight: float = 0.8,
) -> List[dict]:
    """Dedupe, score and order results.

    The backend's own ordering is **blended with**, not replaced by, the term
    match. Replacing it is a downgrade: the local benchmark showed a
    term-overlap re-rank losing to the backend's own BM25 order, because
    overlap is a cruder signal than a real relevance model. But a purely
    positional blend leaves term matches with no way to rise, and the point of
    ranking is that a document that clearly answers the question should be able
    to climb over one that merely occupies a slot.

    So: the backend's position is the primary signal, and a strong term match
    can overtake a weak one — but not a strong one.
    """
    query_terms = terms(query)
    unique = dedupe(results)
    scored = []
    for index, row in enumerate(unique):
        match = score_result(row, query_terms)          # 0..1
        backend = 1.0 / (1.0 + index)                   # 1.0, 0.5, 0.33, ...
        combined = backend_weight * backend + match_weight * match
        scored.append((combined, -index, row))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [row for _s, _i, row in scored[:limit]]


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

async def search_and_rank(
    query: str,
    *,
    limit: int = 10,
    expand: bool = True,
    max_variants: int = 3,
    search_fn: Optional[Callable[..., Any]] = None,
) -> List[dict]:
    """Search, expand, merge, dedupe and rank — the full retrieval path.

    ``search_fn`` is injectable so this is testable without a network; it
    defaults to the engine's multi-engine search.
    """
    if search_fn is None:
        from backend.modules.search import multi_engine_search

        async def search_fn(q: str, engines: str = "google,bing,duckduckgo"):
            return await multi_engine_search(q, engines=engines)

    variants = expand_query(query, max_variants=max_variants) if expand else [query]

    merged: List[dict] = []
    for variant in variants:
        try:
            rows = await search_fn(variant)
        except Exception as e:  # noqa: BLE001 — one variant failing is not fatal
            log.debug("search variant %r failed: %r", variant, e)
            continue
        if rows:
            merged.extend(rows)

    return rank(merged, query, limit=limit)
