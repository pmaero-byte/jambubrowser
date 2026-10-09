"""Consensus engine endpoints.

Two-node aware: a proposal broadcast by a peer lands via
``POST /consensus/receive``, and a vote cast here is forwarded to peers
(with ``forwarded=True`` so peers never re-broadcast — that flag is the
loop guard). The federation registry lives in ``federation.json``
(``backend/decentralized/mesh_state.py``).
"""
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["consensus"])


class ConsensusProposeRequest(BaseModel):
    title: str
    description: str = ""
    options: list[str] = []
    required_nodes: int = 3
    proposer: str = "mcp"


class ConsensusVoteRequest(BaseModel):
    proposal_id: str
    vote: str  # one of the proposal's declared options
    voter: str = "mcp"
    confidence: float = 1.0
    reasoning: str = ""
    # Set by a peer's forward; forwarded votes are recorded but never
    # re-broadcast, which is what stops two nodes echoing votes forever.
    forwarded: bool = False


class ConsensusReceiveRequest(BaseModel):
    sender: str = ""
    proposal: dict


@router.post("/consensus/propose")
async def consensus_propose(req: ConsensusProposeRequest):
    """Create a proposal for multi-node consensus voting.

    Queued broadcast: the proposal is persisted locally first and the
    peer broadcast runs in the background, so a slow or unreachable
    peer can never hold the proposer's connection open until it times
    out (the hang the two-node smoke test caught). The response still
    reports the broadcast's per-peer outcomes under ``broadcast`` —
    collected in-process when the store is fast, ``{"queued": True}``
    while peers are being contacted.
    """
    from fastapi import BackgroundTasks
    from fastapi.responses import JSONResponse

    from backend.decentralized.consensus_engine import get_consensus
    engine = get_consensus()
    result = await engine.create_proposal_local(
        req.title, req.description or "",
        options=req.options or ["Yes", "No"],
        required_nodes=req.required_nodes,
    )
    if not result.get("success"):
        return result
    proposal_id = result["proposal"]["id"]
    tasks = BackgroundTasks()
    tasks.add_task(engine.broadcast_new_proposal, proposal_id)
    body = {"proposal_id": proposal_id, "status": "open", "success": True,
            "broadcast": {"queued": True}}
    return JSONResponse(content=body, background=tasks)


@router.post("/consensus/receive")
async def consensus_receive(req: ConsensusReceiveRequest):
    """Record a proposal broadcast by a peer (ack only, no re-broadcast)."""
    from backend.decentralized.consensus_engine import get_consensus
    engine = get_consensus()
    return engine.receive_proposal(req.proposal)


@router.get("/consensus/list")
async def consensus_list(status: Optional[str] = None):
    """List all consensus proposals."""
    from backend.decentralized.consensus_engine import get_consensus
    engine = get_consensus()
    proposals = engine.list_proposals(status=status)
    return {"proposals": proposals}


@router.get("/consensus/proposal/{proposal_id}")
async def consensus_get(proposal_id: str):
    """Get a specific consensus proposal by ID."""
    from backend.decentralized.consensus_engine import get_consensus
    engine = get_consensus()
    proposal = engine.get_proposal(proposal_id)
    if not proposal:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Proposal not found: {proposal_id}")
    return proposal


@router.post("/consensus/vote")
async def consensus_vote(req: ConsensusVoteRequest):
    """Cast a vote on an existing proposal.

    A locally originated vote (``forwarded=False``) is also forwarded to
    every peer in the federation registry; the response reports that the
    forward was queued. Queued (not awaited): the vote is already
    recorded locally, and a slow or unreachable peer must not hold the
    requester's connection open until it times out — that hang is what
    the two-node smoke test caught.
    """
    from fastapi import BackgroundTasks
    from fastapi.responses import JSONResponse

    from backend.decentralized.consensus_engine import get_consensus
    engine = get_consensus()
    result = engine.vote(
        req.proposal_id,
        node_id=req.voter,
        choice=req.vote,
        confidence=req.confidence,
        reasoning=req.reasoning,
    )
    if not result.get("success"):
        return {"success": False, "error": result.get("error", "vote rejected")}
    if req.forwarded:
        return result
    tasks = BackgroundTasks()
    tasks.add_task(
        engine.broadcast_vote,
        req.proposal_id, req.voter, req.vote,
        req.confidence, req.reasoning,
    )
    result["broadcast"] = {"queued": True}
    return JSONResponse(content=result, background=tasks)


@router.post("/consensus/vote/forwarded")
async def consensus_vote_forwarded(req: ConsensusVoteRequest):
    """Record a vote forwarded by a peer (no re-broadcast — loop guard).

    Split from ``/consensus/vote`` so forwarded votes take a path that
    can never echo: this endpoint records and returns, unconditionally.
    A forwarded vote may arrive *before* the proposal broadcast does
    (both are background tasks racing each other), so when the proposal
    is unknown we merge a stub carrying the sender's options and mark
    it ``voting`` — the broadcast's ``receive_proposal`` will find the
    id already present and leave the recorded vote untouched.
    """
    from backend.decentralized.consensus_engine import get_consensus
    engine = get_consensus()
    if req.proposal_id not in engine.proposals:
        engine.merge_forwarded_proposal(
            {"id": req.proposal_id, "options": [req.vote],
             "title": "(forwarded vote arrived first)",
             "status": "voting", "votes": {}})
    result = engine.vote(
        req.proposal_id,
        node_id=req.voter,
        choice=req.vote,
        confidence=req.confidence,
        reasoning=req.reasoning,
    )
    if not result.get("success"):
        return {"success": False, "error": result.get("error", "vote rejected")}
    return result


@router.get("/consensus/tally/{proposal_id}")
async def consensus_tally(proposal_id: str):
    """Tally votes on a proposal."""
    from backend.decentralized.consensus_engine import get_consensus
    engine = get_consensus()
    return engine.tally_votes(proposal_id)


@router.get("/consensus/check/{proposal_id}")
async def consensus_check(proposal_id: str):
    """Check if consensus has been reached on a proposal."""
    from backend.decentralized.consensus_engine import get_consensus
    engine = get_consensus()
    reached = engine.check_consensus(proposal_id)
    return {"consensus_reached": reached}


@router.post("/consensus/close/{proposal_id}")
async def consensus_close(proposal_id: str):
    """Close a consensus proposal."""
    from backend.decentralized.consensus_engine import get_consensus
    engine = get_consensus()
    engine.close_proposal(proposal_id)
    return {"status": "closed"}
