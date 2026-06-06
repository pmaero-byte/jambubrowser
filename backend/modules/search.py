"""
Metasearch & Discovery
======================
This module handles finding information across the internet.
It uses 'SearXNG', which is like a middle-man that asks Google, 
Bing, and DuckDuckGo for results without telling them who you are.
"""

import httpx
from typing import List

SEARXNG_URL = "http://localhost:8888/search"

async def multi_engine_search(query: str, engines: str = "google,bing,duckduckgo") -> List[dict]:
    """
    Asks many search engines at once for information about a topic.
    - query: What you want to find.
    - engines: Which engines to ask (Google is default).
    
    Returns: A list of result objects with 'url', 'title', and 'content'.
    """
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                SEARXNG_URL, 
                params={"q": query, "format": "json", "engines": engines}, 
                timeout=15.0
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("results", [])
            else:
                return []
        except Exception as e:
            print(f"Search error: {e}")
            return []

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
