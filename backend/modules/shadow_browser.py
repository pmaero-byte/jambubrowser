"""
Shadow Browser - Autonomous Background Surfing
===============================================
EXPERIMENTAL — nothing starts this on a schedule; it only runs when
explicitly triggered via `POST /shadow/start`, which is gated behind
JAMBU_ENABLE_EXPERIMENTAL=1 (returns 501 by default). The read-only
`/shadow/stats` and `/shadow/interests` endpoints remain available.
See docs/FEATURE_MAP.md.

Low-resource background browsing agent that autonomously
explores the web while idle, building a local knowledge base
tailored to user interests.
"""

import logging
import asyncio
import time
import random
import hashlib
import re
from typing import Optional, List, Dict, Set
from dataclasses import dataclass, field
from urllib.parse import urlparse
from collections import deque

import httpx

try:
    from backend.core.socks import make_async_client
except ImportError:
    make_async_client = httpx.AsyncClient

from backend.core.database import get_db_cursor

log = logging.getLogger("jambu.shadow_browser")


@dataclass
class InterestTopic:
    """One thing the shadow crawler is interested in.

    Carries its own crawl budget (``max_depth``) and running totals so
    ``GET /shadow-browser`` can show what a topic has actually produced
    without querying the crawl history.
    """

    name: str
    keywords: List[str]
    seed_urls: List[str]
    priority: int = 1
    max_depth: int = 3
    last_explored: float = 0
    urls_discovered: int = 0
    urls_crawled: int = 0


DEFAULT_INTERESTS = [
    InterestTopic(name="Technology", keywords=["ai", "machine learning", "llm", "gpu", "semiconductor", "quantum computing"],
                  seed_urls=["https://news.ycombinator.com", "https://arstechnica.com"], priority=3),
    InterestTopic(name="Science", keywords=["research", "breakthrough", "discovery", "study", "paper"],
                  seed_urls=["https://scholar.google.com", "https://arxiv.org"], priority=2),
    InterestTopic(name="Security", keywords=["vulnerability", "exploit", "patch", "cve", "zero-day", "breach"],
                  seed_urls=["https://thehackernews.com", "https://krebsonsecurity.com"], priority=4),
]


@dataclass
class URLNode:
    """A queued crawl target.

    ``depth`` is hops from the seed (0 = a seed URL itself) and is
    compared against the topic's ``max_depth`` before any link is
    extracted from the page.
    """

    url: str
    depth: int
    source_url: str
    topic: str
    priority: int
    discovered_at: float = field(default_factory=time.time)


class URLFrontier:
    """Priority queue of URLs waiting to be crawled.

    Priority 5 is the *highest*: ``pop`` drains 5 → 1, so a security
    topic outranks a science one. When the frontier is full it evicts
    from priority 1 first — the lowest-value work goes, not the
    oldest. Deduplication is by scheme+host+path, so ``/a`` and
    ``/a/`` are the same target.
    """

    def __init__(self, max_size: int = 10000):
        self._queues: Dict[int, deque] = {p: deque() for p in range(1, 6)}
        self._seen: Set[str] = set()
        self._max_size = max_size

    def add(self, node: URLNode):
        normalized = self._normalize(node.url)
        if normalized in self._seen:
            return
        if len(self._seen) >= self._max_size:
            for p in range(1, 6):
                if self._queues[p]:
                    evicted = self._queues[p].popleft()
                    self._seen.discard(self._normalize(evicted.url))
                    break
            else:
                return
        self._queues[node.priority].append(node)
        self._seen.add(normalized)

    def pop(self) -> Optional[URLNode]:
        for p in range(5, 0, -1):
            if self._queues[p]:
                node = self._queues[p].popleft()
                self._seen.discard(self._normalize(node.url))
                return node
        return None

    def size(self) -> int:
        return len(self._seen)

    def _normalize(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.hostname}{parsed.path.rstrip('/')}"


