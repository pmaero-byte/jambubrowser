"""
Shadow Browser - Autonomous Background Surfing
===============================================
Low-resource background browsing agent that autonomously
explores the web while idle, building a local knowledge base
tailored to user interests.
"""

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


@dataclass
class InterestTopic:
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
    url: str
    depth: int
    source_url: str
    topic: str
    priority: int
    discovered_at: float = field(default_factory=time.time)


class URLFrontier:
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
        self._frontier = URLFrontier()
        self._interests: List[InterestTopic] = list(DEFAULT_INTERESTS)
        self._running = False
        self._http_client: Optional[httpx.AsyncClient] = None
        self._pages_crawled = 0
        self._pages_indexed = 0
        self._lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = make_async_client(
                headers={"User-Agent": self.USER_AGENT},
                timeout=15.0, follow_redirects=True, max_redirects=3)
        return self._http_client

    def add_interest(self, topic: InterestTopic):
        self._interests.append(topic)
        for url in topic.seed_urls:
            self._frontier.add(URLNode(url=url, depth=0, source_url='', topic=topic.name, priority=topic.priority))

    def remove_interest(self, name: str):
        self._interests = [i for i in self._interests if i.name != name]

    def get_interests(self) -> List[dict]:
        return [{'name': i.name, 'keywords': i.keywords, 'priority': i.priority,
                 'urls_discovered': i.urls_discovered, 'urls_crawled': i.urls_crawled}
                for i in self._interests]

    async def seed_from_existing(self):
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
            pass

    async def _extract_links(self, html: str, base_url: str, topic: InterestTopic) -> List[URLNode]:
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
        return {'running': self._running, 'frontier_size': self._frontier.size(),
                'pages_crawled': self._pages_crawled, 'pages_indexed': self._pages_indexed,
                'interests': self.get_interests()}

    def stop(self):
        self._running = False

    async def close(self):
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
