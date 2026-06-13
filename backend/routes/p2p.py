"""P2P networking, federated query, and peer management endpoints."""
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
    from backend.modules.p2p_discovery import get_p2p_manager
    mgr = get_p2p_manager()
    info = mgr.get_node_info()
    return info


@router.post("/p2p/discover")
async def p2p_discover():
    """Trigger peer discovery on the LAN."""
    from backend.modules.p2p_discovery import get_p2p_manager
    mgr = get_p2p_manager()
    await mgr.discover()
    return {"status": "discovery_triggered"}


@router.get("/p2p/peers")
async def p2p_list_peers(online_only: bool = False):
    """List all known peers."""
    from backend.modules.p2p_discovery import get_p2p_manager
    mgr = get_p2p_manager()
    peers = mgr.list_peers(online_only=online_only)
    return {"peers": peers}


@router.post("/p2p/query")
async def p2p_query_peer(req: P2PQueryRequest):
    """Query a specific peer."""
    from backend.modules.p2p_discovery import get_p2p_manager
    mgr = get_p2p_manager()
    result = await mgr.query_peer(req.peer_id, req.query)
    return result


@router.post("/p2p/start-discovery")
async def p2p_start_discovery():
    """Start background peer discovery loop."""
    from backend.modules.p2p_discovery import get_p2p_manager
    mgr = get_p2p_manager()
    await mgr.start_discovery()
    return {"status": "started"}


@router.get("/p2p/stats")
async def p2p_stats():
    """Get P2P network statistics."""
    from backend.modules.p2p_discovery import get_p2p_manager
    mgr = get_p2p_manager()
    return mgr.get_stats()


# ── Peer (single-node view) ──


@router.get("/peer/info")
async def peer_info_handler():
    """Get peer info handler (for other nodes)."""
    from backend.modules.p2p_discovery import get_p2p_manager
    mgr = get_p2p_manager()
    return mgr.get_node_info()


@router.post("/peer/query")
async def peer_query_handler(request: dict):
    """Federated RAG query from a peer."""
    from backend.modules.p2p_discovery import get_p2p_manager
    mgr = get_p2p_manager()
    return await mgr.handle_peer_query(request)


@router.post("/peer/sync")
async def peer_sync(request: dict):
    """Anonymized research vector exchange with a peer."""
    from backend.modules.p2p_discovery import get_p2p_manager
    mgr = get_p2p_manager()
    return await mgr.handle_sync(request)


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
    from backend.modules.p2p_discovery import get_p2p_manager
    mgr = get_p2p_manager()
    await mgr.discover()
    return {"status": "discovery_triggered", "peers": mgr.list_peers()}


@router.get("/federated/stats")
async def federated_stats():
    """Get federated RAG statistics."""
    from backend.modules.federated_rag import get_federated_rag
    federated = get_federated_rag()
    return federated.get_stats()
