"""
Crawl jobs — asynchronous, pollable crawls
==========================================

A crawl is not a request/response operation. At a few hundred pages it runs for
minutes, and an endpoint that blocks for minutes is unusable from an agent loop
(and will be killed by any proxy in front of it). So ``POST /v1/crawl`` returns
a job id immediately, the work runs in the background, and the caller polls.

Modelled on the shape Firecrawl uses: submit → poll → cancel, with results
expiring after a while rather than accumulating forever.

**Honest scope.** Jobs live in memory. They do not survive a restart, and they
are not shared between processes. That is adequate for a single-process local
engine and is stated rather than implied; the durable-queue pattern the
simulation module uses is the next step if crawls must outlive the process.

Concurrency is capped because each crawl is network-heavy and a caller could
otherwise start unlimited work with one loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from backend.modules.webgraph import CrawlConfig, CrawlPage, crawl

log = logging.getLogger("jambu.crawl_jobs")


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}


def _max_concurrent() -> int:
    try:
        return max(1, int(os.environ.get("JAMBU_CRAWL_CONCURRENCY", "3")))
    except ValueError:
        return 3


def _result_ttl() -> float:
    """How long a finished job's results stay pollable."""
    try:
        return max(60.0, float(os.environ.get("JAMBU_CRAWL_RESULT_TTL", "3600")))
    except ValueError:
        return 3600.0


@dataclass
class CrawlJob:
    id: str
    seed: str
    status: JobStatus = JobStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    page_limit: int = 25
    pages: List[CrawlPage] = field(default_factory=list)
    errors: List[Dict[str, str]] = field(default_factory=list)
    skipped_robots: List[str] = field(default_factory=list)
    truncated: bool = False
    error: Optional[str] = None
    _cancel: bool = False

    @property
    def finished(self) -> bool:
        return self.status in TERMINAL

    def cancel(self) -> bool:
        """Request cancellation. Returns False once the job has finished."""
        if self.finished:
            return False
        self._cancel = True
        return True

    @property
    def should_stop(self) -> bool:
        return self._cancel

    def expires_at(self) -> Optional[float]:
        if self.completed_at is None:
            return None
        return self.completed_at + _result_ttl()

    def to_dict(self, *, include_pages: bool = True) -> Dict[str, Any]:
        """Serialise for the API. ``data`` mirrors Firecrawl's field name."""
        out: Dict[str, Any] = {
            "id": self.id,
            "url": self.seed,
            "status": self.status.value,
            "total": self.page_limit,
            "completed": len(self.pages),
            "truncated": self.truncated,
            "skipped_robots": self.skipped_robots,
            "errors": self.errors,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "expires_at": self.expires_at(),
        }
        if self.error:
            out["error"] = self.error
        if include_pages:
            out["data"] = [
                {
                    "url": p.url,
                    "depth": p.depth,
                    "status": p.status,
                    "markdown": p.text,
                    "receipt": p.receipt.to_dict(),
                }
                for p in self.pages
            ]
        return out


class CrawlJobManager:
    """In-process registry of background crawls."""

    def __init__(self, max_jobs: int = 200):
        self._jobs: Dict[str, CrawlJob] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._max_jobs = max_jobs
        self._sem: Optional[asyncio.Semaphore] = None
        # Test seam: lets a caller await a job without polling.
        self._done_events: Dict[str, asyncio.Event] = {}

    # -- lifecycle ---------------------------------------------------------

    def _semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(_max_concurrent())
        return self._sem

    def _reap(self) -> None:
        """Drop expired finished jobs so the registry cannot grow forever."""
        now = time.time()
        for jid, job in list(self._jobs.items()):
            exp = job.expires_at()
            if exp is not None and now > exp:
                self._jobs.pop(jid, None)
                self._tasks.pop(jid, None)
                self._done_events.pop(jid, None)
        # Hard cap: drop the oldest finished jobs first.
        if len(self._jobs) > self._max_jobs:
            finished = sorted(
                (j for j in self._jobs.values() if j.finished),
                key=lambda j: j.created_at,
            )
            for job in finished[: len(self._jobs) - self._max_jobs]:
                self._jobs.pop(job.id, None)
                self._tasks.pop(job.id, None)
                self._done_events.pop(job.id, None)

    def submit(
        self,
        seed: str,
        fetch: Callable,
        config: CrawlConfig,
        *,
        sanitize=None,
    ) -> CrawlJob:
        """Register and start a crawl. Returns immediately."""
        self._reap()
        job = CrawlJob(id=f"crawl_{uuid.uuid4().hex[:16]}", seed=seed, page_limit=config.page_limit)
        self._jobs[job.id] = job
        self._done_events[job.id] = asyncio.Event()
        try:
            self._tasks[job.id] = asyncio.create_task(self._run(job, fetch, config, sanitize))
        except RuntimeError as e:  # no running loop
            self._jobs.pop(job.id, None)
            raise RuntimeError("crawl jobs require a running event loop") from e
        return job

    async def _run(self, job: CrawlJob, fetch: Callable, config: CrawlConfig, sanitize) -> None:
        async with self._semaphore():
            if job.should_stop:
                job.status = JobStatus.CANCELLED
                job.completed_at = time.time()
                self._fire(job.id)
                return
            job.status = JobStatus.RUNNING
            job.started_at = time.time()
            try:
                # Stream pages into the job so a poller sees progress.
                def on_page(page: CrawlPage) -> None:
                    job.pages.append(page)

                result = await crawl(
                    job.seed,
                    fetch,
                    config,
                    sanitize=sanitize,
                    on_page=on_page,
                    cancel=lambda: job.should_stop,
                )
                job.errors = result.errors
                job.skipped_robots = result.skipped_robots
                job.truncated = result.truncated
                job.status = JobStatus.CANCELLED if result.cancelled else JobStatus.COMPLETED
            except asyncio.CancelledError:
                job.status = JobStatus.CANCELLED
                raise
            except Exception as e:  # noqa: BLE001 — a job must not take the process down
                log.warning("crawl job %s failed: %r", job.id, e)
                job.status = JobStatus.FAILED
                job.error = str(e)
            finally:
                if job.status is JobStatus.RUNNING:
                    job.status = JobStatus.COMPLETED
                job.completed_at = time.time()
                self._fire(job.id)

    def _fire(self, job_id: str) -> None:
        ev = self._done_events.get(job_id)
        if ev is not None:
            ev.set()

    # -- access ------------------------------------------------------------

    def get(self, job_id: str) -> Optional[CrawlJob]:
        self._reap()
        return self._jobs.get(job_id)

    def list_jobs(self) -> List[CrawlJob]:
        self._reap()
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def cancel(self, job_id: str) -> Optional[CrawlJob]:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        job.cancel()
        return job

    async def wait(self, job_id: str, timeout: float = 10.0) -> Optional[CrawlJob]:
        """Await completion. Used by tests; the API path polls instead."""
        ev = self._done_events.get(job_id)
        if ev is None:
            return self._jobs.get(job_id)
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        return self._jobs.get(job_id)

    def reset(self) -> None:
        """Drop everything — used by tests."""
        for task in list(self._tasks.values()):
            if not task.done():
                task.cancel()
        self._jobs.clear()
        self._tasks.clear()
        self._done_events.clear()
        self._sem = None


_MANAGER: Optional[CrawlJobManager] = None


def get_crawl_jobs() -> CrawlJobManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = CrawlJobManager()
    return _MANAGER


def reset_crawl_jobs() -> None:
    global _MANAGER
    if _MANAGER is not None:
        _MANAGER.reset()
    _MANAGER = None
