"""DecentraCode Mesh MCP tools.

DecentraCode Mesh status, inference, models and settlement.

Tools are plain async functions here; ``register(mcp)`` applies FastMCP's
decorator to each one, so a tool can be read, imported and unit-tested
without an MCP server in the loop.
"""
from __future__ import annotations

import json
from backend.mcp_tools import _shared


async def dcm_status() -> str:
    """
    Check the local DecentraCode Mesh (DCM) node: reachability, inference
    runtimes, available models, and connected peers.
    """
    result = await _shared.call_engine("GET", "/dcm/status", timeout=20.0)
    if "error" in result:
        return f"DCM status failed: {result['error']}"
    if not result.get("reachable"):
        return (
            "DCM node unreachable. Start it with: "
            "cd decentracode/backend && npm start"
        )

    lines = ["# DecentraCode Mesh Node\n"]
    inf = result.get("inference_status") or {}
    if isinstance(inf, dict):
        # A top-level "error" only concerns the *default* runtime; a ready
        # secondary (MoE sidecar) still means the node can serve inference.
        top_ready = bool(inf.get("engine_ready", inf.get("ready")))
        moe = inf.get("moe") or {}
        moe_ready = bool(moe.get("ready") or moe.get("available"))
        if top_ready:
            lines.append(f"- Inference: {inf.get('runtime', 'runtime')} ready")
        elif moe_ready:
            lines.append(
                f"- Inference: {moe.get('runtime', 'MoE')} ready"
                + (f" ({inf.get('runtime', 'default')}: {str(inf.get('error'))[:60]})"
                   if inf.get("error") else "")
            )
        else:
            lines.append(
                f"- Inference: not ready — {str(inf.get('error') or 'no runtime')[:80]}"
            )
    elif inf.get("error"):
        lines.append(f"- Inference: unavailable — {inf['error']}")

    models = result.get("models")
    if isinstance(models, list):
        available = [m for m in models if m.get("status") in ("available", "ready")]
        lines.append(f"- Models: {len(available)}/{len(models)} available")

    mesh = result.get("mesh_status") or {}
    if isinstance(mesh, dict) and not mesh.get("error"):
        peers = mesh.get("peers")
        n = len(peers) if isinstance(peers, (list, dict)) else (peers or 0)
        lines.append(f"- Mesh peers: {n}")

    return "\n".join(lines)


async def dcm_infer(prompt: str, model: str = "", max_tokens: int = 64) -> str:
    """
    Run a prompt on the local DecentraCode Mesh (distributed inference).

    Args:
        prompt: The prompt to run
        model: Optional DCM model id (e.g. 'qwen1.5-moe-a2.7b'); empty uses the node default
        max_tokens: Maximum tokens to generate (1-4096)
    """
    payload = {"prompt": prompt, "max_tokens": max_tokens}
    if model:
        payload["model"] = model
    result = await _shared.call_engine("POST", "/dcm/infer", payload, timeout=180.0)
    if "error" in result:
        return f"DCM inference failed: {result['error']}"
    usage = result.get("usage") or {}
    return (
        f"{result.get('content', '').strip()}\n\n"
        f"_model: {result.get('model', '?')} · "
        f"{usage.get('completion_tokens', 0)} tokens · "
        f"{result.get('latency_ms', 0):.0f}ms_"
    )


async def dcm_models() -> str:
    """
    List the local DecentraCode Mesh model catalog with availability and
    runtime per model.
    """
    result = await _shared.call_engine("GET", "/dcm/models", timeout=20.0)
    if "error" in result:
        return f"DCM models failed: {result['error']}"
    models = result.get("models", [])
    if not models:
        return "DCM node returned no models."
    lines = [f"# DCM Models ({len(models)})\n"]
    for m in models:
        status = m.get("status", "?")
        mark = "✅" if status in ("available", "ready") else "·"
        lines.append(
            f"- {mark} `{m.get('id')}` — {m.get('name', '')} "
            f"({m.get('runtime', '?')}, {status})"
        )
    return "\n".join(lines)


async def dcm_earnings(did: str) -> str:
    """
    Show accrued DCT earnings for a provider DID on the local DCM node.

    Args:
        did: Provider DID (e.g. 'did:dcm:...' or the node's registered DID)
    """
    result = await _shared.call_engine("GET", f"/dcm/earnings/{did}", timeout=20.0)
    if "error" in result:
        return f"DCM earnings failed: {result['error']}"
    lines = [f"# DCT Earnings — {did}\n"]
    for key in ("pendingDct", "paidDct", "totalDct", "settledDct", "withdrawnDct"):
        if key in result:
            lines.append(f"- {key}: {result[key]}")
    if len(lines) == 1:
        lines.append(f"```json\n{json.dumps(result, indent=2)[:800]}\n```")
    return "\n".join(lines)


async def dcm_settlement_log(limit: int = 20) -> str:
    """
    Fetch the DCM node's hash-chained settlement receipts (billing audit
    trail: usage, inference-charge, simulation-charge, settlement).

    Args:
        limit: Number of receipts to fetch (1-500)
    """
    result = await _shared.call_engine(
        "GET", "/dcm/settlement-log", {"limit": limit}, timeout=20.0,
    )
    if "error" in result:
        return f"DCM settlement log failed: {result['error']}"
    entries = result.get("entries") or result.get("log") or []
    # DCM nests the chain verdict under "verification"; accept both shapes.
    verification = result.get("verification") or {}
    valid = result.get("valid", verification.get("valid"))
    head = f"# DCM Settlement Log — {len(entries)} receipt(s)"
    if valid is not None:
        head += f" · chain valid: {valid}"
    totals = verification.get("totals") or {}
    lines = [head + "\n"]
    if totals:
        lines.append(
            "- totals: "
            + ", ".join(f"{k}={v}" for k, v in totals.items())
        )
    for e in entries[:limit]:
        kind = e.get("kind") or e.get("type") or "?"
        amount = e.get("amountDct", e.get("amount", e.get("dct", "")))
        when = e.get("timestamp", "")
        lines.append(f"- `{kind}` {amount} — {when}")
    if not entries:
        lines.append("(no receipts yet)")
    return "\n".join(lines)



def register(mcp) -> None:
    """Register all 5 mesh_dcm tools with the server."""
    mcp.tool()(dcm_status)
    mcp.tool()(dcm_infer)
    mcp.tool()(dcm_models)
    mcp.tool()(dcm_earnings)
    mcp.tool()(dcm_settlement_log)
