"""
Built-in tools that wrap existing Jambubrowser capabilities. The agent loop
uses these to actually do work — search, scrape, vault lookup, etc.

Each tool is a thin async function with a typed signature. The tool registry
auto-derives the JSON schema from the signature.
"""

from __future__ import annotations

import json
import logging
from typing import Optional, Annotated

from .tools import get_registry, ToolRegistry, RiskLevel
from .events import emit_event

log = logging.getLogger("jambu.agent.builtin")

_browser = None
_page = None


async def _get_browser():
    global _browser
    if _browser is None:
        try:
            from playwright.async_api import async_playwright
            pw = await async_playwright().start()
            _browser = await pw.chromium.launch(headless=True)
        except Exception:
            return None
    return _browser


async def _get_page():
    global _page, _browser
    if _page is None or _page.is_closed():
        b = await _get_browser()
        if b is None:
            return None
        ctx = await b.new_context()
        _page = await ctx.new_page()
    return _page


async def _teardown_browser():
    global _browser, _page
    _page = None
    if _browser:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

async def web_search(
    query: Annotated[str, "The search query to run against the multi-engine metasearch"],
    top_k: Annotated[int, "Maximum number of results to return"] = 10,
    engines: Annotated[Optional[list[str]], "Search engines to use (SearXNG, DDG, Google)"] = None,
) -> dict:
    """Search the web via SearXNG → DuckDuckGo → Google fallback chain."""
    try:
        from backend.modules.search import multi_engine_search
        results = await multi_engine_search(query, engines=engines)
        return {
            "query": query,
            "results": [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", "")[:300],
                    "engine": r.get("engine", ""),
                }
                for r in results[:top_k]
            ],
            "count": len(results),
        }
    except Exception as e:
        return {"query": query, "error": str(e), "results": []}


async def scrape_url(
    url: Annotated[str, "URL to scrape"],
    format: Annotated[str, "Output format: 'markdown' or 'text'"] = "markdown",
) -> dict:
    """Scrape a single URL and return the cleaned text/markdown content."""
    try:
        from backend.modules.scraper import scrape_url as do_scrape
        content = await do_scrape(url, format=format)
        return {
            "url": url,
            "content": content[:5000] if content else "",
            "truncated": len(content) > 5000 if content else False,
            "length": len(content) if content else 0,
        }
    except Exception as e:
        return {"url": url, "error": str(e)}


async def vault_get(
    domain: Annotated[str, "The domain to look up credentials for (e.g., 'github.com')"],
) -> dict:
    """Look up a credential from the encrypted vault. Returns locked if vault is locked."""
    try:
        from backend.core.vault import get_vault
        v = get_vault()
        if v.is_locked:
            return {"domain": domain, "locked": True, "error": "Vault is locked. User must unlock first."}
        creds = v.list_domains() if hasattr(v, "list_domains") else []
        for c in creds:
            if domain in (c.get("domain", "") if isinstance(c, dict) else str(c)):
                return {"domain": domain, "found": True, "credential": c}
        return {"domain": domain, "found": False, "available_domains": creds}
    except Exception as e:
        return {"domain": domain, "error": str(e)}


async def knowledge_query(
    entity: Annotated[str, "Entity name to look up in the knowledge graph"],
    limit: Annotated[int, "Max relations to return"] = 10,
) -> dict:
    """Query the knowledge graph for an entity and its relations."""
    try:
        from backend.modules.knowledge_graph import get_knowledge_graph
        kg = get_knowledge_graph()
        results = kg.search_entities(entity, limit=limit)
        return {"entity": entity, "results": results, "count": len(results)}
    except Exception as e:
        return {"entity": entity, "error": str(e)}


