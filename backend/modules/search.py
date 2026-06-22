"""
Metasearch & Discovery
======================
This module handles finding information across the internet.
It uses 'SearXNG' as primary, with fallback to DuckDuckGo API
and other search providers when SearXNG is unavailable.
"""

import httpx
import logging
import re

log = logging.getLogger("jambu.search")
from typing import List, Dict
from urllib.parse import quote_plus

# Tor support: when JAMBU_TOR_SOCKS_URL is set, every outbound HTTP in
# this module goes through the SOCKS5 proxy. Without it, behavior is
# unchanged.
try:
    from backend.core.socks import make_async_client, is_tor_enabled
except ImportError:
    # Defensive: don't break if backend.core.socks is unavailable
    # (e.g. when this module is imported standalone in a test).
    make_async_client = httpx.AsyncClient
    is_tor_enabled = lambda: False

SEARXNG_URL = "http://localhost:8888/search"

# Fallback search providers
DUCKDUCKGO_API = "https://api.duckduckgo.com/"


async def _search_duckduckgo(query: str, max_results: int = 10) -> List[Dict]:
    """DuckDuckGo search via duckduckgo_search library (fallback)."""
    results = []
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "url": r.get("href", ""),
                    "title": r.get("title", "")[:100],
                    "content": r.get("body", ""),
                    "engine": "duckduckgo"
                })
    except ImportError:
        # Fallback to instant answer API if library not installed
        try:
            async with make_async_client() as client:
                resp = await client.get(
                    DUCKDUCKGO_API,
                    params={"q": query, "format": "json"},
                    timeout=15.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    abstract = data.get("Abstract", "")
                    abstract_url = data.get("AbstractURL", "")
                    if abstract and abstract_url:
                        results.append({
                            "url": abstract_url,
                            "title": data.get("Heading", query),
                            "content": abstract,
                            "engine": "duckduckgo"
                        })
                    for topic in data.get("RelatedTopics", [])[:max_results - len(results)]:
                        if isinstance(topic, dict) and "Text" in topic:
                            results.append({
                                "url": topic.get("FirstURL", ""),
                                "title": topic.get("Text", "")[:100],
                                "content": topic.get("Text", ""),
                                "engine": "duckduckgo"
                            })
                    for result in data.get("Results", [])[:max_results - len(results)]:
                        results.append({
                            "url": result.get("FirstURL", ""),
                            "title": result.get("Text", "")[:100],
                            "content": result.get("Text", ""),
                            "engine": "duckduckgo"
                        })
        except Exception as e:
            log.warning("DuckDuckGo API fallback error: %s", e)
    except Exception as e:
        log.warning("DuckDuckGo search error: %s", e)
    return results


async def _search_google_scrape(query: str, max_results: int = 10) -> List[Dict]:
    """Direct Google search scraping as fallback (may be blocked)."""
    results = []
    try:
        async with make_async_client() as client:
            resp = await client.get(
                "https://www.google.com/search",
                params={"q": query, "num": max_results},
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                },
                timeout=15.0,
                follow_redirects=True,
            )
            if resp.status_code == 200:
                html = resp.text
                # Simple regex for Google results
                result_pattern = re.compile(
                    r'<a[^>]*href="/url\?q=([^&"]*)"[^>]*>(.*?)</a>',
                    re.DOTALL
                )
                for match in result_pattern.finditer(html):
                    url, title = match.groups()
                    if url.startswith('/url?'):
                        url = url[5:]
                    title = re.sub(r'<[^>]+>', '', title).strip()
                    if title and url.startswith('http'):
                        results.append({
                            "url": url,
                            "title": title,
                            "content": "",
                            "engine": "google"
                        })
                        if len(results) >= max_results:
                            break
    except Exception as e:
        log.warning("Google search error: %s", e)
    return results


