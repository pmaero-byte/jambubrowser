"""Backward-compatibility shim — moved to backend.decentralized.evidence."""
from backend.decentralized.evidence import *  # noqa: F401,F403
from backend.decentralized.evidence import (  # explicit re-exports
    build_bundle, save_bundle, verify_bundle, list_bundles, get_bundle,
)
