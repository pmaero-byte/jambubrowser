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
        from backend.modules.risk_shield import get_risk_shield
        shield = get_risk_shield()
        score = shield.check(url)
        return {
            "url": url,
            "risk_score": getattr(score, "score", None) or (score.get("score") if isinstance(score, dict) else 0.0),
            "blocked": getattr(score, "blocked", None) or (score.get("blocked") if isinstance(score, dict) else False),
            "sources": getattr(score, "sources", None) or (score.get("sources") if isinstance(score, dict) else []),
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
    return r
