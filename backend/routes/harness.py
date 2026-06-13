"""Harness bridge endpoints."""
from fastapi import APIRouter, HTTPException

from backend.core.security import is_safe_url

router = APIRouter(tags=["harness"])


@router.get("/harness/status")
async def harness_status():
    """Check Harness gateway availability and connector list."""
    from backend.modules.harness_bridge import get_harness_bridge
    return await get_harness_bridge().get_status()


@router.post("/harness/research")
async def harness_research(query: str, use_swarm: bool = True, domain: str = "general"):
    """Delegate research to Harness multi-agent swarm."""
    from backend.modules.harness_bridge import get_harness_bridge
    return await get_harness_bridge().research(query, use_swarm=use_swarm, domain=domain)


@router.post("/harness/research/single")
async def harness_research_single(query: str, connector: str = "hermes"):
    """Delegate to a single Harness connector."""
    from backend.modules.harness_bridge import get_harness_bridge
    return await get_harness_bridge().research_single(query, connector)


@router.post("/harness/browse")
async def harness_browse(url: str, action: str = "scrape",
                         selector: str = "", value: str = ""):
    """Use Harness Playwright MCP for browser automation."""
    if not is_safe_url(url):
        raise HTTPException(status_code=400, detail="Invalid or blocked URL")
    from backend.modules.harness_bridge import get_harness_bridge
    return await get_harness_bridge().browse(url, action, selector, value)


@router.post("/harness/llm")
async def harness_llm(prompt: str, model: str = "gemma4:12b",
                       temperature: float = 0.7):
    """Send LLM request through Harness bridge (local + cloud models)."""
    from backend.modules.harness_bridge import get_harness_bridge
    return await get_harness_bridge().llm_chat(prompt, model, temperature=temperature)


@router.post("/harness/context/store")
async def harness_store_context(key: str, value: str, tags: str = ""):
    """Store context in Harness shared memory."""
    from backend.modules.harness_bridge import get_harness_bridge
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    return await get_harness_bridge().store_context(key, value, tag_list)


@router.post("/harness/context/search")
async def harness_search_context(query: str):
    """Search Harness shared memory for relevant context."""
    from backend.modules.harness_bridge import get_harness_bridge
    return await get_harness_bridge().search_context(query)
