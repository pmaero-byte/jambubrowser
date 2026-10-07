"""The pure functions a browser flow is made of.

Nothing here touches a browser or a session: a flow arrives as JSON, and this
module turns it into steps, classifies the results, estimates what it cost and
renders the Markdown report an agent reads. That is why it is its own file —
every function here is unit-testable with dicts, so a flow bug reproduces
without launching a session.

The two database helpers (`record_flow_savings`, `token_savings`) are the
exception: they persist the token-savings ledger.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional
from urllib.parse import urlparse

from backend.core.database import get_db_cursor
from backend.core.security import is_safe_url
from backend.modules.browser_agent_errors import SessionRefused


def estimate_primitive_cost(steps: list[dict], elements: int = 40,
                            text_chars: int = 600) -> dict:
    """What the same work costs as a primitive snapshot/act loop.

    The comparison is deliberately generous to the loop: one open + one close,
    one act per step, and a fresh snapshot before every step that needs refs
    (a loop cannot know refs changed, so it re-observes). A snapshot is charged
    as ``60 + 15*min(elements, 25) + text/4`` tokens, matching the shape of the
    real snapshot renderer. Honest numbers, not marketing: if the flow's own
    report is bigger than this estimate, the flow did not save anything.
    """
    acts = len(steps) or 1
    mutating = sum(
        1 for s in steps
        if isinstance(s, dict) and (s.get("action") or "").lower() in MUTATING_ACTIONS
    )
    snapshots = 1 + mutating
    snapshot_tokens = 60 + 15 * min(max(elements, 0), 25) + max(text_chars, 0) // 4
    calls = 1 + 1 + acts + snapshots
    tokens = 28 + 10 + acts * 12 + snapshots * snapshot_tokens
    return {"calls": calls, "tokens": tokens}


# Engine-lifetime tally of what flows saved versus a primitive loop, so the
# "token-efficient" claim is a number the product can show rather than assert.
_METER: dict = {"runs": 0, "steps": 0, "flow_tokens": 0,
                "primitive_tokens": 0, "calls_avoided": 0}


def record_flow_savings(savings: dict) -> dict:
    """Add one flow's savings to the engine tally and return the running total."""
    _METER["runs"] += 1
    _METER["steps"] += int(savings.get("steps", 0) or 0)
    _METER["flow_tokens"] += int(savings.get("flow_tokens", 0) or 0)
    _METER["primitive_tokens"] += int(savings.get("primitive_tokens", 0) or 0)
    _METER["calls_avoided"] += int(savings.get("calls_avoided", 0) or 0)
    return token_savings()


def token_savings() -> dict:
    """Running totals: tokens/calls a primitive loop would have spent."""
    saved = max(0, _METER["primitive_tokens"] - _METER["flow_tokens"])
    return {
        "runs": _METER["runs"],
        "steps": _METER["steps"],
        "flow_tokens": _METER["flow_tokens"],
        "primitive_tokens": _METER["primitive_tokens"],
        "saved_tokens": saved,
        "calls_avoided": _METER["calls_avoided"],
    }


def reset_token_savings() -> None:
    """Test hook: zero the engine tally."""
    for key in _METER:
        _METER[key] = 0



def classify_step_failure(reason: Optional[str]) -> str:
    """Classify a step failure without changing the historical ``ok`` field."""
    if reason in FLOW_BLOCKED_REASONS:
        return "blocked"
    if reason in {"error", "harness_error", "timeout", "page_closed"}:
        return "inconclusive"
    return "failed"


def classify_flow_status(results: list[dict]) -> str:
    """Return the user-facing run status while preserving step-level detail."""
    statuses = {result.get("status") for result in results}
    if not statuses or statuses <= {"passed"}:
        return "passed"
    if "inconclusive" in statuses:
        return "inconclusive"
    if "blocked" in statuses:
        return "blocked"
    return "failed"


def summarize_flow_diagnostics(results: list[dict], telemetry: dict) -> dict:
    """Build a compact, machine-readable diagnosis for a flow report."""
    failures = [r for r in results if r.get("status") != "passed"]
    categories: dict[str, int] = {}
    for result in failures:
        reason = str(result.get("reason") or result.get("status") or "failed")
        categories[reason] = categories.get(reason, 0) + 1
    diagnostics = []
    for result in failures[:20]:
        diagnostics.append({
            "step": result.get("i"),
            "action": result.get("action"),
            "status": result.get("status"),
            "reason": result.get("reason", ""),
            "error": result.get("error", ""),
            "cause": result.get("cause", {}),
        })
    if telemetry.get("console_errors"):
        diagnostics.append({
            "kind": "console",
            "count": len(telemetry.get("console_errors") or []),
            "samples": list(telemetry.get("console_errors") or [])[:5],
        })
    if telemetry.get("failed_requests"):
        diagnostics.append({
            "kind": "network",
            "count": len(telemetry.get("failed_requests") or []),
            "samples": list(telemetry.get("failed_requests") or [])[:5],
        })
    if telemetry.get("bad_responses"):
        diagnostics.append({
            "kind": "http",
            "count": len(telemetry.get("bad_responses") or []),
            "samples": list(telemetry.get("bad_responses") or [])[:5],
        })
    if telemetry.get("dialogs"):
        diagnostics.append({
            "kind": "dialog",
            "count": len(telemetry.get("dialogs") or []),
            "samples": [
                f"{d.get('type')}:{d.get('message', '')[:80]}"
                f"{'(accepted)' if d.get('accepted') else '(dismissed)'}"
                for d in list(telemetry.get("dialogs") or [])[:5]
            ],
        })
    return {
        "failed_steps": len(failures),
        "categories": categories,
        "items": diagnostics,
    }

