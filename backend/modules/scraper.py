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
        # Fallback: simple HTTP fetch via httpx. follow_redirects=True is
        # critical for arxiv.org which redirects http -> https; without it
        # we save the 301 HTML page as the "markdown" (which is what
        # /research was seeing before this fix).
        import httpx
        try:
            r = httpx.get(
                url,
                timeout=15,
                follow_redirects=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml",
                },
            )
            text = r.text
            # arXiv abstract pages: extract <blockquote class="abstract ...">
            # before stripping tags, so the synthesis step gets the
            # actual paper content instead of the entire page chrome.
            markdown = _extract_arxiv_abstract(text) or _html_to_text(text)
            return {
                "url": url,
                "markdown": markdown[:5000],
                "html": text,
                "metadata": {
                    "source": "httpx-fallback",
                    "status": r.status_code,
                    "final_url": str(r.url),
                },
            }
        except Exception as e:
            return {
                "url": url,
                "markdown": "",
                "html": "",
                "metadata": {"error": str(e)},
            }


def _extract_arxiv_abstract(html: str) -> str:
    """If the page is an arXiv abstract page, extract just the abstract
    block. Returns empty string if not an arXiv abstract page.

    arXiv abstract pages use <blockquote> wrapping a <span class="descriptor">
    "Abstract:" followed by the abstract text. The blockquote may have no
    class at all (the page also has other <blockquote>s for license etc.,
    so we look for the one that contains the Abstract descriptor).
    """
    if "arxiv.org" not in html.lower():
        return ""
    import re
    # Find every <blockquote>...</blockquote> and pick the one that contains
    # the Abstract descriptor span (the real abstract).
    for m in re.finditer(r"<blockquote[^>]*>(.*?)</blockquote>", html, re.DOTALL | re.IGNORECASE):
        body = m.group(1)
        if "Abstract:" in body or "abstract" in body.lower()[:200]:
            # Strip nested HTML, keep the text
            text = re.sub(r"<[^>]+>", " ", body)
            text = re.sub(r"\s+", " ", text).strip()
            # Drop the leading "Abstract:" label that arxiv adds
            text = re.sub(r"^Abstract:\s*", "", text, flags=re.IGNORECASE)
            return text
    return ""


def _html_to_text(html: str) -> str:
    """Crude HTML-to-text fallback when we can't use crawl4ai."""
    import re
    # Drop script/style blocks first
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


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
