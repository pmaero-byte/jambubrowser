"""
YouTube Intelligence
=====================
YouTube video analysis, transcript extraction, and metadata parsing.
Provides autonomous video content understanding without API keys.

Features:
- Transcript/subtitle extraction (auto-generated and manual)
- Video metadata (title, description, duration, channel)
- Chapter/timestamp extraction
- Basic content summarization via LLM
"""

import re
import json
import xml.etree.ElementTree as ET
import asyncio
from typing import Optional, List, Dict
from urllib.parse import urlparse, parse_qs
from dataclasses import dataclass, field

import httpx

try:
    from backend.core.socks import make_async_client
except ImportError:
    make_async_client = httpx.AsyncClient


@dataclass
class YouTubeTranscript:
    """A parsed YouTube transcript segment."""
    text: str
    start: float  # seconds
    duration: float  # seconds


@dataclass
class YouTubeVideo:
    """Complete YouTube video analysis result."""
    video_id: str
    url: str
    title: str = ""
    description: str = ""
    channel: str = ""
    duration_seconds: int = 0
    view_count: int = 0
    publish_date: str = ""
    thumbnail_url: str = ""
    chapters: List[Dict] = field(default_factory=list)
    transcript: List[YouTubeTranscript] = field(default_factory=list)
    transcript_text: str = ""
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            'video_id': self.video_id, 'url': self.url,
            'title': self.title, 'description': self.description[:500],
            'channel': self.channel, 'duration_seconds': self.duration_seconds,
            'view_count': self.view_count, 'publish_date': self.publish_date,
            'thumbnail_url': self.thumbnail_url,
            'chapters': self.chapters,
            'transcript_length': len(self.transcript_text),
            'transcript_preview': self.transcript_text[:1000],
            'summary': self.summary,
        }


class YouTubeAnalyzer:
    """
    Extracts and analyzes YouTube video content.
    Uses oEmbed API for metadata and transcript fetching
    without requiring YouTube API keys.
    """

    YOUTUBE_URL_PATTERN = re.compile(
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|'
        r'youtube\.com/v/|youtube\.com/shorts/)([a-zA-Z0-9_-]{11})',
        re.I
    )
    OEMBED_URL = "https://www.youtube.com/oembed"
    TRANSCRIPT_BASE = "https://youtubetranscript.com"

    def __init__(self):
        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = make_async_client(timeout=30.0)
        return self._http_client

    @staticmethod
    def extract_video_id(url: str) -> Optional[str]:
        """Extract YouTube video ID from any URL format."""
        match = YouTubeAnalyzer.YOUTUBE_URL_PATTERN.search(url)
        return match.group(1) if match else None

    async def get_metadata(self, video_id: str) -> dict:
        """
        Get video metadata using YouTube's oEmbed API.
        No API key required.
        """
        client = await self._get_client()
        try:
            resp = await client.get(
                self.OEMBED_URL,
                params={
                    'url': f'https://www.youtube.com/watch?v={video_id}',
                    'format': 'json',
                },
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return {}

    async def get_transcript(self, video_id: str) -> List[YouTubeTranscript]:
        """
        Fetch video transcript/subtitles.
        Uses the open transcript API (no key required).
        """
        client = await self._get_client()
        transcripts = []

        # Try English transcript first
        for lang in ['en', 'a.en', 'en-US', 'en-GB', '']:
            try:
                lang_suffix = f"?lang={lang}" if lang else ""
                resp = await client.get(
                    f"{self.TRANSCRIPT_BASE}/api/v1/transcripts/{video_id}{lang_suffix}"
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for segment in data.get('transcript', data.get('segments', [])):
                        transcripts.append(YouTubeTranscript(
                            text=segment.get('text', '').strip(),
                            start=float(segment.get('start', 0)),
                            duration=float(segment.get('duration', 0)),
                        ))
                    if transcripts:
                        break
            except Exception:
                continue

        return transcripts

    async def get_chapters(self, video_id: str) -> List[dict]:
        """Extract video chapters from description."""
        metadata = await self.get_metadata(video_id)
        description = metadata.get('description', '') if isinstance(metadata, dict) else ''

        chapters = []
        timestamp_pattern = re.compile(
            r'(\d{1,2}:\d{2}(?::\d{2})?)\s*[-–—]\s*(.+)', re.M
        )

        for match in timestamp_pattern.finditer(description):
            timestamp = match.group(1)
            title = match.group(2).strip()[:100]

            # Parse timestamp to seconds
            parts = timestamp.split(':')
            if len(parts) == 2:
                seconds = int(parts[0]) * 60 + int(parts[1])
            else:
                seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])

            chapters.append({
                'timestamp': timestamp,
                'seconds': seconds,
                'title': title,
            })

        return chapters

    async def analyze(self, url: str, llm_config: dict = None) -> YouTubeVideo:
        """
        Full video analysis: metadata, transcript, chapters, and summary.

        Args:
            url: YouTube video URL
            llm_config: Optional LLM config for summarization

        Returns:
            YouTubeVideo with complete analysis
        """
        video_id = self.extract_video_id(url)
        if not video_id:
            return YouTubeVideo(video_id="", url=url, title="Invalid YouTube URL")

        video = YouTubeVideo(video_id=video_id, url=url)

        # Fetch metadata
        metadata = await self.get_metadata(video_id)
        if isinstance(metadata, dict):
            video.title = metadata.get('title', '')
            video.channel = metadata.get('author_name', '')
            video.thumbnail_url = metadata.get('thumbnail_url', '')

        # Fetch transcript
        video.transcript = await self.get_transcript(video_id)
        video.transcript_text = ' '.join(s.text for s in video.transcript)

        # Extract chapters
        video.chapters = await self.get_chapters(video_id)

        # Generate summary if LLM is available and transcript exists
        if llm_config and video.transcript_text:
            try:
                video.summary = await self._summarize(
                    video.transcript_text[:4000], 
                    video.title,
                    llm_config,
                )
            except Exception:
                video.summary = video.transcript_text[:500]

        return video

    async def _summarize(self, transcript: str, title: str,
                           llm_config: dict) -> str:
        """Summarize transcript using LLM."""
        base_url = llm_config.get("baseUrl", "http://localhost:8080/v1")
        model_id = llm_config.get("modelId", "gemma-4-12b")
        api_key = llm_config.get("apiKey", "")

        prompt = (
            f"Summarize this YouTube video titled '{title}' based on its transcript. "
            f"Provide: 1) Main topic (1 sentence), 2) Key points (3-5 bullets), "
            f"3) Conclusion/takeaway (1 sentence).\n\n"
            f"Transcript:\n{transcript}"
        )

        client = await self._get_client()
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        try:
            resp = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json={
                    "model": model_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "max_tokens": 500,
                },
                timeout=20.0,
            )
            return resp.json()["choices"][0]["message"]["content"]
        except Exception:
            return transcript[:500]

    async def search_transcript(self, video_id: str, 
                                  query: str) -> List[YouTubeTranscript]:
        """Search within a video's transcript for specific content."""
        transcript = await self.get_transcript(video_id)
        query_lower = query.lower()
        return [s for s in transcript if query_lower in s.text.lower()]

    async def close(self):
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None


# Module-level singleton
_analyzer: Optional[YouTubeAnalyzer] = None


def get_youtube_analyzer() -> YouTubeAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = YouTubeAnalyzer()
    return _analyzer
