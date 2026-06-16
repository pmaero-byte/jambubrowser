"""
Harness Defaults — Pre-built HarnessConfigs for common agent modes
===================================================================

Each preset targets a specific use case with optimised prompts, memory
policies, control flow, and LLM routing. These serve as:
- Starting points for the AEGIS evolution engine (Round 0 configs)
- Human-readable templates for agent configuration
- Baseline configs for benchmark comparisons

Modes:
- **research** — deep multi-step web research with synthesis
- **browser** — web automation, form filling, scraping
- **coding** — code generation, debugging, refactoring
- **privacy_standard** — balanced privacy + capability
- **privacy_maximum** — local-only, no cloud providers
- **quick_answer** — single-shot, minimal steps, fast
"""

from __future__ import annotations

from .harness import (
    HarnessConfig,
    MemoryPolicy,
    ControlFlowSpec,
    LLMRoutingSpec,
    PromptConfig,
)


# ---------------------------------------------------------------------------
# Prompt variants
# ---------------------------------------------------------------------------

_RESEARCH_PLANNER = (
    "You are a planning module for a deep-research AI agent. Given a research "
    "question or topic, produce a thorough step-by-step plan to gather and "
    "synthesize information from multiple sources.\n\n"
    "Available tools:\n{tool_descriptions}\n\n"
    "Research goal: {query}\n\n{user_context}\n\n"
    "Instructions:\n"
    "1. Start with a web_search to gather initial information\n"
    "2. Follow up with scrape_url on the most promising results\n"
    "3. Use memory_recall to check for prior knowledge on this topic\n"
    "4. Cross-reference findings with additional searches\n"
    "5. End with final_answer that cites specific sources\n\n"
    'Respond with JSON: {{"steps": [{{"description": "...", "tool": "...", "args": {{...}} }}]}}\n'
    "Max {max_steps} steps."
)


_BROWSER_PLANNER = (
    "You are a web automation agent. Given a task involving web interaction, "
    "produce a precise step-by-step plan using browser tools.\n\n"
    "Available tools:\n{tool_descriptions}\n\n"
    "Task: {query}\n\n{user_context}\n\n"
    "Instructions:\n"
    "1. Navigate to the target URL first\n"
    "2. Interact with the page (click, fill, extract) as needed\n"
    "3. Verify each action's result before proceeding\n"
    "4. End with final_answer summarising what was accomplished\n\n"
    'Respond with JSON: {{"steps": [{{"description": "...", "tool": "...", "args": {{...}} }}]}}\n'
    "Max {max_steps} steps."
)


_CODING_PLANNER = (
    "You are a coding assistant agent. Given a programming task, produce a plan "
    "to implement, debug, or refactor code.\n\n"
    "Available tools:\n{tool_descriptions}\n\n"
    "Task: {query}\n\n{user_context}\n\n"
    "Instructions:\n"
    "1. Understand the existing codebase (use knowledge_query or web_search)\n"
    "2. Plan the change before executing code\n"
    "3. Use code_exec for implementation in sandboxed Python\n"
    "4. Verify the result and iterate if needed\n"
    "5. End with final_answer containing the solution and explanation\n\n"
    'Respond with JSON: {{"steps": [{{"description": "...", "tool": "...", "args": {{...}} }}]}}\n'
    "Max {max_steps} steps."
)


# ---------------------------------------------------------------------------
# Preset configs
# ---------------------------------------------------------------------------

def research_default() -> HarnessConfig:
    """Deep multi-step web research agent."""
    return HarnessConfig(
        description="Deep research config — multi-step web search + synthesis + cross-referencing",
        tags=["research", "default"],
        prompts=PromptConfig(
            task_agent_system=(
                "You are a thorough research assistant. Gather information from multiple "
                "sources, verify facts, and synthesise clear, well-cited answers. "
                "Always prefer primary sources over secondary ones."
            ),
            planner_user_template=_RESEARCH_PLANNER,
        ),
        tool_registry_names=[
            "web_search", "scrape_url", "memory_recall", "memory_store",
            "knowledge_query", "risk_check", "final_answer",
        ],
        memory_policy=MemoryPolicy(
            use_semantic_memory=True,
            use_procedural_memory=True,
            retrieval_k=15,
            vector_weight=0.6,
            recency_weight=0.2,
            importance_weight=0.1,
            fts_weight=0.1,
            recency_tau_days=7.0,  # shorter half-life for fast-moving research
        ),
        control_flow=ControlFlowSpec(
            max_steps=10,
            max_tokens=40000,
            max_seconds=180.0,
            replan_on_failure=True,
            replan_on_weak_progress=True,
            replan_confidence_threshold=0.65,
        ),
        llm_routing=LLMRoutingSpec(
            routing_strategy="quality",
            temperature=0.3,
            tool_use_temperature=0.2,
        ),
    )