class ShadowBrowser:
    CRAWL_DELAY = 5.0
    MAX_PAGE_SIZE = 500 * 1024
    USER_AGENT = "Jambubrowser-Shadow/2.0 (Research Crawler; +https://jambubrowser.dev/bot)"

    def __init__(self):
        """Build an idle crawler: empty frontier, the three default topics, and
        a shared HTTP client that is created on first use.
        """

        self._frontier = URLFrontier()
        self._interests: List[InterestTopic] = list(DEFAULT_INTERESTS)
        self._running = False
        self._http_client: Optional[httpx.AsyncClient] = None
        self._pages_crawled = 0
        self._pages_indexed = 0
        self._lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        """The shared HTTP client, created lazily.

        One client for the whole crawler so connections are reused and
        every request carries the identifying User-Agent a research crawler
        should send. Redirects are capped at 3 so a redirect loop cannot
        pin the crawl loop.
        """

        if self._http_client is None:
            self._http_client = make_async_client(
                headers={"User-Agent": self.USER_AGENT},
                timeout=15.0, follow_redirects=True, max_redirects=3)
        return self._http_client

    def add_interest(self, topic: InterestTopic):
        """Register a topic and queue its seed URLs at depth 0.

        Adding a topic with the same name twice is allowed; both seed
        their URLs, and the frontier's dedup drops the duplicates.
        """

        self._interests.append(topic)
        for url in topic.seed_urls:
            self._frontier.add(URLNode(url=url, depth=0, source_url='', topic=topic.name, priority=topic.priority))

    def remove_interest(self, name: str):
        """Forget a topic by name.

        URLs already queued for it stay in the frontier but are popped with
        no matching topic, so they are crawled and not expanded — the only
        way to fully drain them is a restart.
        """

        self._interests = [i for i in self._interests if i.name != name]

    def get_interests(self) -> List[dict]:
        """The topic list as JSON-safe dicts, for the API response.
        """

        return [{'name': i.name, 'keywords': i.keywords, 'priority': i.priority,
                 'urls_discovered': i.urls_discovered, 'urls_crawled': i.urls_crawled}
                for i in self._interests]

    async def seed_from_existing(self):
        """Queue the root of up to 50 previously-indexed URLs.

        Seeds from the local document store rather than the public web, so
        the crawler starts from what this node already knows. A failure
        here (no documents table yet) is logged and skipped.
        """

        try:
            with get_db_cursor() as cursor:
                cursor.execute("SELECT DISTINCT url FROM documents ORDER BY RANDOM() LIMIT 50")
                rows = cursor.fetchall()
            for row in rows:
                parsed = urlparse(row['url'])
                if parsed.scheme and parsed.hostname:
                    root = f"{parsed.scheme}://{parsed.hostname}"
                    self._frontier.add(URLNode(url=root, depth=0, source_url=row['url'], topic="history", priority=2))
        except Exception:
            log.debug("history seed skipped", exc_info=True)

    async def _extract_links(self, html: str, base_url: str, topic: InterestTopic) -> List[URLNode]:
        """Find same-topic links on a crawled page.

        A link is kept only if its URL mentions one of the topic's
        keywords, and its priority rises with the number it mentions, so a
        page that is on-topic everywhere is crawled first. Capped at 20
        candidates per page: a crawler that follows everything finds
        nothing.
        """

        href_pattern = re.compile(r'href=["\'](https?://[^"\'\s]+)', re.I)
        raw_urls = href_pattern.findall(html)
        links = []
        for url in raw_urls[:20]:
            parsed = urlparse(url)
            if not parsed.hostname:
                continue
            url_text = url.lower()
            keyword_score = sum(1 for kw in topic.keywords if kw.lower() in url_text)
            if keyword_score > 0:
                priority = min(5, topic.priority + keyword_score)
                links.append(URLNode(url=url, depth=1, source_url=base_url, topic=topic.name, priority=priority))
        return links

    async def _crawl_page(self, url: str) -> Optional[str]:
        """Fetch a page and return its visible text, or None.

        None means "not worth indexing": a non-200, a non-HTML content
        type, or fewer than 100 characters of text (which is usually a
        navigation shell or an error page). HTML is truncated to
        MAX_PAGE_SIZE and stripped to text here so the caller never sees
        markup.
        """

        try:
            client = await self._get_client()
            resp = await client.get(url)
            if resp.status_code != 200:
                return None
            content_type = resp.headers.get('content-type', '')
            if 'text/html' not in content_type:
                return None
            html = resp.text[:self.MAX_PAGE_SIZE]
            text = re.sub(r'<[^>]+>', ' ', html)
            text = re.sub(r'\s+', ' ', text).strip()
            return text if len(text) > 100 else None
        except Exception:
            return None

    async def _index_page(self, url: str, text: str, topic_name: str):
        """Chunk a page, embed it, and store it in the search index.

        Embeddings are cached by chunk hash, so re-crawling a page costs
        no inference. If sentence-transformers is not installed the text is
        still stored (unembedded), so keyword search keeps working and only
        semantic search is lost.
        """

        try:
            from backend.core.database import smart_chunking
            from sentence_transformers import SentenceTransformer
            import numpy as np

            model = SentenceTransformer("all-MiniLM-L6-v2")
            with get_db_cursor() as cursor:
                for chunk in smart_chunking(text):
                    chash = hashlib.sha256(chunk.encode()).hexdigest()
                    cursor.execute("SELECT embedding FROM embedding_cache WHERE hash = ?", (chash,))
                    row = cursor.fetchone()
                    emb_bytes = row[0] if row else model.encode(chunk).astype(np.float32).tobytes()
                    if not row:
                        cursor.execute("INSERT OR IGNORE INTO embedding_cache VALUES (?, ?)", (chash, emb_bytes))
                    cursor.execute("INSERT INTO documents (url, text) VALUES (?, ?)", (url, f"[{topic_name}] {chunk}"))
                    cursor.execute("INSERT INTO vec_documents (id, embedding) VALUES (?, ?)", (cursor.lastrowid, emb_bytes))
            self._pages_indexed += 1
        except ImportError:
            with get_db_cursor() as cursor:
                cursor.execute("INSERT INTO documents (url, text) VALUES (?, ?)", (url, f"[{topic_name}] {text[:5000]}"))

    async def run_loop(self):
        """The crawl loop: pop, crawl, index, expand, sleep.

        CRAWL_DELAY between every page is deliberate politeness, not a
        performance setting. An empty frontier sleeps 30s rather than
        spinning. A failure on one page is swallowed after a 10s backoff
        so one bad host cannot end the crawl; ``stop()`` and cancellation
        are the only ways out.
        """

        self._running = True
        await self.seed_from_existing()
        for interest in self._interests:
            for url in interest.seed_urls:
                self._frontier.add(URLNode(url=url, depth=0, source_url='', topic=interest.name, priority=interest.priority))

        while self._running:
            try:
                node = self._frontier.pop()
                if node is None:
                    await asyncio.sleep(30)
                    continue

                topic = next((i for i in self._interests if i.name == node.topic), None)
                text = await self._crawl_page(node.url)

                if text:
                    self._pages_crawled += 1
                    await self._index_page(node.url, text, node.topic)

                    if topic and node.depth < topic.max_depth:
                        new_links = await self._extract_links(text, node.url, topic)
                        async with self._lock:
                            for link in new_links:
                                self._frontier.add(link)
                            if topic:
                                topic.urls_discovered += len(new_links)
                                topic.urls_crawled += 1
                                topic.last_explored = time.time()

                await asyncio.sleep(self.CRAWL_DELAY)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(10)

    def get_stats(self) -> dict:
        """Running flag, queue depth, totals and per-topic progress.
        """

        return {'running': self._running, 'frontier_size': self._frontier.size(),
                'pages_crawled': self._pages_crawled, 'pages_indexed': self._pages_indexed,
                'interests': self.get_interests()}

    def stop(self):
        """Ask the crawl loop to finish after the page it is on.
        """

        self._running = False

    async def close(self):
        """Stop the loop and close the HTTP client.

        Called from the engine lifespan, so a leaked client would keep the
        process alive on shutdown.
        """

        self.stop()
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None


_shadow: Optional[ShadowBrowser] = None


def get_shadow_browser() -> ShadowBrowser:
    global _shadow
    if _shadow is None:
        _shadow = ShadowBrowser()
    return _shadow