async def memory_recall(
    query: Annotated[str, "What to recall from memory"],
    user_id: Annotated[str, "User scope"] = "default",
    k: Annotated[int, "Number of memories to recall"] = 5,
) -> dict:
    """Recall relevant memories for this user and query."""
    try:
        from backend.memory import retrieve_relevant
        hits = retrieve_relevant(query, user_id=user_id, k=k)
        return {
            "query": query,
            "user_id": user_id,
            "hits": [
                {
                    "id": h.memory.id,
                    "content": h.memory.content,
                    "category": h.memory.category,
                    "importance": h.memory.importance,
                    "score": h.score,
                    "matched_by": h.matched_by,
                }
                for h in hits
            ],
        }
    except Exception as e:
        return {"query": query, "error": str(e)}


async def memory_store(
    content: Annotated[str, "The fact/learning to remember"],
    category: Annotated[str, "Category: fact, preference, context, learning, goal, skill"] = "fact",
    importance: Annotated[float, "Importance 0.0-1.0"] = 0.5,
    user_id: Annotated[str, "User scope"] = "default",
) -> dict:
    """Store a new memory entry for the user."""
    try:
        from backend.memory import get_memory
        mem = get_memory()
        mid = mem.store_semantic(user_id, content, category=category, importance=importance)
        return {"id": mid, "stored": True, "content": content[:200]}
    except Exception as e:
        return {"error": str(e)}


async def code_exec(
    code: Annotated[str, "Python code to execute in a sandboxed subprocess"],
    timeout_seconds: Annotated[int, "Max execution time"] = 10,
) -> dict:
    """Execute Python code in a sandboxed subprocess. Limited to read-only operations."""
    try:
        from backend.core.sandbox import execute_sandboxed
        result = execute_sandboxed(code, timeout=timeout_seconds)
        return {"result": str(result)[:5000], "truncated": len(str(result)) > 5000}
    except Exception as e:
        return {"error": str(e)}


async def goal_set(
    goal_text: Annotated[str, "The high-level goal to pursue"],
    criteria: Annotated[Optional[list[str]], "Acceptance criteria for the goal"] = None,
) -> dict:
    """Set a long-running goal for the agent to pursue across sessions."""
    try:
        from backend.modules.goal_orchestrator import get_goal_orchestrator
        orch = get_goal_orchestrator()
        goal = orch.set_goal(goal_text, criteria or [])
        return {"goal_id": goal.get("id"), "goal_text": goal_text, "criteria": criteria}
    except Exception as e:
        return {"error": str(e)}


async def risk_check(
    url: Annotated[str, "URL to check against the risk shield"],
) -> dict:
    """Check a URL's risk score against URLhaus, PhishTank, and heuristic rules."""
    try:
        from backend.modules.risk_shield import get_shield
        shield = get_shield()
        result = await shield.assess_url(url)
        return {
            "url": url,
            "risk_score": result.get("consensus_score", 0.0),
            "risk_level": result.get("risk_level"),
            "blocked": result.get("blocked", False),
            "reason": result.get("reason", ""),
            "sources": [c.get("source") for c in result.get("checks", [])],
        }
    except Exception as e:
        return {"url": url, "error": str(e)}


async def final_answer(
    text: Annotated[str, "The final answer to present to the user"],
    sources: Annotated[Optional[list[str]], "URLs that informed this answer"] = None,
) -> dict:
    """Signal that the agent has produced its final answer. Stops the loop."""
    return {
        "text": text,
        "sources": sources or [],
        "is_final": True,
    }


async def browser_navigate(
    url: Annotated[str, "URL to navigate the browser to"],
) -> dict:
    """Navigate the browser to a URL using Playwright. Returns page title and visible text."""
    try:
        page = await _get_page()
        if page is None:
            return {"url": url, "error": "Playwright not available. Install: pip install playwright && python -m playwright install chromium"}
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        title = await page.title()
        text = await page.inner_text("body")
        return {
            "url": page.url,
            "title": title,
            "content": text[:8000] if text else "",
            "truncated": len(text) > 8000 if text else False,
        }
    except Exception as e:
        await _teardown_browser()
        return {"url": url, "error": str(e)}


