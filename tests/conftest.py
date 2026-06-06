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
