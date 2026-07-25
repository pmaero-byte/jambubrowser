"""P2P networking, federated query, and peer management endpoints.

Single-node in practice (see docs/FEATURE_MAP.md): LAN discovery works, but
with no peers on the network these endpoints return empty/local-only results.
"""
import asyncio
import json
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["p2p"])


class P2PQueryRequest(BaseModel):
    query: str
    peer_id: Optional[str] = None


@router.get("/p2p/info")
async def p2p_node_info():
    """Get this node's info for peer discovery."""
    from backend.modules.p2p_discovery import get_p2p
    return get_p2p().get_node_info()


@router.post("/p2p/discover")
async def p2p_discover():
    """Trigger peer discovery on the LAN."""
    from backend.modules.p2p_discovery import get_p2p
    p2p = get_p2p()
    discovered = await p2p.discover_peers()
    return {"status": "discovery_triggered", "discovered": len(discovered)}


@router.get("/p2p/peers")
async def p2p_list_peers(online_only: bool = False):
    """List all known peers."""
    from backend.modules.p2p_discovery import get_p2p
    peers = get_p2p().get_peers(online_only=online_only)
    return {"peers": peers}


@router.post("/p2p/query")
async def p2p_query_peer(req: P2PQueryRequest):
    """Query a specific peer."""
    from backend.modules.p2p_discovery import get_p2p
    result = await get_p2p().query_peer(req.peer_id, req.query)
    if result is None:
        raise HTTPException(status_code=404, detail="Peer unknown or offline")
    return result


@router.post("/p2p/start-discovery")
async def p2p_start_discovery():
    """Start background peer discovery loop."""
    from backend.modules.p2p_discovery import get_p2p
    p2p = get_p2p()
    if p2p._running:
        return {"status": "already_running"}
    asyncio.create_task(p2p.run_discovery_loop())
    return {"status": "started"}


@router.get("/p2p/stats")
async def p2p_stats():
    """Get P2P network statistics."""
    from backend.modules.p2p_discovery import get_p2p
    return get_p2p().get_stats()


# ── Peer (single-node view) ──


@router.get("/peer/info")
async def peer_info_handler():
    """Get peer info handler (for other nodes)."""
    from backend.modules.p2p_discovery import get_p2p
    return get_p2p().get_node_info()


@router.post("/peer/query")
async def peer_query_handler(request: dict):
    """Federated RAG query from a peer.

    Plain-query compat wrapper: wraps the plaintext query into the encrypted
    context envelope the federated handler expects, so a peer running
    `query_peer` gets anonymized local-vault results (subject to trust and
    rate limits) instead of raw data.
    """
    from backend.modules.federated_rag import get_federated_rag
    from backend.modules.p2p_discovery import get_p2p
    federated = get_federated_rag()
    context = federated._cipher.encrypt(
        json.dumps({"q": request.get("query", "")}).encode()
    ).decode()
    results = await federated.handle_federated_query(
        query_hash="",
        encrypted_context=context,
        requester_id=request.get("requester_id", ""),
    )
    return {"results": results, "responder_id": get_p2p().node_id}


@router.post("/peer/federated-query")
async def peer_federated_query_handler(request: dict):
    """Handle an incoming federated query (posted by a peer's FederatedRAG)."""
    from backend.modules.federated_rag import get_federated_rag
    return await get_federated_rag().handle_federated_query_request(request)


@router.post("/peer/sync")
async def peer_sync(request: dict):
    """Anonymized research vector exchange with a peer."""
    raise HTTPException(
        status_code=501,
        detail="Peer sync is not implemented yet; see docs/FEATURE_MAP.md",
    )


# ── Federated ──


@router.post("/federated/query")
async def federated_query(query: str, min_relevance: float = 0.5, max_results: int = 10):
    """Send anonymized query to trusted peers."""
    from backend.modules.federated_rag import get_federated_rag
    federated = get_federated_rag()
    results = await federated.query(query, min_relevance, max_results)
    return {"results": results}


@router.get("/peers/discover")
async def discover_peers():
    """Trigger P2P peer discovery on the LAN (legacy endpoint)."""
    from backend.modules.p2p_discovery import get_p2p
    p2p = get_p2p()
    await p2p.discover_peers()
    return {"status": "discovery_triggered", "peers": p2p.get_peers()}


@router.get("/federated/stats")
async def federated_stats():
    """Get federated RAG statistics."""
    from backend.modules.federated_rag import get_federated_rag
    federated = get_federated_rag()
    return federated.get_stats()
