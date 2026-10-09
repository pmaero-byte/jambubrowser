"""P2P networking, federated query, and peer management endpoints.

Peer discovery has two paths:

- ``POST /p2p/register`` — deterministic: probe a known URL and add the
  peer. This is what the two-node smoke test (and any NAT'd / routed
  network where UDP broadcast can't reach) uses.
- ``POST /p2p/discover`` — best-effort UDP broadcast on the LAN; may
  find nothing, and finding nothing is not an error.

The federation registry (``federation.json``, node_url per node) that
the consensus broadcaster reads is managed by ``/federation/*`` below.
"""
import asyncio
import json
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.security import is_safe_url
from backend.decentralized import mesh_state

router = APIRouter(tags=["p2p"])


class P2PQueryRequest(BaseModel):
    query: str
    peer_id: Optional[str] = None


class P2PRegisterRequest(BaseModel):
    node_url: str  # base URL of the peer engine, e.g. http://10.0.0.5:8001


class FederationRegisterRequest(BaseModel):
    node_id: str
    node_url: str
    public_key: Optional[str] = None


@router.get("/p2p/info")
async def p2p_node_info():
    """Get this node's info for peer discovery."""
    from backend.decentralized.p2p_discovery import get_p2p
    return get_p2p().get_node_info()


@router.post("/p2p/discover")
async def p2p_discover():
    """Trigger peer discovery on the LAN."""
    from backend.decentralized.p2p_discovery import get_p2p
    p2p = get_p2p()
    discovered = await p2p.discover_peers()
    return {"status": "discovery_triggered", "discovered": len(discovered)}


@router.post("/p2p/register")
async def p2p_register(req: P2PRegisterRequest):
    """Register a peer by probing its ``/peer/info`` endpoint.

    Deterministic alternative to UDP broadcast: the caller already knows
    the peer's URL (they passed it). The probe must succeed and return a
    node_id — we never add unresponsive or foreign-schema peers. Loopback
    and LAN peers are expected here, so ``allow_private=True`` is
    deliberate: this endpoint is the registration path for nodes the
    operator already chose to trust.
    """
    url = (req.node_url or "").strip().rstrip("/")
    if not is_safe_url(url, allow_private=True):
        raise HTTPException(status_code=400, detail="node_url must be an http(s) URL")
    from backend.decentralized.p2p_discovery import get_p2p
    p2p = get_p2p()
    import httpx as _httpx
    try:
        async with _httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{url}/peer/info")
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"peer probe to {url} failed: {exc}"
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"peer probe to {url} returned HTTP {resp.status_code}",
        )
    try:
        info = resp.json()
    except ValueError:
        raise HTTPException(status_code=502, detail="peer returned non-JSON /peer/info")
    node_id = info.get("node_id")
    if not node_id or not isinstance(node_id, str):
        raise HTTPException(status_code=502, detail="peer /peer/info missing node_id")
    if node_id == p2p.node_id:
        raise HTTPException(status_code=400, detail="cannot register this node as its own peer")
    # Trust the URL the caller could reach over the port it reached —
    # the advertised port is the mDNS/UDP service port, not HTTP.
    from urllib.parse import urlparse
    parsed = urlparse(url)
    info["ip_address"] = parsed.hostname or info.get("ip_address", "")
    info["port"] = parsed.port or (443 if parsed.scheme == "https" else 80)
    p2p._add_or_update_peer(info)
    peers = [p for p in p2p.get_peers() if p["node_id"] == node_id]
    return {"registered": True, "peer": peers[0] if peers else info}


@router.get("/p2p/peers")
async def p2p_list_peers(online_only: bool = False):
    """List all known peers."""
    from backend.decentralized.p2p_discovery import get_p2p
    peers = get_p2p().get_peers(online_only=online_only)
    return {"peers": peers}