def browser_default() -> HarnessConfig:
    """Browser automation agent for web interaction tasks."""
    return HarnessConfig(
        description="Browser automation config — navigate, click, fill, extract web pages",
        tags=["browser", "default"],
        prompts=PromptConfig(
            task_agent_system=(
                "You are a web automation agent. Navigate pages, interact with elements, "
                "and extract information. Always verify the page state after each action. "
                "Handle errors gracefully — if a selector fails, try alternatives."
            ),
            planner_user_template=_BROWSER_PLANNER,
        ),
        tool_registry_names=[
            "browser_navigate", "browser_click", "browser_extract",
            "browser_fill", "scrape_url", "risk_check", "final_answer",
        ],
        memory_policy=MemoryPolicy(
            use_session_memory=True,      # keep page state across steps
            use_procedural_memory=True,   # remember successful selectors
            use_semantic_memory=False,
            retrieval_k=5,
        ),
        control_flow=ControlFlowSpec(
            max_steps=15,
            max_tokens=25000,
            max_seconds=120.0,
            replan_on_failure=True,
            replan_on_weak_progress=False,  # browser tasks: action success is the signal
            replan_confidence_threshold=0.5,
            verify_after_each_step=True,
        ),
        llm_routing=LLMRoutingSpec(
            routing_strategy="fastest",
            temperature=0.1,              # deterministic for automation
            tool_use_temperature=0.0,
        ),
    )


def coding_default() -> HarnessConfig:
    """Code generation, debugging, and refactoring agent."""
    return HarnessConfig(
        description="Coding config — implement, debug, refactor code with sandboxed execution",
        tags=["coding", "default"],
        prompts=PromptConfig(
            task_agent_system=(
                "You are an expert software engineer. Write clean, tested, maintainable code. "
                "Understand the existing codebase before making changes. Run code in sandbox "
                "to verify correctness. Explain your reasoning clearly."
            ),
            planner_user_template=_CODING_PLANNER,
        ),
        tool_registry_names=[
            "web_search", "knowledge_query", "memory_recall",
            "code_exec", "final_answer",
        ],
        memory_policy=MemoryPolicy(
            use_semantic_memory=True,
            use_procedural_memory=True,
            retrieval_k=10,
            recency_tau_days=30.0,       # coding knowledge decays slower
        ),
        control_flow=ControlFlowSpec(
            max_steps=8,
            max_tokens=50000,
            max_seconds=300.0,
            replan_on_failure=True,
            replan_on_weak_progress=True,
            replan_confidence_threshold=0.7,
            synthesis_max_tokens=1500,    # code explanations can be longer
        ),
        llm_routing=LLMRoutingSpec(
            routing_strategy="quality",
            temperature=0.2,
            tool_use_temperature=0.1,
        ),
    )


def privacy_standard_default() -> HarnessConfig:
    """Balanced privacy + capability."""
    return HarnessConfig(
        description="Standard privacy config — balanced between privacy and capability",
        tags=["privacy", "standard"],
        prompts=PromptConfig.defaults(),
        tool_registry_names=[
            "web_search", "scrape_url", "memory_recall", "memory_store",
            "knowledge_query", "risk_check", "code_exec", "final_answer",
        ],
        memory_policy=MemoryPolicy(),
        control_flow=ControlFlowSpec(),
        llm_routing=LLMRoutingSpec(
            routing_strategy="fallback",
        ),
    )


