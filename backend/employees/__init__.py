"""Jambubrowser AI Employee Organization.

Each employee is a specialised LLM-powered auditor that analyses a webapp
and produces structured findings (issues, risks, gaps, improvement suggestions).
Employees run in parallel against a shared AuditData snapshot.
"""

from .base import BaseEmployee, Finding, AuditData, Severity, parse_findings
from .security import SecurityAuditor
from .performance import PerformanceInspector
from .ux_ui import UXUIReviewer
from .seo import SEOAnalyzer
from .accessibility import AccessibilityAuditor
from .code_quality import CodeQualityScout

# Ordered by how they appear in the dashboard
ALL_EMPLOYEES: list[type[BaseEmployee]] = [
    SecurityAuditor,
    PerformanceInspector,
    UXUIReviewer,
    SEOAnalyzer,
    AccessibilityAuditor,
    CodeQualityScout,
]

QUICK_SCAN_EMPLOYEES: list[type[BaseEmployee]] = [
    SecurityAuditor,
    PerformanceInspector,
    UXUIReviewer,
]

__all__ = [
    "BaseEmployee", "Finding", "AuditData", "Severity", "parse_findings",
    "SecurityAuditor", "PerformanceInspector", "UXUIReviewer",
    "SEOAnalyzer", "AccessibilityAuditor", "CodeQualityScout",
    "ALL_EMPLOYEES", "QUICK_SCAN_EMPLOYEES",
]