async def _search_bing_scrape(query: str, max_results: int = 10) -> List[Dict]:
    """Bing search via HTML scrape. Bing's results page is server-rendered
    and more scraper-friendly than Google's, so this is a more reliable
    second-fallback than the Google regex.
    """
    results: List[Dict] = []
    try:
        async with make_async_client(follow_redirects=True) as client:
            resp = await client.get(
                "https://www.bing.com/search",
                params={"q": query, "count": max_results},
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=15.0,
            )
            if resp.status_code != 200:
                log.warning("Bing returned HTTP %s", resp.status_code)
                return results
            html = resp.text
            # Bing result items are <li class="b_algo"> with <h2><a> + a snippet.
            item_pattern = re.compile(
                r'<li[^>]*class="[^"]*\bb_algo\b[^"]*"[^>]*>(.*?)</li>',
                re.DOTALL,
            )
            for item_match in item_pattern.finditer(html):
                item_html = item_match.group(1)
                # Extract the URL from the <h2><a href="..."> tag
                url_match = re.search(r'<h2>\s*<a[^>]+href="(https?://[^"]+)"', item_html)
                if not url_match:
                    continue
                url = url_match.group(1)
                # Title is the link text inside the <h2><a>
                title_match = re.search(r'<h2>\s*<a[^>]+>(.*?)</a>', item_html, re.DOTALL)
                title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip() if title_match else ""
                # Snippet is in <p class="b_paractl"> or <p class="b_lineclamp3 ...">
                snippet_match = re.search(
                    r'<p[^>]*class="[^"]*\b(?:b_paractl|b_lineclamp|b_snippet)[^"]*"[^>]*>(.*?)</p>',
                    item_html, re.DOTALL,
                )
                snippet = re.sub(r'<[^>]+>', '', snippet_match.group(1)).strip() if snippet_match else ""
                if url.startswith("http") and title:
                    results.append({
                        "url": url,
                        "title": title[:200],
                        "content": snippet[:300],
                        "engine": "bing",
                    })
                if len(results) >= max_results:
                    break
    except Exception as e:
        log.warning("Bing search error: %s", e)
    return results


async def _search_duckduckgo_html(query: str, max_results: int = 10) -> List[Dict]:
    """DuckDuckGo HTML-lite scrape. The official API mostly returns nothing
    for real queries, but the HTML search page works.
    """
    results: List[Dict] = []
    try:
        async with make_async_client(follow_redirects=True) as client:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                },
                timeout=15.0,
            )
            if resp.status_code != 200:
                return results
            html = resp.text
            # DDG HTML: <a class="result__a" href="...">TITLE</a>
            # + <a class="result__snippet" ...>SNIPPET</a>
            item_pattern = re.compile(
                r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                re.DOTALL,
            )
            for url_match in item_pattern.finditer(html):
                url = url_match.group(1)
                # DDG wraps URLs in /l/?uddg=... — extract the real one
                uddg = re.search(r'uddg=([^&]+)', url)
                if uddg:
                    from urllib.parse import unquote
                    url = unquote(uddg.group(1))
                title = re.sub(r'<[^>]+>', '', url_match.group(2)).strip()
                if url.startswith("http") and title:
                    results.append({
                        "url": url,
                        "title": title[:200],
                        "content": "",
                        "engine": "duckduckgo_html",
                    })
                if len(results) >= max_results:
                    break
    except Exception as e:
        log.warning("DDG HTML search error: %s", e)
    return results


async def multi_engine_search(query: str, engines: str = "google,bing,duckduckgo") -> List[dict]:
    """
    Asks many search engines at once for information about a topic.
    Uses SearXNG as primary, falls back to DuckDuckGo API if unavailable.
    
    - query: What you want to find.
    - engines: Which engines to ask (Google is default).
    
    Returns: A list of result objects with 'url', 'title', and 'content'.
    """
    # Try SearXNG first
    try:
        async with make_async_client() as client:
            resp = await client.get(
                SEARXNG_URL, 
                params={"q": query, "format": "json", "engines": engines}, 
                timeout=15.0
            )
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                if results:
                    return results
    except Exception as e:
        log.warning("SearXNG not available: %s", e)

    # Fallback chain: DuckDuckGo HTML → Bing HTML → Google regex
    # (each is more aggressive; we stop as soon as one returns >= 1 result).
    log.info("Using DuckDuckGo HTML fallback...")
    ddg_html = await _search_duckduckgo_html(query)
    if ddg_html:
        return ddg_html

    log.info("Using Bing HTML fallback...")
    bing_results = await _search_bing_scrape(query)
    if bing_results:
        return bing_results

    log.info("Using Google scraping fallback...")
    return await _search_google_scrape(query)


def filter_trusted_results(results: List[dict], top_n: int = 5) -> List[dict]:
    """
    Cleans up the search results.
    - results: The messy list from search engines.
    - top_n: How many results you want to keep.
    
    It prioritizes 'Trusted' sites like .gov or .edu.
    """
    trusted_suffixes = ['.gov', '.edu', '.org', 'wikipedia.org']
    
    # Sort: Higher score for trusted domains
    sorted_res = sorted(
        results, 
        key=lambda x: any(s in x.get('url', '').lower() for s in trusted_suffixes), 
        reverse=True
    )
    
    # Deduplicate based on URL
    seen = set()
    unique = []
    for r in sorted_res:
        if r['url'] not in seen:
            unique.append(r)
            seen.add(r['url'])
            
    return unique[:top_n]
