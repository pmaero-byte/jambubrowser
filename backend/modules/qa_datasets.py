"""QA datasets + placeholder binding + JUnit export (Milestone 2).

Steps may carry ``{{placeholders}}`` (planner emits ``{{email}}`` /
``{{password}}`` / ``{{query}}`` / ``{{card}}``). Binding order per row:
dataset value → env var of the same name → vault (``vault_<name>`` keys)
→ left unbound and reported.
"""

from __future__ import annotations

import json
import logging
import os
import re
import xml.sax.saxutils as saxutils
from typing import Any, Optional

from backend.core.database import get_db

log = logging.getLogger("jambu.qa_datasets")

PLACEHOLDER_RE = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")
MAX_DATASET_ROWS = 50
MAX_CELL_CHARS = 500


def create_dataset(name: str, rows: list[dict]) -> dict:
    if not name or not name.strip():
        raise ValueError("name must be non-empty")
    if not isinstance(rows, list) or not rows:
        raise ValueError("rows must be a non-empty list")
    if len(rows) > MAX_DATASET_ROWS:
        raise ValueError(f"max {MAX_DATASET_ROWS} rows per dataset")
    clean: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each row must be an object")
        clean.append({str(k)[:64]: str(v)[:MAX_CELL_CHARS]
                      for k, v in row.items()})
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO qa_datasets (name, rows_json) VALUES (?, ?)",
            (name.strip(), json.dumps(clean)),
        )
        conn.commit()
        dataset_id = cur.lastrowid
    return get_dataset(dataset_id)


def get_dataset(dataset_id: int) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM qa_datasets WHERE id = ?", (dataset_id,)
        ).fetchone()
    if not row:
        return None
    return {"id": row["id"], "name": row["name"],
            "rows": json.loads(row["rows_json"] or "[]"),
            "created_at": row["created_at"]}


def list_datasets() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, rows_json, created_at FROM qa_datasets "
            "ORDER BY id").fetchall()
    out = []
    for r in rows:
        parsed = json.loads(r["rows_json"] or "[]")
        out.append({"id": r["id"], "name": r["name"],
                    "row_count": len(parsed),
                    "columns": sorted({k for row in parsed for k in row}),
                    "created_at": r["created_at"]})
    return out


def delete_dataset(dataset_id: int) -> bool:
    with get_db() as conn:
        cur = conn.execute("DELETE FROM qa_datasets WHERE id = ?",
                           (dataset_id,))
        conn.commit()
        return cur.rowcount > 0


def dataset_preview(dataset_id: int) -> dict:
    dataset = get_dataset(dataset_id)
    if dataset is None:
        raise ValueError(f"no such dataset: {dataset_id}")
    rows = dataset["rows"]
    return {"id": dataset["id"], "name": dataset["name"],
            "row_count": len(rows),
            "columns": sorted({k for r in rows for k in r}),
            "rows": rows[:5]}

def placeholders_in_steps(steps: list[dict]) -> list[str]:
    return sorted(set(PLACEHOLDER_RE.findall(json.dumps(steps or []))))


def _vault_lookup(host: str, key: str) -> Optional[str]:
    """Resolve ``vault_<name>`` against the credential vault.

    ``key`` maps to a username (``vault_alice`` → alice on this host) or
    empty/``password`` → most-recent credential. Returns the password
    field only; usernames come from datasets/env.
    """
    from backend.core.vault import get_vault

    name = key[len("vault_"):]
    try:
        vault = get_vault()
        if name and name != "password":
            cred = vault.get_credential(host, name)
        else:
            cred = vault.get_credential(host)
        if cred:
            return cred.get("password")
    except Exception:
        log.debug("vault lookup skipped", exc_info=True)
    return None


def bind_placeholders(steps: list[dict], row: dict, host: str
                      ) -> tuple[list[dict], list[str]]:
    """Bind one dataset row into a step list. Returns (steps, unbound)."""
    unbound: list[str] = []

    def _replace(text: str) -> str:
        def _sub(match: re.Match) -> str:
            key = match.group(1)
            if key in row:
                return str(row[key])
            if key.startswith("vault_"):
                secret = _vault_lookup(host, key)
                if secret is not None:
                    return secret
            env = os.environ.get(key) or os.environ.get(key.upper())
            if env is not None:
                return env
            if key not in unbound:
                unbound.append(key)
            return match.group(0)
        return PLACEHOLDER_RE.sub(_sub, text)

    def _walk(node: Any) -> Any:
        if isinstance(node, str):
            return _replace(node)
        if isinstance(node, list):
            return [_walk(v) for v in node]
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        return node

    return _walk(steps), unbound


def _esc(text: Any) -> str:
    return saxutils.escape(str(text or ""), {'"': "&quot;"})