async def browser_click(
    selector: Annotated[str, "CSS selector or text to click (e.g., 'button.submit' or 'text=Login')"],
) -> dict:
    """Click an element in the current browser page. Returns updated page content."""
    try:
        page = await _get_page()
        if page is None:
            return {"selector": selector, "error": "Playwright not available"}
        if selector.startswith("text="):
            await page.get_by_text(selector[5:]).first.click(timeout=5000)
        else:
            await page.click(selector, timeout=5000)
        await page.wait_for_load_state("domcontentloaded", timeout=10000)
        title = await page.title()
        text = await page.inner_text("body")
        return {
            "clicked": selector,
            "url": page.url,
            "title": title,
            "content": text[:5000] if text else "",
            "truncated": len(text) > 5000 if text else False,
        }
    except Exception as e:
        return {"selector": selector, "error": str(e)}


async def browser_extract(
    selector: Annotated[Optional[str], "Optional CSS selector. If omitted, extracts full page text."] = None,
) -> dict:
    """Extract text content from the current browser page. Use after navigate/click."""
    try:
        page = await _get_page()
        if page is None:
            return {"error": "Playwright not available"}
        if selector:
            elements = await page.query_selector_all(selector)
            texts = []
            for el in elements:
                t = await el.inner_text()
                if t:
                    texts.append(t)
            return {
                "url": page.url,
                "selector": selector,
                "matches": len(texts),
                "content": "\n---\n".join(texts)[:8000],
            }
        text = await page.inner_text("body")
        return {
            "url": page.url,
            "content": text[:8000] if text else "",
            "truncated": len(text) > 8000 if text else False,
        }
    except Exception as e:
        return {"error": str(e)}


async def browser_fill(
    selector: Annotated[str, "CSS selector for the input field"],
    value: Annotated[str, "Text to type into the field"],
) -> dict:
    """Type text into a form field on the current browser page."""
    try:
        page = await _get_page()
        if page is None:
            return {"selector": selector, "error": "Playwright not available"}
        await page.fill(selector, value, timeout=10000)
        return {
            "selector": selector,
            "filled": True,
            "url": page.url,
        }
    except Exception as e:
        return {"selector": selector, "error": str(e)}


def _compact_flow(report: dict) -> dict:
    """Strip bulky screenshot payloads from a flow report (agent token budget)."""
    steps = []
    for step in report.get("steps") or []:
        step = dict(step)
        if "screenshot_base64" in step:
            step["screenshot"] = "captured"
            step.pop("screenshot_base64", None)
        steps.append(step)
    out = dict(report)
    out["steps"] = steps
    return out


async def browser_test_flow(
    url: Annotated[str, "Starting URL, e.g. http://localhost:3000"],
    steps: Annotated[str, "JSON array of step objects (navigate/click/type/press/wait/assert_*)"],
    local: Annotated[bool, "Allow localhost/private hosts for local dev testing"] = True,
    approve: Annotated[bool, "Approve risky/input actions (delete/pay/send) for every step"] = False,
    stop_on_failure: Annotated[bool, "Stop at the first failed step"] = False,
    network: Annotated[str, "JSON network policy: mocks/fail/delay/offline"] = "",
    trace: Annotated[bool, "Capture a Playwright trace artifact"] = False,
    har: Annotated[bool, "Capture a HAR network archive"] = False,
    video: Annotated[bool, "Capture a video recording"] = False,
    resolve_sources: Annotated[bool, "Map console errors through source maps"] = False,
    forbid_evaluate: Annotated[bool, "Refuse JS-dependent evaluate steps"] = False,
) -> dict:
    """Test a web app end-to-end in ONE call.

    Runs a declarative flow (navigate / click / type / press / wait / assert_*)
    against a URL and returns a compact pass/fail report with console errors and
    failed requests already attached. Supports request mocking (``network``) and
    debugging artifacts (``trace``/``har``/``video``). Prefer this over repeated
    navigate/click/extract calls to conserve steps and tokens.
    """
    try:
        parsed = json.loads(steps) if isinstance(steps, str) else steps
        net = json.loads(network) if network else None
    except json.JSONDecodeError as e:
        return {"error": f"steps/network is not valid JSON: {e}"}
    try:
        from backend.modules.browser_agent import get_browser_agent_service
        report = await get_browser_agent_service().run_test(
            url=url, steps=parsed, local=local, approve=approve,
            stop_on_failure=stop_on_failure, network=net,
            trace=trace, har=har, video=video, resolve_sources=resolve_sources,
            forbid_evaluate=forbid_evaluate,
        )
    except Exception as e:
        return {"url": url, "error": str(e)}
    return _compact_flow(report)


