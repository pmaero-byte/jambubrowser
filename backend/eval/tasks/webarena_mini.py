"""
WebArena-style mini-benchmark — browser-based task completion.

Inspired by WebArena's real-website tasks but uses self-contained prompts that
exercise the agent's tool use (web_search, scrape_url, knowledge_query)
without requiring actual live websites.

Each task requires multi-step planning and tool use.
"""

from __future__ import annotations

from ..harness import Task, register_task


SUITE = "webarena-mini"

# W1: Search + summarize
register_task(Task(
    id="web.search1.summary",
    suite=SUITE,
    prompt="Search the web for 'Rust async runtime' and provide a one-paragraph summary.",
    expected=["tokio", "async-std", "smol"],
    category="browser",
    difficulty=2,
    timeout_seconds=30,
    use_agent=True,
    max_steps=3,
    system="Cite the URLs you find.",
))

# W2: Multi-source synthesis
register_task(Task(
    id="web.search2.compare",
    suite=SUITE,
    prompt="Search the web for 'PostgreSQL vs MySQL' and give a 2-3 sentence comparison highlighting one key difference.",
    expected=["PostgreSQL", "MySQL"],
    category="browser",
    difficulty=2,
    timeout_seconds=30,
    use_agent=True,
    max_steps=3,
))

# W3: Latest news (research)
register_task(Task(
    id="web.news1.recent",
    suite=SUITE,
    prompt="What's the latest news about the Rust programming language in 2026? Mention at least one specific event or release.",
    expected=["2026", "Rust"],
    category="browser",
    difficulty=3,
    timeout_seconds=45,
    use_agent=True,
    max_steps=4,
    system="Include at least one specific URL from your search.",
))

# W4: Code search (mock github)
register_task(Task(
    id="web.code1.repo",
    suite=SUITE,
    prompt="Search GitHub for a popular Rust web framework. Tell me the name and what it's used for.",
    expected=["actix", "axum", "rocket", "warp"],
    category="browser",
    difficulty=2,
    timeout_seconds=30,
    use_agent=True,
    max_steps=3,
))

# W5: Tool selection — knowledge graph
register_task(Task(
    id="web.kg1.query",
    suite=SUITE,
    prompt="Query your knowledge graph for 'Rust' and tell me what entities are related to it.",
    expected=["Rust"],
    category="browser",
    difficulty=1,
    timeout_seconds=15,
    use_agent=True,
    max_steps=2,
))

# W6: Multi-hop via tools
register_task(Task(
    id="web.multihop1",
    suite=SUITE,
    prompt="Use multiple tools to: 1) search for the current version of Node.js, 2) check our knowledge graph for related entities, 3) provide a summary.",
    expected=["Node.js", "node", "v"],
    category="browser",
    difficulty=3,
    timeout_seconds=45,
    use_agent=True,
    max_steps=4,
))

# W7: URL safety check
register_task(Task(
    id="web.safety1.url",
    suite=SUITE,
    prompt="Check if https://github.com is safe to visit using the risk check tool.",
    expected=["github.com", "safe", "low"],
    category="browser",
    difficulty=1,
    timeout_seconds=15,
    use_agent=True,
    max_steps=2,
))

# W8: Information aggregation
register_task(Task(
    id="web.agg1.facts",
    suite=SUITE,
    prompt="Find the population of Tokyo, Japan. Just give the number in millions.",
    expected=["37", "13", "14", "9"],
    category="browser",
    difficulty=2,
    timeout_seconds=30,
    use_agent=True,
    max_steps=3,
    system="Round to the nearest million. Just output the number.",
))
