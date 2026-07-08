"""
Memory retrieval — combined semantic + recency + importance + FTS ranking.

Algorithm
---------
1. **Vector similarity** on `semantic_memory.embedding` (sqlite-vec MATCH)
   - If embeddings unavailable or query has no embedding, fall back to text match
2. **Recency boost** — each memory's score is multiplied by `exp(-age_days / tau)`
   with tau = 14 days (configurable). Recent memories rank higher.
3. **Importance boost** — score is multiplied by `(0.5 + importance)` so
   important memories (importance=1.0) get a 1.5x boost over neutral (0.5).
4. **FTS5 keyword match** on user profile interests + work context (10% weight)
5. **Merge** — weighted sum: 0.6 * vector + 0.3 * (recency+importance) + 0.1 * FTS
6. **Dedupe** — drop near-duplicates via cosine > 0.95 (when both have embeddings)
7. **Return top-k** with attribution (which sub-system matched)
"""

from __future__ import annotations

import json
import math
import struct
import time
from dataclasses import dataclass, field
from typing import Optional

from .store import MemoryStore, SemanticMemory, get_memory

# Default recency half-life (days)
RECENCY_TAU_DAYS = 14.0


@dataclass
class RetrievalHit:
    memory: SemanticMemory
    score: float
    matched_by: str  # "vector" | "fts" | "recency" | "importance" | "profile"
    explanation: str = ""


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------

def _try_load_sentence_transformer():
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        return SentenceTransformer("all-MiniLM-L6-v2")
    except Exception:
        return None


_MODEL = None


def _model():
    global _MODEL
    if _MODEL is None:
        _MODEL = _try_load_sentence_transformer()
    return _MODEL


def embed_text(text: str) -> Optional[bytes]:
    """Embed text using sentence-transformers if available. Returns float32 bytes."""
    m = _model()
    if m is None:
        return None
    try:
        vec = m.encode([text], normalize_embeddings=True)[0]
        return struct.pack(f"<{len(vec)}f", *vec.tolist())
    except Exception:
        return None


def cosine(a: bytes, b: bytes) -> float:
    """Cosine similarity between two packed float32 vectors."""
    try:
        n = len(a) // 4
        va = struct.unpack(f"<{n}f", a)
        vb = struct.unpack(f"<{n}f", b)
        dot = sum(x * y for x, y in zip(va, vb))
        na = math.sqrt(sum(x * x for x in va))
        nb = math.sqrt(sum(x * x for x in vb))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def retrieve_relevant(
    query: str,
    user_id: str = "default",
    k: int = 10,
    *,
    store: Optional[MemoryStore] = None,
    tau_days: float = RECENCY_TAU_DAYS,
) -> list[RetrievalHit]:
    """Return top-k relevant memories for the given query + user."""
    s = store or get_memory()
    query_emb = embed_text(query)
    query_emb_dim = len(query_emb) // 4 if query_emb else None

    all_mems = s.list_semantic(user_id, limit=500)
    if not all_mems:
        return []

    now = time.time()
    hits: list[RetrievalHit] = []

    # Profile match: if query tokens overlap with profile interests/context
    profile = s.get_profile(user_id)
    profile_text = (profile.work_context + " " + " ".join(profile.interests)).lower()
    query_tokens = set(query.lower().split())

    for mem in all_mems:
        scores: list[tuple[float, str]] = []
        # Vector score
        if mem.embedding and query_emb and query_emb_dim:
            mem_dim = len(mem.embedding) // 4
            if mem_dim == query_emb_dim:
                sim = cosine(mem.embedding, query_emb)
                scores.append((sim, "vector"))
        # Recency
        age_days = max(0, (now - mem.created_at) / 86400.0)
        recency = math.exp(-age_days / max(0.1, tau_days))
        scores.append((recency, "recency"))
        # Importance
        importance_boost = 0.5 + mem.importance
        scores.append((importance_boost, "importance"))
        # FTS-style: token overlap
        text_lower = mem.content.lower()
        overlap = sum(1 for t in query_tokens if t in text_lower)
        fts_score = min(1.0, overlap / max(1, len(query_tokens)))
        if fts_score > 0:
            scores.append((fts_score, "fts"))
        # Profile interest overlap
        if profile_text and any(tok in profile_text for tok in query_tokens):
            scores.append((0.8, "profile"))

        # Weighted merge
        final = 0.0
        for sc, src in scores:
            if src == "vector":
                final += 0.6 * sc
            elif src in ("recency", "importance"):
                final += 0.3 * (sc / 2)  # share the 0.3 budget
            elif src in ("fts", "profile"):
                final += 0.1 * sc

        if final > 0:
            sources = [src for _, src in scores]
            hits.append(RetrievalHit(
                memory=mem,
                score=final,
                matched_by="+".join(sorted(set(sources))),
                explanation=f"vector={[round(s,3) for s,_ in scores if _[1]=='vector']}",
            ))

    # Sort + dedup near-duplicates
    hits.sort(key=lambda h: h.score, reverse=True)
    deduped: list[RetrievalHit] = []
    for h in hits:
        is_dup = False
        for kept in deduped:
            if h.memory.embedding and kept.memory.embedding:
                if cosine(h.memory.embedding, kept.memory.embedding) > 0.95:
                    is_dup = True
                    break
        if not is_dup:
            deduped.append(h)
        if len(deduped) >= k:
            break

    return deduped


def format_context(hits: list[RetrievalHit], max_chars: int = 2000) -> str:
    """Format retrieval hits as LLM-readable context string."""
    if not hits:
        return ""
    parts: list[str] = ["[User memory context]"]
    char_budget = max_chars
    for h in hits:
        mem = h.memory
        ts = time.strftime("%Y-%m-%d", time.localtime(mem.created_at))
        entry = f"- ({ts}, {mem.category}, importance={mem.importance:.1f}) {mem.content}"
        if char_budget - len(entry) < 0:
            break
        parts.append(entry)
        char_budget -= len(entry)
    if len(parts) == 1:
        return ""
    return "\n".join(parts)


def get_procedural_hints(user_id: str, query: str, limit: int = 5) -> str:
    """Return LLM-readable hints from past successful/failed procedural memories."""
    from backend.memory.store import ProceduralMemory, list_procedural as _list
    memories = _list(user_id, limit=limit)
    if not memories:
        return ""

    lines = ["[Procedural memory — past approaches]"]
    for m in memories:
        sr = m.success_rate
        icon = "✓" if sr >= 0.7 else ("~" if sr >= 0.3 else "✗")
        lines.append(
            f"- {icon} pattern=\"{m.task_pattern[:120]}\" "
            f"approach=\"{m.approach[:120]}\" "
            f"success_rate={sr:.0%} ({m.success_count}/{m.success_count + m.failure_count})"
        )
        if len(lines) >= limit + 2:
            break
    return "\n".join(lines) + "\n"
