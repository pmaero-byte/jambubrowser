"""Backward-compatibility shim — moved to backend.decentralized.a2a."""
from backend.decentralized.a2a import *  # noqa: F401,F403
from backend.decentralized.a2a import (  # explicit re-exports
    agent_card, handle_rpc, A2AError, PARSE_ERROR, INVALID_REQUEST,
)
