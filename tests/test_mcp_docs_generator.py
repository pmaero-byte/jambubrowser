"""Tests for the MCP tool doc generator (tools/mcp/generate_docs.py)."""
from __future__ import annotations

import json
import re
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


# ---------------------------------------------------------------------------
# Drift guards
#
# MCP_TOOLS.md is generated, and several hand-written docs/publish manifests
# quote the tool count. Historically those drifted independently (server.json
# said 28, FEATURE_MAP.md said 21, BROWSER_TESTING.md said 37, MCP_REMOTE.md
# said the curated profile had 27 tools when it had 41), because the generator
# exposes a --check mode that nothing ever invoked. These tests close that hole.
# ---------------------------------------------------------------------------

_DOCS = _REPO_ROOT / "docs"


class TestGeneratedDocIsFresh:
    def test_mcp_tools_md_matches_the_registry(self, mcp_module):
        """docs/MCP_TOOLS.md on disk must be byte-identical to a fresh render.

        This is the CI check the generator has always supported via --check but
        no caller ever ran; running it in-process keeps it in the normal suite.
        """
        from tools.mcp.generate_docs import main as generator_main

        assert generator_main(["--check"]) == 0, (
            "docs/MCP_TOOLS.md is stale — re-run: python tools/mcp/generate_docs.py"
        )


class TestToolCountClaimsDoNotDrift:
    """Every hand-authored "N tools" claim must agree with the registry."""

    # Phrasings that mean "the full MCP surface", as they appear in the docs:
    #   "42 MCP tools" / "42 tools exposed over Model Context" /
    #   "reference (42 tools)" / "all 42 tools" / "**42 tools**"
    _FULL_SURFACE_PATTERNS = (
        r"\b(\d{1,3}) MCP tools\b",
        r"\b(\d{1,3}) tools exposed over Model Context",
        r"reference \((\d{1,3}) tools\)",
        r"\ball (\d{1,3}) tools\b",
        r"\*\*(\d{1,3}) tools\*\*",
        r", (\d{1,3}) tools\)",  # README: "MCP server (stdio + ... HTTP, 42 tools)"
    )

    # Checked by test_curated_and_developer_profile_counts instead.
    _PROFILE_CLAIM = re.compile(r"`(\w+)` [—-] (\d{1,3}) tools")

    @pytest.fixture(autouse=True)
    def _bind_total(self, mcp_module):
        type(self).total = len(_iter_tools(mcp_module))

    def _stale_claims_in(self, path: Path) -> list[str]:
        text = path.read_text(encoding="utf-8")
        text = self._PROFILE_CLAIM.sub("", text)
        stale: list[str] = []
        for pattern in self._FULL_SURFACE_PATTERNS:
            for match in re.finditer(pattern, text):
                if int(match.group(1)) != self.total:
                    stale.append(f"{path.name}: {match.group(0)!r} (registry: {self.total})")
        return stale

    def test_server_json_manifest(self):
        manifest = json.loads((_REPO_ROOT / "server.json").read_text(encoding="utf-8"))
        match = re.search(r"(\d{1,3}) MCP tools", manifest["description"])
        assert match, "server.json description must state the MCP tool count"
        assert int(match.group(1)) == self.total, (
            f"server.json advertises {match.group(1)} tools, registry has {self.total}"
        )

    def test_docs_referencing_the_full_surface(self):
        targets = (
            _REPO_ROOT / "README.md",
            _DOCS / "BROWSER_TESTING.md",
            _DOCS / "MCP_REMOTE.md",
            _DOCS / "IMPROVEMENT_PLAN.md",
            _DOCS / "FEATURE_MAP.md",
        )
        stale: list[str] = []
        for path in targets:
            stale += self._stale_claims_in(path)
        assert not stale, f"stale MCP tool counts: {stale}"

    def test_curated_and_developer_profile_counts(self):
        """The profile bullet list in MCP_REMOTE.md must match the code."""
        from backend import mcp_server

        tools = {name for name, _, _ in _iter_tools(mcp_server)}
        curated = len(tools) - len(set(mcp_server.CURATED_EXCLUDES) & tools)
        assert set(mcp_server.DEVELOPER_TOOLS) <= tools, (
            "DEVELOPER_TOOLS names tools that are not registered"
        )
        developer = len(mcp_server.DEVELOPER_TOOLS)

        text = (_DOCS / "MCP_REMOTE.md").read_text(encoding="utf-8")
        claims = {m.group(1): int(m.group(2)) for m in self._PROFILE_CLAIM.finditer(text)}
        assert claims.get("curated") == curated, (
            f"MCP_REMOTE.md says curated has {claims.get('curated')} tools, "
            f"code derives {curated}"
        )
        assert claims.get("developer") == developer, (
            f"MCP_REMOTE.md says developer has {claims.get('developer')} tools, "
            f"code derives {developer}"
        )