@router.post("/p2p/query")
async def p2p_query_peer(req: P2PQueryRequest):
    """Query a specific peer."""
    from backend.decentralized.p2p_discovery import get_p2p
    result = await get_p2p().query_peer(req.peer_id, req.query)
    if result is None:
        raise HTTPException(status_code=404, detail="Peer unknown or offline")
    return result


@router.post("/p2p/start-discovery")
async def p2p_start_discovery():
    """Start background peer discovery loop."""
    from backend.decentralized.p2p_discovery import get_p2p
    p2p = get_p2p()
    if p2p._running:
        return {"status": "already_running"}
    asyncio.create_task(p2p.run_discovery_loop())
    return {"status": "started"}


@router.get("/p2p/stats")
async def p2p_stats():
    """Get P2P network statistics."""
    from backend.decentralized.p2p_discovery import get_p2p
    return get_p2p().get_stats()


# ── Peer (single-node view) ──


@router.get("/peer/info")
async def peer_info_handler():
    """Get peer info handler (for other nodes)."""
    from backend.decentralized.p2p_discovery import get_p2p
    return get_p2p().get_node_info()


@router.post("/peer/query")
async def peer_query_handler(request: dict):
    """Federated RAG query from a peer.

    Plain-query compat wrapper: wraps the plaintext query into the encrypted
    context envelope the federated handler expects, so a peer running
    `query_peer` gets anonymized local-vault results (subject to trust and
    rate limits) instead of raw data.
    """
    from backend.decentralized.federated_rag import get_federated_rag
    from backend.decentralized.p2p_discovery import get_p2p
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
    from backend.decentralized.federated_rag import get_federated_rag
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
    from backend.decentralized.federated_rag import get_federated_rag
    federated = get_federated_rag()
    results = await federated.query(query, min_relevance, max_results)
    return {"results": results}


@router.get("/peers/discover")
async def discover_peers():
    """Trigger P2P peer discovery on the LAN (legacy endpoint)."""
    from backend.decentralized.p2p_discovery import get_p2p
    p2p = get_p2p()
    await p2p.discover_peers()
    return {"status": "discovery_triggered", "peers": p2p.get_peers()}


@router.get("/federated/stats")
async def federated_stats():
    """Get federated RAG statistics."""
    from backend.decentralized.federated_rag import get_federated_rag
    federated = get_federated_rag()
    return federated.get_stats()


# ── Federation registry (the consensus broadcaster's peer list) ──


@router.post("/federation/register")
async def federation_register(req: FederationRegisterRequest):
    """Add or update a node in this node's federation registry.

    The registry is what ``ConsensusEngine`` reads when it broadcasts a
    proposal or forwards a vote — without it, a proposal never leaves
    the node that created it.
    """
    node_url = (req.node_url or "").strip().rstrip("/")
    if not is_safe_url(node_url, allow_private=True):
        raise HTTPException(status_code=400, detail="node_url must be an http(s) URL")
    if not req.node_id or not isinstance(req.node_id, str):
        raise HTTPException(status_code=400, detail="node_id is required")
    nodes = mesh_state.read_federation()
    entry = {"node_url": node_url}
    if req.public_key:
        entry["public_key"] = req.public_key
    nodes[req.node_id] = entry
    mesh_state.write_federation(nodes)
    return {"registered": True, "node_id": req.node_id, "nodes": len(nodes)}


@router.get("/federation/nodes")
async def federation_nodes():
    """List the nodes in this node's federation registry."""
    return {"nodes": mesh_state.read_federation()}


@router.delete("/federation/unregister/{node_id}")
async def federation_unregister(node_id: str):
    """Remove a node from the federation registry."""
    nodes = mesh_state.read_federation()
    if node_id not in nodes:
        raise HTTPException(status_code=404, detail=f"node {node_id} not registered")
    del nodes[node_id]
    mesh_state.write_federation(nodes)
    return {"unregistered": node_id, "nodes": len(nodes)}
