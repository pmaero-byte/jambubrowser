"""Per-step SARIF rendering for browser test-flow reports.

Findings-level SARIF already exists (``backend.employees.export``) for the
six audit employees. A browser test flow is a different evidence unit: the
natural CI consumer wants *which step failed, on which URL, with what
reason*, not a static-analysis rule taxonomy. This module maps each
non-passed step (plus each failed request / bad response the flow recorded)
to one SARIF 2.1.0 result so a failing UI probe shows up in GitHub code
scanning next to lint and SAST findings.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Mapping

_STEP_LEVEL = {
    "failed": "error",
    "blocked": "warning",
    "inconclusive": "note",
}


def flow_report_to_sarif(
    report: Mapping[str, Any],
    *,
    tool_name: str = "Jambubrowser browser test",
    tool_version: str = "1.0.0",
    run_id: str | None = None,
) -> dict[str, Any]:
    """Convert a flow report (``browser_agent`` shape) to SARIF 2.1.0."""
    results: list[dict[str, Any]] = []
    rules: dict[str, dict[str, Any]] = {}

    def add(rule_id: str, name: str, message: str, level: str, uri: str | None,
            properties: dict[str, Any]) -> None:
        results.append({
            "ruleId": rule_id,
            "level": level,
            "message": {"text": message[:1000]},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": uri or report.get("final_url", "") or "https://example.invalid"},
                },
            }],
            "properties": {"jambu": properties},
        })
        if rule_id not in rules:
            rules[rule_id] = {
                "id": rule_id,
                "name": name,
                "shortDescription": {"text": name},
                "defaultConfiguration": {"level": level},
            }

    for step in report.get("steps") or []:
        status = str(step.get("status") or "").lower()
        if status in _STEP_LEVEL:
            action = str(step.get("action") or "step")
            rule_id = f"BROWSER_STEP_{action.upper()}"
            detail = step.get("reason") or status
            error = step.get("error") or step.get("detail") or ""
            add(
                rule_id, f"Step failed: {action}", f"{detail}: {error}".strip(": "),
                _STEP_LEVEL[status], report.get("final_url"),
                {"step_index": step.get("i"), "action": action, "status": status},
            )

    for req in report.get("failed_requests") or []:
        add(
            "BROWSER_REQUEST_FAILED", "Request failed",
            f"{req.get('method', 'GET')} {req.get('url', '')}: {req.get('failure', '')}",
            "error", req.get("url"), {"kind": "failed_request"},
        )
    for bad in report.get("bad_responses") or []:
        add(
            "BROWSER_BAD_RESPONSE", "HTTP error response",
            f"HTTP {bad.get('status')}: {bad.get('url', '')}",
            "error", bad.get("url"), {"kind": "bad_response", "status": bad.get("status")},
        )

    return {
        "$schema": (
            "https://schemastore.azurewebsites.net/schemas/json/sarif-2.1.0-rtm.5.json"
        ),
        "version": "2.1.0",
        "runs": [{
            "tool": {
                "driver": {
                    "name": tool_name,
                    "version": tool_version,
                    "informationUri": "https://jambubrowser.local/browser-test",
                    "rules": list(rules.values()),
                }
            },
            "invocations": [{
                "executionSuccessful": True,
                "properties": {"jambu": {
                    "run_id": run_id or uuid.uuid4().hex,
                    "flow_status": report.get("status"),
                    "passed": report.get("passed"), "failed": report.get("failed"),
                    "total": report.get("total"),
                }},
            }],
            "originalUriBaseIds": {"FLOW_URL": {"uri": report.get("final_url", "") or "https://example.invalid"}},
            "results": results,
        }],
    }


def flow_sarif_to_json(sarif: Mapping[str, Any]) -> str:
    return json.dumps(sarif, indent=2, sort_keys=False, default=str)
