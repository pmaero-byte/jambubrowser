"""
GAIA benchmark task suite — multi-step reasoning, web research, and tool use.

Based on the GAIA benchmark (https://huggingface.co/spaces/gaia-benchmark/leaderboard).
Tasks require an agent-capable LLM with web search, scraping, and multi-hop reasoning.
All questions are self-contained QA pairs with known ground-truth answers.

Levels:
- L1: Single-step factual lookup (web search or common knowledge)
- L2: Multi-step tool use (search → scrape → compute)
- L3: Complex multi-hop reasoning (multiple tools + synthesis)

Use with: python -m backend.eval run --suite gaia --provider anthropic
"""

from __future__ import annotations

from ..harness import Task, register_task

SUITE = "gaia"


# ── Level 1: Single-step factual lookup ─────────────────────────

register_task(Task(
    id="gaia.l1.currency",
    suite=SUITE,
    prompt="What is the current currency of Japan?",
    expected=["yen", "JPY", "Japanese yen"],
    category="research",
    difficulty=1,
    timeout_seconds=15,
    max_steps=3,
    use_agent=True,
    system="Answer in one short phrase.",
))

register_task(Task(
    id="gaia.l1.planet_order",
    suite=SUITE,
    prompt="What is the fourth planet from the Sun in our solar system?",
    expected=["Mars"],
    category="qa",
    difficulty=1,
    timeout_seconds=10,
    system="Answer with just the planet name.",
))

register_task(Task(
    id="gaia.l1.chemical_symbol",
    suite=SUITE,
    prompt="What is the chemical symbol for gold?",
    expected=["Au"],
    category="qa",
    difficulty=1,
    timeout_seconds=10,
    system="Answer with just the chemical symbol.",
))

register_task(Task(
    id="gaia.l1.un_sg",
    suite=SUITE,
    prompt="Who is the current Secretary-General of the United Nations as of 2026?",
    expected=["António Guterres", "Guterres", "Antonio Guterres"],
    category="research",
    difficulty=2,
    timeout_seconds=20,
    max_steps=3,
    use_agent=True,
    system="Answer with the person's full name.",
))


# ── Level 2: Multi-step tool use ─────────────────────────────────

register_task(Task(
    id="gaia.l2.python_creator",
    suite=SUITE,
    prompt="Who created the Python programming language, and in what year was its first version released?",
    expected=["Guido van Rossum", "1991"],
    category="research",
    difficulty=2,
    timeout_seconds=30,
    max_steps=5,
    use_agent=True,
    system="Answer with the creator name and year.",
))

register_task(Task(
    id="gaia.l2.distance_compute",
    suite=SUITE,
    prompt="The Eiffel Tower is 330 meters tall. If a tourist walks 500 meters away from its base at ground level, what is the straight-line distance (hypotenuse) from the tourist to the top of the tower? Compute and give the answer in meters, rounded to one decimal place.",
    expected=["598.1", "598", "598.0"],
    category="qa",
    difficulty=2,
    timeout_seconds=20,
    system="Use the Pythagorean theorem. Give the numeric answer in meters.",
))

register_task(Task(
    id="gaia.l2.population_ratio",
    suite=SUITE,
    prompt="What is the approximate ratio of the population of India to the population of the United States? (Use the most recent population estimates. Round to the nearest whole number ratio, e.g. '4:1' or '5 to 1')",
    expected=["4", "4:1", "4 to 1"],
    category="research",
    difficulty=2,
    timeout_seconds=30,
    max_steps=4,
    use_agent=True,
    system="Express as a simple ratio like 'X:1' or just the number X.",
))

register_task(Task(
    id="gaia.l2.file_size_calc",
    suite=SUITE,
    prompt="A high-resolution photograph is 6000x4000 pixels with 24-bit color depth. What is the uncompressed file size in megabytes (MB)? Use 1 MB = 1,048,576 bytes.",
    expected=["68.7", "69", "68.66"],
    category="qa",
    difficulty=2,
    timeout_seconds=20,
    system="Show your calculation. Give the final answer in MB.",
))


# ── Level 3: Complex multi-hop reasoning ─────────────────────────

register_task(Task(
    id="gaia.l3.nobel_physics_2025",
    suite=SUITE,
    prompt="Who won the Nobel Prize in Physics in 2025, and what was their primary contribution recognized by the prize?",
    expected=["Hopfield", "Hinton", "machine learning", "neural network", "artificial"],
    category="research",
    difficulty=3,
    timeout_seconds=40,
    max_steps=6,
    use_agent=True,
    system="Answer with the winner's name and their contribution in 2-3 sentences.",
))

register_task(Task(
    id="gaia.l3.carbon_footprint",
    suite=SUITE,
    prompt="If a passenger jet burns approximately 5 gallons of fuel per mile and flies from New York to London (approximately 3,450 miles), how many metric tons of CO2 are produced? Assume each gallon of jet fuel produces 9.57 kg of CO2. Give the answer in metric tons (1 ton = 1000 kg).",
    expected=["165", "165.1", "165.08", "165.0"],
    category="qa",
    difficulty=3,
    timeout_seconds=25,
    system="Show your work step by step. Give the final answer in metric tons.",
))

register_task(Task(
    id="gaia.l3.company_timeline",
    suite=SUITE,
    prompt="Apple Inc. was founded in 1976. Microsoft was founded in 1975. Google was incorporated in 1998. How many years after Apple's founding was Google incorporated?",
    expected=["22", "22 years"],
    category="qa",
    difficulty=2,
    timeout_seconds=15,
    system="Answer with just the number of years.",
))

register_task(Task(
    id="gaia.l3.code_puzzle",
    suite=SUITE,
    prompt="In Python, what is the output of: sum(i*i for i in range(1, 6))?",
    expected=["55"],
    category="coding",
    difficulty=2,
    timeout_seconds=20,
    max_steps=3,
    use_agent=True,
    system="Compute the answer and give just the number.",
))
