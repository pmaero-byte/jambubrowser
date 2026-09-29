"""Backward-compatibility shim — moved to backend.decentralized.federated_rag."""
from backend.decentralized.federated_rag import *  # noqa: F401,F403
from backend.decentralized.federated_rag import (
    FederatedRAG, FederatedQuery, FederatedResult, get_federated_rag,
)
