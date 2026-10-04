"""Browser session MCP tools.

Hardened agent browser sessions (open, act, receipts).

Tools are plain async functions here; ``register(mcp)`` applies FastMCP's
decorator to each one, so a tool can be read, imported and unit-tested
without an MCP server in the loop.
"""
from __future__ import annotations

from backend.mcp_tools import _shared


async def browser_session_open(allow_domains: str, require_approval: bool = True) -> str:
    """
    Open an isolated browser session for agent-driven work, restricted to a
    domain allowlist. Navigations outside it are refused; irreversible-looking
    actions need ``approve=true``; PII is scrubbed from snapshots.

    Args:
        allow_domains: Comma-separated domains the session may visit (subdomains allowed)
        require_approval: Require approve=true for input actions inside the allowlist
    """
    domains = [d.strip() for d in (allow_domains or "").split(",") if d.strip()]
    if not domains:
        return "allow_domains must be a non-empty comma-separated list (sessions fail closed)."
    result = await _shared.call_engine("POST", "/browser/sessions", {
        "allow_domains": domains, "require_approval": require_approval,
    }, timeout=60.0)
    if "error" in result:
        return f"Session open failed: {result['error']}"
    return (
        f"Session {result['session_id']} open\n"
        f"- allowlisted: {', '.join(result['allow_domains'])}\n"
        f"- approval required: {result['require_approval']}\n"
        f"- snapshot next: browser_session_snapshot"
    )


def _render_compact(view: dict) -> str:
    """Render a compact observation (columns/rows or a delta) for a model.

    The projector already dropped default-valued cells, so cells are joined
    positionally against ``columns`` rather than as key/value pairs. Any
    omission the projector reported is surfaced verbatim instead of being
    silently trimmed — a model that cannot see 12 rows must be told so.
    """
    lines = [f"# {view.get('title', '')} — {view.get('url', '')}\n"]
    columns = view.get("columns") or []
    delta = view.get("delta")

    def row(cells: list) -> str:
        return " | ".join(str(c) for c in cells if c not in (None, ""))

    if delta:
        counts = delta.get("counts") or {}
        lines.append(
            f"## changed since last snapshot "
            f"(+{counts.get('added', 0)} ~{counts.get('changed', 0)} "
            f"-{counts.get('removed', 0)})\n"
        )
        for entry in delta.get("added") or []:
            lines.append(f"- added `{row(entry)}`")
        for entry in delta.get("changed") or []:
            lines.append(f"- changed `{row(entry.get('before', []))}` -> `{row(entry.get('after', []))}`")
        for name in delta.get("removed") or []:
            lines.append(f"- removed {name}")
    else:
        lines.append(f"## {view.get('shown', 0)} of {view.get('total', 0)} elements")
        lines.append(f"columns: {' | '.join(columns)}\n")
        for entry in view.get("rows") or []:
            lines.append(f"- `{row(entry)}`")

    if view.get("text"):
        matches = view["text"].get("matches") or []
        lines.append("\nPage text matches:")
        lines.extend(f"  {m}" for m in matches)

    footer = []
    if view.get("hidden_omitted"):
        footer.append(f"{view['hidden_omitted']} hidden")
    if view.get("omitted"):
        footer.append("omitted: " + ", ".join(view["omitted"]))
    if view.get("hint"):
        footer.append(view["hint"])
    if footer:
        lines.append("\n(" + " — ".join(footer) + ")")
    lines.append(f"\n~{view.get('tokens_estimate', 0)} tokens")
    return "\n".join(lines)


