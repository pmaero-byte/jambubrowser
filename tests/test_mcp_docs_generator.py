"""Tests for the MCP tool doc generator (tools/mcp/generate_docs.py)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure repo root is importable so `from backend import mcp_server` works
# when this test is collected from a working directory that isn't the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.mcp.generate_docs import (  # noqa: E402
    _import_mcp_server,
    _iter_tools,
    render_markdown,
)


@pytest.fixture(scope="module")
def mcp_module():
    """Import backend.mcp_server once for the module — it's expensive (loads FastMCP)."""
    return _import_mcp_server()


class TestToolIteration:
    def test_finds_at_least_the_documented_tools(self, mcp_module):
        tools = _iter_tools(mcp_module)
        names = {name for name, _, _ in tools}
        # Spot-check a handful of well-known tools.
        for required in {
            "research_web",
            "search_multi_engine",
            "scrape_page",
            "click_element",
            "check_engine_health",
            "get_brain_stats",
        }:
            assert required in names, f"missing tool {required!r} from registry"

    def test_each_tool_has_a_docstring(self, mcp_module):
        tools = _iter_tools(mcp_module)
        # Most tools are well-documented; allow a small number of empty ones
        # (e.g. thin shims) but expect the vast majority to have docs.
        missing = [name for name, _, doc in tools if not doc.strip()]
        assert len(missing) <= 2, f"too many tools without docstrings: {missing}"

    def test_iter_tools_sorts_alphabetically(self, mcp_module):
        tools = _iter_tools(mcp_module)
        names = [name for name, _, _ in tools]
        assert names == sorted(names)


class TestRenderMarkdown:
    def test_includes_table_of_contents(self, mcp_module):
        md = render_markdown(mcp_module)
        assert "## Table of contents" in md
        # Every tool appears in the TOC.
        for name, _, _ in _iter_tools(mcp_module):
            assert f"[`{name}`]" in md, f"tool {name!r} missing from TOC"

    def test_includes_total_count(self, mcp_module):
        md = render_markdown(mcp_module)
        tool_count = len(_iter_tools(mcp_module))
        assert f"**Total tools:** {tool_count}" in md

    def test_includes_per_tool_sections(self, mcp_module):
        md = render_markdown(mcp_module)
        for name, _, _ in _iter_tools(mcp_module):
            assert f"### `{name}`" in md, f"missing per-tool section for {name!r}"

    def test_includes_signature_code_block(self, mcp_module):
        md = render_markdown(mcp_module)
        # At least one tool's signature should appear as a fenced code block.
        assert "```python" in md
        # The signature should reference the parameter names of a known tool.
        assert "query: str" in md or "url: str" in md

    def test_ends_with_single_newline(self, mcp_module):
        md = render_markdown(mcp_module)
        assert md.endswith("\n")
        assert not md.endswith("\n\n")
