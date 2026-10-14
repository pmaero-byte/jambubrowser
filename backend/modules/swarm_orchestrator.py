"""
Autonomous Multi-Tab Swarm Orchestrator.

Enables parallel worker sessions to crawl, inspect, and cross-reference multiple
web resources (e.g. Tamil Siddha manuscripts, technical papers, corporate sites)
concurrently. Aggregates findings into a unified Swarm Intelligence Dossier and
automatically syncs knowledge to the permanent Memory Bridge (:8095).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from backend.core.security import is_safe_file_url, is_safe_url
from backend.modules.antibot_detector import AntiBotDetector
from backend.modules.browser_agent_errors import SessionRefused
from backend.modules.browser_agent_service import get_browser_agent_service

log = logging.getLogger("jambu.swarm")

# ── Tamil Siddha & Scholarly Keywords for Entity Extraction ──────────────────

SIDDHA_KEYWORDS = [
    "அகத்தியர்", "ஞானவெட்டியான்", "ஞான வெட்டியான்", "போகர்", "சப்தகாண்டம்",
    "தேரையர்", "கொங்கணர்", "திருமூலர்", "திருமந்திரம்", "புலிப்பாணி",
    "ரசவாதம்", "முப்பு", "காயகற்பம்", "சுண்ணம்", "செந்தூரம்", "பற்பம்",
    "குளிகை", "தைலம்", "வாத சூத்திரம்", "வைத்திய காவியம்", "மூலிகை",
    "வல்லாரை", "கரிசலாங்கண்ணி", "கடுக்காய்", "நெல்லி", "தான்றிக்காய்",
    "Agathiyar", "Gnanavettiyan", "Bogar", "Theraiyar", "Thirumoolar",
    "Konganar", "Rasavatham", "Muрри", "Kayakalpam", "Siddha Medicine"
]


@dataclass
class SwarmWorkerResult:
    worker_id: str
    url: str
    status: str = "pending"  # pending | running | completed | failed | challenged
    title: str = ""
    ax_count: int = 0
    extracted_text: str = ""
    citations: List[str] = field(default_factory=list)
    siddha_entities: List[str] = field(default_factory=list)
    challenge_detected: bool = False
    challenge_provider: Optional[str] = None
    memory_synced: bool = False
    latency_ms: int = 0
    error: Optional[str] = None


@dataclass
class SwarmJob:
    swarm_id: str
    name: str
    targets: List[str]
    concurrency: int = 3
    auto_sync_memory: bool = True
    auto_checkpoint: bool = True
    status: str = "running"  # running | completed | cancelled | failed
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    workers: List[SwarmWorkerResult] = field(default_factory=list)
    cross_reference_dossier: Dict[str, Any] = field(default_factory=dict)
    total_tokens_ingested: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "swarm_id": self.swarm_id,
            "name": self.name,
            "targets": self.targets,
            "concurrency": self.concurrency,
            "auto_sync_memory": self.auto_sync_memory,
            "auto_checkpoint": self.auto_checkpoint,
            "status": self.status,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "workers": [asdict(w) for w in self.workers],
            "cross_reference_dossier": self.cross_reference_dossier,
            "total_tokens_ingested": self.total_tokens_ingested,
        }


class SwarmOrchestrator:
    """Manages concurrent web exploration swarms and cross-source synthesis."""

    def __init__(self):
        self._swarms: Dict[str, SwarmJob] = {}
        self._tasks: Dict[str, asyncio.Task] = {}

    def get_swarm(self, swarm_id: str) -> Optional[SwarmJob]:
        return self._swarms.get(swarm_id)

    def list_swarms(self) -> List[Dict[str, Any]]:
        return [s.to_dict() for s in self._swarms.values()]

    def cancel_swarm(self, swarm_id: str) -> bool:
        job = self._swarms.get(swarm_id)
        if not job or job.status != "running":
            return False
        job.status = "cancelled"
        job.completed_at = time.time()
        task = self._tasks.get(swarm_id)
        if task and not task.done():
            task.cancel()
        log.info("Cancelled swarm %s", swarm_id)
        return True

    async def start_swarm(
        self,
        name: str,
        targets: List[str],
        *,
        concurrency: int = 3,
        auto_sync_memory: bool = True,
        auto_checkpoint: bool = True,
    ) -> SwarmJob:
        """Launches a concurrent exploration swarm over a set of target URLs."""
        swarm_id = f"swarm-{uuid.uuid4().hex[:8]}"
        clean_targets = [t.strip() for t in targets if t and t.strip()]
        if not clean_targets:
            raise ValueError("No targets provided for swarm")

        job = SwarmJob(
            swarm_id=swarm_id,
            name=name or f"Swarm Exploration ({len(clean_targets)} targets)",
            targets=clean_targets,
            concurrency=max(1, min(concurrency, 8)),
            auto_sync_memory=auto_sync_memory,
            auto_checkpoint=auto_checkpoint,
        )
        self._swarms[swarm_id] = job

        # Launch background execution
        task = asyncio.create_task(self._execute_swarm(job))
        self._tasks[swarm_id] = task
        return job

    async def _execute_swarm(self, job: SwarmJob) -> None:
        log.info("Executing Swarm [%s]: '%s' with %d targets (concurrency=%d)",
                 job.swarm_id, job.name, len(job.targets), job.concurrency)
        semaphore = asyncio.Semaphore(job.concurrency)
        svc = get_browser_agent_service()

        async def _run_worker(target_url: str, idx: int) -> SwarmWorkerResult:
            async with semaphore:
                worker_res = SwarmWorkerResult(
                    worker_id=f"w-{idx:02d}",
                    url=target_url,
                    status="running",
                )
                job.workers.append(worker_res)
                t0 = time.time()

                session = None
                try:
                    # Validate URL
                    parsed = urlparse(target_url)
                    if parsed.scheme.lower() == "file":
                        if not is_safe_file_url(target_url):
                            worker_res.status = "failed"
                            worker_res.error = "file:// URL outside allowed workspace roots"
                            return worker_res
                    elif not is_safe_url(target_url, allow_private=False):
                        worker_res.status = "failed"
                        worker_res.error = "URL blocked by network safety policy"
                        return worker_res

                    # Open worker browser session
                    host = (parsed.hostname or "127.0.0.1").lower()
                    session = await svc.open(
                        allow_domains=[host, "127.0.0.1", "localhost"],
                        require_approval=False,
                        allow_private=True,
                    )

                    # Navigate
                    await session.navigate(target_url)
                    await asyncio.sleep(0.5)

                    # Anti-bot detection
                    challenge = await AntiBotDetector.detect(session.adapter)
                    if challenge.get("detected"):
                        worker_res.challenge_detected = True
                        worker_res.challenge_provider = challenge.get("provider")
                        worker_res.status = "challenged"
                        log.warning("Worker %s encountered anti-bot challenge: %s",
                                    worker_res.worker_id, challenge.get("provider"))

                    # Extract page state
                    state = await session.snapshot()
                    raw_text = state.get("text") or ""
                    title = state.get("title") or target_url

                    worker_res.title = title
                    worker_res.ax_count = len(state.get("elements") or [])
                    worker_res.extracted_text = raw_text[:5000]

                    # Extract Siddha entities & citations
                    identified_siddha = []
                    for kw in SIDDHA_KEYWORDS:
                        if kw.lower() in raw_text.lower() or kw.lower() in title.lower():
                            identified_siddha.append(kw)
                    worker_res.siddha_entities = list(set(identified_siddha))

                    # Parse URL links/citations on page
                    citations = [target_url]
                    for el in state.get("elements") or []:
                        href = el.get("href")
                        if href and href.startswith("http") and href not in citations:
                            citations.append(href)
                            if len(citations) >= 8:
                                break
                    worker_res.citations = citations

                    # Auto-sync to permanent Memory Bridge
                    if job.auto_sync_memory:
                        clean_md = f"# {title}\nSource: {target_url}\n\n{raw_text[:4000]}"
                        sync_res = await session.sync_to_greenwich(
                            title=f"[Swarm: {job.name}] {title}",
                            clean_markdown=clean_md,
                            url=target_url,
                            citations=citations,
                        )
                        worker_res.memory_synced = bool(sync_res and sync_res.get("ok"))
                        tokens = int(len(clean_md.split()) * 1.3)
                        job.total_tokens_ingested += tokens

                    worker_res.status = "completed" if not worker_res.challenge_detected else "challenged"
                    worker_res.latency_ms = int((time.time() - t0) * 1000)

                except Exception as exc:
                    worker_res.status = "failed"
                    worker_res.error = str(exc)[:200]
                    log.warning("Swarm worker %s failed on %s: %s",
                                worker_res.worker_id, target_url, exc)
                finally:
                    if session:
                        try:
                            await svc.close(session.id)
                        except Exception:
                            pass
                return worker_res

        # Run all workers
        tasks = [_run_worker(target, i + 1) for i, target in enumerate(job.targets)]
        await asyncio.gather(*tasks, return_exceptions=True)

        # Synthesize cross-reference dossier
        job.cross_reference_dossier = self._synthesize_cross_references(job)
        job.status = "completed"
        job.completed_at = time.time()
        log.info("Swarm [%s] completed. Ingested ~%d tokens across %d workers.",
                 job.swarm_id, job.total_tokens_ingested, len(job.workers))

        # Automated Memory Checkpoint Trigger
        if job.auto_checkpoint:
            try:
                from backend.modules.memory_checkpoint_scheduler import get_checkpoint_scheduler
                ckpt = await get_checkpoint_scheduler().trigger_checkpoint(
                    name=f"swarm_{job.swarm_id}_{int(time.time())}",
                    description=f"Auto-checkpoint after swarm '{job.name}' ({len(job.workers)} sources)",
                )
                log.info("Automated post-swarm checkpoint created: %s", ckpt.get("snapshot_name"))
            except Exception as e:
                log.warning("Post-swarm checkpoint trigger error: %s", e)

    def _synthesize_cross_references(self, job: SwarmJob) -> Dict[str, Any]:
        """Cross-correlates entities, verses, and claims found across workers."""
        entity_freq: Dict[str, int] = {}
        entity_sources: Dict[str, List[str]] = {}
        all_citations: List[str] = []

        completed_workers = [w for w in job.workers if w.status == "completed"]

        for w in completed_workers:
            for ent in w.siddha_entities:
                entity_freq[ent] = entity_freq.get(ent, 0) + 1
                if ent not in entity_sources:
                    entity_sources[ent] = []
                entity_sources[ent].append(w.url)
            for c in w.citations:
                if c not in all_citations:
                    all_citations.append(c)

        # Sort entities by frequency of appearance across distinct sources
        cross_verified = [
            {"entity": ent, "frequency": count, "sources": entity_sources[ent]}
            for ent, count in sorted(entity_freq.items(), key=lambda x: x[1], reverse=True)
        ]

        return {
            "total_sources_explored": len(job.workers),
            "successful_extractions": len(completed_workers),
            "cross_verified_entities_count": len(cross_verified),
            "cross_verified_entities": cross_verified,
            "unique_citations_gathered": len(all_citations),
            "citations_sample": all_citations[:15],
            "summary": (
                f"Swarm '{job.name}' analyzed {len(completed_workers)} sources. "
                f"Identified {len(cross_verified)} cross-verified entities and "
                f"{len(all_citations)} unique citations."
            ),
        }


# Singleton orchestrator
_swarm_orchestrator: Optional[SwarmOrchestrator] = None


def get_swarm_orchestrator() -> SwarmOrchestrator:
    global _swarm_orchestrator
    if _swarm_orchestrator is None:
        _swarm_orchestrator = SwarmOrchestrator()
    return _swarm_orchestrator
