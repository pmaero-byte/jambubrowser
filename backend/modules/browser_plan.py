"""
Natural-language → test-flow authoring.

Turns a plain-English goal ("test login", "checkout with an invalid card",
"smoke test") into a declarative step flow the one-call runner can execute.
A curated template library handles the common journeys deterministically; an
optional LLM pass can refine the result, but the endpoint never *requires* a
model to be useful.

The planner only *proposes*. It does not execute and does not touch the
network, so it is safe to call freely.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

log = logging.getLogger("jambu.browser_plan")

# Placeholders a caller should fill before running (kept out of the model's
# context when they are secrets).
PLACEHOLDER_RE = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")


def _steps(*items: dict) -> list[dict]:
    return [dict(item) for item in items]


# Each template: (keywords, factory) where factory(url) -> steps.
def _smoke(url: str) -> list[dict]:
    return _steps(
        {"action": "navigate", "url": url},
        {"action": "assert_url", "value": url.rstrip("/")},
        {"action": "assert_console_clean"},
    )


def _login(url: str) -> list[dict]:
    return _steps(
        {"action": "navigate", "url": url},
        {"action": "assert_visible", "target": "Email"},
        {"action": "type", "target": "Email", "value": "{{email}}"},
        {"action": "type", "target": "Password", "value": "{{password}}"},
        {"action": "click", "target": "Sign in", "approve": True},
        {"action": "assert_console_clean"},
    )


def _signup(url: str) -> list[dict]:
    return _steps(
        {"action": "navigate", "url": url},
        {"action": "click", "target": "Sign up", "approve": True},
        {"action": "type", "target": "Email", "value": "{{email}}"},
        {"action": "type", "target": "Password", "value": "{{password}}"},
        {"action": "click", "target": "Create account", "approve": True},
        {"action": "assert_console_clean"},
    )


def _search(url: str) -> list[dict]:
    return _steps(
        {"action": "navigate", "url": url},
        {"action": "type", "target": "Search", "value": "{{query}}"},
        {"action": "press", "key": "Enter"},
        {"action": "wait", "text": "{{query}}"},
        {"action": "assert_console_clean"},
    )


def _checkout(url: str) -> list[dict]:
    return _steps(
        {"action": "navigate", "url": url},
        {"action": "click", "target": "Add to cart", "approve": True},
        {"action": "click", "target": "Checkout", "approve": True},
        {"action": "type", "target": "Card", "value": "{{card}}"},
        {"action": "click", "target": "Pay", "approve": True},
        {"action": "assert_console_clean"},
    )


def _accessibility(url: str) -> list[dict]:
    return _steps(
        {"action": "navigate", "url": url},
        {"action": "assert_no_a11y_violations"},
    )


def _performance(url: str) -> list[dict]:
    return _steps(
        {"action": "navigate", "url": url},
        {"action": "assert_lcp", "value": 2500},
        {"action": "assert_fcp", "value": 1800},
        {"action": "assert_dom_nodes", "value": 1500},
    )


def _responsive(url: str) -> list[dict]:
    return _steps(
        {"action": "navigate", "url": url},
        {"action": "assert_visible", "target": "Menu"},
        {"action": "assert_console_clean"},
    )


TEMPLATES: list[tuple[tuple[str, ...], callable, str]] = [
    (("login", "sign in", "sign-in", "log in", "authenticate"), _login, "login"),
    (("signup", "sign up", "register", "create account"), _signup, "signup"),
    (("checkout", "add to cart", "buy", "purchase", "order"), _checkout, "checkout"),
    (("search", "find", "query"), _search, "search"),
    (("accessib", "a11y", "wcag"), _accessibility, "accessibility"),
    (("performance", "lighthouse", "web vitals", "load time", "slow"), _performance, "performance"),
    (("responsive", "mobile", "viewport", "breakpoint"), _responsive, "responsive"),
    (("smoke", "loads", "homepage", "home page", "sanity", "renders"), _smoke, "smoke"),
]


def plan_flow(goal: str, url: str, *, kind: Optional[str] = None) -> dict:
    """Return a proposed step flow for a goal. Deterministic (no LLM).

    ``kind`` forces a template; otherwise the goal text is matched by keyword.
    """
    goal_low = (goal or "").strip().lower()
    chosen = None
    for keywords, factory, name in TEMPLATES:
        if kind and kind == name:
            chosen = (factory, name, keywords[0])
            break
        if any(k in goal_low for k in keywords):
            chosen = (factory, name, next(k for k in keywords if k in goal_low))
            break
    if chosen is None:
        chosen = (_smoke, "smoke", "")

    factory, name, matched = chosen
    steps = factory(url)
    placeholders = sorted(set(PLACEHOLDER_RE.findall(str(steps))))
    return {
        "goal": goal,
        "url": url,
        "kind": name,
        "matched": matched,
        "steps": steps,
        "placeholders": placeholders,
        "source": "template",
        "notes": (
            "Replace {{placeholders}} before running; secrets should come from "
            "the vault, not the prompt. Review risky actions (approve=true)."
            if placeholders else
            "Review before running; risky actions carry approve=true."
        ),
    }


def synthesize_with_llm(goal: str, url: str, page_summary: str = "",
                        provider: str = "") -> Optional[dict]:
    """Best-effort: ask the configured LLM to refine a plan.

    Returns ``None`` when no provider is configured or the call fails — the
    caller falls back to :func:`plan_flow`. Kept deliberately thin so the
    feature works with zero model dependency.
    """
    try:
        from backend.llm import get_registry  # type: ignore
    except Exception:
        return None
    try:
        registry = get_registry()
        prompt = (
            "You are a browser test planner. Return ONLY a JSON array of step "
            "objects using actions navigate/click/type/press/wait/assert_url/"
            "assert_visible/assert_text/assert_console_clean/assert_no_a11y_violations. "
            f"App URL: {url}. Goal: {goal}. Page summary: {page_summary[:500]}."
        )
        result = registry.complete(prompt, provider=provider or None)  # type: ignore
        text = result if isinstance(result, str) else getattr(result, "text", "")
        import json

        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return None
        steps = json.loads(match.group(0))
        if not isinstance(steps, list) or not steps:
            return None
        return {
            "goal": goal, "url": url, "kind": "llm", "steps": steps,
            "placeholders": sorted(set(PLACEHOLDER_RE.findall(str(steps)))),
            "source": "llm", "notes": "LLM-generated — review before running.",
        }
    except Exception:
        log.warning("LLM plan synthesis failed; using template", exc_info=True)
        return None


def plan(goal: str, url: str, *, kind: Optional[str] = None,
         use_llm: bool = False, provider: str = "") -> dict:
    """Full planner entry point: LLM refinement (optional) then template."""
    if use_llm:
        refined = synthesize_with_llm(goal, url, provider=provider)
        if refined is not None:
            return refined
    return plan_flow(goal, url, kind=kind)
