"""MeshPay settlement MCP tools.

MeshPay settlement audit and anchoring.

Tools are plain async functions here; ``register(mcp)`` applies FastMCP's
decorator to each one, so a tool can be read, imported and unit-tested
without an MCP server in the loop.
"""
from __future__ import annotations

from backend.mcp_tools import _shared


async def meshpay_audit(limit: int = 200) -> str:
    """
    Independently audit the DCM settlement receipt chain and preview the
    USDC payout plan. Replays the hash chain with MeshPay's own verifier
    and compares it to DCM's verdict.

    Args:
        limit: Receipts to audit (1-200)
    """
    result = await _shared.call_engine("GET", "/meshpay/audit", {"limit": limit}, timeout=30.0)
    if "error" in result:
        return f"MeshPay audit failed: {result['error']}"

    v = result.get("verification") or {}
    lines = ["# MeshPay Audit\n"]
    lines.append(f"- Receipts checked: {v.get('checked', 0)}")
    lines.append(f"- Chain valid (independent): {v.get('valid')}")
    if v.get("broken_at") is not None:
        lines.append(f"- Broken at: #{v['broken_at']} — {v.get('broken_reason')}")
    if result.get("agreement") is not None:
        lines.append(f"- Agrees with DCM's own verdict: {result['agreement']}")
    epochs = result.get("epochs") or []
    lines.append(f"- Epochs: {len(epochs)}")
    payout = result.get("payout") or {}
    totals = payout.get("totals") or {}
    if totals:
        lines.append(
            f"- Latest epoch plan: {totals.get('grossDct')} DCT gross → "
            f"{totals.get('usdc')} USDC net (fee {payout.get('protocol_fee_pct')})"
        )
        providers = payout.get("providers") or []
        for p in providers[:5]:
            lines.append(
                f"  - {p['nodeId']}: {p['netDct']} DCT → {p['usdc']} USDC"
            )
    return "\n".join(lines)


async def meshpay_anchor(epoch_index: int = -1, epoch_size: int = 50) -> str:
    """
    Anchor an epoch's Merkle receipt root (Solana memo program on the
    configured cluster, or the explicit mock transport). Returns the
    signature and explorer link when a real cluster is configured.

    Args:
        epoch_index: Epoch to anchor (-1 = latest)
        epoch_size: Receipts per epoch
    """
    result = await _shared.call_engine("POST", "/meshpay/anchor", {
        "epoch_index": epoch_index,
        "epoch_size": epoch_size,
        "limit": 200,
    }, timeout=60.0)
    if "error" in result:
        return f"MeshPay anchor failed: {result['error']}"
    lines = ["# MeshPay Anchor\n"]
    lines.append(f"- Epoch: {result.get('epoch', {}).get('index')} "
                 f"({result.get('epoch', {}).get('receipts')} receipts)")
    lines.append(f"- Root: `{result.get('root')}`")
    lines.append(f"- Transport: {result.get('transport')} "
                 f"(cluster: {result.get('cluster')})")
    lines.append(f"- Signature: `{result.get('signature')}`")
    if result.get("explorer_url"):
        lines.append(f"- Explorer: {result['explorer_url']}")
    else:
        lines.append("- (mock transport — no chain transaction; nothing to explore)")
    return "\n".join(lines)



def register(mcp) -> None:
    """Register all 2 meshpay tools with the server."""
    mcp.tool()(meshpay_audit)
    mcp.tool()(meshpay_anchor)
