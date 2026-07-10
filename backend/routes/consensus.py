"""Consensus engine endpoints."""
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
    vote: str  # approve, reject, abstain
    voter: str = "mcp"


@router.post("/consensus/propose")
async def consensus_propose(req: ConsensusProposeRequest):
    """Create a proposal for multi-node consensus voting."""
    from backend.modules.consensus_engine import ConsensusEngine
    engine = ConsensusEngine()
    proposal = await engine.create_proposal(
        req.title, req.description or "",
        options=req.options or ["Yes", "No"],
        required_nodes=req.required_nodes,
    )
    return {"proposal_id": proposal.get("proposal", {}).get("id", ""), "status": "open", "success": proposal.get("success", True)}


@router.get("/consensus/list")
async def consensus_list(status: Optional[str] = None):
    """List all consensus proposals."""
    from backend.modules.consensus_engine import ConsensusEngine
    engine = ConsensusEngine()
    proposals = engine.list_proposals(status=status)
    return {"proposals": proposals}


@router.get("/consensus/proposal/{proposal_id}")
async def consensus_get(proposal_id: str):
    """Get a specific consensus proposal by ID."""
    from backend.modules.consensus_engine import ConsensusEngine
    engine = ConsensusEngine()
    proposal = engine.get_proposal(proposal_id)
    if not proposal:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Proposal not found: {proposal_id}")
    return proposal


@router.post("/consensus/vote")
async def consensus_vote(req: ConsensusVoteRequest):
    """Cast a vote on an existing proposal."""
    from backend.modules.consensus_engine import ConsensusEngine
    engine = ConsensusEngine()
    success = engine.cast_vote(req.proposal_id, req.voter, req.vote)
    return {"success": success}


@router.get("/consensus/tally/{proposal_id}")
async def consensus_tally(proposal_id: str):
    """Tally votes on a proposal."""
    from backend.modules.consensus_engine import ConsensusEngine
    engine = ConsensusEngine()
    return engine.tally_votes(proposal_id)


@router.get("/consensus/check/{proposal_id}")
async def consensus_check(proposal_id: str):
    """Check if consensus has been reached on a proposal."""
    from backend.modules.consensus_engine import ConsensusEngine
    engine = ConsensusEngine()
    reached = engine.check_consensus(proposal_id)
    return {"consensus_reached": reached}


@router.post("/consensus/close/{proposal_id}")
async def consensus_close(proposal_id: str):
    """Close a consensus proposal."""
    from backend.modules.consensus_engine import ConsensusEngine
    engine = ConsensusEngine()
    engine.close_proposal(proposal_id)
    return {"status": "closed"}
