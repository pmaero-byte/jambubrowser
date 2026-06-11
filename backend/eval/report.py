"""
Report generation — markdown + JSON outputs from SuiteResult.
"""

from __future__ import annotations

import json
from typing import Optional, Union

from .harness import SuiteResult, TaskResult, TaskStatus


def _fmt_ms(ms: float) -> str:
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.2f}s"


def _fmt_usd(usd: float) -> str:
    if usd < 0.01:
        return f"${usd * 100:.2f}¢"
    return f"${usd:.4f}"


def _status_emoji(status: TaskStatus) -> str:
    return {
        TaskStatus.PASSED: "✓",
        TaskStatus.FAILED: "✗",
        TaskStatus.ERROR: "⚠",
        TaskStatus.TIMEOUT: "⏱",
        TaskStatus.SKIPPED: "—",
    }.get(status, "?")


def to_markdown(sr: SuiteResult) -> str:
    """Render a single suite result as Markdown."""
    lines: list[str] = []
    lines.append(f"# Eval Run: {sr.suite}")
    lines.append("")
    lines.append(f"- **Provider:** `{sr.provider}`")
    lines.append(f"- **Model:** `{sr.model}`")
    lines.append(f"- **Run ID:** `{sr.run_id}`")
    lines.append(f"- **Started:** {sr.started_at:.0f} ({_fmt_ms((sr.completed_at or sr.started_at) - sr.started_at)} total)")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| **Success rate** | **{sr.success_rate * 100:.1f}%** ({sr.passed}/{sr.total}) |")
    lines.append(f"| Passed | {sr.passed} |")
    lines.append(f"| Failed | {sr.failed} |")
    lines.append(f"| Errored | {sr.errored} |")
    lines.append(f"| Total tokens | {sr.total_tokens:,} |")
    lines.append(f"| Total cost | {_fmt_usd(sr.total_cost_usd)} |")
    lines.append(f"| Avg duration | {_fmt_ms(sr.avg_duration_ms)} |")
    lines.append("")

    # Per-task breakdown
    lines.append("## Per-Task Results")
    lines.append("")
    lines.append("| Status | Task ID | Category | Score | Duration | Tokens | Cost | Steps |")
    lines.append("|--------|---------|----------|-------|----------|--------|------|-------|")
    for r in sr.results:
        cat = r.task_id.split(".")[0] if "." in r.task_id else "—"
        lines.append(
            f"| {_status_emoji(r.status)} {r.status.value} | `{r.task_id}` | {cat} | "
            f"{r.score:.2f} | {_fmt_ms(r.duration_ms)} | {r.total_tokens} | "
            f"{_fmt_usd(r.cost_usd)} | {r.steps} |"
        )
    lines.append("")

    # Failures
    failures = [r for r in sr.results if r.status != TaskStatus.PASSED]
    if failures:
        lines.append("## Failures")
        lines.append("")
        for r in failures:
            lines.append(f"### `{r.task_id}` — {r.status.value}")
            lines.append(f"- **Expected:** {r.expected[:200] if r.expected else '(none)'}")
            lines.append(f"- **Got:** {r.answer[:300] if r.answer else '(none)'}…")
            if r.error:
                lines.append(f"- **Error:** `{r.error[:200]}`")
            lines.append("")

    return "\n".join(lines)


def to_json(sr: SuiteResult) -> str:
    return json.dumps(sr.to_dict(), indent=2, default=str)


def compare_markdown(suite_results: list[SuiteResult]) -> str:
    """Render a comparison table across multiple suite runs."""
    lines = ["# Provider Comparison", ""]
    lines.append("| Provider | Model | Suite | Pass | Total | Success | Tokens | Cost | Avg Time |")
    lines.append("|----------|-------|-------|------|-------|---------|--------|------|----------|")
    for sr in suite_results:
        lines.append(
            f"| {sr.provider} | `{sr.model}` | {sr.suite} | "
            f"{sr.passed} | {sr.total} | {sr.success_rate * 100:.1f}% | "
            f"{sr.total_tokens:,} | {_fmt_usd(sr.total_cost_usd)} | {_fmt_ms(sr.avg_duration_ms)} |"
        )
    lines.append("")
    if len(suite_results) >= 2:
        # Highlight best
        best = max(suite_results, key=lambda s: s.success_rate)
        cheapest = min(suite_results, key=lambda s: s.total_cost_usd)
        fastest = min(suite_results, key=lambda s: s.avg_duration_ms)
        lines.append("## Highlights")
        lines.append(f"- **Best success rate:** {best.provider} ({best.success_rate * 100:.1f}%)")
        lines.append(f"- **Cheapest:** {cheapest.provider} ({_fmt_usd(cheapest.total_cost_usd)})")
        lines.append(f"- **Fastest:** {fastest.provider} ({_fmt_ms(fastest.avg_duration_ms)} avg)")
    return "\n".join(lines)


def generate_report(sr: Union[SuiteResult, list[SuiteResult]], *, fmt: str = "markdown") -> str:
    if isinstance(sr, list):
        if fmt == "json":
            return json.dumps([s.to_dict() for s in sr], indent=2, default=str)
        return compare_markdown(sr)
    if fmt == "json":
        return to_json(sr)
    return to_markdown(sr)
