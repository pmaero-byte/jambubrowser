"""
A2A agent — let other agents hire this engine (v0.3 JSON-RPC binding).

Implements the parts of the Agent2Agent protocol that make Jambubrowser a
*hireable worker*:

- ``/.well-known/agent-card.json`` — the public card: identity, capabilities
  (no streaming, no push yet), input/output modes, bearer security scheme,
  and **skills** that map to existing engine capabilities.
- JSON-RPC methods at ``/a2a``: ``SendMessage``, ``GetTask``, ``CancelTask``
  with spec-shaped ``Task``/``Message``/``Artifact``/``Part`` objects and the
  ``TASK_STATE_*`` state enum.

Skills (each one is a thin wrapper over something already shipped):

| Skill | Does | Reuses |
|---|---|---|
| ``audit_web_app`` | quick audit of a URL | shared audit pipeline |
| ``agent_eval_certify`` | run an eval suite under a frozen spec | E5 certificates |
| ``mesh_inference`` | prompt completion on the DCM node | ``dcm`` provider |

Access control: a valid engine API key, an x402 payment (the paywall already
gates ``POST /a2a`` when enabled), or ``JAMBU_A2A_OPEN=1`` for local dev.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Callable, Optional

from backend import __version__
from backend.core.database import get_db

log = logging.getLogger("jambu.a2a")

STREAMING = False          # advertised honestly in the card
PUSH_NOTIFICATIONS = False

TERMINAL_STATES = {
    "TASK_STATE_COMPLETED", "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED", "TASK_STATE_REJECTED",
}

# JSON-RPC error codes (spec §3.3.2 / §9)
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
TASK_NOT_FOUND = -32001
TASK_NOT_CANCELABLE = -32002
UNSUPPORTED_OPERATION = -32003
PUSH_NOT_SUPPORTED = -32004


class A2AError(Exception):
    def __init__(self, code: int, message: str, reason: str = "", data: dict | None = None):
        self.code = code
        self.message = message
        self.reason = reason
        self.data = data or {}
        super().__init__(message)

    def to_jsonrpc(self, request_id: Any) -> dict:
        payload: dict[str, Any] = {
            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
            "reason": self.reason or self.message.upper().replace(" ", "_"),
            "domain": "a2a-protocol.org",
            "metadata": self.data,
        }
        return {
            "jsonrpc": "2.0", "id": request_id,
            "error": {"code": self.code, "message": self.message, "data": [payload]},
        }


class SkillError(Exception):
    """A skill refused or failed; surfaced as a FAILED task (not a protocol error)."""


# ---------------------------------------------------------------------------
# Skill registry
# ---------------------------------------------------------------------------

SkillFn = Callable[[dict], Any]

SKILLS: dict[str, SkillFn] = {}


def register_skill(name: str, fn: SkillFn) -> None:
    SKILLS[name] = fn


def skill_catalog() -> list[dict]:
    return [
        {
            "id": "audit_web_app",
            "name": "Audit a web app",
            "description": "Quick audit (3 AI employees: security, performance, UX) of a live URL.",
            "tags": ["audit", "security", "performance", "ux"],
            "examples": ["Audit https://example.com"],
            "inputModes": ["text/plain", "application/json"],
            "outputModes": ["text/plain", "application/json"],
        },
        {
            "id": "agent_eval_certify",
            "name": "Certify an agent evaluation",
            "description": "Run an eval suite under a frozen spec and return a signed certificate.",
            "tags": ["evaluation", "certification", "benchmark"],
            "examples": ["Certify suite=smoke"],
            "inputModes": ["application/json"],
            "outputModes": ["application/json"],
        },
        {
            "id": "mesh_inference",
            "name": "Mesh inference",
            "description": "Run a prompt on the local DecentraCode mesh node.",
            "tags": ["inference", "compute", "mesh"],
            "examples": ["Say hello in three words"],
            "inputModes": ["text/plain"],
            "outputModes": ["text/plain"],
        },
    ]


# -- skill implementations (thin wrappers over shipped capabilities) -------

async def _skill_audit_web_app(payload: dict) -> dict:
    url = (payload.get("url") or payload.get("text") or "").strip()
    if not url.startswith("http"):
        raise SkillError("audit_web_app needs an http(s) URL")
    from backend.modules.audit_monitor import _execute_audit

    done = await _execute_audit(url, "quick")
    findings = done.get("findings") or []
    top = [
        {"severity": f.get("severity"), "title": f.get("title")}
        for f in findings[:10]
    ]
    return {
        "text": (
            f"Quick audit of {url}: {len(findings)} finding(s), "
            f"{done.get('dismissed_count', 0)} dismissed."
        ),
        "data": {
            "url": url,
            "findings_count": len(findings),
            "by_severity": done.get("by_severity") or {},
            "top_findings": top,
            "audit_id": done.get("audit_id"),
        },
    }


async def _skill_agent_eval_certify(payload: dict) -> dict:
    suite = (payload.get("suite") or "").strip()
    if not suite:
        raise SkillError("agent_eval_certify needs a suite (e.g. 'smoke')")
    from backend.modules import eval_cert

    bundle = await eval_cert.run_and_certify(
        suite,
        task_ids=payload.get("task_ids"),
        provider=payload.get("provider"),
        pass_threshold=float(payload.get("pass_threshold", eval_cert.DEFAULT_PASS_THRESHOLD)),
    )
    verdict = bundle["payload"]["verdict"]
    return {
        "text": (
            f"Certificate #{bundle.get('id')} for suite {suite}: "
            f"{verdict['verdict']} ({verdict['summary']['pass_rate']:.0%} pass rate)"
        ),
        "data": {
            "certificate_id": bundle.get("id"),
            "verdict": verdict["verdict"],
            "summary": verdict["summary"],
            "spec_hash": bundle["payload"]["spec_hash"],
        },
    }


async def _skill_mesh_inference(payload: dict) -> dict:
    prompt = (payload.get("prompt") or payload.get("text") or "").strip()
    if not prompt:
        raise SkillError("mesh_inference needs a prompt")
    from backend.llm.base import ChatMessage, Role
    from backend.llm.registry import get_registry

    provider = get_registry().get("dcm")
    response = await provider.chat(
        [ChatMessage(role=Role.USER, content=prompt)],
        max_tokens=int(payload.get("max_tokens", 64)),
    )
    return {
        "text": response.content or "",
        "data": {
            "model": response.model,
            "tokens": response.usage.total_tokens,
        },
    }


register_skill("audit_web_app", _skill_audit_web_app)
register_skill("agent_eval_certify", _skill_agent_eval_certify)
register_skill("mesh_inference", _skill_mesh_inference)


# ---------------------------------------------------------------------------
# Agent card
# ---------------------------------------------------------------------------

def agent_card(base_url: str = "") -> dict:
    base = (base_url or "").rstrip("/")
    return {
        "name": "Jambubrowser",
        "description": (
            "Audits web apps with AI employees, drives a hardened browser, "
            "certifies agent evaluations, and runs mesh compute."
        ),
        "url": f"{base}/a2a" if base else "/a2a",
        "version": __version__,
        "provider": {
            "organization": "Jambubrowser",
            "url": "https://github.com/pmaero-byte/jambubrowser",
        },
        "capabilities": {
            "streaming": STREAMING,
            "pushNotifications": PUSH_NOTIFICATIONS,
        },
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "securitySchemes": {
            "bearer": {"type": "http", "scheme": "bearer"},
        },
        "security": [{"bearer": []}],
        "skills": skill_catalog(),
    }


# ---------------------------------------------------------------------------
# Task store
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _insert_task(task_id: str, context_id: str, skill: str, message: dict) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO a2a_tasks
                (id, context_id, skill, state, message_json, artifacts_json,
                 history_json, created_at, updated_at)
            VALUES (?, ?, ?, 'TASK_STATE_SUBMITTED', ?, '[]', ?, ?, ?)
            """,
            (
                task_id, context_id, skill, json.dumps(message),
                json.dumps([message]), time.time(), time.time(),
            ),
        )
        conn.commit()