def runs_to_junit(case_name: str, runs: list[dict],
                  suite_name: str = "jambubrowser-qa") -> str:
    """Render persisted run verdicts as JUnit XML (CI-native)."""
    total = len(runs)
    failures = sum(1 for r in runs if not r.get("ok"))
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             f'<testsuite name="{_esc(suite_name)}" tests="{total}" '
             f'failures="{failures}" errors="0" skipped="0">']
    for run in runs:
        if run.get("dataset_rows"):
            label = (f"{case_name} [row {run.get('dataset_index')}"
                     f"/{run.get('dataset_rows')}]")
        else:
            label = case_name
        ms = run.get("duration_ms", 0)
        lines.append(
            f'  <testcase classname="{_esc(suite_name)}" '
            f'name="{_esc(label)}" time="{ms / 1000:.3f}">')
        if not run.get("ok"):
            bits = []
            for step in run.get("failed_steps") or []:
                bits.append(f"step {step.get('i')} {step.get('action')}: "
                            f"{step.get('reason')}: {step.get('error')}")
            for err in (run.get("console_errors") or [])[:3]:
                bits.append(f"console: {err[:160]}")
            if run.get("error"):
                bits.append(f"runner: {run['error'][:200]}")
            msg = "; ".join(bits) or run.get("status", "failed")
            lines.append(f'    <failure message="{_esc(msg)[:500]}">'
                         f"{_esc(msg)[:2000]}</failure>")
        lines.append("  </testcase>")
    lines.append("</testsuite>")
    return "\n".join(lines) + "\n"


# Severity → SARIF level (SARIF 2.1.0: none | note | warning | error).
_SARIF_LEVEL = {
    "critical": "error", "high": "error", "medium": "warning",
    "low": "note", "info": "note",
}
# Failure reason → SARIF rule id + description (stable ids: code scanning
# dedupes across runs on ruleId + partialFingerprints).
_SARIF_RULES = {
    "target_not_found": ("QA001", "Element not found for step target"),
    "target_ambiguous": ("QA002", "Step target matched multiple elements"),
    "unknown_ref": ("QA003", "Stale element ref (re-snapshot required)"),
    "assertion_failed": ("QA004", "Assertion failed"),
    "approval_required": ("QA005", "Step needed explicit approval"),
    "blocked_domain": ("QA006", "Navigation outside the session allowlist"),
    "wait_timeout": ("QA007", "Wait timed out"),
    "unbound_placeholder": ("QA008", "Step placeholder was never bound"),
    "runner_error": ("QA009", "Runner error (browser/engine)"),
}


def runs_to_sarif(case_name: str, runs: list[dict],
                  *, case_severity: str = "medium",
                  url: str = "") -> dict:
    """Render run verdicts as SARIF 2.1.0 (GitHub code scanning native).

    Every failed step becomes a result with a stable rule id and a
    fingerprint derived from case + step + reason + target, so repeated
    failures dedupe instead of spamming the security tab.
    """
    results: list[dict] = []
    used_rules: set[str] = set()

    def _add(reason: str, message: str, step: dict, location: str) -> None:
        rule_id, _desc = _SARIF_RULES.get(reason, ("QA000", "QA failure"))
        used_rules.add(rule_id)
        fingerprint = f"{case_name}|{step.get('i')}|{reason}|" \
                      f"{step.get('action')}|{location}"
        results.append({
            "ruleId": rule_id,
            "level": _SARIF_LEVEL.get(case_severity, "warning"),
            "message": {"text": message[:1000]},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": location or case_name},
                },
            }],
            "partialFingerprints": {
                "jambubrowserCaseStep": _fingerprint(fingerprint),
            },
            "properties": {
                "step": step.get("i"), "action": step.get("action"),
                "reason": reason, "case": case_name,
            },
        })

    for run in runs:
        if run.get("ok") and not run.get("flaky"):
            continue
        location = url or case_name
        if run.get("flaky"):
            results.append({
                "ruleId": "QA010",
                "level": "note",
                "message": {"text": (
                    f"{case_name}: passed only after a retry "
                    f"(attempt {run.get('attempt', 2)}) — flaky")},
                "locations": [{"physicalLocation": {
                    "artifactLocation": {"uri": location}}}],
                "partialFingerprints": {
                    "jambubrowserCaseStep": _fingerprint(
                        f"{case_name}|flaky")},
                "properties": {"flaky": True, "case": case_name},
            })
            used_rules.add("QA010")
        for step in run.get("failed_steps") or []:
            reason = step.get("reason") or "assertion_failed"
            message = (f"{case_name} step {step.get('i')} "
                       f"({step.get('action')}) failed: {reason}: "
                       f"{step.get('error') or ''}")
            _add(reason, message, step, location)
        if run.get("error") and not (run.get("failed_steps") or []):
            _add("runner_error", f"{case_name}: {run['error']}", {}, location)

    rules = [
        {"id": rid, "name": rid,
         "shortDescription": {"text": desc},
         "helpUri": "https://github.com/pmaero-byte/jambubrowser"}
        for rid, desc in sorted(set(_SARIF_RULES.values()))
    ]
    if "QA010" in used_rules:
        rules.append({"id": "QA010", "name": "QA010",
                      "shortDescription": {"text": "Flaky test (passed on retry)"}})
    return {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [{
            "tool": {"driver": {
                "name": "jambubrowser-qa",
                "informationUri":
                    "https://github.com/pmaero-byte/jambubrowser",
                "rules": rules,
            }},
            "results": results,
        }],
    }


def _fingerprint(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]

