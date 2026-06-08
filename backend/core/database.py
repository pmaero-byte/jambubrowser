"""
Database & Semantic Memory Management
=====================================
This module handles the 'Memory' of the browser. It creates a local
SQLite database that stores everything the agent finds.

The 'vec0' part allows us to save text as numbers (vectors) so the
AI can 'remember' things by their meaning, not just exact words.

Refactored with connection pooling and context manager support.
"""

import sqlite3
import sqlite_vec
import os
import threading
from contextlib import contextmanager
from typing import Optional

DB_PATH = os.environ.get("JAMBU_DB_PATH", "rag_data.db")

# Module-level singleton for in-memory databases to allow cross-request state sharing
_memory_db_conn = None
_memory_db_lock = threading.Lock()

# Thread-local connection pool
_local = threading.local()

def _get_local_conn() -> Optional[sqlite3.Connection]:
    """Get the thread-local connection if it exists."""
    return getattr(_local, 'conn', None)

def _set_local_conn(conn: sqlite3.Connection):
    """Set the thread-local connection."""
    _local.conn = conn

def init_db(db_path: str = None) -> sqlite3.Connection:
    """
    Initializes the local database and creates the tables needed for
    storing research documents, vector embeddings, and background missions.
    
    Returns a connection. Caller is responsible for closing it unless
    using get_db() context manager.
    """
    global _memory_db_conn
    path = db_path or DB_PATH
    
    # Reuse singleton connection for in-memory databases
    if path == ":memory:":
        with _memory_db_lock:
            if _memory_db_conn is None:
                _memory_db_conn = sqlite3.connect(path)
                _memory_db_conn.row_factory = sqlite3.Row
                # Enable load extension if supported (not available in Apple's Python)
                if hasattr(_memory_db_conn, 'enable_load_extension'):
                    _memory_db_conn.enable_load_extension(True)
                    sqlite_vec.load(_memory_db_conn)
                    _memory_db_conn.enable_load_extension(False)
                else:
                    # Fallback: try to load sqlite_vec without extension loading
                    try:
                        sqlite_vec.load(_memory_db_conn)
                    except Exception:
                        pass  # sqlite_vec not available
            conn = _memory_db_conn
    else:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        # Enable load extension if supported (not available in Apple's Python)
        if hasattr(conn, 'enable_load_extension'):
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
        else:
            # Fallback: try to load sqlite_vec without extension loading
            try:
                sqlite_vec.load(conn)
            except Exception:
                pass  # sqlite_vec not available
    
    cursor = conn.cursor()
    
    # Standard table for raw text and URLs
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT,
            text TEXT,
            created_at REAL DEFAULT (julianday('now'))
        )
    """)
    
    # Virtual table for high-speed AI 'meaning' search (384 dimensions for all-MiniLM)
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_documents USING vec0(
            id INTEGER PRIMARY KEY,
            embedding float[384]
        )
    """)
    
    # Cache for embeddings to save computer power
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS embedding_cache (
            hash TEXT PRIMARY KEY,
            embedding BLOB
        )
    """)
    
    # Tables for background tasks and custom AI skills
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS missions (
            id TEXT PRIMARY KEY,
            query TEXT,
            status TEXT DEFAULT 'active',
            last_run REAL,
            next_run REAL,
            schedule TEXT,
            created_at REAL DEFAULT (julianday('now'))
        )
    """)
    
    # Migration: Add next_run column if missing (for existing databases)
    try:
        cursor.execute("SELECT next_run FROM missions LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE missions ADD COLUMN next_run REAL")
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS custom_tools (
            name TEXT PRIMARY KEY,
            description TEXT,
            file_path TEXT,
            created_at REAL DEFAULT (julianday('now'))
        )
    """)
    
    # Credential vault table (Phase 1: Foundation Hardening)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS credential_vault (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL,
            url_pattern TEXT,
            username TEXT,
            password_encrypted TEXT NOT NULL,
            metadata TEXT,
            created_at REAL DEFAULT (julianday('now')),
            last_used REAL,
            UNIQUE(domain, username)
        )
    """)
    
    # Consensus engine tables (Phase 4: Federated Sovereign Mesh)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS proposals (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            options_json TEXT NOT NULL DEFAULT '[]',
            required_nodes INTEGER NOT NULL DEFAULT 3,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at REAL NOT NULL,
            decided_at REAL,
            winner TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS votes (
            id TEXT PRIMARY KEY,
            proposal_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            choice TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0,
            reasoning TEXT,
            created_at REAL NOT NULL,
            FOREIGN KEY (proposal_id) REFERENCES proposals(id),
            UNIQUE(proposal_id, node_id)
        )
    """)
    # Browser session persistence table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS browser_sessions (
            id TEXT PRIMARY KEY,
            name TEXT,
            cookies TEXT,
            local_storage TEXT,
            user_agent TEXT,
            proxy TEXT,
            created_at REAL DEFAULT (julianday('now')),
            last_used REAL
        )
    """)

    # ── Phase 1: Harness Gateway Compatibility ──────────────────────────
    # Memory entries (Harness-compatible FTS5 store)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memory_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL DEFAULT 'general',
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            importance REAL DEFAULT 0.5,
            created_at REAL DEFAULT (julianday('now')),
            last_accessed REAL,
            access_count INTEGER DEFAULT 0
        )
    """)

    # FTS5 virtual table for full-text memory search
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
            category, key, value,
            content='memory_entries',
            content_rowid='id'
        )
    """)

    # Auto-sync triggers: keep FTS5 in sync with memory_entries
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS memory_fts_insert AFTER INSERT ON memory_entries BEGIN
            INSERT INTO memory_fts(rowid, category, key, value)
            VALUES (new.id, new.category, new.key, new.value);
        END
    """)

    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS memory_fts_delete AFTER DELETE ON memory_entries BEGIN
            INSERT INTO memory_fts(memory_fts, rowid, category, key, value)
            VALUES ('delete', old.id, old.category, old.key, old.value);
        END
    """)

    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS memory_fts_update AFTER UPDATE ON memory_entries BEGIN
            INSERT INTO memory_fts(memory_fts, rowid, category, key, value)
            VALUES ('delete', old.id, old.category, old.key, old.value);
            INSERT INTO memory_fts(rowid, category, key, value)
            VALUES (new.id, new.category, new.key, new.value);
        END
    """)

    # Sessions table (Harness-compatible)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            name TEXT,
            created_at REAL DEFAULT (julianday('now')),
            last_active REAL,
            task_count INTEGER DEFAULT 0,
            total_duration_ms INTEGER DEFAULT 0,
            endpoints_used TEXT,
            status TEXT DEFAULT 'active'
        )
    """)

    # ── Phase 1: Analytics Engine ─────────────────────────────────────────
    # Task performance metrics
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS task_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint TEXT NOT NULL,
            method TEXT NOT NULL,
            status TEXT NOT NULL,
            duration_ms INTEGER,
            error_type TEXT,
            session_id TEXT,
            timestamp REAL DEFAULT (julianday('now'))
        )
    """)

    # Tool usage tracking
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tool_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tool_name TEXT UNIQUE NOT NULL,
            call_count INTEGER DEFAULT 0,
            success_count INTEGER DEFAULT 0,
            failure_count INTEGER DEFAULT 0,
            total_duration_ms INTEGER DEFAULT 0,
            avg_duration_ms REAL DEFAULT 0.0,
            last_used REAL
        )
    """)

    # Provider quota tracking
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS provider_quota (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT UNIQUE NOT NULL,
            daily_limit INTEGER,
            daily_used INTEGER DEFAULT 0,
            reset_date TEXT
        )
    """)

    # Session analytics
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS session_analytics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            task_count INTEGER DEFAULT 0,
            total_duration_ms INTEGER DEFAULT 0,
            endpoints_used TEXT,
            started_at REAL,
            ended_at REAL,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
    """)

    conn.commit()
    return conn


