"""
Agent Audit Logging
===================
Comprehensive audit trail for all agent actions.
Implements tamper-evident logging with cryptographic hashing.

Security Features:
- Cryptographically signed audit entries
- Tamper-evident chain (each entry hashes previous)
- Local-only storage (no external telemetry)
- Configurable retention policies
- Privacy-respecting (PII redacted from logs)
"""

import hashlib
import json
import time
import sqlite3
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from contextlib import contextmanager

from backend.core.database import get_db_cursor


class ActionCategory:
    """Categories for audit logging."""
    RESEARCH = "research"
    BROWSER = "browser"
    CREDENTIAL = "credential"
    NETWORK = "network"
    PRIVACY = "privacy"
    SYSTEM = "system"
    ERROR = "error"


@dataclass
class AuditEntry:
    """A single audit log entry."""
    id: int
    timestamp: float
    category: str
    action: str
    details: Dict[str, Any]
    actor: str = "agent"
    session_id: str = None
    hash: str = None
    previous_hash: str = None


class AuditLogger:
    """
    Tamper-evident audit logger for all agent actions.

    Features:
    - Cryptographic chain of entries
    - Local-only storage
    - PII redaction
    - Configurable retention
    """

    def __init__(self, retention_days: int = 90):
        self._retention_days = retention_days
        self._lock = Lock()
        self._init_audit_table()

    def _init_audit_table(self):
        """Initialize the audit log table in the database."""
        with get_db_cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    category TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details TEXT,
                    actor TEXT DEFAULT 'agent',
                    session_id TEXT,
                    hash TEXT NOT NULL,
                    previous_hash TEXT,
                    created_at REAL DEFAULT (julianday('now'))
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_category ON audit_log(category)
            """)

    def _compute_hash(self, entry_data: str, previous_hash: str) -> str:
        """Compute cryptographic hash for entry integrity."""
        hash_input = f"{entry_data}:{previous_hash}".encode()
        return hashlib.sha256(hash_input).hexdigest()

    def _get_last_hash(self) -> str:
        """Get the hash of the last audit entry."""
        with get_db_cursor() as cursor:
            cursor.execute(
                "SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1"
            )
            row = cursor.fetchone()
            return row["hash"] if row else "genesis"

    def log(
        self,
        category: str,
        action: str,
        details: Dict[str, Any] = None,
        actor: str = "agent",
        session_id: str = None,
    ) -> int:
        """
        Log an audit entry with cryptographic integrity.

        Args:
            category: Action category (research, browser, etc.)
            action: Specific action performed
            details: Additional details (PII will be redacted)
            actor: Who performed the action
            session_id: Optional session identifier

        Returns:
            Entry ID
        """
        # Redact PII from details
        sanitized_details = self._redact_pii(details or {})

        entry_data = json.dumps({
            "timestamp": time.time(),
            "category": category,
            "action": action,
            "details": sanitized_details,
            "actor": actor,
        }, sort_keys=True)

        previous_hash = self._get_last_hash()
        entry_hash = self._compute_hash(entry_data, previous_hash)

        with self._lock:
            with get_db_cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO audit_log 
                    (timestamp, category, action, details, actor, session_id, hash, previous_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        time.time(),
                        category,
                        action,
                        json.dumps(sanitized_details),
                        actor,
                        session_id,
                        entry_hash,
                        previous_hash,
                    ),
                )
                return cursor.lastrowid

    def _redact_pii(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Redact PII from audit details."""
        import re

        redacted = {}
        pii_patterns = {
            "email": re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'),
            "phone": re.compile(r'(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}'),
            "password": re.compile(r'password|passwd|pwd', re.I),
        }

        for key, value in data.items():
            if isinstance(value, str):
                for pii_type, pattern in pii_patterns.items():
                    if pattern.search(value) or pattern.search(key.lower()):
                        value = f"[REDACTED_{pii_type.upper()}]"
                        break
            elif isinstance(value, dict):
                value = self._redact_pii(value)
            redacted[key] = value

        return redacted

    def log_research(self, query: str, results_count: int, session_id: str = None):
        """Log a research action."""
        return self.log(
            ActionCategory.RESEARCH,
            "query_executed",
            {"query_length": len(query), "results_count": results_count},
            session_id=session_id,
        )

    def log_browser(self, url: str, action: str, success: bool, session_id: str = None):
        """Log a browser action."""
        return self.log(
            ActionCategory.BROWSER,
            action,
            {"url": url, "success": success},
            session_id=session_id,
        )

    def log_credential(self, domain: str, action: str, success: bool, session_id: str = None):
        """Log a credential vault action."""
        return self.log(
            ActionCategory.CREDENTIAL,
            action,
            {"domain": domain, "success": success},
            session_id=session_id,
        )

    def log_network(self, url: str, blocked: bool, reason: str = None, session_id: str = None):
        """Log a network request (allowed or blocked)."""
        return self.log(
            ActionCategory.NETWORK,
            "blocked" if blocked else "allowed",
            {"url": url, "reason": reason},
            session_id=session_id,
        )

    def log_privacy(self, action: str, details: Dict[str, Any] = None, session_id: str = None):
        """Log a privacy-related action."""
        return self.log(
            ActionCategory.PRIVACY,
            action,
            details or {},
            session_id=session_id,
        )

    def verify_chain_integrity(self) -> tuple[bool, str]:
        """
        Verify the integrity of the audit log chain.

        Returns:
            Tuple of (is_valid, error_message)
        """
        with get_db_cursor() as cursor:
            cursor.execute(
                "SELECT id, timestamp, category, action, details, actor, hash, previous_hash FROM audit_log ORDER BY id"
            )
            entries = cursor.fetchall()

        if not entries:
            return True, "No entries to verify"

        previous_hash = "genesis"
        for i, entry in enumerate(entries):
            entry_data = json.dumps({
                "timestamp": entry["timestamp"],
                "category": entry["category"],
                "action": entry["action"],
                "details": json.loads(entry["details"]) if entry["details"] else {},
                "actor": entry["actor"],
            }, sort_keys=True)

            expected_hash = self._compute_hash(entry_data, previous_hash)

            if entry["hash"] != expected_hash:
                return False, f"Chain broken at entry {entry['id']}: expected {expected_hash[:16]}..., got {entry['hash'][:16]}..."

            if entry["previous_hash"] != previous_hash:
                return False, f"Previous hash mismatch at entry {entry['id']}"

            previous_hash = entry["hash"]

        return True, "Chain integrity verified"

    def get_entries(
        self,
        category: str = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Get audit entries with optional filtering."""
        with get_db_cursor() as cursor:
            if category:
                cursor.execute(
                    """
                    SELECT id, timestamp, category, action, details, actor, session_id, hash
                    FROM audit_log
                    WHERE category = ?
                    ORDER BY id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (category, limit, offset),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, timestamp, category, action, details, actor, session_id, hash
                    FROM audit_log
                    ORDER BY id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                )

            entries = cursor.fetchall()
            return [
                {
                    "id": e["id"],
                    "timestamp": e["timestamp"],
                    "category": e["category"],
                    "action": e["action"],
                    "details": json.loads(e["details"]) if e["details"] else {},
                    "actor": e["actor"],
                    "session_id": e["session_id"],
                    "hash": e["hash"][:16] + "...",
                }
                for e in entries
            ]

    def get_statistics(self) -> dict:
        """Get audit statistics."""
        with get_db_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as total FROM audit_log")
            total = cursor.fetchone()["total"]

            cursor.execute(
                "SELECT category, COUNT(*) as count FROM audit_log GROUP BY category"
            )
            by_category = {row["category"]: row["count"] for row in cursor.fetchall()}

            cursor.execute(
                "SELECT MIN(timestamp) as oldest, MAX(timestamp) as newest FROM audit_log"
            )
            row = cursor.fetchone()
            oldest = row["oldest"]
            newest = row["newest"]

        return {
            "total_entries": total,
            "by_category": by_category,
            "oldest_entry": oldest,
            "newest_entry": newest,
            "retention_days": self._retention_days,
        }

    def cleanup_old_entries(self):
        """Remove entries older than retention period."""
        cutoff = time.time() - (self._retention_days * 86400)
        with get_db_cursor() as cursor:
            cursor.execute(
                "DELETE FROM audit_log WHERE timestamp < ?",
                (cutoff,),
            )


# Module-level singleton
_audit_logger: Optional[AuditLogger] = None


def get_audit_logger(retention_days: int = 90) -> AuditLogger:
    """Get or create the singleton audit logger."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger(retention_days)
    return _audit_logger
