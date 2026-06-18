"""Research, search, scrape, and sandbox execution endpoints."""
import asyncio
import hashlib
import json
import logging
import re
import time
from typing import Optional

import httpx
import xml.etree.ElementTree as ET
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, validator

from backend.core.audit import get_audit_logger, ActionCategory
from backend.core.database import get_db_cursor
from backend.core.security import is_safe_url

log = logging.getLogger("jambu.research")
from backend.core.privacy import sanitize_content_for_storage
from backend.core.sandbox import execute_sandboxed
from backend.core.database import get_db_cursor
from backend.engine_runtime import (
    LATEST_LLM_CONFIG, CLOUD_PROVIDERS, active_tasks, _task_token_counts,
    _task_token_starts, cancel_flags,
    manager, _call_llm, _resolve_llm_config, _new_task_id, safe_task,
    broadcast_agent_state, broadcast_agent_telemetry,
    broadcast_task_start, broadcast_task_end, is_cancelled,
)

router = APIRouter(tags=["research"])

last_activity_local = time.time()


# ── Pydantic Models ──


class ResearchRequest(BaseModel):
    query: str
    client_id: str = "default"
    brain_only: bool = False
    domain: str = "general"  # general, academic, coding
    top_n: int = 8
    use_agent: bool = False
    llm_provider: Optional[str] = None
    llm_config: Optional[dict] = None

    @validator("query")
    def validate_query(cls, v):
        if not v or not v.strip():
            raise ValueError("query must not be empty")
        if len(v) > 10000:
            raise ValueError("query exceeds 10000 character limit")
        return v

    @validator("top_n")
    def validate_top_n(cls, v):
        if v < 1:
            raise ValueError("top_n must be at least 1")
        if v > 50:
            raise ValueError("top_n cannot exceed 50")
        return v

    @validator("domain")
    def validate_domain(cls, v):
        allowed = {"general", "academic", "coding"}
        if v not in allowed:
            raise ValueError(f"domain must be one of {allowed}")
        return v


class SearchRequest(BaseModel):
    q: str = ""
    engines: str = "google,bing,duckduckgo"
    format: str = "json"


class ScrapeRequest(BaseModel):
    url: str
    query: str = ""
    client_id: str = "default"

    @validator("url")
    def validate_url(cls, v):
        if not is_safe_url(v):
            raise ValueError("Invalid or blocked URL")
        return v


class ExecRequest(BaseModel):
    code: str
    timeout: int = 30
    client_id: str = "default"

    @validator("timeout")
    def validate_timeout(cls, v):
        if v < 1:
            raise ValueError("timeout must be at least 1 second")
        if v > 120:
            raise ValueError("timeout cannot exceed 120 seconds")
        return v

    @validator("code")
    def validate_code_length(cls, v):
        if len(v) > 50000:
            raise ValueError("code exceeds 50000 character limit")
        return v


class InterruptRequest(BaseModel):
    new_instruction: str = ""
    client_id: str = "default"


async def _brain_only_research(query: str) -> dict:
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
        from backend.core.vector_search import search_similar, is_sqlite_vec_available

        model = SentenceTransformer("all-MiniLM-L6-v2")
        query_vec = model.encode(query).astype(np.float32).tobytes()
        rows = search_similar(query_vec, k=15)

        scored = sorted(
            [(sum(1 for w in set(query.lower().split()) if w in r[0].lower()), r[0], r[1])
             for r in rows], reverse=True,
        )[:8]

        context_text = "\n\n".join([f"Source: {r[2]}\n{r[1]}" for r in scored])
        sources_list = list(set([r[2] for r in scored]))
        answer = context_text[:500] if context_text else "No results found in knowledge vault."

        if context_text and scored:
            try:
                answer_text, usage = await _call_llm(
                    prompt=f"Based on this research context, provide a concise answer to: '{query}'\n\nContext:\n{context_text[:3000]}",
                    max_tokens=500,
                    temperature=0.3,
                    timeout=30.0,
                )
                if answer_text:
                    answer = answer_text
                    for cid, tid in list(active_tasks.items()):
                        if tid:
                            completion = usage.get("completion_tokens", 0) or len(answer_text.split())
                            _task_token_counts[tid] = _task_token_counts.get(tid, 0) + completion
            except Exception as e:
                log.warning("[brain_only] LLM synthesis failed: %r", e)

        return {
            "answer": answer,
            "context": context_text,
            "sources": sources_list,
            "doc_count": 0,
        }
    except ImportError:
        return {"answer": "", "context": "", "sources": [], "doc_count": 0}