async def browser_session_snapshot(session_id: str, compact: bool = False,
                                   delta: bool = False, query: str = "",
                                   roles: str = "", max_tokens: int = 0) -> str:
    """
    Perception step: accessibility-style snapshot with a typed element
    catalog (refs @e1…). Act on refs, never on selector guesses.

    Set ``compact`` to get the token-budgeted projection instead of the full
    catalog: ``columns``/``rows`` rather than one dict per element, narrowed by
    ``query`` (matched against an element's name/value/href/role) and
    ``roles`` (comma-separated), and halved until it fits ``max_tokens``.
    Prefer it on large pages — a 200-element catalog is the single largest
    line item in your context. Add ``delta`` to report only what moved since
    the previous snapshot, which is the cheapest way to check whether an act
    changed anything.

    Args:
        session_id: Session from browser_session_open
        compact: Return the token-budgeted projection instead of the full catalog
        delta: Report only what changed since the previous snapshot
        query: Only elements matching these words (compact mode)
        roles: Comma-separated role/tag allowlist, e.g. "button,input" (compact mode)
        max_tokens: Shrink the compact view until it fits this budget
    """
    params: dict = {}
    if compact or delta:
        params = {"compact": compact, "delta": delta}
        if query:
            params["query"] = query
        if roles:
            params["roles"] = roles
        if max_tokens:
            params["max_tokens"] = max_tokens
    result = await _shared.call_engine(
        "GET", f"/browser/sessions/{session_id}/snapshot", json_data=params or None,
        timeout=60.0,
    )
    if "error" in result:
        return f"Snapshot failed: {result['error']}"
    if params:
        return _render_compact(result)
    lines = [f"# {result.get('title', '')} — {result.get('url', '')}\n"]
    for e in (result.get("elements") or [])[:25]:
        risk = f" ⚠{e['risk']}" if e.get("risk") else ""
        lines.append(f"- `{e['ref']}` {e.get('tag')} {e.get('name', '')[:60]}{risk}")
    if result.get("count", 0) > 25:
        lines.append(f"… and {result['count'] - 25} more")
    if result.get("text"):
        lines.append(f"\nPage text (scrubbed):\n{result['text'][:600]}")
    return "\n".join(lines)


async def browser_session_act(session_id: str, action: str, ref: str,
                              text: str = "", approve: bool = False,
                              files: str = "", dialog: str = "") -> str:
    """
    Deterministic dispatch by catalog ref. Refusals are explicit: blocked
    domains, unknown refs, and actions needing approval (risky elements such
    as delete/pay/send always require approve=true).

    Args:
        session_id: Session id
        action: "click", "type" or "upload"
        ref: Element ref from the last snapshot (e.g. @e3)
        text: Text to type (for action="type")
        approve: Explicit approval for input/risky actions
        files: Comma-separated local file paths (for action="upload"); must sit inside JAMBU_UPLOAD_ROOTS
        dialog: Answer the dialog this action raises: "accept", "dismiss", or "accept:<text>" for prompt()
    """
    payload: dict = {
        "action": action, "ref": ref, "text": text, "approve": approve,
    }
    if files.strip():
        payload["files"] = [f.strip() for f in files.split(",") if f.strip()]
    if dialog.strip():
        payload["dialog"] = dialog.strip()
    result = await _shared.call_engine(
        "POST", f"/browser/sessions/{session_id}/act", payload, timeout=60.0,
    )
    if "error" in result:
        return f"Action refused/failed: {result['error']}"
    if result.get("uploaded"):
        return (f"ok — uploaded {', '.join(result['uploaded'])} "
                f"via {result.get('mode')} (step {result.get('step', {}).get('seq')})")
    return f"ok — {result.get('outcome')} at {result.get('url')} (step {result.get('step', {}).get('seq')})"


async def browser_session_act_batch(session_id: str, actions: str,
                                    approve: bool = False,
                                    stop_on_error: bool = True) -> str:
    """
    Run several primitives in ONE call — the cheap way to fill a form or walk a
    wizard. Every act still gets its own receipt and refusal; only the round
    trips (and therefore the tokens) are shared, and no re-snapshot happens
    between acts while refs stay valid.

    Args:
        session_id: Session id
        actions: JSON array of acts, e.g. [{"action":"type","ref":"@e3","text":"a@b.com"},{"action":"check","ref":"@e7"},{"action":"press","ref":"@e9","text":"Enter"},{"action":"assert_visible","target":"Welcome"}]. Any flow step works: click/type/upload/press/select/check/hover/wait/assert_*/dialog/download
        approve: Explicit approval applied to acts that do not set their own
        stop_on_error: Stop at the first refusal (default) or run all acts and report each
    """
    import json as _json

    try:
        parsed = _json.loads(actions) if isinstance(actions, str) else actions
    except _json.JSONDecodeError as exc:
        return f"actions is not valid JSON: {exc}"
    if not isinstance(parsed, list) or not parsed:
        return "actions must be a non-empty JSON array of act objects."
    result = await _shared.call_engine(
        "POST", f"/browser/sessions/{session_id}/act_batch",
        {"actions": parsed, "approve": approve,
         "stop_on_error": stop_on_error},
        timeout=180.0,
    )
    if "error" in result:
        return f"Batch refused/failed: {result['error']}"
    lines = [f"# Batch — {result.get('passed')}/{result.get('count')} acts ok "
             f"at {result.get('url', '')}"]
    for entry in (result.get("results") or []):
        mark = "✓" if entry.get("ok") else "✗"
        detail = entry.get("detail") or entry.get("error") or ""
        lines.append(f"- {mark} #{entry['i']} {entry['action']} {str(detail)[:80]}")
    if result.get("stopped_at") is not None:
        lines.append(f"- stopped at act #{result['stopped_at']}")
    if result.get("reobserve"):
        lines.append("- page navigated: call browser_session_snapshot before further acts")
    if result.get("telemetry"):
        lines.append(f"- telemetry: {result['telemetry']}")
    return "\n".join(lines)


