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
                _memory_db_conn.enable_load_extension(True)
                sqlite_vec.load(_memory_db_conn)
                _memory_db_conn.enable_load_extension(False)
            conn = _memory_db_conn
    else:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    
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
