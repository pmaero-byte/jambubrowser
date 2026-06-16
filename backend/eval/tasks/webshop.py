"""
WebShop benchmark task suite — e-commerce search and decision-making.

Based on the WebShop benchmark (https://webshop-pnlp.github.io/).
Tasks require the agent to search for products matching specified criteria,
compare options across multiple attributes (color, price, size, brand, rating),
and select the best match. Tests search query formulation, filtering, and
multi-attribute comparison capabilities.

All tasks are self-contained with known acceptable answers — no live
e-commerce API calls required. Answers are verified via fuzzy contains match.
"""

from __future__ import annotations

from ..harness import Task, register_task

SUITE = "webshop"


# ── Single-attribute search ──────────────────────────────────────

register_task(Task(
    id="ws.single.color",
    suite=SUITE,
    prompt=(
        "I'm looking for a red backpack for everyday use. What color backpack "
        "should I search for and what would be a good choice?"
    ),
    expected=["red"],
    category="research",
    difficulty=1,
    timeout_seconds=15,
    max_steps=3,
    use_agent=True,
    system="Answer with a brief product recommendation. Include the color in your answer.",
))

register_task(Task(
    id="ws.single.brand",
    suite=SUITE,
    prompt=(
        "I need a pair of running shoes from Nike. What brand should I look for, "
        "and what's a popular Nike running shoe model?"
    ),
    expected=["Nike", "nike"],
    category="research",
    difficulty=1,
    timeout_seconds=15,
    max_steps=3,
    use_agent=True,
    system="Name at least one specific Nike running shoe model.",
))

register_task(Task(
    id="ws.single.size",
    suite=SUITE,
    prompt=(
        "I need king-size cotton bedsheets in blue. What size and material "
        "should the sheets be, and what would I typically search for?"
    ),
    expected=["king", "cotton", "blue"],
    category="research",
    difficulty=1,
    timeout_seconds=15,
    max_steps=3,
    use_agent=True,
    system="Mention the size, material, and color in your answer.",
))


# ── Multi-attribute comparison ───────────────────────────────────

register_task(Task(
    id="ws.multi.headphones",
    suite=SUITE,
    prompt=(
        "I want wireless noise-cancelling headphones with at least 20 hours of "
        "battery life, under $200. What specific model should I consider? "
        "Give me the best match."
    ),
    expected=["Sony", "Bose", "JBL", "Anker", "Soundcore", "Sennheiser", "headphone"],
    category="research",
    difficulty=2,
    timeout_seconds=30,
    max_steps=5,
    use_agent=True,
    system="Recommend a specific brand and model. State the key features that match the criteria.",
))

register_task(Task(
    id="ws.multi.laptop",
    suite=SUITE,
    prompt=(
        "I need a lightweight laptop (under 3 lbs) with at least 16GB RAM, "
        "a 14-inch screen, and good battery life (8+ hours). My budget is $1500. "
        "What laptop best fits these requirements?"
    ),
    expected=["MacBook", "Dell", "ThinkPad", "ZenBook", "laptop", "14"],
    category="research",
    difficulty=2,
    timeout_seconds=30,
    max_steps=5,
    use_agent=True,
    system="Recommend a specific laptop model. Justify how it meets each criterion.",
))

register_task(Task(
    id="ws.multi.camera",
    suite=SUITE,
    prompt=(
        "I want a mirrorless camera with at least 24 megapixels, 4K video recording, "
        "and weather sealing. Budget is under $2000 for the body only. "
        "What camera should I buy?"
    ),
    expected=["Sony", "Canon", "Nikon", "Fujifilm", "camera", "mirrorless"],
    category="research",
    difficulty=3,
    timeout_seconds=30,
    max_steps=5,
    use_agent=True,
    system="Recommend a specific camera model. Mention resolution, video capability, and weather sealing.",
))


# ── Price comparison & decision ──────────────────────────────────

register_task(Task(
    id="ws.price.phone",
    suite=SUITE,
    prompt=(
        "I'm comparing two smartphones: Phone A costs $800 and has a 48MP camera "
        "and 128GB storage. Phone B costs $650 and has a 50MP camera and 256GB storage. "
        "Which phone offers better value for money and why?"
    ),
    expected=["Phone B", "B", "second"],
    category="qa",
    difficulty=2,
    timeout_seconds=20,
    max_steps=3,
    use_agent=True,
    system="Compare the two phones on price, camera, and storage. State which is better value.",
))

register_task(Task(
    id="ws.price.tv",
    suite=SUITE,
    prompt=(
        "Three 55-inch 4K TVs: TV1 is $500 with 3 HDMI ports and 60Hz refresh, "
        "TV2 is $700 with 4 HDMI ports and 120Hz refresh, "
        "TV3 is $600 with 3 HDMI ports and 120Hz refresh. "
        "If I care most about refresh rate for gaming, which is the best value?"
    ),
    expected=["TV3", "third", "3"],
    category="qa",
    difficulty=2,
    timeout_seconds=20,
    max_steps=3,
    use_agent=True,
    system="Compare all three. State which TV is best for gaming value. Mention the price.",
))

register_task(Task(
    id="ws.price.coffee",
    suite=SUITE,
    prompt=(
        "A coffee maker costs $120 and lasts 5 years. Another costs $80 and lasts "
        "3 years. Which has the lower cost per year of use?"
    ),
    expected=["80", "second", "cheaper", "$80", "$26.67", "26"],
    category="qa",
    difficulty=2,
    timeout_seconds=15,
    system="Calculate cost per year for each. State which is lower.",
))


# ── Constraint satisfaction ──────────────────────────────────────

register_task(Task(
    id="ws.constraint.monitor",
    suite=SUITE,
    prompt=(
        "I need a computer monitor that is: 27 inches, 4K resolution, IPS panel, "
        "USB-C connectivity, and under $500. What should I look for and what model "
        "would you suggest?"
    ),
    expected=["Dell", "LG", "Samsung", "ASUS", "monitor", "27", "4K"],
    category="research",
    difficulty=3,
    timeout_seconds=30,
    max_steps=5,
    use_agent=True,
    system="Suggest a specific monitor model that meets all criteria. List the matching features.",
))

register_task(Task(
    id="ws.constraint.vacuum",
    suite=SUITE,
    prompt=(
        "I want a robot vacuum that: supports mapping/navigation, has a self-emptying "
        "base, works with Alexa, and costs under $600. What's the best option?"
    ),
    expected=["Roomba", "Roborock", "Shark", "Eufy", "vacuum", "robot"],
    category="research",
    difficulty=2,
    timeout_seconds=25,
    max_steps=5,
    use_agent=True,
    system="Recommend a specific robot vacuum model. Mention mapping, self-emptying, and smart home features.",
))