async def browser_session_telemetry(session_id: str, drain: bool = False) -> str:
    """
    Ask "did anything break?" without paying for a snapshot: collected console
    errors, failed/4xx-5xx requests and native dialogs since the session opened.

    Args:
        session_id: Session id
        drain: Clear the buffers after reading, so the next call is a fresh delta
    """
    result = await _shared.call_engine(
        "GET", f"/browser/sessions/{session_id}/telemetry?drain={'true' if drain else 'false'}",
        timeout=30.0,
    )
    if "error" in result:
        return f"Telemetry failed: {result['error']}"
    lines = [f"# Telemetry — {result.get('url', '')}"]
    for key in ("console_errors", "console_warnings", "failed_requests",
                "bad_responses", "dialogs"):
        items = result.get(key) or []
        if items:
            lines.append(f"- {key}: {len(items)}")
            for item in items[:5]:
                lines.append(f"    · {str(item)[:120]}")
    if len(lines) == 1:
        lines.append("- clean: no console errors, failed requests or dialogs")
    return "\n".join(lines)


async def browser_token_savings() -> str:
    """
    What the flow/batch verbs saved versus driving the browser one primitive per
    tool call: calls avoided and estimated tokens, counted for this engine.

    (no args)
    """
    result = await _shared.call_engine("GET", "/browser/sessions/savings", timeout=30.0)
    if "error" in result:
        return f"Savings lookup failed: {result['error']}"
    return (
        f"Flows run: {result.get('runs', 0)} ({result.get('steps', 0)} steps)\n"
        f"- as flows:     ~{result.get('flow_tokens', 0)} tokens, "
        f"{result.get('runs', 0)} call(s)\n"
        f"- as primitives: ~{result.get('primitive_tokens', 0)} tokens, "
        f"{(result.get('runs', 0) or 0) + result.get('calls_avoided', 0)} call(s)\n"
        f"- saved: ~{result.get('saved_tokens', 0)} tokens, "
        f"{result.get('calls_avoided', 0)} round trip(s)"
    )


async def browser_session_receipts(session_id: str) -> str:
    """
    Hash-chained receipt log for a session (every action, blocked or not),
    with the Merkle root that can be signed into an evidence bundle.

    Args:
        session_id: Session id
    """
    result = await _shared.call_engine(
        "GET", f"/browser/sessions/{session_id}/receipts", timeout=30.0,
    )
    if "error" in result:
        return f"Receipts failed: {result['error']}"
    lines = [f"# Receipts — {result.get('count', 0)} step(s)",
             f"merkle_root: `{result.get('merkle_root')}`\n"]
    for s in (result.get("steps") or [])[-10:]:
        lines.append(
            f"- #{s['seq']} {s['action']} [{s['outcome']}] {s.get('ref') or ''} {s.get('detail', '')[:60]}"
        )
    return "\n".join(lines)


async def browser_session_close(session_id: str) -> str:
    """
    Close a browser session (ephemeral context is torn down).

    Args:
        session_id: Session id
    """
    result = await _shared.call_engine(
        "DELETE", f"/browser/sessions/{session_id}", timeout=30.0,
    )
    if "error" in result:
        return f"Close failed: {result['error']}"
    return f"Session {session_id} closed ({result.get('steps', 0)} steps recorded)."



def register(mcp) -> None:
    """Register all 8 browser_sessions tools with the server."""
    mcp.tool()(browser_session_open)
    mcp.tool()(browser_session_snapshot)
    mcp.tool()(browser_session_act)
    mcp.tool()(browser_session_act_batch)
    mcp.tool()(browser_session_telemetry)
    mcp.tool()(browser_token_savings)
    mcp.tool()(browser_session_receipts)
    mcp.tool()(browser_session_close)

    # Helpers in this module are not tools: they render engine
    # output for a tool body and must stay undecorated.
