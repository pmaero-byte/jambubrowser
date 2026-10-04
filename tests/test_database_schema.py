"""
The schema itself is a contract: every route, monitor and payment path reads
from one of these objects, and a table that quietly stops being created only
surfaces as a runtime error in a feature nobody was testing.

`init_db` used to be one 891-line function, so there was nothing to diff when
a migration went wrong. It is now 13 per-domain helpers called in a fixed
order. This test pins the resulting schema so a future split, rename or
dropped CREATE has to be a deliberate, visible edit here.
"""

from __future__ import annotations

import sqlite3

import pytest

from backend.core.database import init_db


# Every table, index, trigger and view a fresh database ends up with, in the
# order sqlite reports them. 69 objects.
EXPECTED_OBJECTS = [
        ("index", "idx_a2a_tasks"),
        ("index", "idx_dismissed_url"),
        ("index", "idx_evidence_bundles"),
        ("index", "idx_flow_monitor_runs"),
        ("index", "idx_meshpay_anchors"),
        ("index", "idx_meshpay_payouts"),
        ("index", "idx_mission_results_mission_id"),
        ("index", "idx_monitor_runs"),
        ("index", "idx_qa_heal_events"),
        ("index", "idx_qa_runs"),
        ("index", "idx_simulation_jobs_created"),
        ("index", "idx_simulation_jobs_idempotency"),
        ("index", "idx_simulation_jobs_spec_hash"),
        ("index", "idx_worker_verdicts"),
        ("index", "idx_x402_receipts"),
        ("table", "a2a_tasks"),
        ("table", "api_keys"),
        ("table", "audit_history"),
        ("table", "audit_monitor_runs"),
        ("table", "audit_monitors"),
        ("table", "audit_usage"),
        ("table", "browser_sessions"),
        ("table", "credential_vault"),
        ("table", "custom_tools"),
        ("table", "dismissed_findings"),
        ("table", "documents"),
        ("table", "embedding_cache"),
        ("table", "evidence_bundles"),
        ("table", "finding_assignments"),
        ("table", "flow_monitor_runs"),
        ("table", "flow_monitors"),
        ("table", "memory_entries"),
        ("table", "memory_fts"),
        ("table", "memory_fts_config"),
        ("table", "memory_fts_data"),
        ("table", "memory_fts_docsize"),
        ("table", "memory_fts_idx"),
        ("table", "meshpay_anchors"),
        ("table", "meshpay_payouts"),
        ("table", "meshpay_wallets"),
        ("table", "mission_results"),
        ("table", "missions"),
        ("table", "proposals"),
        ("table", "provider_quota"),
        ("table", "qa_cases"),
        ("table", "qa_datasets"),
        ("table", "qa_heal_events"),
        ("table", "qa_runs"),
        ("table", "session_analytics"),
        ("table", "session_recordings"),
        ("table", "sessions"),
        ("table", "simulation_jobs"),
        ("table", "task_metrics"),
        ("table", "team_activity"),
        ("table", "team_members"),
        ("table", "teams"),
        ("table", "tool_usage"),
        ("table", "vec_documents"),
        ("table", "vec_documents_chunks"),
        ("table", "vec_documents_info"),
        ("table", "vec_documents_rowids"),
        ("table", "vec_documents_vector_chunks00"),
        ("table", "votes"),
        ("table", "worker_verdicts"),
        ("table", "x402_nonce_claims"),
        ("table", "x402_receipts"),
        ("trigger", "memory_fts_delete"),
        ("trigger", "memory_fts_insert"),
        ("trigger", "memory_fts_update"),
]


@pytest.fixture(scope="module")
def fresh_schema(tmp_path_factory) -> list[tuple[str, str]]:
    """Build a brand-new database and list every object it contains."""
    path = tmp_path_factory.mktemp("schema") / "schema.db"
    init_db(str(path))
    conn = sqlite3.connect(str(path))
    try:
        return [
            (kind, name)
            for kind, name in conn.execute(
                "SELECT type, name FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            )
        ]
    finally:
        conn.close()


def test_fresh_database_matches_the_expected_schema(fresh_schema):
    assert fresh_schema == EXPECTED_OBJECTS


def test_init_db_is_idempotent(tmp_path):
    """Re-running init_db on an existing database must not raise."""
    path = tmp_path / "twice.db"
    init_db(str(path))
    conn = init_db(str(path))          # second run applies the same DDL again
    names = [
        name for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
    ]
    conn.close()
    assert "simulation_jobs" in names
    assert len(names) == len(set(names)), "duplicate schema objects"


def test_schema_helpers_are_all_called(tmp_path, monkeypatch):
    """Every `_schema_*` helper must be reachable from init_db.

    A helper that is defined but never called is a table that silently does
    not exist on a fresh install — the exact failure mode this refactor had
    to avoid introducing.
    """
    import backend.core.database as db

    called: list[str] = []
    helpers = [n for n in dir(db) if n.startswith("_schema_")]
    assert helpers, "expected the schema helpers to exist"
    for name in helpers:
        original = getattr(db, name)

        def wrapper(cursor, _name=name, _original=original):
            called.append(_name)
            return _original(cursor)

        monkeypatch.setattr(db, name, wrapper)

    init_db(str(tmp_path / "called.db"))

    assert sorted(called) == sorted(helpers), "a schema helper was never called"
