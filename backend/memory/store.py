"""
Memory store — the unified entry point for all memory operations.

Owns the SQLite connection (reuses backend.core.database.init_db) and provides
typed accessors for the four memory types:

- UserProfile      → `user_profile` table
- SessionMemory    → `session_memory` table
- SemanticMemory   → `semantic_memory` table (+ embeddings via sqlite-vec)
- ProceduralMemory → `procedural_memory` table

Migrations are idempotent and live in `migrations.py` (auto-applied on init).
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Optional
import threading

from backend.core.database import init_db, DB_PATH


class MemoryCategory(str, Enum):
    FACT = "fact"
    PREFERENCE = "preference"
    CONTEXT = "context"
    LEARNING = "learning"
    GOAL = "goal"
    SKILL = "skill"


@dataclass
class UserProfile:
    user_id: str
    display_name: str = ""
    interests: list[str] = field(default_factory=list)
    expertise: dict[str, str] = field(default_factory=dict)  # domain -> level
    language: str = "en"
    work_context: str = ""
    preferences: dict = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["interests"] = list(self.interests)
        d["expertise"] = dict(self.expertise)
        d["preferences"] = dict(self.preferences)
        return d


@dataclass
class SessionMemory:
    session_id: str
    user_id: str = "default"
    topic: str = ""
    summary: str = ""
    active_goals: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    created_at: float = 0.0
    last_active: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SemanticMemory:
    id: int = 0
    user_id: str = "default"
    category: str = "fact"
    content: str = ""
    embedding: Optional[bytes] = None
    importance: float = 0.5
    source_session: Optional[str] = None
    created_at: float = 0.0
    last_accessed: float = 0.0
    access_count: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("embedding", None)
        return d


@dataclass
class ProceduralMemory:
    id: int = 0
    user_id: str = "default"
    task_pattern: str = ""
    approach: str = ""
    success_count: int = 0
    failure_count: int = 0
    avg_duration_ms: float = 0.0
    last_used: float = 0.0

    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        if total == 0:
            return 0.0
        return self.success_count / total

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class MemoryStore:
    """Unified memory store. All four memory types in one class."""

    _SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS user_profile (
        user_id TEXT PRIMARY KEY,
        display_name TEXT,
        interests TEXT,        -- JSON array
        expertise TEXT,        -- JSON object
        language TEXT DEFAULT 'en',
        work_context TEXT,
        preferences TEXT,      -- JSON
        created_at REAL,
        updated_at REAL
    );
    CREATE TABLE IF NOT EXISTS session_memory (
        session_id TEXT PRIMARY KEY,
        user_id TEXT,
        topic TEXT,
        summary TEXT,
        active_goals TEXT,     -- JSON array
        entities TEXT,         -- JSON array
        created_at REAL,
        last_active REAL
    );
    CREATE TABLE IF NOT EXISTS semantic_memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        category TEXT NOT NULL,
        content TEXT NOT NULL,
        embedding BLOB,
        importance REAL DEFAULT 0.5,
        source_session TEXT,
        created_at REAL,
        last_accessed REAL,
        access_count INTEGER DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_semantic_user ON semantic_memory(user_id, category);
    CREATE TABLE IF NOT EXISTS procedural_memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        task_pattern TEXT,
        approach TEXT,
        success_count INTEGER DEFAULT 0,
        failure_count INTEGER DEFAULT 0,
        avg_duration_ms REAL DEFAULT 0.0,
        last_used REAL
    );
    CREATE INDEX IF NOT EXISTS idx_procedural_user ON procedural_memory(user_id, task_pattern);
    """

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path
        self._lock = threading.RLock()
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        return init_db(self._db_path)

    def _ensure_schema(self) -> None:
        with self._lock:
            with self._conn() as conn:
                conn.executescript(self._SCHEMA_SQL)
                conn.commit()

    # -----------------------------------------------------------------------
    # UserProfile
    # -----------------------------------------------------------------------

    def get_profile(self, user_id: str) -> UserProfile:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM user_profile WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            now = time.time()
            return UserProfile(user_id=user_id, created_at=now, updated_at=now)
        d = dict(row)
        d["interests"] = json.loads(d.get("interests") or "[]")
        d["expertise"] = json.loads(d.get("expertise") or "{}")
        d["preferences"] = json.loads(d.get("preferences") or "{}")
        return UserProfile(**d)

    def upsert_profile(self, profile: UserProfile) -> UserProfile:
        now = time.time()
        if not profile.created_at:
            profile.created_at = now
        profile.updated_at = now
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO user_profile (user_id, display_name, interests, expertise, language, work_context, preferences, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    interests=excluded.interests,
                    expertise=excluded.expertise,
                    language=excluded.language,
                    work_context=excluded.work_context,
                    preferences=excluded.preferences,
                    updated_at=excluded.updated_at
                """,
                (
                    profile.user_id,
                    profile.display_name,
                    json.dumps(list(profile.interests)),
                    json.dumps(dict(profile.expertise)),
                    profile.language,
                    profile.work_context,
                    json.dumps(dict(profile.preferences)),
                    profile.created_at,
                    profile.updated_at,
                ),
            )
        return profile

    def add_interest(self, user_id: str, interest: str) -> UserProfile:
        p = self.get_profile(user_id)
        if interest and interest not in p.interests:
            p.interests.append(interest)
        return self.upsert_profile(p)

    def set_expertise(self, user_id: str, domain: str, level: str) -> UserProfile:
        p = self.get_profile(user_id)
        p.expertise[domain] = level
        return self.upsert_profile(p)

    # -----------------------------------------------------------------------
    # SessionMemory
    # -----------------------------------------------------------------------

    def get_session(self, session_id: str) -> SessionMemory:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM session_memory WHERE session_id = ?", (session_id,)).fetchone()
        if not row:
            return SessionMemory(session_id=session_id)
        d = dict(row)
        d["active_goals"] = json.loads(d.get("active_goals") or "[]")
        d["entities"] = json.loads(d.get("entities") or "[]")
        return SessionMemory(**d)

    def upsert_session(self, session: SessionMemory) -> SessionMemory:
        now = time.time()
        if not session.created_at:
            session.created_at = now
        session.last_active = now
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO session_memory (session_id, user_id, topic, summary, active_goals, entities, created_at, last_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    user_id=excluded.user_id,
                    topic=excluded.topic,
                    summary=excluded.summary,
                    active_goals=excluded.active_goals,
                    entities=excluded.entities,
                    last_active=excluded.last_active
                """,
                (
                    session.session_id,
                    session.user_id,
                    session.topic,
                    session.summary,
                    json.dumps(list(session.active_goals)),
                    json.dumps(list(session.entities)),
                    session.created_at,
                    session.last_active,
                ),
            )
        return session

    def list_sessions(self, user_id: str, limit: int = 20) -> list[SessionMemory]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM session_memory WHERE user_id = ? ORDER BY last_active DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        out: list[SessionMemory] = []
        for r in rows:
            d = dict(r)
            d["active_goals"] = json.loads(d.get("active_goals") or "[]")
            d["entities"] = json.loads(d.get("entities") or "[]")
            out.append(SessionMemory(**d))
        return out

    # -----------------------------------------------------------------------
    # SemanticMemory
    # -----------------------------------------------------------------------

    def store_semantic(
        self,
        user_id: str,
        content: str,
        category: str = "fact",
        importance: float = 0.5,
        embedding: Optional[bytes] = None,
        source_session: Optional[str] = None,
    ) -> int:
        now = time.time()
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO semantic_memory (user_id, category, content, embedding, importance, source_session, created_at, last_accessed, access_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (user_id, category, content, embedding, importance, source_session, now, now),
            )
            return int(cur.lastrowid or 0)

    def list_semantic(self, user_id: str, category: Optional[str] = None, limit: int = 50) -> list[SemanticMemory]:
        with self._conn() as conn:
            if category:
                rows = conn.execute(
                    "SELECT * FROM semantic_memory WHERE user_id = ? AND category = ? ORDER BY importance DESC, created_at DESC LIMIT ?",
                    (user_id, category, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM semantic_memory WHERE user_id = ? ORDER BY importance DESC, created_at DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
        return [SemanticMemory(**dict(r)) for r in rows]

    def get_semantic(self, mem_id: int) -> Optional[SemanticMemory]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM semantic_memory WHERE id = ?", (mem_id,)).fetchone()
        if not row:
            return None
        return SemanticMemory(**dict(row))

    def delete_semantic(self, mem_id: int, user_id: Optional[str] = None) -> bool:
        with self._conn() as conn:
            if user_id:
                cur = conn.execute(
                    "DELETE FROM semantic_memory WHERE id = ? AND user_id = ?",
                    (mem_id, user_id),
                )
            else:
                cur = conn.execute("DELETE FROM semantic_memory WHERE id = ?", (mem_id,))
        return cur.rowcount > 0

    def access_semantic(self, mem_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE semantic_memory SET last_accessed = ?, access_count = access_count + 1 WHERE id = ?",
                (time.time(), mem_id),
            )

    def store_embedding(self, mem_id: int, embedding_bytes: bytes) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE semantic_memory SET embedding = ? WHERE id = ?", (embedding_bytes, mem_id))

    def list_all_with_embeddings(self, user_id: str) -> list[SemanticMemory]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM semantic_memory WHERE user_id = ? AND embedding IS NOT NULL ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        return [SemanticMemory(**dict(r)) for r in rows]

    # -----------------------------------------------------------------------
    # ProceduralMemory
    # -----------------------------------------------------------------------

    def get_or_create_procedural(
        self, user_id: str, task_pattern: str, approach: str
    ) -> ProceduralMemory:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM procedural_memory WHERE user_id = ? AND task_pattern = ? AND approach = ?",
                (user_id, task_pattern, approach),
            ).fetchone()
        if row:
            return ProceduralMemory(**dict(row))
        now = time.time()
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO procedural_memory (user_id, task_pattern, approach, success_count, failure_count, avg_duration_ms, last_used) VALUES (?, ?, ?, 0, 0, 0, ?)",
                (user_id, task_pattern, approach, now),
            )
            pid = int(cur.lastrowid or 0)
        return ProceduralMemory(id=pid, user_id=user_id, task_pattern=task_pattern, approach=approach, last_used=now)

    def record_procedural_outcome(
        self, proc_id: int, success: bool, duration_ms: float
    ) -> ProceduralMemory:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM procedural_memory WHERE id = ?", (proc_id,)).fetchone()
            if not row:
                raise ValueError(f"Procedural memory {proc_id} not found")
            d = dict(row)
            old_avg = float(d["avg_duration_ms"] or 0.0)
            n = (d["success_count"] or 0) + (d["failure_count"] or 0)
            new_avg = ((old_avg * n) + duration_ms) / (n + 1) if n >= 0 else duration_ms
            new_success = (d["success_count"] or 0) + (1 if success else 0)
            new_failure = (d["failure_count"] or 0) + (0 if success else 1)
            conn.execute(
                """
                UPDATE procedural_memory
                SET success_count = ?, failure_count = ?, avg_duration_ms = ?, last_used = ?
                WHERE id = ?
                """,
                (new_success, new_failure, new_avg, time.time(), proc_id),
            )
            row = conn.execute("SELECT * FROM procedural_memory WHERE id = ?", (proc_id,)).fetchone()
        return ProceduralMemory(**dict(row))

    def list_procedural(self, user_id: str, limit: int = 20) -> list[ProceduralMemory]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM procedural_memory WHERE user_id = ? ORDER BY last_used DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [ProceduralMemory(**dict(r)) for r in rows]

    def best_approach(self, user_id: str, task_pattern: str, min_attempts: int = 2) -> Optional[ProceduralMemory]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM procedural_memory
                WHERE user_id = ? AND task_pattern = ?
                  AND (success_count + failure_count) >= ?
                ORDER BY (CAST(success_count AS REAL) / MAX(1, success_count + failure_count)) DESC, avg_duration_ms ASC
                LIMIT 1
                """,
                (user_id, task_pattern, min_attempts),
            ).fetchall()
        if not rows:
            return None
        return ProceduralMemory(**dict(rows[0]))

    # -----------------------------------------------------------------------
    # Bulk operations
    # -----------------------------------------------------------------------

    def stats(self, user_id: str) -> dict:
        with self._conn() as conn:
            n_profile = conn.execute("SELECT COUNT(*) FROM user_profile WHERE user_id = ?", (user_id,)).fetchone()[0]
            n_session = conn.execute("SELECT COUNT(*) FROM session_memory WHERE user_id = ?", (user_id,)).fetchone()[0]
            n_semantic = conn.execute("SELECT COUNT(*) FROM semantic_memory WHERE user_id = ?", (user_id,)).fetchone()[0]
            n_proc = conn.execute("SELECT COUNT(*) FROM procedural_memory WHERE user_id = ?", (user_id,)).fetchone()[0]
        return {
            "profiles": n_profile,
            "sessions": n_session,
            "semantic_memories": n_semantic,
            "procedural_memories": n_proc,
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_STORE: Optional[MemoryStore] = None
_STORE_LOCK = threading.Lock()


def get_memory() -> MemoryStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = MemoryStore()
    return _STORE


def reset_memory(db_path: Optional[str] = None) -> MemoryStore:
    """Reset the singleton and clear all memory tables (used by tests)."""
    global _STORE
    with _STORE_LOCK:
        _STORE = MemoryStore(db_path=db_path)
        # Wipe tables so subsequent tests start clean
        try:
            with _STORE._conn() as conn:
                conn.executescript("""
                    DELETE FROM user_profile;
                    DELETE FROM session_memory;
                    DELETE FROM semantic_memory;
                    DELETE FROM procedural_memory;
                """)
                conn.commit()
        except Exception:
            pass
    return _STORE