async def _expand_query(query: str, client_id: str, llm_config: dict) -> list:
    cfg = _resolve_llm_config(llm_config)
    base_url = cfg.get("baseUrl", "http://localhost:11434/v1")
    model_id = cfg.get("modelId", "gemma4:12b-it-qat")
    api_key = cfg.get("apiKey", "")
    provider = cfg.get("provider", "ollama")

    prompt = f"Diverse search queries for: '{query}'. Return exactly 3 lines, one query per line."
    async with httpx.AsyncClient() as client:
        try:
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            if provider == "ollama":
                url = f"{base_url.removesuffix('/v1')}/api/generate"
                payload = {"model": model_id, "prompt": prompt, "stream": False}
            else:
                url = f"{base_url}/chat/completions"
                payload = {"model": model_id, "messages": [{"role": "user", "content": prompt}]}

            resp = await client.post(url, json=payload, headers=headers, timeout=10.0)
            if resp.status_code != 200:
                return [query]

            data = resp.json()
            if provider == "ollama":
                content = data.get("response", "")
            else:
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

            return [line.strip() for line in content.strip().split("\n") if line.strip()][:3]
        except Exception:
            return [query]


async def _assess_url_risk(url: str, client_id: str, llm_config: dict) -> bool:
    base_url = llm_config.get("baseUrl", "http://localhost:8080/v1")
    model_id = llm_config.get("modelId", "gemma-4-12b")
    api_key = llm_config.get("apiKey", "")

    prompt = f"Analyze this URL for security risks: '{url}'. Respond 'SAFE' or 'RISKY' with reason."
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model_id, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
                timeout=10.0,
            )
            analysis = resp.json()["choices"][0]["message"]["content"]
            return "RISKY" in analysis.upper()
        except Exception:
            return False


async def _fetch_arxiv(query: str) -> list:
    # arXiv redirects http->https; use https directly to avoid the
    # 301 round-trip and a transient empty response on the first call.
    url = f"https://export.arxiv.org/api/query?search_query=all:{query}&max_results=3"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=15.0)
        root = ET.fromstring(resp.text)
        ns = "{http://www.w3.org/2005/Atom}"
        return [
            {
                "url": e.find(f"{ns}id").text,
                "markdown": e.find(f"{ns}summary").text or "",
            }
            for e in root.findall(f"{ns}entry")
        ]


async def _fetch_github(query: str) -> list:
    url = f"https://api.github.com/search/repositories?q={query}&per_page=3"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers={"Accept": "application/vnd.github.v3+json"}, timeout=15.0)
        return [
            {"url": i["html_url"], "markdown": i.get("description", "")}
            for i in resp.json().get("items", [])
        ]


# ── Interrupt ──


@router.post("/interrupt/{task_id}")
async def interrupt_task(task_id: str, req: InterruptRequest):
    """Cancel the active task and optionally inject a new instruction."""
    from backend.engine_runtime import cancel_flags
    flag = cancel_flags.get(task_id)
    if flag:
        flag.set()
    await broadcast_task_end(req.client_id, task_id, status="interrupted")

    new_id = _new_task_id()
    new_query = req.new_instruction.strip() if req.new_instruction else ""
    if not new_query:
        return {"ok": True, "interrupted": task_id, "new_task_id": None}

    await broadcast_task_start(req.client_id, new_query, new_id)
    safe_task(_run_followup(req.client_id, new_query, new_id), "run_followup")
    return {"ok": True, "interrupted": task_id, "new_task_id": new_id}


async def _run_followup(client_id: str, query: str, task_id: str) -> None:
    try:
        await broadcast_agent_state(client_id, "thinking")
        await broadcast_agent_telemetry(client_id, action=f"New instruction: {query[:80]}")
        if is_cancelled(task_id):
            await broadcast_task_end(client_id, task_id, status="cancelled")
            return
        await _brain_only_research(query)
        if is_cancelled(task_id):
            await broadcast_task_end(client_id, task_id, status="cancelled")
            return
        await broadcast_task_end(client_id, task_id, status="completed", result_preview=query)
    except Exception as e:
        await broadcast_task_end(client_id, task_id, status="failed", result_preview=str(e))
    finally:
        await broadcast_agent_state(client_id, "idle")


