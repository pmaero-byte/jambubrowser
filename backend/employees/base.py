"""
Base classes for AI employees.

Defines the shared Finding schema, AuditData container, and the BaseEmployee
class that every specialist extends. Employees produce structured JSON findings
that are parsed, validated, and deduplicated by the orchestration engine.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

log = logging.getLogger("jambu.employees")


# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @classmethod
    def from_str(cls, value: str | None) -> "Severity":
        if not value:
            return cls.MEDIUM
        try:
            return cls(value.lower().strip())
        except ValueError:
            log.warning("Unknown severity %r, defaulting to MEDIUM", value)
            return cls.MEDIUM


# ---------------------------------------------------------------------------
# Finding — single issue / risk / gap / suggestion
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    """A single actionable finding from one employee."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    employee: str = ""           # e.g. "Security Auditor"
    severity: Severity = Severity.MEDIUM
    category: str = ""           # e.g. "csp", "lcp", "contrast", "meta"
    title: str = ""
    description: str = ""
    fix_suggestion: str = ""     # actionable remediation
    evidence_snippet: str = ""   # relevant code / header / metric value
    wcag_criterion: Optional[str] = None   # accessibility only
    score_impact: Optional[str] = None     # performance only

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "employee": self.employee,
            "severity": self.severity.value,
            "category": self.category,
            "title": self.title,
            "description": self.description,
            "fix_suggestion": self.fix_suggestion,
            "evidence_snippet": self.evidence_snippet,
        }
        if self.wcag_criterion:
            d["wcag_criterion"] = self.wcag_criterion
        if self.score_impact:
            d["score_impact"] = self.score_impact
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Finding":
        return cls(
            id=d.get("id", uuid.uuid4().hex[:12]),
            employee=str(d.get("employee") or ""),
            severity=Severity.from_str(d.get("severity", "medium")),
            category=str(d.get("category") or ""),
            title=str(d.get("title") or ""),
            description=str(d.get("description") or ""),
            fix_suggestion=str(d.get("fix_suggestion") or ""),
            evidence_snippet=str(d.get("evidence_snippet") or ""),
            wcag_criterion=d.get("wcag_criterion"),
            score_impact=d.get("score_impact"),
        )


# ---------------------------------------------------------------------------
# AuditData — snapshot of everything collected about a page
# ---------------------------------------------------------------------------

@dataclass
class AuditData:
    """Full-spectrum data collected from a single page visit.

    Not every employee needs every field — the orchestration engine passes
    only the relevant subset to each employee.
    """

    url: str = ""
    title: str = ""

    # Visual
    screenshot_base64: Optional[str] = None       # viewport PNG
    fullpage_screenshot_base64: Optional[str] = None

    # DOM / structure
    dom_snapshot: Optional[str] = None            # accessibility tree text
    page_source: Optional[str] = None             # raw HTML after JS render

    # Network
    network_requests: list[dict[str, Any]] = field(default_factory=list)
    response_headers: dict[str, str] = field(default_factory=dict)
    cookies: list[dict[str, Any]] = field(default_factory=list)

    # Console
    console_logs: list[dict[str, Any]] = field(default_factory=list)

    # Performance / audits
    lighthouse_report: Optional[dict[str, Any]] = None

    # Metadata
    viewport_width: int = 1440
    viewport_height: int = 900
    load_time_ms: float = 0.0
    collected_at: str = ""

    def summary(self) -> str:
        """One-line summary for logs."""
        return (
            f"AuditData({self.url!r}, "
            f"{len(self.network_requests)} reqs, "
            f"{len(self.console_logs)} console, "
            f"lighthouse={'yes' if self.lighthouse_report else 'no'})"
        )


# ---------------------------------------------------------------------------
# BaseEmployee
# ---------------------------------------------------------------------------

class BaseEmployee:
    """Every AI employee extends this.

    Subclasses override:
    - `name` — display name (e.g. "Security Auditor")
    - `emoji` — icon for the dashboard
    - `system_prompt` — the actual prompt that makes them a specialist
    - `_build_messages()` — optionally customise the message list
    """

    name: str = "Base Employee"
    emoji: str = "🤖"
    system_prompt: str = ""
    max_tokens: int = 4000
    temperature: float = 0.3

    async def analyze(self, data: AuditData) -> list[Finding]:
        """Run this employee against audit data. Returns findings."""
        messages = self._build_messages(data)
        try:
            from backend.llm import ChatMessage, Role, get_registry, normalize_llm_response
            llm_messages = [
                ChatMessage(role=Role(m.role.value if hasattr(m.role, 'value') else str(m.role)), content=m.content)
                for m in messages
            ]
            response = await get_registry().chat(
                llm_messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
            raw = normalize_llm_response(response.content)
            return parse_findings(raw, self.name)
        except Exception as exc:
            log.error("%s failed: %s", self.name, exc)
            return []

    def _build_messages(self, data: AuditData) -> list:
        """Build the message list for the LLM call.

        The default implementation sends a system prompt followed by the
        relevant data as a user message. Subclasses can override to send
        data differently (e.g. inline it in the system prompt).
        """
        from backend.llm import ChatMessage, Role

        user_content = self._prepare_data(data)
        return [
            ChatMessage(role=Role.SYSTEM, content=self.system_prompt),
            ChatMessage(role=Role.USER, content=user_content),
        ]

    def _prepare_data(self, data: AuditData) -> str:
        """Subclasses override this to select & format the data they need."""
        return f"URL: {data.url}\n\nNo specialised data extractor defined for {self.name}."


# ---------------------------------------------------------------------------
# Structured output parser
# ---------------------------------------------------------------------------

_FINDING_JSON_RE = re.compile(r"\[\s*\{.*?\}\s*\]", re.DOTALL)


def parse_findings(raw: str, employee_name: str) -> list[Finding]:
    """Parse the LLM's JSON output into a list of Finding objects.

    Tolerates common LLM output quirks:
    - markdown code fences (```json ... ```)
    - leading/trailing text outside the JSON array
    - single objects instead of arrays
    - missing optional fields
    """
    # Strip markdown fences
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, count=1)
        cleaned = re.sub(r"\s*```$", "", cleaned, count=1)
        cleaned = cleaned.strip()

    # Try direct parse first
    try:
        parsed = json.loads(cleaned)
        return _coerce_to_findings(parsed, employee_name)
    except json.JSONDecodeError:
        pass

    # Extract the first JSON array via regex
    match = _FINDING_JSON_RE.search(cleaned)
    if match:
        try:
            parsed = json.loads(match.group(0))
            return _coerce_to_findings(parsed, employee_name)
        except json.JSONDecodeError:
            pass

    # Try extracting a single JSON object
    single_re = re.search(r"\{\s*\"(?:severity|category|title)\".*?\}", cleaned, re.DOTALL)
    if single_re:
        try:
            obj = json.loads(single_re.group(0))
            findings = _coerce_to_findings(obj, employee_name)
            if findings:
                return findings
        except json.JSONDecodeError:
            pass

    log.warning("%s returned unparseable output: %.200s...", employee_name, raw)
    return []


def _coerce_to_findings(parsed, employee_name: str) -> list[Finding]:
    """Normalise parsed JSON into a list of Findings."""
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return []

    findings = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        f = Finding.from_dict(item)
        f.employee = employee_name
        findings.append(f)
    return findings
