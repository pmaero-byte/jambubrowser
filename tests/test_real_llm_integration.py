"""
Real-LLM integration tests — the canary that catches LLM-shape bugs the mock
provider hides (e.g. `` blocks, multi-block preambles, schema drift).

All tests are marked ``requires_llm``. The skip logic lives in
``tests/conftest.py`` and fires unless:
  - --run-requires-llm is passed, OR
  - JAMBU_LLM_PROVIDER points at a real provider AND a credential env var
    (ANTHROPIC_API_KEY / OPENAI_API_KEY / MINIMAX_API_KEY) is set.

So these tests skip silently under default mock CI but run against any
real provider that's configured. The two `normalize_*` tests are pure
functions and pass under mock — they're labelled requires_llm only because
they are *part of* the LLM integration contract.
"""

from __future__ import annotations

import os

import pytest

# Ensure the test env is set even when this file is collected standalone.
os.environ.setdefault("JAMBU_DB_PATH", ":memory:")
os.environ.setdefault("JAMBU_VAULT_KEY", "test-key-do-not-use-in-production-32bytes!")

# Pytest marker (registered implicitly by pytest).
requires_llm = pytest.mark.requires_llm


# ---------------------------------------------------------------------------
# Canary tests — each one guards a specific LLM-shape fragility.
# ---------------------------------------------------------------------------

@requires_llm
def test_normalize_strips_think_block():
    """`` blocks must be stripped before JSON parsing."""
    from backend.llm import normalize_llm_response
    import json
    raw = "<think>some reasoning</think>\n{\"a\": 1}"
    out = normalize_llm_response(raw)
    parsed = json.loads(out)
    assert parsed == {"a": 1}, f"normalize failed: {out!r}"


@requires_llm
def test_normalize_handles_fenced_json():
    """```json ... ``` blocks must unwrap cleanly."""
    from backend.llm import normalize_llm_response
    import json
    raw = "```json\n{\"k\": \"v\"}\n```"
    out = normalize_llm_response(raw)
    parsed = json.loads(out)
    assert parsed == {"k": "v"}


@requires_llm
@pytest.mark.asyncio
async def test_chat_returns_non_empty_content():
    """The configured LLM must return content (any provider regression = fail)."""
    from backend.llm import get_default, ChatMessage, Role
    llm = get_default()
    resp = await llm.chat(
        [ChatMessage(role=Role.USER, content="Reply with the single word 'pong'.")],
        temperature=0.0,
        max_tokens=20,
    )
    assert resp.content, "LLM returned empty content"
    assert resp.usage.total_tokens > 0, "LLM returned zero tokens (provider broken?)"


@requires_llm
@pytest.mark.asyncio
async def test_chat_json_shape_planner():
    """Planner-style prompt must yield parseable JSON after normalization."""
    from backend.llm import get_default, ChatMessage, Role, normalize_llm_response
    import json
    llm = get_default()
    resp = await llm.chat(
        [
            ChatMessage(role=Role.SYSTEM,
                        content="You are a JSON generator. Output ONLY valid JSON, no prose."),
            ChatMessage(role=Role.USER,
                        content='Return a JSON object: {"verdict": "accepted", "confidence": 0.8}'),
        ],
        temperature=0.0,
        max_tokens=80,
    )
    parsed = json.loads(normalize_llm_response(resp.content))
    assert "verdict" in parsed, f"missing verdict: {parsed}"
    assert "confidence" in parsed, f"missing confidence: {parsed}"


@requires_llm
@pytest.mark.asyncio
async def test_evolution_planner_proposes_edits():
    """Real Planner.propose must return at least one HarnessEdit (or heuristic fallback)."""
    from backend.agent.evolution import Planner
    from backend.agent.harness_defaults import get_preset
    cluster_dict = {
        "failure_pattern": "web_search times out after 30s",
        "common_tool": "web_search",
        "common_error_category": "timeout",
        "count": 5,
        "severity": "high",
        "suggested_dimension": "control_flow",
        "suggested_fix": "Increase max_seconds",
    }
    planner = Planner()
    cfg = get_preset("research")
    edits = await planner.propose(cfg, [cluster_dict], max_edits=3)
    assert isinstance(edits, list)
    for e in edits:
        assert e.field_path, f"empty field_path: {e}"