def privacy_maximum_default() -> HarnessConfig:
    """Local-only, maximum privacy — no cloud providers, no external API calls."""
    return HarnessConfig(
        description="Maximum privacy config — local-only LLM, no cloud providers, no tracking",
        tags=["privacy", "maximum", "local_only"],
        prompts=PromptConfig.defaults(),
        tool_registry_names=[
            "memory_recall", "memory_store", "knowledge_query",
            "code_exec", "final_answer",
            # No web_search, no scrape_url — offline only
        ],
        memory_policy=MemoryPolicy(
            retrieval_k=20,
            vector_weight=0.7,
            recency_weight=0.15,
            importance_weight=0.1,
            fts_weight=0.05,
        ),
        control_flow=ControlFlowSpec(
            max_steps=6,
            max_tokens=15000,
            max_seconds=60.0,
        ),
        llm_routing=LLMRoutingSpec(
            provider="ollama",            # local-first
            routing_strategy="local_only",
        ),
    )


def quick_answer_default() -> HarnessConfig:
    """Single-shot, minimal steps for fast answers to simple questions."""
    return HarnessConfig(
        description="Quick answer config — single-shot, minimal overhead",
        tags=["quick", "default"],
        prompts=PromptConfig.defaults(),
        tool_registry_names=["final_answer"],
        memory_policy=MemoryPolicy(
            use_semantic_memory=True,
            retrieval_k=3,
        ),
        control_flow=ControlFlowSpec(
            max_steps=2,                  # goal decomposition + final answer
            max_tokens=4000,
            max_seconds=15.0,
            plan_strategy="single_step",
            replan_on_failure=False,
            replan_on_weak_progress=False,
            verify_after_each_step=False,  # skip verification for speed
            synthesize_final_answer=True,
            synthesis_max_tokens=400,
        ),
        llm_routing=LLMRoutingSpec(
            routing_strategy="fastest",
            temperature=0.5,              # more creative for quick answers
        ),
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_PRESETS: dict[str, HarnessConfig] = {}


def _ensure_presets():
    global _PRESETS
    if not _PRESETS:
        _PRESETS = {
            "research": research_default(),
            "browser": browser_default(),
            "coding": coding_default(),
            "privacy_standard": privacy_standard_default(),
            "privacy_maximum": privacy_maximum_default(),
            "quick_answer": quick_answer_default(),
        }


def get_preset(name: str) -> HarnessConfig:
    """Return a preset config by name. Clone it so callers can mutate safely."""
    _ensure_presets()
    if name not in _PRESETS:
        available = sorted(_PRESETS.keys())
        raise KeyError(f"Unknown preset {name!r}. Available: {available}")
    return _PRESETS[name].clone()


def list_presets() -> dict[str, str]:
    """Return {preset_name: description} for all available presets."""
    _ensure_presets()
    return {name: cfg.description for name, cfg in _PRESETS.items()}


def build_config(
    *,
    presets: list[str] | None = None,
    tools: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
    max_steps: int | None = None,
    temperature: float | None = None,
    tags: list[str] | None = None,
) -> HarnessConfig:
    """Build a custom HarnessConfig by layering presets + overrides.

    Presets are applied left-to-right (last wins for overlapping fields).
    Explicit keyword overrides are applied after all presets.

    Example:
        cfg = build_config(
            presets=["research", "privacy_maximum"],
            max_steps=5,
            temperature=0.1,
        )
    """
    # Start with the first preset or a blank config
    base: HarnessConfig | None = None
    for name in (presets or ["research"]):
        if base is None:
            base = get_preset(name)
        else:
            # Merge: later preset's non-default fields override earlier
            overlay = get_preset(name)
            base.prompts = overlay.prompts
            base.tool_registry_names = overlay.tool_registry_names
            base.memory_policy = overlay.memory_policy
            base.control_flow = overlay.control_flow
            base.llm_routing = overlay.llm_routing
            base.tags = list(set(base.tags) | set(overlay.tags))

    cfg = base or get_preset("research")

    # Apply explicit overrides
    if tools is not None:
        cfg.tool_registry_names = list(tools)
    if provider is not None:
        cfg.llm_routing.provider = provider
    if model is not None:
        cfg.llm_routing.model = model
    if max_steps is not None:
        cfg.control_flow.max_steps = max_steps
    if temperature is not None:
        cfg.llm_routing.temperature = temperature
    if tags is not None:
        cfg.tags = list(tags)

    cfg.__post_init__()  # recompute hash
    return cfg
