"""
GAIA-style mini-benchmark — multi-step reasoning, file handling, and
information retrieval tasks. Inspired by the GAIA benchmark but small enough
to run in a few minutes.

Each task is a single-prompt question with a verifiable ground-truth answer.
Tasks span:
- Q&A (factual, arithmetic)
- Multi-hop reasoning
- File / table reasoning
- Science & common sense

Use the full agent loop (use_agent=True) for multi-step tasks.
"""

from __future__ import annotations

from ..harness import Task, register_task


# ── Suite: gaia-mini ────────────────────────────────────────────
SUITE = "gaia-mini"

# Q1: Trivia
register_task(Task(
    id="gaia.q1.capital",
    suite=SUITE,
    prompt="What is the capital of France?",
    expected="Paris",
    category="qa",
    difficulty=1,
    timeout_seconds=10,
    system="Answer in one short sentence.",
))

# Q2: Arithmetic
register_task(Task(
    id="gaia.q2.arithmetic",
    suite=SUITE,
    prompt="If I have 3 apples and you give me 5 more, then I eat 2, how many do I have left? Show your work.",
    expected=["6", "six"],
    category="qa",
    difficulty=1,
    timeout_seconds=10,
    system="Think step by step. End with a single number answer.",
))

# Q3: Multi-hop (research)
register_task(Task(
    id="gaia.q3.multihop",
    suite=SUITE,
    prompt="Who is the current CEO of the company that makes the Python programming language?",
    expected=["Sam Altman", "altman"],
    category="research",
    difficulty=2,
    timeout_seconds=30,
    use_agent=True,  # needs web search
    max_steps=4,
))

# Q4: Common sense
register_task(Task(
    id="gaia.q4.commonsense",
    suite=SUITE,
    prompt="A person is standing in line at a coffee shop. They have $5 and a $10 bill. The menu shows a latte costs $4.50. Can they buy the latte?",
    expected="yes",
    category="qa",
    difficulty=1,
    timeout_seconds=10,
    system="Answer only 'yes' or 'no' with a brief explanation.",
))

# Q5: Logic
register_task(Task(
    id="gaia.q5.logic",
    suite=SUITE,
    prompt="All roses are flowers. Some flowers fade quickly. Therefore, can we conclude that some roses fade quickly?",
    expected=["no", "cannot conclude", "not necessarily"],
    category="qa",
    difficulty=2,
    timeout_seconds=10,
    system="Answer with one word: 'yes' or 'no', followed by a brief explanation.",
))

# Q6: Date / time reasoning
register_task(Task(
    id="gaia.q6.date",
    suite=SUITE,
    prompt="If today is June 11, 2026, what day of the week was January 1, 2026?",
    expected=["Thursday", "thursday"],
    category="qa",
    difficulty=3,
    timeout_seconds=15,
    system="Think step by step. End with the day of the week.",
))

# Q7: Math word problem
register_task(Task(
    id="gaia.q7.math",
    suite=SUITE,
    prompt="A train leaves station A at 9:00 AM traveling at 60 mph. Another train leaves station B (200 miles away from A) at 10:00 AM traveling toward A at 80 mph. At what time do they meet?",
    expected=["11:36", "11:36 AM", "11.6 hours"],
    category="qa",
    difficulty=3,
    timeout_seconds=15,
    system="Show your work. State the time they meet in HH:MM AM/PM format.",
))

# Q8: Causal reasoning
register_task(Task(
    id="gaia.q8.causal",
    suite=SUITE,
    prompt="If a power plant burns less coal, what happens to atmospheric CO2 levels?",
    expected=["decrease", "decreases", "lower", "drop", "reduce", "reduced"],
    category="qa",
    difficulty=1,
    timeout_seconds=10,
))

# Q9: Reading comprehension (text)
register_task(Task(
    id="gaia.q9.reading",
    suite=SUITE,
    prompt="Read the following passage and answer: 'The mitochondrion is the powerhouse of the cell. It produces ATP through oxidative phosphorylation, which requires oxygen. Without oxygen, cells switch to anaerobic glycolysis, producing far less ATP and generating lactic acid as a byproduct.'\n\nWhat byproduct is produced during anaerobic metabolism?",
    expected=["lactic acid"],
    category="qa",
    difficulty=1,
    timeout_seconds=10,
    system="Answer in one short phrase.",
))

# Q10: Conversion
register_task(Task(
    id="gaia.q10.conversion",
    suite=SUITE,
    prompt="How many bytes are in 4 kilobytes? (Use 1 KB = 1024 bytes)",
    expected=["4096", "4,096"],
    category="qa",
    difficulty=2,
    timeout_seconds=10,
    system="Just the number.",
))
