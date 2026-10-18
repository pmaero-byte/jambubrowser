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
import json
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
    # A job the process died in the middle of. Without this, a restart leaves
    # pollers waiting forever on a job that will never advance again.
    INTERRUPTED = "interrupted"


TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.INTERRUPTED}

# Stored per-page text is capped: results are already returned to the caller, so
# the persisted copy exists only so a poll after a restart is not empty.
_STORED_TEXT_CHARS = 4000
_PROGRESS_WRITE_EVERY = 5


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


@dataclass
class BatchJob:
    """A batch scrape: a fixed list of URLs fetched concurrently.

    Unlike a crawl this never discovers new URLs, so it is bounded, predictable
    and the natural fit for "fetch these 200 pages I already know about".
    """

    id: str
    urls: List[str] = field(default_factory=list)
    status: JobStatus = JobStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    results: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[Dict[str, str]] = field(default_factory=list)
    error: Optional[str] = None
    _cancel: bool = False

    @property
    def total(self) -> int:
        return len(self.urls)

    @property
    def finished(self) -> bool:
        return self.status in TERMINAL

    def cancel(self) -> bool:
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
        out: Dict[str, Any] = {
            "id": self.id,
            "kind": "batch",
            "status": self.status.value,
            "total": self.total,
            "completed": len(self.results),
            "errors": self.errors,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "expires_at": self.expires_at(),
        }
        if self.error:
            out["error"] = self.error
        if include_pages:
            out["data"] = list(self.results)
        return out


def _batch_concurrency() -> int:
    try:
        return max(1, int(os.environ.get("JAMBU_BATCH_CONCURRENCY", "5")))
    except ValueError:
        return 5


# ---------------------------------------------------------------------------
# Durable store
# ---------------------------------------------------------------------------
#
# Job metadata is written on every state change so status survives a restart.
# Results are written once, at the end, with capped text. Every database call is
# best-effort: a persistence failure must degrade the durability guarantee, not
# break the crawl that is already running.

def _db():
    from backend.core.database import get_db

    return get_db()


