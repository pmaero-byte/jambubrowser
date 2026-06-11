"""
Migrations for the memory system.

These are applied idempotently at first import via MemoryStore._ensure_schema.
The SQL itself lives in MemoryStore._SCHEMA_SQL for simplicity — this module
documents the version history.

Schema versions
---------------
v1 (current) — user_profile, session_memory, semantic_memory, procedural_memory
"""

from __future__ import annotations

# Reserved for future schema migrations. Today the schema is in
# MemoryStore._SCHEMA_SQL and is idempotent via CREATE TABLE IF NOT EXISTS.
#
# When the schema changes:
# 1. Bump _SCHEMA_VERSION
# 2. Add a new function `migrate_v1_to_v2(conn)`
# 3. Wire it into MemoryStore._ensure_schema
SCHEMA_VERSION = 1
