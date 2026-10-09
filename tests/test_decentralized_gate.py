"""Regression tests for the decentralized package migration and feature gate."""
from __future__ import annotations

import importlib
import os
import subprocess
import sys


def test_decentralized_routes_registered_when_enabled() -> None:
    module = importlib.import_module("backend.engine")
    paths = set(module.app.openapi().get("paths", {}))
    assert "/meshpay/config" in paths
    assert "/verification/policy" in paths
    assert "/p2p/stats" in paths


def test_decentralized_routes_not_registered_when_disabled() -> None:
    code = (
        "import backend.engine as e; "
        "paths=set(e.app.openapi().get('paths', {})); "
        "assert '/meshpay/config' not in paths; "
        "assert '/verification/policy' not in paths; print('ok')"
    )
    env = {**os.environ, "JAMBU_ENABLE_DECENTRALIZED": "0"}
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True,
        text=True, check=True, timeout=30,
    )
    assert result.stdout.strip().endswith("ok")


def test_legacy_import_shims_resolve_to_new_package() -> None:
    pairs = {
        "backend.modules.a2a": "backend.decentralized.a2a",
        "backend.modules.consensus_engine": "backend.decentralized.consensus_engine",
        "backend.modules.dcm_client": "backend.decentralized.dcm_client",
        "backend.modules.evidence": "backend.decentralized.evidence",
        "backend.modules.federated_rag": "backend.decentralized.federated_rag",
        "backend.modules.meshpay": "backend.decentralized.meshpay",
        "backend.modules.p2p_discovery": "backend.decentralized.p2p_discovery",
        "backend.modules.verification": "backend.decentralized.verification",
        "backend.modules.x402": "backend.decentralized.x402",
    }
    for old, new in pairs.items():
        old_module = importlib.import_module(old)
        new_module = importlib.import_module(new)
        assert old_module.__doc__
        if old.endswith(".meshpay"):
            assert old_module.hash_receipt is new_module.hash_receipt


def test_disabled_decentralized_routes_return_501_not_404() -> None:
    """When the gate is off, hitting /dcm/*, /p2p/*, … used to 404 (opaque —
    looks like a missing feature). It must now 501 with an actionable hint
    naming the env flag, so "disabled" is distinguishable from "broken"."""
    code = (
        "from fastapi.testclient import TestClient;"
        "import backend.engine as e;"
        "c = TestClient(e.app);"
        "assert c.get('/dcm/status').status_code == 501;"
        "assert c.post('/p2p/query', json={}).status_code == 501;"
        "assert c.get('/x402/paywall').status_code == 501;"
        "assert 'JAMBU_ENABLE_DECENTRALIZED=1' in c.get('/dcm/status').json()['detail'];"
        "print('ok')"
    )
    env = {**os.environ, "JAMBU_ENABLE_DECENTRALIZED": "0",
           "JAMBU_LLM_PROVIDER": "mock"}
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True,
        text=True, check=True, timeout=60,
    )
    assert result.stdout.strip().endswith("ok")


def test_enabled_decentralized_routes_not_shadowed_by_fallback() -> None:
    """With the gate on, the real routers must win — the disabled-501 fallback
    is only registered in the else branch, so /dcm/status must NOT be 501."""
    module = importlib.import_module("backend.engine")
    paths = set(module.app.openapi().get("paths", {}))
    # The real route exists and the wildcard fallback is absent.
    assert "/dcm/status" in paths
    assert "/dcm/{subpath}" not in paths