def _update_task(task_id: str, **fields) -> None:
    allowed = {"state", "artifacts_json", "history_json", "status_message", "error"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    updates["updated_at"] = time.time()
    assignments = ", ".join(f"{k} = ?" for k in updates)
    with get_db() as conn:
        conn.execute(
            f"UPDATE a2a_tasks SET {assignments} WHERE id = ?",
            list(updates.values()) + [task_id],
        )
        conn.commit()


def _row_to_task(row) -> dict:
    def _load(value, fallback):
        try:
            return json.loads(value) if value else fallback
        except (TypeError, ValueError):
            return fallback

    status: dict[str, Any] = {"state": row["state"], "timestamp": _now_iso()}
    if row["status_message"]:
        status["message"] = json.loads(row["status_message"])
    task: dict[str, Any] = {
        "kind": "task",
        "id": row["id"],
        "contextId": row["context_id"],
        "status": status,
    }
    artifacts = _load(row["artifacts_json"], [])
    if artifacts:
        task["artifacts"] = artifacts
    history = _load(row["history_json"], [])
    if history:
        task["history"] = history
    if row["error"]:
        task["metadata"] = {"error": row["error"]}
    return task


def get_task(task_id: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM a2a_tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        return None
    # Orphaned work: the engine restarted while a task was running.
    if row["state"] == "TASK_STATE_WORKING" and task_id not in _RUNNERS:
        _update_task(
            task_id, state="TASK_STATE_FAILED",
            error="engine restarted while the task was running",
        )
        with get_db() as conn:
            row = conn.execute("SELECT * FROM a2a_tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_task(row)


# ---------------------------------------------------------------------------
# Message helpers
# ---------------------------------------------------------------------------

def _parts_text(message: dict) -> str:
    parts = message.get("parts") or []
    chunks = []
    for part in parts:
        if isinstance(part, dict):
            if part.get("kind") == "text" and part.get("text"):
                chunks.append(str(part["text"]))
            elif part.get("text"):
                chunks.append(str(part["text"]))
    return "\n".join(chunks).strip()


def _parts_data(message: dict) -> dict:
    payload: dict[str, Any] = {}
    for part in message.get("parts") or []:
        if isinstance(part, dict) and isinstance(part.get("data"), dict):
            payload.update(part["data"])
    return payload


def resolve_skill(message: dict) -> tuple[str, dict]:
    """Pick a skill from metadata, a data part, or a URL-looking text part."""
    metadata = message.get("metadata") or {}
    payload = _parts_data(message)
    skill = (
        metadata.get("skill")
        or payload.get("skill")
        or ""
    ).strip()
    text = _parts_text(message)
    if not skill:
        if text.startswith("http"):
            skill = "audit_web_app"
        else:
            raise A2AError(
                INVALID_PARAMS,
                "No skill selected. Send message.metadata.skill or a data part "
                "with 'skill'.",
                reason="SKILL_REQUIRED",
                data={"available_skills": sorted(SKILLS)},
            )
    if skill not in SKILLS:
        raise A2AError(
            INVALID_PARAMS, f"Unknown skill: {skill}",
            reason="SKILL_NOT_FOUND",
            data={"available_skills": sorted(SKILLS)},
        )
    if text and "text" not in payload:
        payload["text"] = text
    return skill, payload


def _artifact_from_result(result: dict, skill: str) -> list[dict]:
    return [{
        "artifactId": uuid.uuid4().hex,
        "name": f"{skill}_result",
        "parts": [
            {"kind": "text", "text": result.get("text", "")},
            {"kind": "data", "data": result.get("data", {})},
        ],
    }]


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

_RUNNERS: dict[str, asyncio.Task] = {}


async def _run_task(task_id: str, skill: str, payload: dict) -> None:
    _update_task(task_id, state="TASK_STATE_WORKING")
    try:
        result = await SKILLS[skill](payload)
        artifacts = _artifact_from_result(result, skill)
        agent_message = {
            "kind": "message",
            "messageId": uuid.uuid4().hex,
            "role": "ROLE_AGENT",
            "parts": artifacts[0]["parts"],
        }
        with get_db() as conn:
            row = conn.execute(
                "SELECT history_json FROM a2a_tasks WHERE id = ?", (task_id,),
            ).fetchone()
        history = json.loads(row["history_json"]) if row and row["history_json"] else []
        history.append(agent_message)
        _update_task(
            task_id, state="TASK_STATE_COMPLETED",
            artifacts_json=json.dumps(artifacts),
            history_json=json.dumps(history),
        )
    except asyncio.CancelledError:
        _update_task(task_id, state="TASK_STATE_CANCELED", error="canceled by client")
        raise
    except Exception as e:
        log.warning("A2A skill %s failed: %s", skill, e)
        _update_task(task_id, state="TASK_STATE_FAILED", error=str(e))
    finally:
        _RUNNERS.pop(task_id, None)


async def send_message(params: dict) -> dict:
    message = params.get("message")
    if not isinstance(message, dict):
        raise A2AError(INVALID_PARAMS, "message object is required", reason="INVALID_MESSAGE")
    if not message.get("parts"):
        raise A2AError(INVALID_PARAMS, "message.parts must be non-empty", reason="INVALID_MESSAGE")

    skill, payload = resolve_skill(message)
    blocking = bool((params.get("configuration") or {}).get("blocking", True))

    task_id = uuid.uuid4().hex
    context_id = message.get("contextId") or uuid.uuid4().hex
    message.setdefault("kind", "message")
    message.setdefault("messageId", uuid.uuid4().hex)
    message.setdefault("role", "ROLE_USER")
    _insert_task(task_id, context_id, skill, message)

    if blocking:
        await _run_task(task_id, skill, payload)
        return {"task": get_task(task_id)}

    runner = asyncio.create_task(_run_task(task_id, skill, payload))
    _RUNNERS[task_id] = runner
    return {"task": get_task(task_id)}


def cancel_task(params: dict) -> dict:
    task_id = params.get("id") or params.get("taskId")
    if not task_id:
        raise A2AError(INVALID_PARAMS, "id is required", reason="INVALID_PARAMS")
    task = get_task(task_id)
    if task is None:
        raise A2AError(
            TASK_NOT_FOUND, "Task not found",
            reason="TASK_NOT_FOUND", data={"taskId": task_id},
        )
    state = task["status"]["state"]
    if state in TERMINAL_STATES:
        raise A2AError(
            TASK_NOT_CANCELABLE, f"Task is not cancelable in state {state}",
            reason="TASK_NOT_CANCELABLE", data={"taskId": task_id, "state": state},
        )
    runner = _RUNNERS.get(task_id)
    if runner is not None:
        runner.cancel()
    _update_task(task_id, state="TASK_STATE_CANCELED", error="canceled by client")
    return {"task": get_task(task_id)}


async def handle_rpc(payload: dict) -> dict:
    """JSON-RPC 2.0 dispatcher for the A2A methods."""
    request_id = payload.get("id")
    if payload.get("jsonrpc") != "2.0" or not payload.get("method"):
        error = A2AError(INVALID_REQUEST, "jsonrpc 2.0 request with a method is required")
        return error.to_jsonrpc(request_id)

    method = payload["method"]
    params = payload.get("params") or {}
    try:
        if method == "SendMessage":
            result = await send_message(params)
        elif method == "GetTask":
            task_id = params.get("id") or params.get("taskId")
            if not task_id:
                raise A2AError(INVALID_PARAMS, "id is required")
            task = get_task(task_id)
            if task is None:
                raise A2AError(
                    TASK_NOT_FOUND, "Task not found",
                    reason="TASK_NOT_FOUND", data={"taskId": task_id},
                )
            result = {"task": task}
        elif method == "CancelTask":
            result = cancel_task(params)
        elif method in ("SendStreamingMessage", "SubscribeToTask"):
            raise A2AError(
                UNSUPPORTED_OPERATION, "streaming is not supported by this agent",
                reason="STREAMING_NOT_SUPPORTED",
            )
        elif method in (
            "CreateTaskPushNotificationConfig", "GetTaskPushNotificationConfig",
            "ListTaskPushNotificationConfigs", "DeleteTaskPushNotificationConfig",
        ):
            raise A2AError(
                PUSH_NOT_SUPPORTED, "push notifications are not supported",
                reason="PUSH_NOTIFICATION_NOT_SUPPORTED",
            )
        else:
            raise A2AError(METHOD_NOT_FOUND, f"Method not found: {method}")
    except A2AError as e:
        return e.to_jsonrpc(request_id)
    except Exception as e:  # pragma: no cover - defensive
        log.exception("A2A internal error in %s", method)
        return A2AError(INTERNAL_ERROR, str(e)).to_jsonrpc(request_id)
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def reset_runners() -> None:
    """Test hook: drop in-process runners (tasks stay in the DB)."""
    for runner in list(_RUNNERS.values()):
        runner.cancel()
    _RUNNERS.clear()
