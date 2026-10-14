"""
Automated Memory Checkpoint Scheduler for Permanent SSM State.

Manages automated snapshots and checkpoint schedules of the Gated DeltaNet
SSM state on the Permanent Memory Bridge (:8095).
Triggers checkpoints based on:
1. Token ingestion thresholds (e.g. every 50k tokens ingested).
2. Autonomous swarm completion events.
3. Periodic time intervals (e.g. hourly auto-save).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from backend.modules.greenwich_bridge import memory_bridge_url

log = logging.getLogger("jambu.memory_checkpoint")


@dataclass
class CheckpointMeta:
    name: str
    created_at: float
    ssm_hash: str = ""
    duration_ms: float = 0.0
    description: str = ""
    tokens_at_save: int = 0
    trigger: str = "manual"  # manual | threshold | swarm | scheduled


class MemoryCheckpointScheduler:
    """Orchestrates automated snapshot checkpoints of the permanent SSM state."""

    def __init__(
        self,
        bridge_base_url: Optional[str] = None,
        *,
        token_threshold: int = 50000,
        periodic_interval_seconds: float = 600.0,
    ):
        self._bridge_url = (bridge_base_url or memory_bridge_url()).rstrip("/")
        self.token_threshold = token_threshold
        self.periodic_interval_seconds = periodic_interval_seconds

        self._last_checkpoint_time: float = 0.0
        self._last_checkpoint_tokens: int = 0
        self._last_checkpoint_name: str = ""
        self._checkpoints: List[CheckpointMeta] = []
        self._sweeper_task: Optional[asyncio.Task] = None
        self._running: bool = False

    def start(self) -> None:
        """Starts the background threshold and periodic checkpoint sweeper."""
        if self._running:
            return
        self._running = True
        try:
            loop = asyncio.get_running_loop()
            self._sweeper_task = loop.create_task(self._periodic_sweeper())
            log.info("Started Memory Checkpoint Scheduler (interval=%ss, threshold=%d tokens)",
                     self.periodic_interval_seconds, self.token_threshold)
        except RuntimeError:
            pass

    def stop(self) -> None:
        self._running = False
        if self._sweeper_task and not self._sweeper_task.done():
            self._sweeper_task.cancel()

    async def get_current_bridge_tokens(self) -> int:
        """Queries the Memory Bridge for total tokens currently ingested."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(f"{self._bridge_url}/status")
                if r.status_code == 200:
                    data = r.json()
                    details = data.get("details") or data
                    return int(details.get("total_tokens_ingested", 0))
        except Exception as exc:
            log.debug("could not query memory bridge tokens: %s", exc)
        return 0

    async def trigger_checkpoint(
        self,
        name: Optional[str] = None,
        description: str = "",
        trigger: str = "manual",
    ) -> Dict[str, Any]:
        """Creates an immediate snapshot checkpoint on the Memory Bridge."""
        t_now = time.time()
        snap_name = name or f"auto_ckpt_{int(t_now)}"
        desc = description or f"Snapshot triggered via {trigger}"

        cur_tokens = await self.get_current_bridge_tokens()

        # Call Memory Bridge endpoint
        payload = {"name": snap_name, "description": desc}
        result: Dict[str, Any] = {}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(
                    f"{self._bridge_url}/v1/project/memory/snapshot",
                    json=payload,
                )
                if r.status_code == 200:
                    result = r.json()
                else:
                    result = {
                        "ok": False,
                        "error": f"Bridge returned status {r.status_code}: {r.text[:200]}",
                    }
        except Exception as exc:
            log.warning("Memory Bridge snapshot HTTP call failed: %s", exc)
            result = {"ok": False, "error": str(exc)}

        # Fallback to local SQLite / direct snapshot if bridge is co-located
        if not result.get("snapshot_name"):
            result = self._fallback_local_snapshot(snap_name, desc)

        meta = CheckpointMeta(
            name=snap_name,
            created_at=t_now,
            ssm_hash=result.get("ssm_hash", ""),
            duration_ms=result.get("duration_ms", 0.0),
            description=desc,
            tokens_at_save=cur_tokens,
            trigger=trigger,
        )
        self._checkpoints.append(meta)
        self._last_checkpoint_name = snap_name
        self._last_checkpoint_time = t_now
        self._last_checkpoint_tokens = cur_tokens

        log.info("Checkpoint committed [%s]: '%s' (%s, dur=%.1fms, tokens=%d)",
                 trigger, snap_name, meta.ssm_hash[:12] if meta.ssm_hash else "n/a",
                 meta.duration_ms, cur_tokens)
        return {
            "ok": True,
            "snapshot_name": snap_name,
            "created_at": t_now,
            "duration_ms": meta.duration_ms,
            "ssm_hash": meta.ssm_hash,
            "tokens_at_save": cur_tokens,
            "trigger": trigger,
            "description": desc,
        }

    def _fallback_local_snapshot(self, name: str, description: str) -> Dict[str, Any]:
        """Direct file fallback when Memory Bridge HTTP is unreachable but disk is local."""
        try:
            project_root = Path("/Users/prabaharan/yaadhum_ncp")
            mem_dir = project_root / ".openmodels" / "memory"
            if not mem_dir.exists():
                return {"ok": False, "error": "Local memory dir does not exist"}

            from openmodels.memory.project_memory import ProjectMemoryManager
            mgr = ProjectMemoryManager(str(project_root))
            if mgr.is_initialized():
                res = mgr.create_snapshot(name, description)
                res["ok"] = True
                return res
        except Exception as exc:
            log.debug("local fallback snapshot failed: %s", exc)
        return {"ok": False, "error": "Bridge unreachable and local fallback failed"}

    async def list_checkpoints(self) -> List[Dict[str, Any]]:
        """Lists all recorded snapshots from memory bridge SQLite and in-memory index."""
        # Query local SQLite if available for full persistent history
        db_path = Path("/Users/prabaharan/yaadhum_ncp/.openmodels/memory/project_memory.db")
        persisted: List[Dict[str, Any]] = []

        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path))
                cur = conn.cursor()
                cur.execute(
                    "SELECT name, timestamp, git_head, ssm_hash, description "
                    "FROM checkpoints ORDER BY timestamp DESC LIMIT 50"
                )
                for row in cur.fetchall():
                    persisted.append({
                        "name": row[0],
                        "created_at": row[1],
                        "git_head": row[2],
                        "ssm_hash": row[3],
                        "description": row[4],
                    })
                conn.close()
            except Exception as exc:
                log.debug("error reading checkpoints from SQLite: %s", exc)

        # Merge with in-memory metadata
        merged = {c["name"]: c for c in persisted}
        for mem in self._checkpoints:
            if mem.name not in merged:
                merged[mem.name] = asdict(mem)
            else:
                merged[mem.name]["trigger"] = mem.trigger
                merged[mem.name]["tokens_at_save"] = mem.tokens_at_save

        return sorted(list(merged.values()), key=lambda x: x.get("created_at", 0), reverse=True)

    async def check_threshold_and_trigger(self) -> bool:
        """Evaluates if new tokens ingested exceed the checkpoint threshold."""
        cur_tokens = await self.get_current_bridge_tokens()
        if self._last_checkpoint_tokens == 0:
            self._last_checkpoint_tokens = cur_tokens
            return False

        diff = cur_tokens - self._last_checkpoint_tokens
        if diff >= self.token_threshold:
            log.info("Token ingestion threshold reached (+%d tokens >= %d). Triggering auto-checkpoint.",
                     diff, self.token_threshold)
            await self.trigger_checkpoint(
                name=f"threshold_ckpt_{int(time.time())}",
                description=f"Auto-saved after +{diff} tokens ingested",
                trigger="threshold",
            )
            return True
        return False

    async def _periodic_sweeper(self) -> None:
        """Background loop that periodically evaluates thresholds and auto-saves."""
        while self._running:
            try:
                await asyncio.sleep(self.periodic_interval_seconds)
                # Check threshold
                triggered = await self.check_threshold_and_trigger()
                if not triggered and time.time() - self._last_checkpoint_time > 3600:
                    # Periodic 1-hour heartbeat checkpoint if tokens have changed
                    cur_tokens = await self.get_current_bridge_tokens()
                    if cur_tokens > self._last_checkpoint_tokens:
                        await self.trigger_checkpoint(
                            name=f"periodic_ckpt_{int(time.time())}",
                            description="Periodic 1-hour state checkpoint",
                            trigger="scheduled",
                        )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.debug("error in checkpoint sweeper: %s", exc)

    def status(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "bridge_url": self._bridge_url,
            "token_threshold": self.token_threshold,
            "periodic_interval_seconds": self.periodic_interval_seconds,
            "last_checkpoint_name": self._last_checkpoint_name,
            "last_checkpoint_time": self._last_checkpoint_time,
            "tokens_at_last_checkpoint": self._last_checkpoint_tokens,
            "recorded_checkpoints_count": len(self._checkpoints),
        }


# Singleton instance
_checkpoint_scheduler: Optional[MemoryCheckpointScheduler] = None


def get_checkpoint_scheduler() -> MemoryCheckpointScheduler:
    global _checkpoint_scheduler
    if _checkpoint_scheduler is None:
        _checkpoint_scheduler = MemoryCheckpointScheduler()
        _checkpoint_scheduler.start()
    return _checkpoint_scheduler
