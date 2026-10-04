"""Simulation-compute MCP tools.

Decentralised simulation-compute quotes, submits and jobs.

Tools are plain async functions here; ``register(mcp)`` applies FastMCP's
decorator to each one, so a tool can be read, imported and unit-tested
without an MCP server in the loop.
"""
from __future__ import annotations

from backend.mcp_tools import _shared


async def simulation_quote(
    module: str, kind: str = "native", steps: int = 0, seed: int = 0,
    replicates: int = 1,
) -> str:
    """
    Price a simulation job and show the verification tier its value implies.

    Nothing is dispatched — this is the pre-flight check. Use it to learn
    what a job costs and whether it will be replicated before paying for it.

    Args:
        module: Simulation module to run (e.g. 'heat', 'fluid', 'solve')
        kind: Workload kind: 'wasm', 'inference', or 'native' (default native)
        steps: Simulation steps (0 = configured default)
        seed: Deterministic seed for reproducible runs
        replicates: How many independent nodes should run it (1-32)
    """
    payload = {"module": module, "kind": kind, "seed": seed, "replicates": replicates}
    if steps:
        payload["steps"] = steps
    result = await _shared.call_engine("POST", "/simulation/quote", payload, timeout=20.0)
    if "error" in result:
        return f"Simulation quote failed: {result['error']}"
    lines = [
        "# Simulation Quote\n",
        f"- Module: `{module}` ({result.get('spec', {}).get('kind', kind)})",
        f"- Spec hash: `{result.get('spec_hash', '?')[:16]}…` (frozen before dispatch)",
        f"- Work: {result.get('work_units')} units over {result.get('replicates')} replica(s)",
        f"- Cost: {result.get('dct')} DCT → {result.get('usdc')} USDC",
        f"- Required tier: **{result.get('tier')}**",
        f"- Nodes available: {result.get('nodes_available')}",
        f"_{result.get('rate_note', '')}_",
    ]
    return "\n".join(lines)


async def simulation_submit(
    module: str, kind: str = "native", steps: int = 0, seed: int = 0,
    replicates: int = 1, idempotency_key: str = "", queued: bool = False,
) -> str:
    """
    Run a simulation across mesh nodes, verify the replicas agree, and settle.

    The spec is frozen and hashed before dispatch. Replicas are compared
    numerically; if they disagree the job is QUARANTINED and nothing is
    charged. Reusing an idempotency_key returns the original job instead of
    billing you twice.

    Args:
        module: Simulation module to run (e.g. 'heat', 'fluid', 'solve')
        kind: Workload kind: 'wasm', 'inference', or 'native' (default native)
        steps: Simulation steps (0 = configured default)
        seed: Deterministic seed for reproducible runs
        replicates: Independent nodes to run it on (1-32)
        idempotency_key: Reuse to make retries safe (never double-charges)
        queued: Enqueue and let the durable worker settle it later; the job
            comes back QUEUED and can be polled with simulation_jobs
    """
    payload = {"module": module, "kind": kind, "seed": seed, "replicates": replicates}
    if steps:
        payload["steps"] = steps
    if idempotency_key:
        payload["idempotency_key"] = idempotency_key
    if queued:
        payload["queued"] = True
    result = await _shared.call_engine("POST", "/simulation/submit", payload, timeout=300.0)
    if "error" in result:
        return f"Simulation failed: {result['error']}"

    status = result.get("status", "?")
    lines = [f"# Simulation — {status}\n"]
    lines.append(f"- Job: `{result.get('id')}`")
    lines.append(f"- Spec hash: `{result.get('spec_hash', '?')[:16]}…`")
    lines.append(f"- Nodes: {', '.join(result.get('nodes_used') or []) or '—'}")
    lines.append(f"- Charged: {result.get('charged_dct')} DCT")

    verification = result.get("verification") or {}
    if verification:
        lines.append(
            f"- Replica verdict: **{verification.get('verdict')}** "
            f"({verification.get('replicas_compared')} compared)"
        )
        if verification.get("agreeing"):
            lines.append(f"  - agreed: {', '.join(verification['agreeing'])}")
        if verification.get("diverging"):
            lines.append(
                f"  - diverged: {', '.join(verification['diverging'])} "
                "(minority excluded from the result)"
            )
        if verification.get("disputed"):
            lines.append(
                f"  - disputed: {', '.join(verification['disputed'])} "
                "(no majority, nobody blamed)"
            )
        if verification.get("max_deviation_path"):
            lines.append(
                f"- Worst deviation: {verification.get('max_rel_deviation')} "
                f"at `{verification.get('max_deviation_path')}`"
            )
    if result.get("idempotent_replay"):
        lines.append("- (idempotent replay — returned the stored job, no new charge)")
    if result.get("error"):
        lines.append(f"- Reason: {result['error']}")

    for attempt in result.get("attempts") or []:
        if not attempt.get("ok"):
            lines.append(f"  - `{attempt['node_id']}` failed: {attempt.get('error')}")
    return "\n".join(lines)


