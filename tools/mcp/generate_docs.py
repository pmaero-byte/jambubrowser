"""
Generate docs/MCP_TOOLS.md from the live MCP tool registry.

Why this exists
---------------
The MCP server in backend/mcp_server.py registers 21+ tools with
``@mcp.tool()``. Without this generator, the only way to learn what
tools are available is to read the source. This script introspects the
module at import time and emits a Markdown reference so:

  * users can browse the tool surface at a glance,
  * the doc stays in sync with the code (re-run after any new tool),
  * the same data can be served via ``GET /mcp/tools/docs``.

Usage::

    python3 tools/mcp/generate_docs.py             # writes docs/MCP_TOOLS.md
    python3 tools/mcp/generate_docs.py --stdout    # prints to stdout
    python3 tools/mcp/generate_docs.py --check     # exits non-zero on drift
"""
from __future__ import annotations

import argparse
import inspect
import sys
from pathlib import Path
from typing import Any, get_type_hints


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "docs" / "MCP_TOOLS.md"


def _import_mcp_server():
    """Import the MCP server module without booting the full FastAPI app.

    Importing ``backend.mcp_server`` runs only the @mcp.tool() decorators
    (which register handlers in FastMCP's in-memory registry). It does
    NOT start the engine, open a port, or touch the network.
    """
    # Running this file by path puts tools/mcp/ on sys.path, not the repo
    # root — add the root so `backend` resolves no matter how we're invoked.
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from backend import mcp_server  # noqa: F401
    return mcp_server


def _iter_tools(mcp_server_module) -> list[tuple[str, Any, str]]:
    """Return [(name, function, docstring_or_empty), ...] for every MCP tool."""
    tools: list[tuple[str, Any, str]] = []
    for name, obj in inspect.getmembers(mcp_server_module, inspect.isfunction):
        if not name.startswith("_"):
            # FastMCP decorates tool functions; the original function
            # is still inspectable. We accept anything defined in this
            # module (not imported) so we don't accidentally document
            # private helpers.
            mod = getattr(obj, "__module__", "")
            if mod == mcp_server_module.__name__:
                tools.append((name, obj, inspect.getdoc(obj) or ""))
    tools.sort(key=lambda t: t[0])
    return tools


def _format_signature(func) -> str:
    """Render a clean, Markdown-friendly signature for a tool function."""
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return "()"
    hints = {}
    try:
        hints = get_type_hints(func)
    except Exception:
        pass

    parts: list[str] = []
    for pname, param in sig.parameters.items():
        if pname == "self":
            continue
        ann = hints.get(pname, param.annotation)
        ann_s = getattr(ann, "__name__", str(ann)) if ann is not inspect.Parameter.empty else "Any"
        if param.default is inspect.Parameter.empty:
            parts.append(f"{pname}: {ann_s}")
        else:
            parts.append(f"{pname}: {ann_s} = {param.default!r}")
    return ", ".join(parts) if parts else ""


def render_markdown(mcp_server_module) -> str:
    """Return the full Markdown document for the given MCP server module."""
    tools = _iter_tools(mcp_server_module)
    lines: list[str] = []
    lines.append("# MCP Tools Reference")
    lines.append("")
    lines.append(
        "Auto-generated from `backend/mcp_server.py` by "
        "`tools/mcp/generate_docs.py`. Do not edit by hand — re-run the "
        "generator after adding or renaming a tool."
    )
    lines.append("")
    lines.append(f"**Total tools:** {len(tools)}")
    lines.append("")
    lines.append("## Table of contents")
    lines.append("")
    for name, _fn, _doc in tools:
        lines.append(f"- [`{name}`](#{name})")
    lines.append("")
    lines.append("## Tools")
    lines.append("")
    for name, fn, doc in tools:
        sig = _format_signature(fn)
        lines.append(f"### `{name}`")
        lines.append("")
        if sig:
            lines.append("**Signature**")
            lines.append("")
            lines.append("```python")
            lines.append(f"{name}({sig})")
            lines.append("```")
            lines.append("")
        if doc:
            lines.append("**Description**")
            lines.append("")
            # Render the docstring verbatim; dedent so it aligns with the bullet.
            lines.append(doc.rstrip())
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate MCP tool docs")
    parser.add_argument("--stdout", action="store_true", help="Print to stdout instead of writing a file")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"Output path (default: {DEFAULT_OUT})")
    parser.add_argument("--check", action="store_true", help="Exit non-zero if the file would change")
    args = parser.parse_args(argv)

    mcp_server_module = _import_mcp_server()
    doc = render_markdown(mcp_server_module)

    if args.stdout:
        sys.stdout.write(doc)
        return 0

    if args.check:
        if args.out.exists() and args.out.read_text() == doc:
            return 0
        print(f"drift detected: {args.out} is out of date", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(doc)
    print(f"wrote {args.out} ({len(tools := _iter_tools(mcp_server_module))} tools)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