# ── Research ──


@router.post("/research")
async def research(req: ResearchRequest):
    """Primary autonomous research endpoint with swarm, scrape, and RAG."""
    cid = req.client_id
    global last_activity_local
    last_activity_local = time.time()

    task_id = _new_task_id()
    await broadcast_task_start(cid, req.query, task_id)

    # V2 Agent Delegation
    if req.use_agent and not req.brain_only:
        try:
            from backend.agent import Agent
            from backend.memory import get_memory, retrieve_relevant, format_context
            mem = get_memory()
            hits = retrieve_relevant(req.query, user_id=cid, k=5)
            context_str = format_context(hits) if hits else ""
            profile = mem.get_profile(cid)
            if profile.work_context or profile.interests:
                context_str += (("\n\n" if context_str else "") +
                                f"User: {', '.join(profile.interests) or '(no interests)'}. {profile.work_context}")
            agent = Agent(max_steps=8, max_tokens=20000, max_seconds=90)
            result = await agent.run_to_completion(req.query, user_id=cid, context=context_str)
            await broadcast_task_end(cid, task_id, status="completed", result_preview=result.answer[:200])
            return {
                "answer": result.answer,
                "context": context_str,
                "sources": result.sources,
                "doc_count": len(result.sources),
                "agent_run": {
                    "run_id": result.run_id,
                    "steps": result.steps_executed,
                    "duration_ms": result.duration_ms,
                    "tokens": result.total_usage.total_tokens,
                    "cost_usd": result.total_usage.cost_usd,
                    "plan": result.plan.to_dict(),
                },
            }
        except Exception as e:
            log.warning("[research] agent delegation failed, falling back: %s", e)

    try:
        if req.llm_provider and req.llm_provider != "ollama":
            preset = CLOUD_PROVIDERS.get(req.llm_provider, {})
            global LATEST_LLM_CONFIG
            LATEST_LLM_CONFIG = {**LATEST_LLM_CONFIG, "provider": req.llm_provider, **preset}
        else:
            LATEST_LLM_CONFIG = {**LATEST_LLM_CONFIG, **(req.llm_config or {})}

        if is_cancelled(task_id):
            await broadcast_task_end(cid, task_id, status="cancelled")
            return {"answer": "[INTERRUPTED]", "context": "", "sources": [], "doc_count": 0}

        await broadcast_agent_state(cid, "thinking")
        await broadcast_agent_telemetry(cid, action="Planning research approach")

        if req.brain_only:
            await broadcast_agent_state(cid, "reading", zone="cabinet")
            await broadcast_agent_telemetry(cid, action="Searching local knowledge vault")
            result = await _brain_only_research(req.query)
            if is_cancelled(task_id):
                await broadcast_task_end(cid, task_id, status="cancelled")
                return {"answer": "[INTERRUPTED]", "context": "", "sources": [], "doc_count": 0}
            await broadcast_task_end(cid, task_id, status="completed", result_preview=result.get("answer"))
            return result

        await broadcast_agent_state(cid, "searching", zone="pile")

        # Expand search queries
        expanded = await _expand_query(req.query, cid, req.llm_config)

        # Multi-engine search with fallback
        all_res = []
        if req.domain == "academic":
            arxiv_data = await _fetch_arxiv(req.query)
            for item in arxiv_data:
                all_res.append({"url": item["url"], "content": item["markdown"], "score": 100})
        elif req.domain == "coding":
            github_data = await _fetch_github(req.query)
            for item in github_data:
                all_res.append({"url": item["url"], "content": item["markdown"], "score": 100})
        else:
            from backend.modules.search import multi_engine_search

            for q in expanded:
                try:
                    results = await multi_engine_search(q)
                    for r in results:
                        all_res.append({
                            "url": r.get("url", ""),
                            "content": r.get("content", ""),
                            "score": r.get("score", 0),
                        })
                except Exception as e:
                    log.warning("[search] error for query '%s': %r", q, e)
                    continue

        # Deduplicate and rank
        seen = set()
        unique = []
        for r in all_res:
            url = r.get("url")
            if not url or url in seen:
                continue
            unique.append(r)
            seen.add(url)

        trusted = [".gov", ".edu", ".org", "wikipedia.org", "reuters.com"]
        unique.sort(
            key=lambda x: (sum(5 for t in trusted if t in x.get("url", "").lower()), x.get("score", 0)),
            reverse=True,
        )
        search_results = unique[:req.top_n]

        if is_cancelled(task_id):
            await broadcast_task_end(cid, task_id, status="cancelled")
            return {"answer": "[INTERRUPTED]", "context": "", "sources": [], "doc_count": 0}

        if not search_results:
            await broadcast_agent_state(cid, "reading", zone="cabinet")
            await broadcast_agent_telemetry(cid, action="No web results — falling back to local knowledge vault")
            brain_result = await _brain_only_research(req.query)
            await broadcast_task_end(cid, task_id, status="completed", result_preview=brain_result.get("answer"))
            return {
                "answer": brain_result.get("answer", "No results found. Try brain_only mode or start SearXNG."),
                "context": brain_result.get("context", ""),
                "sources": brain_result.get("sources", []),
                "doc_count": brain_result.get("doc_count", 0),
            }

        await broadcast_agent_state(cid, "reading", zone="cabinet")
        await broadcast_agent_telemetry(
            cid,
            action=f"Reading {len(search_results)} web sources",
            file_path=search_results[0].get("url") if search_results else None,
        )

        for r in search_results:
            if is_cancelled(task_id):
                await broadcast_task_end(cid, task_id, status="cancelled")
                return {"answer": "[INTERRUPTED]", "context": "", "sources": [], "doc_count": 0}
            await broadcast_agent_telemetry(cid, action="Reading source", file_path=r.get("url"))

        # Security screening
        safe_urls = []
        for r in search_results:
            is_risky = await _assess_url_risk(r["url"], cid, req.llm_config)
            if not is_risky:
                safe_urls.append(r)

        sources = safe_urls[:req.top_n]

        # Scrape each source
        context_parts = []
        for src in sources:
            if is_cancelled(task_id):
                await broadcast_task_end(cid, task_id, status="cancelled")
                return {"answer": "[INTERRUPTED]", "context": "", "sources": [], "doc_count": 0}
            try:
                await broadcast_agent_telemetry(cid, action="Scraping source", file_path=src.get("url"))
                scrape_result = await _scrape_source(src["url"])
                if scrape_result:
                    context_parts.append(scrape_result)
            except Exception as e:
                log.warning("[scrape] error for %s: %r", src["url"], e)

        context_text = "\n\n".join(context_parts)

        # Save scraped content to DB
        for src in sources:
            try:
                with get_db_cursor() as cursor:
                    cursor.execute(
                        "INSERT OR IGNORE INTO documents (text, url) VALUES (?, ?)",
                        (src.get("content", "")[:50000], src.get("url", "")),
                    )
            except Exception:
                pass

        # LLM synthesis
        await broadcast_agent_state(cid, "reasoning", zone="reason")
        await broadcast_agent_telemetry(cid, action="Synthesizing research findings")

        synthesis_prompt = (
            f"Research topic: {req.query}\n\n"
            f"Sources analyzed ({len(sources)}):\n" + "\n".join(f"- {s['url']}" for s in sources) + "\n\n"
            f"Scraped content:\n{context_text[:8000]}\n\n"
            "Provide a comprehensive, well-structured answer."
        )

        answer, usage = await _call_llm(
            prompt=synthesis_prompt,
            max_tokens=2000,
            temperature=0.3,
            timeout=60.0,
        )

        if is_cancelled(task_id):
            await broadcast_task_end(cid, task_id, status="cancelled")
            return {"answer": "[INTERRUPTED]", "context": "", "sources": [], "doc_count": 0}

        completion = usage.get("completion_tokens", 0) or len(answer.split())
        _task_token_counts[task_id] = _task_token_counts.get(task_id, 0) + completion

        await broadcast_task_end(cid, task_id, status="completed", result_preview=answer[:200])
        return {
            "answer": answer,
            "context": context_text,
            "sources": [s["url"] for s in sources],
            "doc_count": len(sources),
            "usage": usage,
        }
    except Exception as e:
        await broadcast_task_end(cid, task_id, status="failed", result_preview=str(e))
        raise HTTPException(status_code=500, detail=str(e))