def _dumps(value) -> str:
    try:
        return json.dumps(value, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return "null"


def _loads(raw, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _persist_upsert(job) -> None:
    """Write the job's current state. Never raises."""
    try:
        kind = "batch" if getattr(job, "urls", None) is not None else "crawl"
        target = _dumps({"urls": job.urls} if kind == "batch" else {"seed": job.seed})
        meta = _dumps({
            "errors": job.errors,
            "skipped_robots": getattr(job, "skipped_robots", []),
            "truncated": getattr(job, "truncated", False),
        })
        expired = job.expires_at()
        with _db() as conn:
            conn.execute(
                "INSERT INTO crawl_jobs (id, kind, target, status, total, completed, "
                "error, meta, created_at, started_at, completed_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "status=excluded.status, total=excluded.total, completed=excluded.completed, "
                "error=excluded.error, meta=excluded.meta, started_at=excluded.started_at, "
                "completed_at=excluded.completed_at, expires_at=excluded.expires_at",
                (
                    job.id, kind, target, job.status.value,
                    job.total if kind == "batch" else job.page_limit,
                    len(job.results) if kind == "batch" else len(job.pages),
                    job.error, meta,
                    job.created_at, job.started_at, job.completed_at, expired,
                ),
            )
            conn.commit()
    except Exception as e:  # noqa: BLE001
        log.debug("crawl-job persistence failed for %s: %r", job.id, e)


def _persist_results(job) -> None:
    """Write the final results. Never raises."""
    try:
        if getattr(job, "urls", None) is not None:
            results = list(job.results)
        else:
            results = [
                {
                    "url": p.url,
                    "depth": p.depth,
                    "status": p.status,
                    "markdown": (p.text or "")[:_STORED_TEXT_CHARS],
                    "receipt": p.receipt.to_dict(),
                }
                for p in job.pages
            ]
        with _db() as conn:
            conn.execute(
                "UPDATE crawl_jobs SET results = ?, completed = ? WHERE id = ?",
                (_dumps(results), len(results), job.id),
            )
            conn.commit()
    except Exception as e:  # noqa: BLE001
        log.debug("crawl-job result persistence failed for %s: %r", job.id, e)


def _row_to_job(row) -> Optional[Any]:
    """Reconstruct a job object from a stored row."""
    from backend.modules.webgraph import CrawlPage, FetchReceipt

    (jid, kind, target, status, total, completed, error, meta,
     results, created_at, started_at, completed_at, expires_at) = row

    meta = _loads(meta, {})
    results = _loads(results, [])
    target = _loads(target, {})
    try:
        job_status = JobStatus(status)
    except ValueError:
        job_status = JobStatus.INTERRUPTED

    common = dict(
        id=jid, status=job_status, created_at=created_at or 0.0,
        started_at=started_at, completed_at=completed_at, error=error,
        errors=meta.get("errors", []),
    )

    if kind == "batch":
        job = BatchJob(urls=target.get("urls", []), results=results, **common)
    else:
        pages = []
        for item in results:
            receipt = FetchReceipt(
                url=item.get("url", ""),
                fetched_at=item.get("receipt", {}).get("fetched_at", 0.0),
                status=item.get("receipt", {}).get("status", 0),
                content_sha256=item.get("receipt", {}).get("content_sha256", ""),
                bytes_in=item.get("receipt", {}).get("bytes_in", 0),
                bytes_out=item.get("receipt", {}).get("bytes_out", 0),
                removed_invisible=item.get("receipt", {}).get("removed_invisible", 0),
                injection_markers=item.get("receipt", {}).get("injection_markers", []),
            )
            pages.append(CrawlPage(
                url=item.get("url", ""), depth=item.get("depth", 0),
                status=item.get("status", 0), text=item.get("markdown", ""),
                receipt=receipt,
            ))
        job = CrawlJob(
            seed=target.get("seed", "https://"), page_limit=total or 0,
            pages=pages, skipped_robots=meta.get("skipped_robots", []),
            truncated=meta.get("truncated", False), **common,
        )
    return job


def load_job(job_id: str) -> Optional[Any]:
    """Read a job from the durable store. Never raises."""
    try:
        with _db() as conn:
            row = conn.execute(
                "SELECT id, kind, target, status, total, completed, error, meta, "
                "results, created_at, started_at, completed_at, expires_at "
                "FROM crawl_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return _row_to_job(row) if row else None
    except Exception as e:  # noqa: BLE001
        log.debug("crawl-job load failed for %s: %r", job_id, e)
        return None


def load_jobs(limit: int = 100) -> List[Any]:
    """Read recent jobs from the durable store. Never raises."""
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT id, kind, target, status, total, completed, error, meta, "
                "results, created_at, started_at, completed_at, expires_at "
                "FROM crawl_jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [j for j in (_row_to_job(r) for r in rows) if j is not None]
    except Exception as e:  # noqa: BLE001
        log.debug("crawl-job list failed: %r", e)
        return []


def purge_expired(now: Optional[float] = None) -> int:
    """Delete rows whose results have expired. Never raises."""
    now = now if now is not None else time.time()
    try:
        with _db() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM crawl_jobs WHERE expires_at IS NOT NULL AND expires_at < ?",
                (now,),
            )
            conn.commit()
            return cur.rowcount
    except Exception as e:  # noqa: BLE001
        log.debug("crawl-job purge failed: %r", e)
        return 0


def recover_orphaned() -> int:
    """Mark jobs the process died in the middle of as interrupted.

    Called once per process. Without it a restart leaves those jobs "running"
    forever and any poller waits on a job that will never move.
    """
    try:
        with _db() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE crawl_jobs SET status = ?, completed_at = ? "
                "WHERE status IN (?, ?)",
                (
                    JobStatus.INTERRUPTED.value, time.time(),
                    JobStatus.RUNNING.value, JobStatus.PENDING.value,
                ),
            )
            conn.commit()
            if cur.rowcount:
                log.info("marked %d interrupted crawl job(s) from a previous run", cur.rowcount)
            return cur.rowcount
    except Exception as e:  # noqa: BLE001
        log.debug("crawl-job recovery failed: %r", e)
        return 0


class CrawlJobManager:
    """In-process registry of background jobs (crawls and batches)."""

    def __init__(self, max_jobs: int = 200):
        self._jobs: Dict[str, Any] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._max_jobs = max_jobs
        self._sem: Optional[asyncio.Semaphore] = None
        self._last_purge = 0.0
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
        # Purging on every poll would turn a read into a write; once a minute is
        # plenty for expiry hygiene.
        if now - self._last_purge > 60.0:
            self._last_purge = now
            purge_expired(now)
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
        _persist_upsert(job)
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
            _persist_upsert(job)
            try:
                # Stream pages into the job so a poller sees progress.
                def on_page(page: CrawlPage) -> None:
                    job.pages.append(page)
                    # Throttled: a durable write per page would make a crawl
                    # database-bound for no benefit.
                    if len(job.pages) % _PROGRESS_WRITE_EVERY == 0:
                        _persist_upsert(job)

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
                # Terminal write: without this the durable row stays at
                # "running" and a restart reports the job as interrupted.
                _persist_upsert(job)
                _persist_results(job)
                self._fire(job.id)

    def _fire(self, job_id: str) -> None:
        ev = self._done_events.get(job_id)
        if ev is not None:
            ev.set()

    # -- batch -------------------------------------------------------------

    def submit_batch(
        self,
        urls: List[str],
        fetch: Callable,
        *,
        max_chars: int = 20000,
        concurrency: Optional[int] = None,
    ) -> "BatchJob":
        """Register and start a batch scrape over a fixed URL list."""
        self._reap()
        job = BatchJob(id=f"batch_{uuid.uuid4().hex[:16]}", urls=list(urls))
        self._jobs[job.id] = job
        self._done_events[job.id] = asyncio.Event()
        _persist_upsert(job)
        try:
            self._tasks[job.id] = asyncio.create_task(
                self._run_batch(job, fetch, max_chars, concurrency or _batch_concurrency())
            )
        except RuntimeError as e:
            self._jobs.pop(job.id, None)
            raise RuntimeError("batch jobs require a running event loop") from e
        return job

    async def _run_batch(self, job: "BatchJob", fetch: Callable, max_chars: int, concurrency: int) -> None:
        from backend.core.untrusted import sanitize_untrusted
        from backend.modules.webgraph import make_receipt

        async with self._semaphore():
            job.status = JobStatus.RUNNING
            job.started_at = time.time()
            _persist_upsert(job)
            gate = asyncio.Semaphore(max(1, concurrency))

            async def one(url: str) -> None:
                if job.should_stop:
                    return
                async with gate:
                    if job.should_stop:
                        return
                    try:
                        status, body = await fetch(url)
                    except Exception as e:  # noqa: BLE001 — one bad URL is not a bad batch
                        job.errors.append({"url": url, "error": str(e)})
                        return
                    cleaned = sanitize_untrusted(body or "", max_chars=max_chars)
                    job.results.append({
                        "url": url,
                        "status": status,
                        "markdown": cleaned.text,
                        "receipt": make_receipt(url, status, body or "", cleaned).to_dict(),
                    })

            try:
                await asyncio.gather(*(one(u) for u in job.urls))
                job.status = JobStatus.CANCELLED if job.should_stop else JobStatus.COMPLETED
            except asyncio.CancelledError:
                job.status = JobStatus.CANCELLED
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("batch job %s failed: %r", job.id, e)
                job.status = JobStatus.FAILED
                job.error = str(e)
            finally:
                if job.status is JobStatus.RUNNING:
                    job.status = JobStatus.COMPLETED
                job.completed_at = time.time()
                _persist_upsert(job)
                _persist_results(job)
                self._fire(job.id)

    # -- access ------------------------------------------------------------

    def get(self, job_id: str) -> Optional[Any]:
        """Look in memory first, then the durable store.

        The fallback is what makes a poll after a restart return data instead
        of a 404 for a job that genuinely happened.
        """
        self._reap()
        job = self._jobs.get(job_id)
        if job is not None:
            return job
        return load_job(job_id)

    def list_jobs(self) -> List[Any]:
        """Recent jobs, memory first, then durable records not in memory."""
        self._reap()
        merged: Dict[str, Any] = {j.id: j for j in self._jobs.values()}
        for stored in load_jobs():
            merged.setdefault(stored.id, stored)
        return sorted(merged.values(), key=lambda j: j.created_at, reverse=True)

    def cancel(self, job_id: str) -> Optional[Any]:
        job = self._jobs.get(job_id)
        if job is None:
            # A job that only exists durably is already finished or interrupted;
            # either way there is nothing running to cancel.
            return load_job(job_id)
        job.cancel()
        _persist_upsert(job)
        return job

    async def wait(self, job_id: str, timeout: float = 10.0) -> Optional[Any]:
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
        # Once per process: a job the previous run died inside would otherwise
        # sit at "running" forever and any poller would wait on it.
        recover_orphaned()
    return _MANAGER


def reset_crawl_jobs() -> None:
    global _MANAGER
    if _MANAGER is not None:
        _MANAGER.reset()
    _MANAGER = None


def clear_job_store() -> int:
    """Delete every durable job row. For tests and explicit maintenance."""
    try:
        with _db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM crawl_jobs")
            conn.commit()
            return cur.rowcount
    except Exception as e:  # noqa: BLE001
        log.debug("crawl-job store clear failed: %r", e)
        return 0