@contextmanager
def get_db(db_path: str = None):
    """
    Context manager for database connections.

    Usage:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT ...")

    Automatically commits on success, rolls back on exception.
    Does NOT close in-memory singleton connections (shared across requests).
    """
    path = db_path or DB_PATH
    is_memory = (path == ":memory:")
    conn = init_db(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if not is_memory:
            conn.close()


@contextmanager
def get_db_cursor(db_path: str = None):
    """
    Context manager that provides a cursor directly.
    
    Usage:
        with get_db_cursor() as cursor:
            cursor.execute("SELECT ...")
            rows = cursor.fetchall()
    """
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        try:
            yield cursor
        finally:
            cursor.close()


def clear_memory(db_path: str = None):
    """Wipes all saved research data. Use this for a fresh start."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM documents")
        cursor.execute("DELETE FROM vec_documents")
        # Don't clear missions, custom_tools, or credential_vault
        conn.commit()


def clear_all(db_path: str = None):
    """Wipes ALL data including missions, tools, and credentials."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM documents")
        cursor.execute("DELETE FROM vec_documents")
        cursor.execute("DELETE FROM embedding_cache")
        cursor.execute("DELETE FROM missions")
        cursor.execute("DELETE FROM custom_tools")
        cursor.execute("DELETE FROM credential_vault")
        cursor.execute("DELETE FROM browser_sessions")
        conn.commit()


