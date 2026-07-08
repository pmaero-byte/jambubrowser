import pytest

# Quarantine: exclude legacy phase tests from the default collection so the
# core suite stays honest. Run `pytest tests/legacy/` to opt in.
collect_ignore = [
    "test_phase2.py",
    "test_phase3.py",
    "test_phase4.py",
    "test_phase5.py",
]
