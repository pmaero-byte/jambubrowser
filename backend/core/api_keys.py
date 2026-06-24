"""API Key management — generate, validate, store keys per user/team."""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

from backend.core.database import get_db


@dataclass
class APIKey:
    id: int
    key_prefix: str
    key_hash: str
    name: str
    tier: str
    owner: str
    created_at: float
    last_used: Optional[float]
    is_active: bool
    monthly_audit_limit: int
    monthly_audits_used: int


TIER_LIMITS = {
    "free": {"quick_scans": 5, "full_audits": 1, "monthly_audits": 6},
    "pro": {"quick_scans": -1, "full_audits": 50, "monthly_audits": 50},
    "team": {"quick_scans": -1, "full_audits": -1, "monthly_audits": -1},
    "enterprise": {"quick_scans": -1, "full_audits": -1, "monthly_audits": -1},
}


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_api_key(
    name: str,
    tier: str = "free",
    owner: str = "default",
) -> tuple[str, APIKey]:
    """Generate a new API key. Returns (raw_key, APIKey metadata)."""
    raw_key = f"jambu_{secrets.token_urlsafe(32)}"
    key_hash = _hash_key(raw_key)
    key_prefix = raw_key[:12]
    limits = TIER_LIMITS.get(tier, TIER_LIMITS["free"])

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO api_keys (key_prefix, key_hash, name, tier, owner, monthly_audit_limit)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (key_prefix, key_hash, name, tier, owner, limits["monthly_audits"]))
        conn.commit()
        key_id = cursor.lastrowid

    api_key = APIKey(
        id=key_id,
        key_prefix=key_prefix,
        key_hash=key_hash,
        name=name,
        tier=tier,
        owner=owner,
        created_at=time.time(),
        last_used=None,
        is_active=True,
        monthly_audit_limit=limits["monthly_audits"],
        monthly_audits_used=0,
    )
    return raw_key, api_key


def validate_api_key(raw_key: str) -> Optional[APIKey]:
    """Validate an API key and return its metadata, or None if invalid."""
    if not raw_key or not raw_key.startswith("jambu_"):
        return None

    key_hash = _hash_key(raw_key)
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM api_keys WHERE key_hash = ? AND is_active = 1",
            (key_hash,),
        ).fetchone()

    if not row:
        return None

    now = time.time()
    month_start = now - (now % (30 * 86400))

    with get_db() as conn:
        usage_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM audit_usage WHERE key_id = ? AND created_at > ?",
            (row["id"], month_start),
        ).fetchone()
        audits_used = usage_row["cnt"] if usage_row else 0

    with get_db() as conn:
        conn.execute(
            "UPDATE api_keys SET last_used = ? WHERE id = ?",
            (now, row["id"]),
        )
        conn.commit()

    return APIKey(
        id=row["id"],
        key_prefix=row["key_prefix"],
        key_hash=row["key_hash"],
        name=row["name"],
        tier=row["tier"],
        owner=row["owner"],
        created_at=row["created_at"],
        last_used=now,
        is_active=bool(row["is_active"]),
        monthly_audit_limit=row["monthly_audit_limit"],
        monthly_audits_used=audits_used,
    )


def list_api_keys(owner: str = "default") -> list[APIKey]:
    """List all API keys for an owner."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM api_keys WHERE owner = ? ORDER BY created_at DESC",
            (owner,),
        ).fetchall()

    keys = []
    now = time.time()
    month_start = now - (now % (30 * 86400))

    for row in rows:
        with get_db() as conn:
            usage_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM audit_usage WHERE key_id = ? AND created_at > ?",
                (row["id"], month_start),
            ).fetchone()

        keys.append(APIKey(
            id=row["id"],
            key_prefix=row["key_prefix"],
            key_hash=row["key_hash"],
            name=row["name"],
            tier=row["tier"],
            owner=row["owner"],
            created_at=row["created_at"],
            last_used=row["last_used"],
            is_active=bool(row["is_active"]),
            monthly_audit_limit=row["monthly_audit_limit"],
            monthly_audits_used=usage_row["cnt"] if usage_row else 0,
        ))
    return keys


def deactivate_api_key(key_id: int, owner: str = "default") -> bool:
    """Deactivate an API key. Returns True if found and deactivated."""
    with get_db() as conn:
        result = conn.execute(
            "UPDATE api_keys SET is_active = 0 WHERE id = ? AND owner = ?",
            (key_id, owner),
        )
        conn.commit()
        return result.rowcount > 0


def check_audit_quota(api_key: APIKey, audit_mode: str) -> tuple[bool, str]:
    """Check if the API key has quota for this audit. Returns (allowed, reason)."""
    if api_key.monthly_audit_limit == -1:
        return True, "unlimited"

    if api_key.monthly_audits_used >= api_key.monthly_audit_limit:
        return False, f"Monthly audit limit reached ({api_key.monthly_audit_limit}). Upgrade your plan for more."

    return True, "ok"


def record_audit_usage(key_id: int, audit_mode: str, url: str, findings_count: int, duration_ms: float):
    """Record an audit in the usage table."""
    with get_db() as conn:
        conn.execute("""
            INSERT INTO audit_usage (key_id, audit_mode, url, findings_count, duration_ms)
            VALUES (?, ?, ?, ?, ?)
        """, (key_id, audit_mode, url, findings_count, duration_ms))
        conn.commit()