def normalize_flow_steps(steps) -> list[dict]:
    """Accept a JSON string, a ``{"steps": [...]}`` wrapper, or a list.

    Bare strings become navigate steps (``["https://x", ...]``), which keeps
    the common smoke-test case terse.
    """
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except json.JSONDecodeError as exc:
            raise SessionRefused("invalid_flow", f"steps is not valid JSON: {exc}") from exc
    if isinstance(steps, dict):
        steps = steps.get("steps")
    if not isinstance(steps, list) or not steps:
        raise SessionRefused("invalid_flow", "steps must be a non-empty list")
    if len(steps) > MAX_FLOW_STEPS:
        raise SessionRefused("flow_too_long", f"max {MAX_FLOW_STEPS} steps per flow")
    out: list[dict] = []
    for step in steps:
        if isinstance(step, dict):
            out.append(step)
        elif isinstance(step, str):
            out.append({"action": "navigate", "url": step})
        else:
            raise SessionRefused("invalid_step", f"unsupported step: {step!r}")
    return out


def parse_dialog_spec(spec: Any) -> tuple[bool, str]:
    """Normalise a dialog answer spec to ``(accept, prompt_text)``.

    Accepts ``"accept"`` / ``"dismiss"`` / ``true`` / ``{"accept": false}`` so
    both the JSON flow and the MCP string args read naturally.
    """
    if isinstance(spec, dict):
        raw = spec.get("accept", spec.get("action", True))
        text = str(spec.get("text", spec.get("prompt_text", "")) or "")
    else:
        raw, text = spec, ""
    if isinstance(raw, str):
        verb = raw.strip()
        # "accept:my answer" answers prompt() without the object form.
        if ":" in verb and verb.split(":", 1)[0].strip().lower() in (
            "accept", "dismiss", "ok", "no", "cancel"
        ):
            verb, _, text = verb.partition(":")
        accept = verb.strip().lower() in ("accept", "ok", "yes", "true", "1", "y")
    else:
        accept = bool(raw)
    return accept, text


def normalize_acts(actions: Any) -> list[dict]:
    """Accept a JSON string, an ``{"actions": [...]}`` wrapper, or a list."""
    if isinstance(actions, str):
        try:
            actions = json.loads(actions)
        except json.JSONDecodeError as exc:
            raise SessionRefused("invalid_step", f"actions is not valid JSON: {exc}") from exc
    if isinstance(actions, dict):
        actions = actions.get("actions") or actions.get("steps")
    if not isinstance(actions, list) or not actions:
        raise SessionRefused("invalid_step", "actions must be a non-empty list")
    if len(actions) > MAX_BATCH_ACTS:
        raise SessionRefused(
            "invalid_step", f"at most {MAX_BATCH_ACTS} acts per batch",
        )
    out: list[dict] = []
    for item in actions:
        if isinstance(item, dict):
            out.append(item)
        else:
            raise SessionRefused("invalid_step", f"unsupported act: {item!r}")
    return out


