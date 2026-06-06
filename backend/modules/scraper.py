"""
Internet Connection & Scraping Tools
====================================
This module is the 'Hands' of the browser. It actually goes out to
the internet, visits websites, and brings back the text.

It uses 'Crawl4AI' which is like a specialized robot that can 
avoid trackers and convert messy webpages into clean text (Markdown).
"""


def _get_crawl4ai():
    """Lazy import crawl4ai - only loaded when first used."""
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
        from crawl4ai.content_filter_strategy import BM25ContentFilter
        return AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, DefaultMarkdownGenerator, BM25ContentFilter
    except ImportError:
        raise ImportError(
            "crawl4ai not installed. Install with: pip install crawl4ai"
        )

async def get_sovereign_crawler(proxy: str = None):
    """
    Creates a new browser robot.
    If a proxy is provided (like Tor), the robot will use it to
    stay anonymous.
    """
    _, BrowserConfig, AsyncWebCrawler, _, _ = _get_crawl4ai()
    config = BrowserConfig(proxy=proxy, headless=True)
    crawler = AsyncWebCrawler(config=config)
    return crawler


async def scrape_url(url: str, user_query: str = "") -> dict:
    """Scrape a URL using crawl4ai (preferred) or httpx (fallback)."""
    try:
        crawler = await get_sovereign_crawler()
        result = await crawler.arun(url=url)
        if hasattr(result, 'success') and result.success:
            return {
                "url": url,
                "markdown": result.markdown or "",
                "html": result.html or "",
                "metadata": result.metadata or {},
            }
        else:
            return {
                "url": url,
                "markdown": "",
                "html": "",
                "metadata": {"error": getattr(result, 'error', 'Unknown error')},
            }
    except Exception:
        # Fallback: simple HTTP fetch via httpx
        import httpx
        try:
            r = httpx.get(url, timeout=15)
            return {
                "url": url,
                "markdown": r.text[:5000],
                "html": r.text,
                "metadata": {"source": "httpx-fallback", "status": r.status_code},
            }
        except Exception as e:
            return {
                "url": url,
                "markdown": "",
                "html": "",
                "metadata": {"error": str(e)},
            }


def get_scrape_config(user_query: str):
    """
    Sets up the 'How' for the scraping robot.
    Uses only valid CrawlerRunConfig parameters for crawl4ai 0.8.x.
    """
    _, _, CrawlerRunConfig, DefaultMarkdownGenerator, BM25ContentFilter = _get_crawl4ai()
    markdown_strategy = DefaultMarkdownGenerator(
        content_filter=BM25ContentFilter(user_query=user_query, bm25_threshold=0.4),
        options={
            "ignore_links": True,
            "ignore_images": True,
            "strip_comments": True
        }
    )
    return CrawlerRunConfig(
        markdown_generator=markdown_strategy,
        wait_until="networkidle",
        delay_before_return_html=1.0,
    )

def is_special_media(url: str) -> bool:
    """Checks if the URL needs special handling (YouTube, PDF, etc.)."""
    if "youtube.com" in url or "youtu.be" in url:
        return True
    if url.lower().endswith('.pdf'):
        return True
    return False

async def get_special_content(url: str) -> str:
    """Handle special media types: YouTube videos via the YouTube module."""
    if "youtube.com" in url or "youtu.be" in url:
        try:
            from backend.modules.youtube import get_youtube_analyzer
            analyzer = get_youtube_analyzer()
            video = await analyzer.analyze(url)
            if video.transcript_text:
                return f"# {video.title}\n\n{video.transcript_text[:5000]}"
            return f"# {video.title}\n\n[Transcript unavailable for this video]"
        except Exception:
            return f"# YouTube Video\n[Analysis not available for {url}]"
    return "Unsupported special media."
