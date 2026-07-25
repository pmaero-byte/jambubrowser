"""
Consensus Engine
================

NOTE — single-node in practice: proposals and votes work locally (persisted
to a JSON file), but "multi-node" consensus only means anything when peers
from `p2p_discovery` participate; on a single-node install the local node
votes alone. See docs/FEATURE_MAP.md.

Multi-node consensus for governance decisions in the sovereign browser
mesh. Supports proposal creation, weighted voting, and tie-breaking by
average confidence.

Persistence
-----------
All proposals and votes are stored in
``~/.jambubrowser/consensus.json``. A small in-process event log keeps
the last 200 entries for auditability.

Status state-machine
--------------------
``pending`` → ``voting`` (broadcast) → ``decided`` (consensus reached)
or ``closed`` (manually finalised without consensus).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

try:
    from backend.core.socks import make_async_client
except ImportError:
    make_async_client = httpx.AsyncClient


DEFAULT_STORE_PATH = Path.home() / ".jambubrowser" / "consensus.json"
DEFAULT_TIMEOUT = 10.0
VALID_STATUSES = {"pending", "voting", "decided", "closed"}


class ConsensusEngine:
    """Multi-node consensus with weighted, confidence-aware voting.

    A *proposal* is a decision that requires the agreement of at least
    ``required_nodes`` voters. Each vote carries an optional confidence
    in ``[0.0, 1.0]`` and a free-text reasoning string.

    Tie-breaking rule
    -----------------
    When two or more options have the same top vote count, the option
    with the highest **average confidence** wins. If that is also tied,
    the proposal remains undecided and ``check_consensus`` returns
    ``reached=False``.
    """

    def __init__(
        self,
        store_path: Optional[Path] = None,
        node_id: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize the consensus engine.

        Args:
            store_path: Optional override for the JSON store.
            node_id: Identifier of *this* node; auto-generated if not
                provided. Used as the default voter when a caller
                doesn't supply one.
            timeout: HTTP timeout (seconds) for broadcast calls.
        """
        self.store_path = Path(store_path) if store_path else DEFAULT_STORE_PATH
        self.node_id = node_id or f"node-{uuid.uuid4().hex[:8]}"
        self.timeout = timeout

        # In-memory state.
        self.proposals: Dict[str, Dict[str, Any]] = {}
        self.event_log: List[Dict[str, Any]] = []

        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            self._load()
        except Exception as exc:  # pragma: no cover
            self.event_log.append(
                {"ts": time.time(), "event": "load_error", "error": str(exc)}
            )

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        if not self.store_path.exists():
            return
        try:
            raw = self.store_path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
        except (json.JSONDecodeError, OSError):
            return
        self.proposals = data.get("proposals", {}) or {}
        self.event_log = data.get("event_log", []) or []

    def _save(self) -> None:
        payload = {
            "node_id": self.node_id,
            "proposals": self.proposals,
            "event_log": self.event_log[-200:],
        }
        try:
            tmp = self.store_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self.store_path)
        except OSError as exc:
            self.event_log.append(
                {"ts": time.time(), "event": "save_error", "error": str(exc)}
            )

    # ------------------------------------------------------------------ #
    # Proposal lifecycle
    # ------------------------------------------------------------------ #
    async def create_proposal(
        self,
        title: str,
        description: str,
        options: List[str],
        required_nodes: int = 3,
    ) -> Dict[str, Any]:
        """Create a new decision proposal.

        Args:
            title: Short human-readable title.
            description: Long-form description / rationale.
            options: Two or more voting options. Must be unique within
                the proposal.
            required_nodes: Number of distinct voters required for the
                result to be considered authoritative.

        Returns:
            ``{"success": True, "proposal": {...}}`` on success.
        """
        if not title or not isinstance(title, str):
            return {"success": False, "error": "title must be a non-empty string"}
        if not isinstance(description, str):
            return {"success": False, "error": "description must be a string"}
        if not isinstance(options, list) or len(options) < 2:
            return {
                "success": False,
                "error": "options must be a list of at least 2 strings",
            }
        if not all(isinstance(o, str) and o.strip() for o in options):
            return {"success": False, "error": "every option must be a non-empty string"}
        if len(set(options)) != len(options):
            return {"success": False, "error": "options must be unique"}
        if not isinstance(required_nodes, int) or required_nodes < 1:
            return {"success": False, "error": "required_nodes must be a positive int"}

        proposal_id = f"prop-{uuid.uuid4().hex[:10]}"
        now = time.time()
        proposal = {
            "id": proposal_id,
            "title": title,
            "description": description,
            "options": list(options),
            "required_nodes": required_nodes,
            "status": "pending",
            "created_at": now,
            "created_by": self.node_id,
            "decided_at": None,
            "winner": None,
            "votes": {},  # node_id -> {choice, confidence, reasoning, ts}
        }
        self.proposals[proposal_id] = proposal
        self.event_log.append(
            {
                "ts": now,
                "event": "proposal_created",
                "proposal_id": proposal_id,
                "options": list(options),
            }
        )

        # Attempt to broadcast; failures are non-fatal.
        broadcast = await self._broadcast_proposal(proposal_id)
        if broadcast.get("success"):
            proposal["status"] = "voting"

        self._save()
        return {"success": True, "proposal": proposal, "broadcast": broadcast}

    def vote(
        self,
        proposal_id: str,
        node_id: str,
        choice: str,
        confidence: float = 1.0,
        reasoning: str = "",
    ) -> Dict[str, Any]:
        """Cast a vote on an open proposal.

        Each node may revise its vote while the proposal is ``pending``
        or ``voting``; re-voting overwrites the prior entry.

        Args:
            proposal_id: Target proposal.
            node_id: Voter identifier. Defaults to ``self.node_id``
                when omitted by callers.
            choice: Must be one of the proposal's declared options.
            confidence: Numeric weight in ``[0.0, 1.0]``.
            reasoning: Optional free-text justification.

        Returns:
            ``{"success": True, "vote": {...}}`` on success.
        """
        voter = node_id or self.node_id
        proposal = self.proposals.get(proposal_id)
        if not proposal:
            return {"success": False, "error": f"Proposal '{proposal_id}' not found"}
        if proposal["status"] not in ("pending", "voting"):
            return {
                "success": False,
                "error": f"Proposal is {proposal['status']}; voting is closed",
            }
        if choice not in proposal["options"]:
            return {
                "success": False,
                "error": f"choice must be one of {proposal['options']}",
            }
        try:
            confidence_f = float(confidence)
        except (TypeError, ValueError):
            return {"success": False, "error": "confidence must be numeric"}
        if not (0.0 <= confidence_f <= 1.0):
            return {"success": False, "error": "confidence must be in [0.0, 1.0]"}

        was_new = voter not in proposal["votes"]
        proposal["votes"][voter] = {
            "choice": choice,
            "confidence": confidence_f,
            "reasoning": reasoning or "",
            "ts": time.time(),
        }
        if proposal["status"] == "pending":
            proposal["status"] = "voting"

        self.event_log.append(
            {
                "ts": time.time(),
                "event": "vote_cast",
                "proposal_id": proposal_id,
                "voter": voter,
                "choice": choice,
                "confidence": confidence_f,
                "revised": not was_new,
            }
        )
        self._save()
        return {
            "success": True,
            "proposal_id": proposal_id,
            "vote": proposal["votes"][voter],
            "vote_count": len(proposal["votes"]),
        }

    def get_proposal(self, proposal_id: str) -> Dict[str, Any]:
        """Return a proposal with current vote tally.

        Returns ``{"success": False, ...}`` if the proposal is unknown.
        """
        proposal = self.proposals.get(proposal_id)
        if not proposal:
            return {"success": False, "error": f"Proposal '{proposal_id}' not found"}
        tally = self._tally(proposal)
        return {
            "success": True,
            "proposal": proposal,
            "tally": tally,
        }

    def tally_votes(self, proposal_id: str) -> Dict[str, Any]:
        """Tally votes and return counts + winner-with-tiebreak info.

        The result is purely informational — the proposal status is
        **not** mutated here. Use ``check_consensus`` to decide whether
        the bar has been met.
        """
        proposal = self.proposals.get(proposal_id)
        if not proposal:
            return {"success": False, "error": f"Proposal '{proposal_id}' not found"}
        return {"success": True, "proposal_id": proposal_id, **self._tally(proposal)}

    def check_consensus(self, proposal_id: str) -> Dict[str, Any]:
        """Determine whether the proposal has reached consensus.

        Consensus is reached when:
          * The number of distinct voters is ``>= required_nodes``, AND
          * A single option holds a strict majority of votes, AND
          * That majority holds even after averaging confidences.

        Ties are broken by the option with the highest **average
        confidence**; if that is also tied, consensus is *not* reached.

        On success, the proposal's status is advanced to ``decided``
        with the winning option stored.
        """
        proposal = self.proposals.get(proposal_id)
        if not proposal:
            return {"success": False, "error": f"Proposal '{proposal_id}' not found"}
        if proposal["status"] == "decided":
            return {
                "success": True,
                "reached": True,
                "winner": proposal.get("winner"),
                "vote_count": self._count_per_option(proposal),
                "confidence": self._avg_confidence(proposal, proposal.get("winner")),
                "status": proposal["status"],
            }

        tally = self._tally(proposal)
        vote_counts: Dict[str, int] = tally["vote_counts"]
        avg_confidences: Dict[str, float] = tally["avg_confidences"]
        total_votes = sum(vote_counts.values())

        if total_votes < proposal["required_nodes"]:
            return {
                "success": True,
                "reached": False,
                "reason": "not enough voters",
                "vote_count": vote_counts,
                "confidence": None,
                "voters": total_votes,
                "required": proposal["required_nodes"],
            }

        # Determine top count and which options share it.
        top_count = max(vote_counts.values()) if vote_counts else 0
        leaders = [opt for opt, c in vote_counts.items() if c == top_count]
        if top_count == 0:
            return {
                "success": True,
                "reached": False,
                "reason": "no votes",
                "vote_count": vote_counts,
                "confidence": None,
            }

        if len(leaders) > 1:
            # Tiebreak by avg confidence.
            best_conf = max(avg_confidences.get(o, 0.0) for o in leaders)
            tied_at_best = [
                o for o in leaders if avg_confidences.get(o, 0.0) == best_conf
            ]
            if len(tied_at_best) > 1:
                return {
                    "success": True,
                    "reached": False,
                    "reason": "tie between options at top confidence",
                    "vote_count": vote_counts,
                    "confidence": best_conf,
                    "tied": tied_at_best,
                }
            winner = tied_at_best[0]
        else:
            winner = leaders[0]

        # Require a *strict* majority if there are more than 2 options,
        # otherwise a plurality suffices (the spec says "majority wins"
        # so we treat plurality == majority when there are exactly 2
        # options, but require > 50% when there are 3+).
        if len(proposal["options"]) >= 3:
            threshold = total_votes / 2.0
            if vote_counts[winner] <= threshold:
                return {
                    "success": True,
                    "reached": False,
                    "reason": "no option has a strict majority",
                    "vote_count": vote_counts,
                    "confidence": avg_confidences.get(winner, 0.0),
                }

        proposal["status"] = "decided"
        proposal["winner"] = winner
        proposal["decided_at"] = time.time()
        self.event_log.append(
            {
                "ts": proposal["decided_at"],
                "event": "consensus_reached",
                "proposal_id": proposal_id,
                "winner": winner,
            }
        )
        self._save()
        return {
            "success": True,
            "reached": True,
            "winner": winner,
            "vote_count": vote_counts,
            "confidence": avg_confidences.get(winner, 0.0),
        }

    def list_proposals(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return all proposals, optionally filtered by status."""
        items = list(self.proposals.values())
        if status:
            if status not in VALID_STATUSES:
                return []
            items = [p for p in items if p.get("status") == status]
        # Newest first.
        items.sort(key=lambda p: p.get("created_at", 0), reverse=True)
        return items

    def close_proposal(self, proposal_id: str) -> Dict[str, Any]:
        """Manually close a proposal without consensus.

        The final tally is computed and recorded, but ``status`` becomes
        ``closed`` (not ``decided``) so callers can distinguish manual
        closure from a genuine consensus outcome.
        """
        proposal = self.proposals.get(proposal_id)
        if not proposal:
            return {"success": False, "error": f"Proposal '{proposal_id}' not found"}
        if proposal["status"] in ("decided", "closed"):
            return {
                "success": True,
                "proposal_id": proposal_id,
                "status": proposal["status"],
                "message": "Already finalised",
            }
        tally = self._tally(proposal)
        proposal["status"] = "closed"
        proposal["decided_at"] = time.time()
        self.event_log.append(
            {
                "ts": proposal["decided_at"],
                "event": "proposal_closed",
                "proposal_id": proposal_id,
            }
        )
        self._save()
        return {
            "success": True,
            "proposal_id": proposal_id,
            "status": "closed",
            "tally": tally,
        }

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    async def _broadcast_proposal(self, proposal_id: str) -> Dict[str, Any]:
        """Best-effort POST the new proposal to peer ``/consensus`` endpoints.

        Peers are read from a sidecar file (``federation.json``) when
        available; the engine does not own the federation registry
        directly, so we look it up lazily and tolerate its absence.
        """
        proposal = self.proposals.get(proposal_id)
        if not proposal:
            return {"success": False, "error": "proposal not found"}

        peer_urls = self._discover_peer_urls()
        if not peer_urls:
            # Nothing to broadcast to — still success.
            return {"success": True, "broadcast_to": 0, "note": "no peers discovered"}

        async with make_async_client(timeout=self.timeout) as client:
            results = []
            for url in peer_urls:
                try:
                    resp = await client.post(
                        url,
                        json={
                            "sender": self.node_id,
                            "proposal": proposal,
                        },
                    )
                    results.append(
                        {
                            "url": url,
                            "status": resp.status_code,
                            "ok": resp.status_code < 400,
                        }
                    )
                except Exception as exc:
                    results.append(
                        {"url": url, "status": None, "ok": False, "error": str(exc)}
                    )

        return {
            "success": True,
            "broadcast_to": len(peer_urls),
            "results": results,
        }

    def _discover_peer_urls(self) -> List[str]:
        """Read the federation store (if any) and return peer base URLs."""
        candidates = [
            Path.home() / ".jambubrowser" / "federation.json",
        ]
        urls: List[str] = []
        for path in candidates:
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            for node in (data.get("nodes") or {}).values():
                base = (node.get("node_url") or "").rstrip("/")
                if base:
                    urls.append(f"{base}/consensus")
        return urls

    def _validate_vote(self, proposal_id: str, node_id: str) -> bool:
        """True iff ``node_id`` has not yet voted on ``proposal_id``."""
        proposal = self.proposals.get(proposal_id)
        if not proposal:
            return False
        return node_id not in proposal["votes"]

    @staticmethod
    def _count_per_option(proposal: Dict[str, Any]) -> Dict[str, int]:
        counts = Counter(v["choice"] for v in proposal["votes"].values())
        # Ensure every declared option appears, even with 0 votes.
        for opt in proposal["options"]:
            counts.setdefault(opt, 0)
        return dict(counts)

    @staticmethod
    def _avg_confidence(
        proposal: Dict[str, Any], option: Optional[str]
    ) -> Optional[float]:
        if not option:
            return None
        confs = [
            v["confidence"]
            for v in proposal["votes"].values()
            if v["choice"] == option
        ]
        if not confs:
            return 0.0
        return round(sum(confs) / len(confs), 4)

    def _tally(self, proposal: Dict[str, Any]) -> Dict[str, Any]:
        vote_counts = self._count_per_option(proposal)
        avg_confidences = {
            opt: self._avg_confidence(proposal, opt) or 0.0
            for opt in proposal["options"]
        }
        total = sum(vote_counts.values())
        top_option = (
            max(vote_counts.items(), key=lambda kv: kv[1])[0] if vote_counts else None
        )
        return {
            "vote_counts": vote_counts,
            "avg_confidences": avg_confidences,
            "total_votes": total,
            "leading": top_option,
            "voters": list(proposal["votes"].keys()),
        }
