"""The built-in tool registry: names, risk levels and grouping.

`register_builtin_tools` registered 18 tools in 186 lines and was one function,
so the only way to check "what is registered, and at what risk" was to read it.
It is now eight registrars grouped by domain.

This test is the contract that split depends on, and it also pins two safety
properties that are easy to lose when a registration moves:

* `vault_get` is MEDIUM risk and `code_exec` is **HIGH** — they hand out a
  secret and run code respectively, and a careless edit that drops either one
  would silently make the registry approve it.
* `final_answer` is always registered: every run ends with it, so a run whose
  registry lacks it has no way to finish.
"""
from __future__ import annotations

import pytest

from backend.agent.builtin_tools import (
    register_builtin_tools,
    _register_answer,
    _register_browser_actions,
    _register_browser_testing,
    _register_execution,
    _register_goals,
    _register_knowledge,
    _register_secrets,
    _register_web,
)
from backend.agent.tools import RiskLevel, ToolRegistry


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()


@pytest.fixture
def populated(registry: ToolRegistry) -> ToolRegistry:
    register_builtin_tools(registry)
    return registry


EXPECTED_TOOLS = {
    # web
    "web_search", "scrape_url",
    # knowledge
    "knowledge_query", "memory_recall", "memory_store",
    # secrets
    "vault_get",
    # execution
    "code_exec",
    # goals & risk
    "goal_set", "risk_check",
    # answer
    "final_answer",
    # browser actions
    "browser_navigate", "browser_click", "browser_extract", "browser_fill",
    # browser testing
    "browser_test_flow", "browser_test_plan", "browser_import_playwright",
}


class TestRegisteredSurface:
    def test_exactly_the_expected_tools_are_registered(self, populated):
        assert set(populated._tools) == EXPECTED_TOOLS

    def test_every_tool_has_a_description_and_schema(self, populated):
        for name, tool in populated._tools.items():
            assert tool.spec.description, f"{name} has no description"
            assert isinstance(tool.spec.parameters, dict), name

    def test_the_returned_registry_is_the_one_passed_in(self, registry):
        assert register_builtin_tools(registry) is registry

    def test_registration_is_idempotent(self, populated):
        before = dict(populated._tools)
        register_builtin_tools(populated)
        assert populated._tools == before


class TestRiskLevels:
    """The two tools that can do real damage must not be silently downgraded."""

    def test_vault_get_is_medium(self, populated):
        assert populated._tools["vault_get"].spec.risk_level is RiskLevel.MEDIUM

    def test_code_exec_is_high(self, populated):
        """It runs code, so it is the only HIGH-risk built-in."""
        assert populated._tools["code_exec"].spec.risk_level is RiskLevel.HIGH

    def test_network_tools_declare_that_they_need_the_network(self, populated):
        for name in ("web_search", "scrape_url", "browser_navigate"):
            assert populated._tools[name].spec.requires_network is True, name

    def test_read_only_memory_tools_stay_low(self, populated):
        for name in ("memory_recall", "knowledge_query"):
            assert populated._tools[name].spec.risk_level is RiskLevel.LOW, name

    def test_exactly_one_built_in_is_high_risk(self, populated):
        high = [n for n, t in populated._tools.items()
                if t.spec.risk_level is RiskLevel.HIGH]
        assert high == ["code_exec"]


class TestGrouping:
    """Each registrar owns exactly its own tools — a copy-paste that registers
    `vault_get` in the web group would otherwise pass every other test."""

    CASES = [
        (_register_web, {"web_search", "scrape_url"}),
        (_register_knowledge, {"knowledge_query", "memory_recall", "memory_store"}),
        (_register_secrets, {"vault_get"}),
        (_register_execution, {"code_exec"}),
        (_register_goals, {"goal_set", "risk_check"}),
        (_register_answer, {"final_answer"}),
        (_register_browser_actions, {"browser_navigate", "browser_click",
                                     "browser_extract", "browser_fill"}),
        (_register_browser_testing, {"browser_test_flow", "browser_test_plan",
                                     "browser_import_playwright"}),
    ]

    @pytest.mark.parametrize("registrar,expected", CASES)
    def test_registrar_registers_exactly_its_group(self, registrar, expected):
        reg = ToolRegistry()
        registrar(reg)
        assert set(reg._tools) == expected

    def test_the_groups_partition_the_whole_surface(self):
        union = set()
        for _registrar, expected in self.CASES:
            assert not (union & expected), "a tool is registered by two groups"
            union |= expected
        assert union == EXPECTED_TOOLS

    def test_final_answer_is_always_present(self, registry):
        """Every run ends with it; a registry without it cannot finish."""
        _register_answer(registry)
        assert "final_answer" in registry._tools


class TestPlaywrightImportSchema:
    """The import tool's schema is what the planner reads to call it, so it is
    pinned rather than left to a refactor."""

    def test_code_is_required(self, populated):
        schema = populated._tools["browser_import_playwright"].spec.parameters
        assert schema["required"] == ["code"]
        assert schema["properties"]["code"]["type"] == "string"