def render_flow_report(report: dict, *, max_errors: int = 5) -> str:
    """Render a flow report as a compact, token-lean Markdown digest."""
    icon = str(report.get("status") or ("PASS" if report.get("ok") else "FAIL")).upper()
    head = (
        f"# Browser test {icon} — {report.get('passed', 0)}/{report.get('total', 0)} steps "
        f"in {report.get('duration_ms', 0)}ms\n"
        f"final: {report.get('title', '') or '(untitled)'} — {report.get('final_url', '')}\n"
        f"~{report.get('tokens_estimate', estimate_tokens(report))} tokens"
        f"{' · uses JS evaluate' if report.get('uses_evaluate') else ''}"
    )
    savings = report.get("savings") or {}
    if savings.get("saved_tokens"):
        head += (
            f"\nsaved ~{savings['saved_tokens']} tokens and "
            f"{savings.get('calls_avoided', 0)} tool calls vs a snapshot/act loop"
        )
    lines = [head, ""]
    for step in report.get("steps") or []:
        status = step.get("status") or ("passed" if step.get("ok", True) else "failed")
        mark = {"passed": "ok", "blocked": "BLOCK", "inconclusive": "INCONCLUSIVE"}.get(
            str(status), "FAIL",
        )
        bit = f"{mark} #{step.get('i')} {step.get('action')}"
        if step.get("detail"):
            bit += f" — {step['detail']}"
        if status != "passed":
            bit += f" — {step.get('reason')}: {step.get('error')}"
        lines.append(bit)
        for cand in step.get("candidates") or []:
            lines.append(f"      candidate {cand.get('ref')}: {cand.get('name')}")
        cause = step.get("cause") or {}
        cause_bits = []
        if cause.get("dom"):
            d = cause["dom"]
            cause_bits.append(f"dom +{d.get('added', 0)}/-{d.get('removed', 0)}/~{d.get('changed', 0)}")
        if cause.get("failed_requests"):
            cause_bits.append(f"{len(cause['failed_requests'])} failed req")
        if cause.get("console_errors"):
            cause_bits.append(f"{len(cause['console_errors'])} console error")
        if cause_bits:
            lines.append(f"      cause: {'; '.join(cause_bits)}")

    for item in (report.get("console_errors_source") or [])[:max_errors]:
        if item.get("source"):
            lines.append(f"console {item['source']}:{item.get('source_line')} — {item.get('text', '')[:120]}")

    errors = report.get("console_errors") or []
    if errors:
        lines.append(f"\nconsole errors ({len(errors)}):")
        lines.extend(f"  - {e[:160]}" for e in errors[:max_errors])
    failed_reqs = report.get("failed_requests") or []
    if failed_reqs:
        lines.append(f"\nfailed requests ({len(failed_reqs)}):")
        lines.extend(
            f"  - {r.get('method')} {r.get('url')[:120]} — {r.get('failure')[:80]}"
            for r in failed_reqs[:max_errors]
        )
    bad = report.get("bad_responses") or []
    if bad:
        lines.append(f"\nHTTP >=400 ({len(bad)}):")
        lines.extend(f"  - {r.get('status')} {r.get('method')} {r.get('url')[:120]}" for r in bad[:max_errors])
    dialogs = report.get("dialogs") or []
    if dialogs:
        lines.append(f"\ndialogs ({len(dialogs)}):")
        lines.extend(
            f"  - {d.get('type')} {str(d.get('message', ''))[:100]} "
            f"{'accepted' if d.get('accepted') else 'dismissed'}"
            for d in dialogs[:max_errors]
        )
    artifacts = report.get("artifacts") or {}
    if artifacts:
        lines.append("\nartifacts: " + ", ".join(f"{k}={v}" for k, v in artifacts.items()))
    return "\n".join(lines)


def estimate_tokens(payload) -> int:
    """Rough token estimate for a payload (~4 chars per token).

    Reports how much context a flow report actually costs the agent, so token
    claims are measured rather than marketing.
    """
    if isinstance(payload, str):
        text = payload
    else:
        try:
            text = json.dumps(payload, separators=(",", ":"))
        except Exception:
            # Not JSON-serialisable: the length of its repr is still a usable
            # upper bound, and this number is only ever an estimate.
            text = str(payload)
    return max(1, len(text) // 4)




# ── Flow vocabulary ───────────────────────────────────────────────────────
# These describe what a flow *is*, not how a session runs one, so they live
# with the flow helpers; browser_agent imports them from here.

# Bounds on a single flow: a step count, the candidate-refs a target may match,
# and the acts one batched call may carry.
MAX_FLOW_STEPS = 100
MAX_TARGET_CANDIDATES = 5
MAX_BATCH_ACTS = 25

# Actions that change page state and therefore trigger an internal re-observe.
MUTATING_ACTIONS = frozenset({
    "click", "type", "press", "select", "hover", "navigate", "reload",
    "back", "forward", "check", "uncheck", "evaluate", "upload", "download",
    # Pointer gestures move real input events, so they gate the same way a click
    # does (human_takeover must refuse them too).
    "dblclick", "click_at", "drag", "wheel", "mouse", "set_range",
    # Reading a downloaded file touches the disk, like uploading does.
    "expect_download", "assert_download",
})

# Refusal reasons that mean "we stopped on purpose", not "the step failed".
# A flow whose steps all hit these is `blocked`, which is what a human needs to
# see: the fix is an approval or an allowlist entry, not a retry.
FLOW_BLOCKED_REASONS = frozenset({
    "approval_required", "blocked_domain", "blocked_protocol", "human_takeover",
    "private_address", "unsafe_url", "invalid_url", "target_required",
    "unknown_ref", "dns_resolution_failed", "redirect_loop",
    "upload_path_denied", "upload_limit",
})


def host_of(url: str) -> str:
    """Lower-cased hostname of ``url``, or "" when it cannot be parsed."""
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        # A malformed URL is not an error here: callers treat "" as "no host"
        # and fall through to their own refusal reason.
        return ""
