"""
DecentraCode Mesh (DCM) REST client.

Thin async client for a local DCM node's HTTP API: inference status, model
catalog, mesh/peer state, join info, and the DCT billing ledger. Used by the
Jambubrowser engine, CLI, and tests to operate a DCM node as a first-class
backend (see ``backend/llm/providers/dcm.py`` for the LLM path).

Everything here maps to *existing* DCM endpoints; nothing needs to change on
the DCM side. Endpoints follow DCM's ``/api/...`` routes (see DCM
``backend/routes/{inference,network,billing,token}.js``).
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:3001"


class DcmError(RuntimeError):
    """Raised when a DCM endpoint returns a non-200 response."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"DCM {status_code}: {detail}")


class DcmClient:
    """Async client for a DCM node's REST API.

    Args:
        base_url: DCM backend root (default ``http://127.0.0.1:3001``).
        auth: optional ``Authorization`` header value (production DCM nodes
            require ``DID-Sig <did>:<signature>``).
        timeout: per-request timeout in seconds.
        transport: injectable ``httpx`` transport (tests).
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        auth: str = "",
        timeout: float = 10.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.auth = (auth or "").strip()
        self.timeout = timeout
        self._transport = transport

    # -- plumbing ------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.auth:
            headers["Authorization"] = self.auth
        return headers

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport)

    async def _get(
        self,
        path: str,
        timeout: Optional[float] = None,
        ok_statuses: tuple[int, ...] = (200,),
    ) -> Any:
        try:
            async with self._client() as client:
                r = await client.get(
                    f"{self.base_url}{path}",
                    headers=self._headers(),
                    timeout=timeout or self.timeout,
                )
        except httpx.ConnectError as e:
            raise DcmError(0, f"unreachable at {self.base_url}: {e}") from e
        except httpx.TimeoutException as e:
            raise DcmError(0, f"timeout on {path}: {e}") from e
        if r.status_code not in ok_statuses:
            raise DcmError(r.status_code, r.text[:300])
        try:
            return r.json()
        except ValueError as e:
            raise DcmError(r.status_code, f"non-JSON response: {e}") from e

    async def _post(
        self,
        path: str,
        payload: dict,
        timeout: Optional[float] = None,
        ok_statuses: tuple[int, ...] = (200,),
    ) -> Any:
        try:
            async with self._client() as client:
                r = await client.post(
                    f"{self.base_url}{path}",
                    headers={**self._headers(), "Content-Type": "application/json"},
                    json=payload,
                    timeout=timeout or self.timeout,
                )
        except httpx.ConnectError as e:
            raise DcmError(0, f"unreachable at {self.base_url}: {e}") from e
        except httpx.TimeoutException as e:
            raise DcmError(0, f"timeout on {path}: {e}") from e
        if r.status_code not in ok_statuses:
            raise DcmError(r.status_code, r.text[:300])
        try:
            return r.json()
        except ValueError as e:
            raise DcmError(r.status_code, f"non-JSON response: {e}") from e

    # -- inference -----------------------------------------------------------

    async def inference_status(self) -> dict:
        """``GET /api/inference/status`` — engine readiness + model availability.

        DCM answers **503 with a full state body** when the default runtime
        is unavailable (e.g. the candle binary isn't built) while other
        runtimes are still ready — the body is the status, so both 200 and
        503 are treated as valid answers.
        """
        return await self._get("/api/inference/status", ok_statuses=(200, 503))

    async def models(self) -> list[dict]:
        """``GET /api/models`` — DCM's model catalog (id, status, runtime)."""
        data = await self._get("/api/models")
        if isinstance(data, dict):
            return data.get("models") or []
        return data or []

    # -- mesh ----------------------------------------------------------------

    async def mesh_status(self) -> dict:
        """``GET /api/network/status`` — node id, peers, mesh state."""
        return await self._get("/api/network/status")

    async def join_info(self) -> dict:
        """``GET /api/network/join-info`` — LAN hosts + peer page ports.

        Used by the "Become a node" flow: returns the URLs a browser peer
        should open to join the mesh from this machine.
        """
        return await self._get("/api/network/join-info")

    async def peers(self) -> dict:
        """``GET /api/network/peers`` — connected libp2p peers."""
        return await self._get("/api/network/peers")

    async def simulate(
        self,
        problem_id: str,
        dofs: int,
        partitions: int = 1,
        iterations: Optional[int] = None,
    ) -> dict:
        """``POST /api/simulation/run`` — dispatch a distributed solve.

        ``dofs`` is the TOTAL grid-cell count (the node derives the per-side
        grid as sqrt(dofs)). The node bills the caller's DCT ledger on
        success — convergence-verified, so a non-converged solve costs only
        the setup fee — and answers 402 INSUFFICIENT_BALANCE when the ledger
        is short. Solves run synchronously, so the transport timeout is much
        longer than the request-scoped default.
        """
        payload: dict = {
            "problemId": problem_id,
            "dofs": int(dofs),
            "partitions": int(partitions),
        }
        if iterations is not None:
            payload["iterations"] = int(iterations)
        return await self._post(
            "/api/simulation/run",
            payload,
            timeout=300.0,
        )

    # -- realtime fabric (the lender mesh) -----------------------------------

    async def fabric_peers(self) -> dict:
        """``GET /api/realtime/peers`` — connected browser lenders.

        Distinct from :meth:`peers` (libp2p mesh membership): this is the
        roster of devices that will actually EXECUTE a submitted job.
        """
        return await self._get("/api/realtime/peers")

    async def submit_job(
        self,
        workload: str = "poisson-cg",
        dofs: Optional[int] = None,
        n: Optional[int] = None,
        tol: Optional[float] = None,
        max_iter: Optional[int] = None,
        max_dct: Optional[float] = None,
        timeout_ms: Optional[int] = None,
    ) -> dict:
        """``POST /api/jobs`` — submit to the realtime fabric.

        Answers 201 with the job id (and the escrow lock) or 402
        INSUFFICIENT_BALANCE. ``dofs`` is TOTAL cells; the node clamps the
        per-side grid to its own reference-run cap.
        """
        payload: dict = {"workload": workload}
        if dofs is not None:
            payload["dofs"] = int(dofs)
        if n is not None:
            payload["n"] = int(n)
        if tol is not None:
            payload["tol"] = float(tol)
        if max_iter is not None:
            payload["maxIter"] = int(max_iter)
        if max_dct is not None:
            payload["maxDct"] = float(max_dct)
        if timeout_ms is not None:
            payload["timeoutMs"] = int(timeout_ms)
        return await self._post("/api/jobs", payload, ok_statuses=(200, 201))

    async def job(self, job_id: str) -> dict:
        """``GET /api/jobs/:id`` — state, checkpoints, verdict, settlement."""
        return await self._get(f"/api/jobs/{job_id}")

    async def job_settlement(self, job_id: str) -> dict:
        """``GET /api/realtime/settlement/:jobId`` — escrow view + verdict."""
        return await self._get(f"/api/realtime/settlement/{job_id}")

    async def faucet(self, did: str, amount: float) -> dict:
        """``POST /api/realtime/faucet`` — dev funding for a caller DID.

        The node refuses this when DID auth is enforced.
        """
        return await self._post(
            "/api/realtime/faucet",
            {"did": did, "amount": float(amount)},
        )

    # -- billing / token -----------------------------------------------------

    async def earnings(self, did: str) -> dict:
        """``GET /api/billing/earnings/:did`` — accrued DCT for a provider DID."""
        return await self._get(f"/api/billing/earnings/{did}")

    async def settlement_log(self, limit: int = 50) -> dict:
        """``GET /api/billing/settlement-log`` — hash-chained settlement receipts.

        The receipt chain is DCM's auditability primitive; callers that care
        about integrity should re-verify the chain locally (see
        ``docs/CHANGELOG.md`` for the analogous Jambubrowser audit-log bug).
        """
        return await self._get(f"/api/billing/settlement-log?limit={int(limit)}")

    async def token_balance(self, did: str) -> dict:
        """``GET /api/token/balance/:did`` — DCT ledger balance."""
        return await self._get(f"/api/token/balance/{did}")

    # -- composite -----------------------------------------------------------

    async def health(self) -> bool:
        """True when the node answers ``/health``."""
        try:
            async with self._client() as client:
                r = await client.get(
                    f"{self.base_url}/health",
                    timeout=min(self.timeout, 3.0),
                )
                return r.status_code == 200
        except Exception:
            return False

    async def summary(self) -> dict:
        """One-shot node overview for CLI/UI: reachable, models, peers, DCT."""
        out: dict[str, Any] = {
            "base_url": self.base_url,
            "reachable": await self.health(),
        }
        if not out["reachable"]:
            return out
        for key, coro in (
            ("inference_status", self.inference_status()),
            ("mesh_status", self.mesh_status()),
        ):
            try:
                out[key] = await coro
            except DcmError as e:
                out[key] = {"error": str(e)}
        try:
            models = await self.models()
            out["models"] = [
                {"id": m.get("id"), "status": m.get("status")} for m in models
            ]
        except DcmError as e:
            out["models"] = {"error": str(e)}
        return out
