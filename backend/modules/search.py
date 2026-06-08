"""
Metasearch & Discovery
======================
This module handles finding information across the internet.
It uses 'SearXNG' as primary, with fallback to direct DuckDuckGo
and Google search when SearXNG is unavailable.
"""

import httpx
import re
from typing import List, Dict
from urllib.parse import quote_plus

SEARXNG_URL = "http://localhost:8888/search"

# Fallback search providers
DUCKDUCKGO_HTML = "https://html.duckduckgo.com/html/"
GOOGLE_SEARCH = "https://www.google.com/search"


async def _search_duckduckgo(query: str, max_results: int = 10) -> List[Dict]:
    """Direct DuckDuckGo HTML search as fallback."""
    results = []
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                DUCKDUCKGO_HTML,
                data={"q": query, "b": ""},
                timeout=15.0,
                follow_redirects=True,
            )
            if resp.status_code == 200:
                # Parse HTML results
                html = resp.text
                # Extract result blocks
                result_pattern = re.compile(
                    r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
                    r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
                    re.DOTALL
                )
                for match in result_pattern.finditer(html)[:max_results]:
                    url, title, snippet = match
                    # Clean HTML tags
                    title = re.sub(r'<[^>]+>', '', title).strip()
                    snippet = re.sub(r'<[^>]+>', '', snippet).strip()
                    # Decode URL
                    if 'uddg=' in url:
                        url = url.split('uddg=')[1].split('&')[0]
                    results.append({
                        "url": url,
                        "title": title,
                        "content": snippet,
                        "engine": "duckduckgo"
                    })
    except Exception as e:
        print(f"DuckDuckGo search error: {e}")
    return results


async def _search_google(query: str, max_results: int = 10) -> List[Dict]:
    """Direct Google search as fallback (limited - may be blocked)."""
    results = []
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                GOOGLE_SEARCH,
                params={"q": query, "num": max_results},
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
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
    Uses SearXNG as primary, falls back to direct search if unavailable.
    
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
    
    # Fallback to direct search
    print("Using fallback search providers...")
    all_results = []
    
    # Try DuckDuckGo (most reliable fallback)
    ddg_results = await _search_duckduckgo(query)
    all_results.extend(ddg_results)
    
    # If not enough results, try Google
    if len(all_results) < 5:
        google_results = await _search_google(query)
        all_results.extend(google_results)
    
    # Deduplicate by URL
    seen = set()
    unique_results = []
    for r in all_results:
        url = r.get("url", "")
        if url not in seen:
            seen.add(url)
            unique_results.append(r)
    
    return unique_results


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
