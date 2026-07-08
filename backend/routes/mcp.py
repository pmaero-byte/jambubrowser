"""MCP (Model Context Protocol) endpoints — serves generated tool docs."""
from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["mcp"])


@router.get("/mcp/tools/docs", response_class=PlainTextResponse)
async def mcp_tools_docs(format: str = "markdown"):
    """Return the auto-generated MCP tool reference.

    Defaults to Markdown (the format written to ``docs/MCP_TOOLS.md``).
    Pass ``?format=json`` for a machine-readable list of tools with
    name + description + signature.
    """
    from tools.mcp.generate_docs import _import_mcp_server, render_markdown, _iter_tools
    mcp_server = _import_mcp_server()
    tools = _iter_tools(mcp_server)
    if format == "json":
        import json
        from inspect import getdoc, signature
        try:
            from typing import get_type_hints
        except ImportError:
            get_type_hints = lambda _f: {}
        payload = []
        for name, fn, _doc in tools:
            try:
                sig = str(signature(fn))
            except (TypeError, ValueError):
                sig = "()"
            payload.append({
                "name": name,
                "description": (getdoc(fn) or "").strip(),
                "signature": sig,
            })
        return PlainTextResponse(
            content=json.dumps({"count": len(payload), "tools": payload}, indent=2),
            media_type="application/json",
        )
    return PlainTextResponse(content=render_markdown(mcp_server), media_type="text/markdown")
