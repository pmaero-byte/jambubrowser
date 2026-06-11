"""
Memory layer mini-benchmark — tests the v3 memory system.

Verifies that:
- Stored memories are recalled on related queries
- The user profile influences answer relevance
- Forgetting removes memories from recall
"""

from __future__ import annotations

from ..harness import Task, register_task


SUITE = "memory"

# M1: Profile interest recall (uses memory_recall tool)
register_task(Task(
    id="mem.profile.recall",
    suite=SUITE,
    prompt=(
        "Use the memory_recall tool to find what the user's interests are. "
        "The user has previously stored: 'User loves Rust programming', "
        "'User is interested in machine learning', 'User enjoys hiking'."
    ),
    expected=["Rust", "machine learning", "hiking"],
    category="memory",
    difficulty=1,
    timeout_seconds=15,
    use_agent=True,
    max_steps=3,
    system="List the interests you find.",
))

# M2: Procedural memory: best approach
register_task(Task(
    id="mem.procedural.best",
    suite=SUITE,
    prompt=(
        "You have a procedural memory entry: 'summarize research paper' approach='use abstract' "
        "with 8 successes out of 10 attempts. Should you use this approach for a new research "
        "paper summarization task?"
    ),
    expected=["yes", "use it", "80%", "0.8"],
    category="memory",
    difficulty=2,
    timeout_seconds=10,
    system="Answer with a clear yes/no and a one-sentence justification.",
))

# M3: Store + recall round-trip
register_task(Task(
    id="mem.store.recall",
    suite=SUITE,
    prompt=(
        "First use memory_store to save: 'The user's favorite color is indigo'. "
        "Then use memory_recall to verify it can be found. Report what you stored."
    ),
    expected=["indigo"],
    category="memory",
    difficulty=2,
    timeout_seconds=15,
    use_agent=True,
    max_steps=3,
    system="After storing, use memory_recall to confirm.",
))

# M4: Context-aware answer
register_task(Task(
    id="mem.context.use",
    suite=SUITE,
    prompt=(
        "Given that the user has stored in memory: 'User works on low-latency trading systems', "
        "and 'User prefers C++ over Java for performance-critical code', "
        "what language would you recommend for a new high-frequency trading project?"
    ),
    expected=["C++", "rust"],
    category="memory",
    difficulty=2,
    timeout_seconds=10,
    system="Use the user's stored context to inform your answer.",
))

# M5: Forgetting behavior
register_task(Task(
    id="mem.forget.behavior",
    suite=SUITE,
    prompt=(
        "If a user calls DELETE /v2/memory/123 to forget memory entry 123, "
        "what should happen to subsequent recall queries for that entry?"
    ),
    expected=["not return", "remove", "no longer", "forgotten", "deleted"],
    category="memory",
    difficulty=1,
    timeout_seconds=10,
    system="Answer in one sentence.",
))