async def simulation_nodes() -> str:
    """
    Fleet health and reputation for the simulation mesh.

    Health = can the node run right now (quarantined nodes recover on their
    own). Reputation = does it agree with the rest of the mesh. Nodes are
    listed in the order dispatch will actually use them.
    """
    result = await _shared.call_engine("GET", "/simulation/nodes", timeout=20.0)
    if "error" in result:
        return f"Simulation nodes failed: {result['error']}"
    nodes = result.get("nodes") or []
    lines = [
        f"# Simulation Nodes — {result.get('available', 0)}/"
        f"{result.get('count', 0)} available\n",
    ]
    for node in nodes:
        health = node.get("health") or {}
        rep = node.get("reputation") or {}
        mark = "OK " if health.get("available") else "OUT"
        score = rep.get("score")
        lines.append(
            f"- `{node.get('node_id')}` [{mark}] "
            f"reputation={'new' if score is None else f'{score:.2f}'} · "
            f"ok={health.get('successes', 0)} fail={health.get('failures', 0)}"
            + (f" · diverged={health['divergences']}" if health.get("divergences") else "")
        )
        if health.get("quarantined"):
            lines.append(
                f"  - quarantined after {health.get('consecutive_failures')} "
                f"consecutive failures: {health.get('last_error', '')[:80]}"
            )
    if not nodes:
        lines.append("(no nodes registered)")
    lines.append(f"\n_{result.get('note', '')}_")
    return "\n".join(lines)


async def simulation_jobs(limit: int = 20, status: str = "") -> str:
    """
    List simulation jobs with outcomes and spend.

    Args:
        limit: Jobs to return (1-500)
        status: Filter by outcome: SETTLED, FAILED, or QUARANTINED
    """
    params: dict = {"limit": limit}
    if status:
        params["status"] = status
    result = await _shared.call_engine("GET", "/simulation/jobs", params, timeout=20.0)
    if "error" in result:
        return f"Simulation jobs failed: {result['error']}"
    jobs = result.get("jobs") or []
    totals = result.get("totals") or {}
    lines = [f"# Simulation Jobs ({len(jobs)})\n"]
    for job in jobs:
        lines.append(
            f"- `{job.get('status')}` {job.get('spec_hash', '')[:12]}… · "
            f"{job.get('replicas')} replica(s) · {job.get('charged_dct')} DCT"
        )
    if not jobs:
        lines.append("(no jobs yet)")
    lines.append(
        f"\n_Total: {totals.get('jobs')} jobs · {totals.get('chargedDct')} DCT "
        f"charged · {totals.get('unpaidJobs')} unpaid_"
    )
    return "\n".join(lines)



def register(mcp) -> None:
    """Register all 4 simulation tools with the server."""
    mcp.tool()(simulation_quote)
    mcp.tool()(simulation_submit)
    mcp.tool()(simulation_nodes)
    mcp.tool()(simulation_jobs)
