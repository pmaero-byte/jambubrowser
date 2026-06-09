"""
Metasearch & Discovery
======================
This module handles finding information across the internet.
It uses 'SearXNG' as primary, with fallback to DuckDuckGo API
and other search providers when SearXNG is unavailable.
"""

import httpx
import re
from typing import List, Dict
from urllib.parse import quote_plus

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
            async with httpx.AsyncClient() as client:
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
            print(f"DuckDuckGo API fallback error: {e}")
    except Exception as e:
        print(f"DuckDuckGo search error: {e}")
    return results


async def _search_google_scrape(query: str, max_results: int = 10) -> List[Dict]:
    """Direct Google search scraping as fallback (may be blocked)."""
    results = []
    try:
        async with httpx.AsyncClient() as client:
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
        print(f"Google search error: {e}")
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
        async with httpx.AsyncClient() as client:
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
        print(f"SearXNG not available: {e}")
    
    # Fallback to DuckDuckGo API
    print("Using DuckDuckGo API fallback...")
    ddg_results = await _search_duckduckgo(query)
    
    if ddg_results:
        return ddg_results
    
    # Last resort: Google scraping (may be blocked)
    print("Using Google scraping fallback...")
    google_results = await _search_google_scrape(query)
    return google_results


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