def smart_chunking(text: str) -> list:
    """
    Splits text into overlapping semantic chunks for indexing.
    Uses paragraph breaks and sentence boundaries.
    
    Args:
        text: Raw text to chunk
        
    Returns:
        List of text chunks (max ~1000 chars each)
    """
    import re
    chunks = re.split(r'\n\n|\.\s', text)
    processed = []
    current = ""
    for c in chunks:
        if len(current) + len(c) < 1000:
            current += c + " "
        else:
            if current.strip():
                processed.append(current.strip())
            current = c + " "
    if current.strip():
        processed.append(current.strip())
    return processed if processed else [text[:1000]]


def get_stats(db_path: str = None) -> dict:
    """Returns statistics about the database contents."""
    with get_db_cursor(db_path) as cursor:
        cursor.execute("SELECT COUNT(*) FROM documents")
        doc_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM missions WHERE status = 'active'")
        mission_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM custom_tools")
        tool_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM credential_vault")
        cred_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM browser_sessions")
        session_count = cursor.fetchone()[0]
    
    return {
        "documents": doc_count,
        "active_missions": mission_count,
        "custom_tools": tool_count,
        "credentials": cred_count,
        "browser_sessions": session_count,
        "db_path": db_path or DB_PATH
    }


# ── Phase 1: Harness Gateway Compatibility Helpers ────────────────────────

def memory_add(category: str, key: str, value: str,
 importance: float = 0.5, db_path: str = None) -> int:
    """Add a memory entry. Returns the row ID."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO memory_entries (category, key, value, importance) VALUES (?, ?, ?, ?)",
            (category, key, value, importance)
        )
        return cursor.lastrowid


def memory_search(query: str, limit: int = 10, db_path: str = None) -> list:
    """
    FTS5 full-text search over memory entries.
    Uses bm25 ranking with importance weighting.
    """
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        try:
            # Try bm25 ranking (SQLite FTS5)
            cursor.execute("""
                SELECT m.id, m.category, m.key, m.value, m.importance,
                       bm25(memory_fts) as rank,
                       CAST(m.importance * 40 + m.access_count * 3 AS REAL) / 100 AS relevance
 FROM memory_fts f
                JOIN memory_entries m ON m.id = f.rowid
                WHERE memory_fts MATCH ?
                ORDER BY relevance DESC, rank ASC
                LIMIT ?
            """, (query, limit))
        except Exception:
            # Fallback: LIKE search
            cursor.execute("""
                SELECT id, category, key, value, importance, 0 as rank, importance as relevance
                FROM memory_entries
                WHERE key LIKE ? OR value LIKE ?
                ORDER BY importance DESC
                LIMIT ?
            """, (f"%{query}%", f"%{query}%", limit))

        rows = cursor.fetchall()
        # Update access stats
        for row in rows:
            cursor.execute(
                "UPDATE memory_entries SET last_accessed = julianday('now'), access_count = access_count + 1 WHERE id = ?",
                (row[0],)
            )
        return [dict(row) for row in rows]


def memory_list(category: str = None, limit: int = 50, db_path: str = None) -> list:
    """List memory entries, optionally filtered by category."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        if category:
            cursor.execute("""
                SELECT id, category, key, value, importance, created_at, access_count
                FROM memory_entries
                WHERE category = ?
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
            """, (category, limit))
        else:
            cursor.execute("""
                SELECT id, category, key, value, importance, created_at, access_count
                FROM memory_entries
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
            """, (limit,))
        return [dict(row) for row in cursor.fetchall()]


def memory_delete(entry_id: int, db_path: str = None) -> bool:
    """Delete a memory entry by ID."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM memory_entries WHERE id = ?", (entry_id,))
        return cursor.rowcount > 0


# ── Phase 1: Session Management ──────────────────────────────────────────

def session_create(session_id: str, name: str = None, db_path: str = None) -> dict:
    """Create a new session."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO sessions (id, name) VALUES (?, ?)",
            (session_id, name or f"Session {session_id[:8]}")
        )
        return {"id": session_id, "name": name}


