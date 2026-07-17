"""
Pytest configuration for Jambubrowser test suite.
Uses pytest-asyncio for async endpoint testing.
"""

import pytest
import os
import sys

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set test environment
os.environ["JAMBU_DB_PATH"] = ":memory:"  # Use in-memory DB for tests
os.environ["JAMBU_VAULT_KEY"] = "test-key-do-not-use-in-production-32bytes!"  # 32-byte test key

# Default-skip network-dependent tests. test_search_integration.py boots a
# subprocess and hits a live SearXNG — if SearXNG isn't running locally the
# engine's /search endpoint 404s and the tests fail for the wrong reason.
# Run with `JAMBU_SKIP_NETWORK_TESTS=0 pytest` to opt in.
os.environ.setdefault("JAMBU_SKIP_NETWORK_TESTS", "1")


# ---------------------------------------------------------------------------
# Real-LLM integration test gate
# ---------------------------------------------------------------------------
# Tests marked @pytest.mark.requires_llm skip unless --run-requires-llm is passed.
# This is the canary that catches LLM-shape bugs the mock provider hides
# (think blocks, multi-block preambles, schema drift, etc.).

def pytest_addoption(parser):
    parser.addoption(
        "--run-requires-llm",
        action="store_true",
        default=False,
        help="Run tests marked as requires_llm (skipped by default under mock)",
    )


def _llm_can_run() -> bool:
    """True if the current env points at a real LLM provider with credentials."""
    provider = os.environ.get("JAMBU_LLM_PROVIDER", "mock")
    if provider in ("", "mock", "auto"):
        return False
    # Check that at least one provider's credentials are present.
    cred_vars = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "MINIMAX_API_KEY")
    return any(os.environ.get(v) for v in cred_vars)


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-requires-llm", default=False):
        return  # user explicitly asked for them
    if _llm_can_run():
        return  # env already points at a real provider
    skip_marker = pytest.mark.skip(
        reason="requires_llm: no real LLM provider configured (use --run-requires-llm)"
    )
    for item in items:
        if "requires_llm" in item.keywords:
            item.add_marker(skip_marker)


# ---------------------------------------------------------------------------
# Existing fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def test_db():
    """Provides a clean in-memory database for each test."""
    from backend.core.database import init_db, clear_all
    conn = init_db(":memory:")
    yield conn
    try:
        clear_all(":memory:")
    except Exception:
        pass
    conn.close()


@pytest.fixture
def test_client():
    """Provides a FastAPI test client for endpoint testing."""
    from fastapi.testclient import TestClient
    from backend.engine import app
    with TestClient(app) as client:
        yield client


@pytest.fixture(autouse=True)
def _isolate_module_singletons():
    """Reset cross-test module singletons that would otherwise leak state.

    - Rate limiter buckets (otherwise tests 100+ get 429)
    - LLM config singleton (otherwise .env overrides leak between tests)
    """
    from backend.core.rate_limiter import get_limiter
    from backend.llm import reload_config
    get_limiter().reset()
    reload_config()
    yield
    get_limiter().reset()


@pytest.fixture
def sample_query():
    """Standard test query."""
    return "What is quantum computing?"


@pytest.fixture
def sample_llm_config():
    """Standard LLM config for tests."""
    return {
        "baseUrl": "http://localhost:8080/v1",
        "modelId": "test-model",
        "apiKey": ""
    }
