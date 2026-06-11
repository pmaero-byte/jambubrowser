"""
Memory & Personalization System
================================

A unified memory layer that gives Jambubrowser persistent identity about its
user and accumulated knowledge across sessions. Replaces the barely-used
`memory_entries` FTS5 table with a proper, queryable, hybrid-retrieval store.

Four memory types:

1. **UserProfile** — who the user is (interests, expertise, preferences, lang)
2. **SessionMemory** — per-conversation context (topic, summary, active goals)
3. **SemanticMemory** — long-term knowledge (facts, learnings, with embeddings)
4. **ProceduralMemory** — what approaches have worked before (success rates)

Public API
----------
- `MemoryStore`              — unified store with all 4 sub-stores
- `get_memory()`             — singleton accessor
- `reset_memory()`           — for tests
- `retrieve_relevant()`      — combined semantic + recency + importance ranking
- `UserProfile`, `SessionMemory`, `SemanticMemory`, `ProceduralMemory` data classes
"""

from .store import (
    MemoryStore,
    get_memory,
    reset_memory,
    UserProfile,
    SessionMemory,
    SemanticMemory,
    ProceduralMemory,
    MemoryCategory,
)
from .retrieval import retrieve_relevant, RetrievalHit, format_context, embed_text

__all__ = [
    "MemoryStore",
    "get_memory",
    "reset_memory",
    "UserProfile",
    "SessionMemory",
    "SemanticMemory",
    "ProceduralMemory",
    "MemoryCategory",
    "retrieve_relevant",
    "RetrievalHit",
    "format_context",
    "embed_text",
]