async def browser_test_plan(
    url: Annotated[str, "App URL, e.g. http://localhost:3000"],
    goal: Annotated[str, "What to test, e.g. 'test login and the dashboard'"],
    kind: Annotated[str, "Force a template: smoke|login|signup|checkout|search|accessibility|performance|responsive"] = "",
    use_llm: Annotated[bool, "Refine the plan with the configured LLM"] = False,
) -> dict:
    """Author a browser test flow from a natural-language goal (does not run it).

    Returns ready-to-run steps for browser_test_flow; fill any placeholder
    values before running.
    """
    try:
        from backend.modules.browser_plan import plan
        return plan(goal, url, kind=kind or None, use_llm=use_llm)
    except Exception as e:
        return {"url": url, "error": str(e)}


async def browser_import_playwright(
    code: Annotated[str, "Playwright Test source text to convert into a flow"],
) -> dict:
    """Convert a Playwright Test source into a declarative flow.

    Translates the common getBy/keyboard/expect subset; unparsed lines are
    reported so the agent knows exactly what needs a hand.
    """
    try:
        from backend.modules.browser_codegen import playwright_to_flow
        return playwright_to_flow(code)
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_builtin_tools(registry: Optional[ToolRegistry] = None) -> ToolRegistry:
    """Register all built-in tools into the registry."""
    r = registry or get_registry()
    r.register(
        "web_search", web_search,
        description="Search the web using the multi-engine metasearch (SearXNG, DuckDuckGo, Google).",
        requires_network=True,
        risk_level=RiskLevel.LOW,
    )
    r.register(
        "scrape_url", scrape_url,
        description="Scrape a single URL and return its content as markdown or text.",
        requires_network=True,
        risk_level=RiskLevel.LOW,
    )
    r.register(
        "vault_get", vault_get,
        description="Look up a credential from the encrypted vault for a given domain.",
        risk_level=RiskLevel.MEDIUM,
    )
    r.register(
        "knowledge_query", knowledge_query,
        description="Query the knowledge graph for an entity and its relations.",
        risk_level=RiskLevel.LOW,
    )
    r.register(
        "memory_recall", memory_recall,
        description="Recall relevant memories for the current user and query.",
        risk_level=RiskLevel.LOW,
    )
    r.register(
        "memory_store", memory_store,
        description="Store a new memory entry (fact, preference, context, learning, etc.) for the user.",
        risk_level=RiskLevel.MEDIUM,
    )
    r.register(
        "code_exec", code_exec,
        description="Execute Python code in a sandboxed subprocess with timeout.",
        risk_level=RiskLevel.HIGH,
    )
    r.register(
        "goal_set", goal_set,
        description="Set a long-running goal for the agent to pursue across sessions.",
        risk_level=RiskLevel.MEDIUM,
    )
    r.register(
        "risk_check", risk_check,
        description="Check a URL's risk score against URLhaus, PhishTank, and heuristic rules.",
        requires_network=True,
        risk_level=RiskLevel.LOW,
    )
    r.register(
        "final_answer", final_answer,
        description="Signal that the agent has produced its final answer. Stops the loop.",
        risk_level=RiskLevel.LOW,
    )
    r.register(
        "browser_navigate", browser_navigate,
        description="Navigate the browser to a URL using Playwright. Returns page title and visible text content.",
        requires_network=True,
        risk_level=RiskLevel.LOW,
    )
    r.register(
        "browser_click", browser_click,
        description="Click an element on the current page (CSS selector or 'text=...' for text matching). Returns updated page content.",
        requires_network=True,
        risk_level=RiskLevel.MEDIUM,
    )
    r.register(
        "browser_extract", browser_extract,
        description="Extract text from the current page. Use an optional CSS selector to target specific elements.",
        risk_level=RiskLevel.LOW,
    )
    r.register(
        "browser_fill", browser_fill,
        description="Type text into a form field (CSS selector) on the current page.",
        requires_network=True,
        risk_level=RiskLevel.MEDIUM,
    )
    r.register(
        "browser_test_flow", browser_test_flow,
        description=(
            "Test a web app end-to-end in ONE call: run a declarative flow "
            "(navigate/click/type/press/wait/assert_*) against a URL and get a "
            "compact pass/fail report with console errors attached. Use "
            "local=true for localhost dev servers."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Starting URL, e.g. http://localhost:3000",
                },
                "steps": {
                    "type": "string",
                    "description": (
                        "JSON array of step objects. Actions: navigate{url}, "
                        "click{target|ref}, type{target|ref,value}, press{key}, "
                        "hover, select{value}, check/uncheck, reload, back, "
                        "forward, wait{selector|text|url_contains}, screenshot, "
                        "assert_visible/assert_not_visible/assert_text{value}/"
                        "assert_text_equals/assert_value/assert_url/assert_title/"
                        "assert_count/assert_checked/assert_console_clean/"
                        "assert_no_failed_requests. 'target' matches element text "
                        "by exact name, unique substring, or \"role name\"."
                    ),
                },
                "local": {
                    "type": "boolean", "default": True,
                    "description": "Allow localhost/private hosts (local dev testing)",
                },
                "approve": {
                    "type": "boolean", "default": False,
                    "description": "Approve risky/input actions for every step",
                },
                "stop_on_failure": {
                    "type": "boolean", "default": False,
                    "description": "Stop at the first failed step",
                },
                "network": {
                    "type": "string",
                    "description": (
                        "Optional JSON network policy: "
                        '{"mocks":[{"url":"**/api/user","json":{...}}],'
                        '"fail":["**/analytics/**"],'
                        '"delay":[{"url":"**/slow","ms":3000}],"offline":false}'
                    ),
                },
                "trace": {"type": "boolean", "default": False,
                          "description": "Capture a Playwright trace artifact"},
                "har": {"type": "boolean", "default": False,
                        "description": "Capture a HAR network archive"},
                "video": {"type": "boolean", "default": False,
                          "description": "Capture a video recording"},
                "resolve_sources": {
                    "type": "boolean", "default": False,
                    "description": "Map console errors through source maps to original files",
                },
                "forbid_evaluate": {
                    "type": "boolean", "default": False,
                    "description": "Refuse JS-dependent evaluate steps",
                },
            },
            "required": ["url", "steps"],
        },
        requires_network=True,
        risk_level=RiskLevel.MEDIUM,
    )
    r.register(
        "browser_test_plan", browser_test_plan,
        description=(
            "Author a browser test flow from a natural-language goal without "
            "running it. Returns steps for browser_test_flow; fill placeholders."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "App URL"},
                "goal": {"type": "string", "description": "What to test"},
                "kind": {
                    "type": "string",
                    "description": "smoke|login|signup|checkout|search|accessibility|performance|responsive",
                },
                "use_llm": {"type": "boolean", "default": False},
            },
            "required": ["url", "goal"],
        },
        risk_level=RiskLevel.LOW,
    )
    r.register(
        "browser_import_playwright", browser_import_playwright,
        description=(
            "Convert a Playwright Test source into a declarative flow for "
            "browser_test_flow. Unparsed lines are reported."
        ),
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Playwright Test source text"},
            },
            "required": ["code"],
        },
        risk_level=RiskLevel.LOW,
    )
    return r
