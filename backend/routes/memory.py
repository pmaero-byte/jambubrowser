"""Memory endpoints — v1, v2, and legacy /memory/recall."""
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.database import (
    memory_add, memory_search, memory_list, memory_delete,
    session_create, session_update, session_list, session_get,
    get_db_cursor,
)

router = APIRouter(tags=["memory"])


# ── Legacy /memory/recall ──


@router.get("/memory/recall")
async def recall_memory(query: str):
    """Cross-session semantic recall from the knowledge vault."""
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np

        model = SentenceTransformer("all-MiniLM-L6-v2")

        with get_db_cursor() as cursor:
            query_vec = model.encode(query).astype(np.float32).tobytes()
            from backend.core.vector_search import search_similar
            rows = search_similar(query_vec, k=10)

        return {
            "memory": [
                {"text": r[0][:300], "url": r[1]}
                for r in rows
            ]
        }
    except ImportError:
        return {"memory": []}
    except Exception as e:
        return {"memory": [], "error": str(e)}


# ── /v1/memory ──


class MemoryEntry(BaseModel):
    category: str = "general"
    key: str
    value: str
    importance: Optional[float] = 0.5


class MemorySearch(BaseModel):
    query: str
    limit: Optional[int] = 10


class SessionCreate(BaseModel):
    name: Optional[str] = None


@router.post("/v1/memory")
async def v1_memory_add(req: MemoryEntry):
    """Harness-compatible: add a memory entry."""
    entry_id = memory_add(req.category, req.key, req.value, req.importance or 0.5)
    return {"id": entry_id, "category": req.category, "key": req.key, "value": req.value}


@router.get("/v1/memory")
async def v1_memory_list(category: Optional[str] = None, limit: int = 50):
    """Harness-compatible: list memory entries."""
    return {"results": memory_list(category, limit)}


@router.post("/v1/memory/search")
async def v1_memory_search(req: MemorySearch):
    """Harness-compatible: FTS5 full-text memory search."""
    results = memory_search(req.query, req.limit or 10)
    return {"results": results}


@router.delete("/v1/memory/{entry_id}")
async def v1_memory_delete(entry_id: int):
    """Harness-compatible: delete a memory entry."""
    memory_delete(entry_id)
    return {"deleted": True}


# ── /v2/memory ──


class MemoryStoreRequest(BaseModel):
    user_id: str = "default"
    content: str
    category: str = "fact"
    importance: float = 0.5
    source_session: Optional[str] = None


class MemoryRecallRequest(BaseModel):
    query: str
    user_id: str = "default"
    k: int = 5


class MemoryUpdateProfileRequest(BaseModel):
    user_id: str
    display_name: Optional[str] = None
    interests: Optional[List[str]] = None
    expertise: Optional[Dict[str, str]] = None
    language: Optional[str] = None
    work_context: Optional[str] = None
    preferences: Optional[Dict[str, Any]] = None


@router.get("/v2/memory/profile")
async def memory_get_profile(user_id: str = "default"):
    """Get the user profile (interests, expertise, preferences)."""
    from backend.memory import get_memory
    return get_memory().get_profile(user_id).to_dict()


@router.put("/v2/memory/profile")
async def memory_update_profile(req: MemoryUpdateProfileRequest):
    """Update fields of a user profile (partial update supported)."""
    from backend.memory import get_memory, UserProfile
    mem = get_memory()
    p = mem.get_profile(req.user_id)
    if req.display_name is not None:
        p.display_name = req.display_name
    if req.interests is not None:
        p.interests = req.interests
    if req.expertise is not None:
        p.expertise = req.expertise
    if req.language is not None:
        p.language = req.language
    if req.work_context is not None:
        p.work_context = req.work_context
    if req.preferences is not None:
        p.preferences = req.preferences
    return mem.upsert_profile(p).to_dict()


@router.get("/v2/memory/sessions")
async def memory_list_sessions(user_id: str = "default", limit: int = 20):
    """List recent sessions for a user."""
    from backend.memory import get_memory
    sessions = get_memory().list_sessions(user_id, limit=limit)
    return {"sessions": [s.to_dict() for s in sessions]}


@router.get("/v2/memory/session/{session_id}")
async def memory_get_session(session_id: str):
    """Fetch a specific session by ID."""
    from backend.memory import get_memory
    return get_memory().get_session(session_id).to_dict()


@router.put("/v2/memory/session/{session_id}")
async def memory_update_session(session_id: str, body: dict):
    """Update a session (creates it if missing)."""
    from backend.memory import get_memory, SessionMemory
    s = SessionMemory(
        session_id=session_id,
        user_id=body.get("user_id", "default"),
        topic=body.get("topic", ""),
        summary=body.get("summary", ""),
        active_goals=body.get("active_goals", []),
        entities=body.get("entities", []),
    )
    return get_memory().upsert_session(s).to_dict()


@router.post("/v2/memory/store")
async def memory_store(req: MemoryStoreRequest):
    """Store a semantic memory entry."""
    from backend.memory import get_memory
    mid = get_memory().store_semantic(
        req.user_id, req.content,
        category=req.category, importance=req.importance,
        source_session=req.source_session,
    )
    return {"id": mid, "stored": True}


@router.post("/v2/memory/recall")
async def memory_recall(req: MemoryRecallRequest):
    """Recall relevant memories for a query."""
    from backend.memory import retrieve_relevant
    hits = retrieve_relevant(req.query, user_id=req.user_id, k=req.k)
    return {
        "query": req.query,
        "user_id": req.user_id,
        "hits": [
            {
                "id": h.memory.id,
                "content": h.memory.content,
                "category": h.memory.category,
                "importance": h.memory.importance,
                "score": h.score,
                "matched_by": h.matched_by,
                "created_at": h.memory.created_at,
            }
            for h in hits
        ],
    }


@router.delete("/v2/memory/{mem_id}")
async def memory_delete(mem_id: int, user_id: Optional[str] = None):
    """Forget a semantic memory entry."""
    from backend.memory import get_memory
    return {"deleted": get_memory().delete_semantic(mem_id, user_id=user_id)}


@router.get("/v2/memory/procedural")
async def memory_procedural(user_id: str = "default", limit: int = 20):
    """List learned procedural patterns (what worked, what didn't)."""
    from backend.memory import get_memory
    procs = get_memory().list_procedural(user_id, limit=limit)
    return {
        "patterns": [
            {
                **p.to_dict(),
                "success_rate": p.success_rate(),
            }
            for p in procs
        ]
    }


@router.post("/v2/memory/procedural/record")
async def memory_procedural_record(body: dict):
    """Record the outcome of an attempt: {id, success, duration_ms}."""
    from backend.memory import get_memory
    pid = int(body.get("id", 0))
    success = bool(body.get("success", False))
    duration = float(body.get("duration_ms", 0))
    p = get_memory().record_procedural_outcome(pid, success, duration)
    return {"updated": True, "success_rate": p.success_rate(), "avg_ms": p.avg_duration_ms}


@router.get("/v2/memory/stats")
async def memory_stats(user_id: str = "default"):
    """Get memory statistics for a user."""
    from backend.memory import get_memory
    return get_memory().stats(user_id)