def session_update(session_id: str, endpoints_used: list = None,
                   task_count_delta: int = 0, duration_ms: int = 0,
                   db_path: str = None) -> dict:
    """Update session stats after a task completes."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        if endpoints_used:
            cursor.execute(
                "UPDATE sessions SET last_active = julianday('now'), "
                "task_count = task_count + ?, total_duration_ms = total_duration_ms + ?, "
                "endpoints_used = ? WHERE id = ?",
                (task_count_delta, duration_ms, ",".join(endpoints_used), session_id)
            )
        else:
            cursor.execute(
                "UPDATE sessions SET last_active = julianday('now'), "
                "task_count = task_count + ?, total_duration_ms = total_duration_ms + ? "
                "WHERE id = ?",
                (task_count_delta, duration_ms, session_id)
            )
        return {"id": session_id}


def session_list(limit: int = 20, db_path: str = None) -> list:
    """List recent sessions."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, name, created_at, last_active, task_count,
                   total_duration_ms, endpoints_used, status
            FROM sessions
            ORDER BY last_active DESC, created_at DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]


def session_get(session_id: str, db_path: str = None) -> dict:
    """Get session detail."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, name, created_at, last_active, task_count,
                   total_duration_ms, endpoints_used, status
            FROM sessions WHERE id = ?
        """, (session_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


# ── Phase 1: Analytics Helpers ───────────────────────────────────────────

def record_task_metric(endpoint: str, method: str, status: str,
                      duration_ms: int = None, error_type: str = None,
                      session_id: str = None, db_path: str = None):
    """Record a task execution metric."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO task_metrics (endpoint, method, status, duration_ms, error_type, session_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (endpoint, method, status, duration_ms, error_type, session_id))


def record_tool_usage(tool_name: str, success: bool, duration_ms: int = 0,
                      db_path: str = None):
    """Record tool usage, updating aggregate stats."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT call_count, total_duration_ms FROM tool_usage WHERE tool_name = ?",
 (tool_name,))
        row = cursor.fetchone()
        if row:
            new_count = row[0] + 1
            new_total = row[1] + (duration_ms or 0)
            new_avg = new_total / new_count
            success_col = "success_count" if success else "failure_count"
            cursor.execute(f"""
                UPDATE tool_usage SET call_count = ?, {success_col} = {success_col} + 1,
                total_duration_ms = ?, avg_duration_ms = ?, last_used = julianday('now')
                WHERE tool_name = ?
            """, (new_count, new_total, new_avg, tool_name))
        else:
            cursor.execute("""
                INSERT INTO tool_usage (tool_name, call_count, success_count, failure_count,
                                        total_duration_ms, avg_duration_ms, last_used)
                VALUES (?, 1, ?,0, ?, ?, julianday('now'))
            """, (tool_name, 1 if success else 0, duration_ms or 0, duration_ms or 0))


def get_analytics_summary(days: int = 7, db_path: str = None) -> dict:
    """Return analytics summary for the last N days."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()

        # Task metrics by endpoint
        cursor.execute("""
            SELECT endpoint, status, COUNT(*) as count, AVG(duration_ms) as avg_ms
            FROM task_metrics
            WHERE timestamp > datetime('now', '-' || ? || ' days')
            GROUP BY endpoint, status""", (days,))
        task_metrics = [dict(row) for row in cursor.fetchall()]

        # Error rate
        cursor.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors
            FROM task_metrics
            WHERE timestamp > datetime('now', '-' || ? || ' days')""", (days,))
        row = cursor.fetchone()
        error_rate = (row["errors"] / row["total"] * 100) if row["total"] > 0 else 0

        # Top endpoints
        cursor.execute("""
            SELECT endpoint, COUNT(*) as calls
            FROM task_metrics
            WHERE timestamp > datetime('now', '-' || ? || ' days')
            GROUP BY endpoint
            ORDER BY calls DESC
            LIMIT 10""", (days,))
        top_endpoints = [dict(row) for row in cursor.fetchall()]

        # Uptime
        cursor.execute("SELECT MIN(timestamp) FROM task_metrics")
        row = cursor.fetchone()
        uptime_s = int((sqlite3.Connection(db_path or DB_PATH)
                        .execute("SELECT julianday('now')").fetchone()[0]
                        - (row[0] or 0)) * 86400) if row[0] else 0

        return {
            "task_metrics": task_metrics,
            "error_rate_pct": round(error_rate, 2),
            "top_endpoints": top_endpoints,
            "uptime_s": uptime_s,
            "days": days,
        }
