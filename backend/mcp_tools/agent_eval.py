"""Agent evaluation certificate MCP tools.

Agent evaluation certificates.

Tools are plain async functions here; ``register(mcp)`` applies FastMCP's
decorator to each one, so a tool can be read, imported and unit-tested
without an MCP server in the loop.
"""
from __future__ import annotations

from backend.mcp_tools import _shared


async def agent_eval_certify(suite: str, provider: str = "",
                             pass_threshold: float = 0.8) -> str:
    """
    Run an eval suite under a frozen spec and issue a signed certificate.
    The spec (task list + scoring + provider) is hashed before the run, so
    dropping failed tasks afterwards is detectable; verdicts are PASS, FAIL,
    INCONCLUSIVE (harness errors) or INVALID (coverage mismatch).

    Args:
        suite: Suite name, e.g. "smoke" (see the GET /eval/suites list)
        provider: LLM provider under test (empty = engine default)
        pass_threshold: Pass rate required for PASS (0-1)
    """
    result = await _shared.call_engine("POST", "/eval/certificates", {
        "suite": suite, "provider": provider or None,
        "pass_threshold": pass_threshold,
    }, timeout=600.0)
    if "error" in result:
        return f"Certification failed: {result['error']}"
    verdict = (result.get("payload") or {}).get("verdict") or {}
    summary = verdict.get("summary") or {}
    lines = [
        f"# Certificate #{result.get('id')} — {result.get('kind')}",
        f"- suite: {suite} | verdict: **{verdict.get('verdict')}**",
        f"- pass rate: {summary.get('pass_rate')} "
        f"({summary.get('passed')}/{summary.get('committed')} passed, "
        f"{summary.get('error')} errors)",
        f"- spec_hash: `{(result.get('payload') or {}).get('spec_hash')}`",
    ]
    for reason in verdict.get("reasons") or []:
        lines.append(f"- reason: {reason}")
    return "\n".join(lines)


async def agent_eval_verify(certificate_id: int) -> str:
    """
    Verify a certificate's signature and recompute its verdict from the
    embedded results (a signed certificate whose verdict doesn't follow
    from its data is rejected).

    Args:
        certificate_id: Bundle id from agent_eval_certify
    """
    result = await _shared.call_engine(
        "GET", f"/eval/certificates/{certificate_id}", timeout=60.0,
    )
    if "error" in result:
        return f"Verification failed: {result['error']}"
    verification = result.get("verification") or {}
    lines = [f"# Certificate #{certificate_id} verification"]
    for name, ok in (verification.get("checks") or {}).items():
        lines.append(f"- [{'PASS' if ok else 'FAIL'}] {name}")
    lines.append(f"\n{'VALID' if verification.get('valid') else 'INVALID'}"
                 + (f" — {verification.get('reason')}" if verification.get("reason") else ""))
    return "\n".join(lines)



def register(mcp) -> None:
    """Register all 2 agent_eval tools with the server."""
    mcp.tool()(agent_eval_certify)
    mcp.tool()(agent_eval_verify)
