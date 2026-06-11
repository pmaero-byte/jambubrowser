"""
Smoke test suite — 5 fast tasks for quick CI verification.
Should complete in <30s on any provider. Use as a pre-commit / pre-deploy gate.
"""

from __future__ import annotations

from ..harness import Task, register_task


SUITE = "smoke"

register_task(Task(
    id="smoke.1.hello",
    suite=SUITE,
    prompt="Reply with just the word 'hello'.",
    expected=["hello", "Hello", "HELLO"],
    category="qa",
    difficulty=1,
    timeout_seconds=10,
))

register_task(Task(
    id="smoke.2.math",
    suite=SUITE,
    prompt="What is 7 multiplied by 8?",
    expected=["56"],
    category="qa",
    difficulty=1,
    timeout_seconds=10,
))

register_task(Task(
    id="smoke.3.capital",
    suite=SUITE,
    prompt="What's the capital of Japan?",
    expected="Tokyo",
    category="qa",
    difficulty=1,
    timeout_seconds=10,
))

register_task(Task(
    id="smoke.4.list",
    suite=SUITE,
    prompt="List three primary colors.",
    expected=["red", "blue", "yellow"],
    category="qa",
    difficulty=1,
    timeout_seconds=10,
    system="Comma-separated.",
))

register_task(Task(
    id="smoke.5.json",
    suite=SUITE,
    prompt='Return a JSON object with one key "status" set to "ok".',
    expected=["status", "ok"],
    category="qa",
    difficulty=2,
    timeout_seconds=10,
    system="Output ONLY valid JSON, nothing else.",
))