async def _scrape_source(url: str) -> Optional[str]:
    """Scrape a single URL for research context."""
    try:
        from backend.modules.scraper import scrape_url
        return await scrape_url(url, "")
    except Exception:
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as cl:
                resp = await cl.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code == 200:
                    text = resp.text[:10000]
                    return re.sub(r"<[^>]+>", "", text)[:5000]
        except Exception:
            return None


# ── Search ──


@router.get("/search")
async def search(q: str, engines: str = "google,bing,duckduckgo", format: str = "json"):
    """Raw multi-engine metasearch without scraping."""
    try:
        from backend.modules.search import multi_engine_search
        results = await multi_engine_search(q, engines)
        return {"results": results, "query": q}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Scrape ──


@router.post("/scrape")
async def scrape(req: ScrapeRequest):
    """Single-page scraping endpoint with audit logging."""
    audit = get_audit_logger()

    audit.log(
        category=ActionCategory.BROWSER,
        action="scrape",
        details={"url": req.url, "query": req.query},
        session_id=req.client_id,
    )

    # Try crawl4ai first, fallback to Playwright
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
        from crawl4ai.content_filter_strategy import BM25ContentFilter

        markdown_strategy = DefaultMarkdownGenerator(
            content_filter=BM25ContentFilter(user_query=req.query or "content", bm25_threshold=0.3),
            options={"ignore_links": True, "ignore_images": True, "strip_comments": True},
        )
        browser_config = BrowserConfig(headless=True)
        run_config = CrawlerRunConfig(
            markdown_generator=markdown_strategy,
            wait_until="networkidle",
            magic=True,
            screenshot=True,
        )

        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await crawler.arun(url=req.url, config=run_config)

        if result.success:
            content = result.markdown[:50000]
            sanitized_content, sanitization_result = sanitize_content_for_storage(content)
            audit.log(
                category=ActionCategory.BROWSER,
                action="scrape_success",
                details={
                    "url": req.url,
                    "content_length": len(sanitized_content),
                    "pii_removed": len(sanitization_result.pii_removed),
                    "engine": "crawl4ai",
                },
                session_id=req.client_id,
            )
            return {
                "success": True,
                "url": req.url,
                "markdown": sanitized_content,
                "title": result.metadata.get("title", "") if result.metadata else "",
            }
        return {"success": False, "url": req.url, "error": "Failed to scrape page"}
    except ImportError:
        pass
    except Exception as e:
        log.error("crawl4ai error: %s", e)

    # Playwright fallback
    try:
        from backend.modules.playwright_scraper import scrape_with_playwright

        result = await scrape_with_playwright(req.url)

        if result["success"]:
            content = result["content"]
            sanitized_content, sanitization_result = sanitize_content_for_storage(content)
            audit.log(
                category=ActionCategory.BROWSER,
                action="scrape_success",
                details={
                    "url": req.url,
                    "content_length": len(sanitized_content),
                    "pii_removed": len(sanitization_result.pii_removed),
                    "engine": "playwright",
                },
                session_id=req.client_id,
            )
            return {
                "success": True,
                "url": req.url,
                "markdown": sanitized_content,
                "title": result.get("title", ""),
            }
        return {"success": False, "url": req.url, "error": result.get("error", "Failed to scrape page")}
    except Exception as e:
        audit.log(
            category=ActionCategory.ERROR,
            action="scrape_error",
            details={"url": req.url, "error": str(e)},
            session_id=req.client_id,
        )
        raise HTTPException(status_code=500, detail=str(e))


# ── Sandbox Execution ──


@router.post("/exec")
async def execute_code(req: ExecRequest):
    """Execute Python code in a sandboxed environment."""
    if not req.code or not req.code.strip():
        return {"success": False, "output": "", "error": "Empty code - nothing to execute",
                "execution_time": 0, "exit_code": -1, "sandbox_type": "subprocess"}
    try:
        result = await execute_sandboxed(req.code, req.timeout)
        await manager.broadcast(req.client_id, f"⚡ Sandbox ({result['sandbox_type']}): Execution completed in {result['execution_time']}s")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
