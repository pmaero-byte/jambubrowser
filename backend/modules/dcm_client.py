"""Backward-compatibility shim — moved to backend.decentralized.dcm_client."""
from backend.decentralized.dcm_client import *  # noqa: F401,F403
from backend.decentralized.dcm_client import (  # explicit re-exports
    DcmClient, DcmError,
)
