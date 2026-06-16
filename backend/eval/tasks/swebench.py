"""
SWE-bench Verified task suite — self-contained software engineering problems.

Based on the SWE-bench Verified benchmark (https://www.swebench.com/).
Tasks are coding puzzles with known solutions. The agent must understand the
problem description, produce correct code, and handle edge cases.

All tasks are self-contained — no external GitHub repos or live APIs needed.
The agent uses the code_exec tool to write and test Python solutions.
"""

from __future__ import annotations

from ..harness import Task, register_task

SUITE = "swebench"


# ── Bug fixes ─────────────────────────────────────────────────────

register_task(Task(
    id="swe.fix.empty_input",
    suite=SUITE,
    prompt=(
        "Fix this Python function that crashes on empty input:\n\n"
        "```python\ndef parse_numbers(text):\n"
        "    parts = text.split(',')\n"
        "    return [int(p) for p in parts]\n"
        "```\n\n"
        "When text='' or text=None, it crashes. Write the fixed function that "
        "returns an empty list for empty or None input."
    ),
    expected=["return []", "if not text", "if text is None", "empty list"],
    category="coding",
    difficulty=2,
    timeout_seconds=30,
    max_steps=5,
    use_agent=True,
    system="Write the complete fixed function. Include the fix for empty/None input.",
))

register_task(Task(
    id="swe.fix.off_by_one",
    suite=SUITE,
    prompt=(
        "Fix the off-by-one error in this loop (it should print numbers 1 through 5):\n\n"
        "```python\nfor i in range(1, 5):\n"
        "    print(i)\n"
        "```\n\n"
        "What change is needed to print 1, 2, 3, 4, 5?"
    ),
    expected=["range(1, 6)", "6", "range(1,5+1)"],
    category="coding",
    difficulty=1,
    timeout_seconds=15,
    system="State the fix. Show the corrected code.",
))

register_task(Task(
    id="swe.fix.key_error",
    suite=SUITE,
    prompt=(
        "Fix this dictionary lookup to use a default value instead of crashing:\n\n"
        "```python\ndef get_setting(config, key):\n"
        "    return config[key]\n"
        "```\n\n"
        "When key is missing, it should return 'default' instead of raising KeyError."
    ),
    expected=["get", "default", "config.get(key", ".get("],
    category="coding",
    difficulty=2,
    timeout_seconds=20,
    max_steps=3,
    use_agent=True,
    system="Write the fixed function. Use .get() with a default value.",
))


# ── Input validation ─────────────────────────────────────────────

register_task(Task(
    id="swe.validate.age",
    suite=SUITE,
    prompt=(
        "Add input validation to this function. It should reject negative ages "
        "and non-integer inputs:\n\n"
        "```python\ndef calculate_birth_year(age, current_year=2026):\n"
        "    return current_year - age\n"
        "```\n\n"
        "Add validation that raises ValueError for invalid age values."
    ),
    expected=["ValueError", "isinstance", "type(age)", "raise ValueError", "negative"],
    category="coding",
    difficulty=2,
    timeout_seconds=30,
    max_steps=5,
    use_agent=True,
    system="Write the fixed function with input validation. Raise ValueError for invalid inputs.",
))

register_task(Task(
    id="swe.validate.email",
    suite=SUITE,
    prompt=(
        "Write a Python function `is_valid_email(email: str) -> bool` that checks "
        "if a string looks like a valid email address. A valid email must: "
        "contain exactly one '@', have at least one character before and after '@', "
        "and contain a '.' after the '@'."
    ),
    expected=["@", "split('@')", "return", "False", "True", "def is_valid_email"],
    category="coding",
    difficulty=2,
    timeout_seconds=25,
    max_steps=4,
    use_agent=True,
    system="Write the complete function. Include the @ check, length checks, and dot check.",
))


# ── Algorithm implementation ────────────────────────────────────

register_task(Task(
    id="swe.algo.fibonacci",
    suite=SUITE,
    prompt=(
        "Write a Python function `fibonacci(n: int) -> int` that returns the "
        "nth Fibonacci number using iteration (not recursion). F(0)=0, F(1)=1. "
        "Handle n=0 and n=1 correctly. Raise ValueError for negative n."
    ),
    expected=["def fibonacci", "return", "for", "ValueError", "negative"],
    category="coding",
    difficulty=2,
    timeout_seconds=25,
    max_steps=4,
    use_agent=True,
    system="Write the complete iterative function. Include error handling for negative input.",
))

register_task(Task(
    id="swe.algo.retry",
    suite=SUITE,
    prompt=(
        "Implement a retry decorator/function in Python that retries a function "
        "up to 3 times on failure, with a 1-second delay between retries. "
        "If all retries fail, re-raise the last exception.\n\n"
        "Write the function `retry(func, max_attempts=3, delay=1.0)` that returns "
        "a wrapped function."
    ),
    expected=["import time", "def retry", "for", "range", "try", "except", "sleep", "raise"],
    category="coding",
    difficulty=3,
    timeout_seconds=35,
    max_steps=6,
    use_agent=True,
    system="Write the complete retry decorator. Use time.sleep() for delays.",
))

register_task(Task(
    id="swe.algo.deduplicate",
    suite=SUITE,
    prompt=(
        "Write a Python function `deduplicate(items: list, key=None) -> list` that "
        "removes duplicate items while preserving order. If key is provided, "
        "use key(item) for comparison instead of the item itself. "
        "Example: deduplicate([1, 2, 2, 3, 1]) should return [1, 2, 3]."
    ),
    expected=["def deduplicate", "set", "seen", "not in", "key(x)", "for"],
    category="coding",
    difficulty=2,
    timeout_seconds=25,
    max_steps=4,
    use_agent=True,
    system="Write the complete function. Preserve order of first occurrence.",
))


# ── Edge case handling ────────────────────────────────────────────

register_task(Task(
    id="swe.edge.none_handling",
    suite=SUITE,
    prompt=(
        "Fix this function to handle None values gracefully in the input list:\n\n"
        "```python\ndef average(numbers):\n"
        "    return sum(numbers) / len(numbers)\n"
        "```\n\n"
        "It crashes if numbers is None, empty, or contains None values. "
        "Rewrite it to return 0.0 for invalid inputs."
    ),
    expected=["return 0.0", "if not numbers", "if numbers is None", "filter", "None"],
    category="coding",
    difficulty=2,
    timeout_seconds=25,
    max_steps=4,
    use_agent=True,
    system="Write the fixed function. Return 0.0 for None, empty list, or all-None list.",
))

register_task(Task(
    id="swe.edge.format_string",
    suite=SUITE,
    prompt=(
        "Fix this string formatting to handle edge cases:\n\n"
        "```python\ndef describe_person(name, age=None, city=None):\n"
        "    parts = []\n"
        "    parts.append(f'Name: {name}')\n"
        "    parts.append(f'Age: {age}')\n"
        "    parts.append(f'City: {city}')\n"
        "    return ', '.join(parts)\n"
        "```\n\n"
        "It shows 'Age: None' when age isn't provided. Fix it to only include "
        "fields that have actual values."
    ),
    expected=["if age", "if city", "is not None", "append", "not None"],
    category="coding",
    difficulty=2,
    timeout_seconds=25,
    max_steps=4,
    use_agent=True,
    system="Write the fixed function. Only include fields with non-None values.",
))
