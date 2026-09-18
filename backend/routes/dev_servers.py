"""Dev-server discovery routes — find and identify a local dev server."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from backend.modules import dev_server

router = APIRouter(prefix="/browser/dev-servers", tags=["dev-servers"])


@router.get("")
async def scan(host: str = "127.0.0.1", ports: Optional[str] = None):
    """Scan common dev ports and identify any running servers."""
    parsed = None
    if ports:
        parsed = tuple(int(p) for p in ports.split(",") if p.strip())
    found = await dev_server.scan_local_ports(host=host, ports=parsed)
    return {"host": host, "servers": found, "count": len(found)}


@router.get("/probe")
async def probe(url: str = Query(..., description="URL to probe")):
    """Probe a single URL: reachability, framework, title."""
    return await dev_server.probe_url(url)
