"""
SQLite-backed storage for benchmark results.

Persists SuiteResult + TaskResult rows so you can compare across runs and
over time. Reuses the same SQLite connection as the rest of the app
(backend.core.database).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Optional

from backend.core.database import init_db, DB_PATH


_SCHEMA = """
CREATE TABLE IF NOT EXISTS eval_runs (
    run_id TEXT PRIMARY KEY,
    suite TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    started_at REAL,
    completed_at REAL,
    passed INTEGER,
    failed INTEGER,
    errored INTEGER,
    total INTEGER,
    success_rate REAL,
    total_tokens INTEGER,
    total_cost_usd REAL,
    avg_duration_ms REAL,
    metadata TEXT,
    created_at REAL DEFAULT (julianday('now'))
);
CREATE INDEX IF NOT EXISTS idx_eval_runs_suite ON eval_runs(suite, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_eval_runs_provider ON eval_runs(provider, started_at DESC);

CREATE TABLE IF NOT EXISTS eval_task_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    suite TEXT,
    category TEXT,
    difficulty INTEGER,
    status TEXT,
    score REAL,
    duration_ms REAL,
    total_tokens INTEGER,
    cost_usd REAL,
    steps INTEGER,
    answer TEXT,
    expected TEXT,
    error TEXT,
    timestamp REAL,
    FOREIGN KEY (run_id) REFERENCES eval_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_eval_tasks_run ON eval_task_results(run_id);
CREATE INDEX IF NOT EXISTS idx_eval_tasks_task ON eval_task_results(task_id);
"""


class ResultsStore:
    """Persist + retrieve eval results."""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path
        self._lock = threading.RLock()
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        return init_db(self._db_path)

    def _ensure_schema(self) -> None:
        with self._lock:
            with self._conn() as conn:
                conn.executescript(_SCHEMA)
                conn.commit()

    def save_suite(self, sr) -> None:
        """Save a SuiteResult (and all its TaskResults)."""
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO eval_runs
                        (run_id, suite, provider, model, started_at, completed_at,
                         passed, failed, errored, total, success_rate,
                         total_tokens, total_cost_usd, avg_duration_ms, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sr.run_id, sr.suite, sr.provider, sr.model,
                        sr.started_at, sr.completed_at or time.time(),
                        sr.passed, sr.failed, sr.errored, sr.total, sr.success_rate,
                        sr.total_tokens, sr.total_cost_usd, sr.avg_duration_ms,
                        json.dumps(sr.metadata or {}),
                    ),
                )
                for r in sr.results:
                    conn.execute(
                        """
                        INSERT INTO eval_task_results
                            (run_id, task_id, suite, category, difficulty, status,
                             score, duration_ms, total_tokens, cost_usd, steps,
                             answer, expected, error, timestamp)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            sr.run_id, r.task_id, r.suite, r.task_id.split(".")[0] if "." in r.task_id else "",
                            1, r.status.value, r.score, r.duration_ms,
                            r.total_tokens, r.cost_usd, r.steps,
                            r.answer[:2000] if r.answer else "",
                            r.expected[:500] if r.expected else "",
                            r.error or "", r.timestamp,
                        ),
                    )
                conn.commit()

    def list_runs(self, suite: Optional[str] = None, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            if suite:
                rows = conn.execute(
                    "SELECT * FROM eval_runs WHERE suite = ? ORDER BY started_at DESC LIMIT ?",
                    (suite, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM eval_runs ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_run(self, run_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM eval_runs WHERE run_id = ?", (run_id,)).fetchone()
            if not row:
                return None
            run = dict(row)
            run["metadata"] = json.loads(run.get("metadata") or "{}")
            tasks = conn.execute(
                "SELECT * FROM eval_task_results WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
            run["tasks"] = [dict(t) for t in tasks]
        return run

    def compare_runs(self, run_ids: list[str]) -> list[dict]:
        out = []
        for rid in run_ids:
            r = self.get_run(rid)
            if r:
                out.append(r)
        return out


_STORE: Optional[ResultsStore] = None
_LOCK = threading.Lock()


def get_store() -> ResultsStore:
    global _STORE
    with _LOCK:
        if _STORE is None:
            _STORE = ResultsStore()
    return _STORE


def reset_store(db_path: Optional[str] = None) -> ResultsStore:
    """Reset the singleton and clear eval tables (used by tests)."""
    global _STORE
    with _LOCK:
        _STORE = ResultsStore(db_path=db_path)
        try:
            with _STORE._conn() as conn:
                conn.executescript("""
                    DELETE FROM eval_runs;
                    DELETE FROM eval_task_results;
                """)
                conn.commit()
        except Exception:
            pass
    return _STORE
