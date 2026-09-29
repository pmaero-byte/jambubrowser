"""Regression tests for the decentralized package migration and feature gate."""
from __future__ import annotations

import importlib
import os
import subprocess
import sys

from fastapi.routing import APIRoute


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
