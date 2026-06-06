"""
Jambubrowser Action Engine v2.0
===============================
Sovereign Autonomous Research Engine.
Connects the UI to the internet, memory, sandboxed execution,
credential vault, and AI reasoning.

Endpoints:
- /health, /stats              : System status
- /research                    : Autonomous swarm research
- /search                      : Raw metasearch
- /scrape                      : Single-page scraping
- /act, /workflow/execute      : Browser automation
- /exec                        : Sandboxed code execution
- /login                       : Credential vault login
- /vision/grounding            : Visual page analysis
- /memory/recall               : Cross-session recall
- /mission, /mission/stop      : Background missions
- /tool/save, /tools, /tool/exec : Agent skill management
- /discover_api, /api/call     : API discovery
- /graph_data                  : 3D brain visualization data
- /peers/discover, /peer/sync  : P2P mesh (stubs)
"""

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Dict, Optional
import uvicorn
import asyncio
import time
import hashlib
import json
import os
import psutil
import re
import httpx
import xml.etree.ElementTree as ET
import importlib.util

# --- Importing Modular Parts ---
from backend.core.database import init_db, get_db, get_db_cursor, get_stats as db_stats, clear_memory
from backend.core.sandbox import execute_sandboxed
from backend.core.vault import get_vault
from backend.modules.search import multi_engine_search, filter_trusted_results
from backend.modules.scraper import get_sovereign_crawler, get_scrape_config, is_special_media, get_special_content

# ---- App Init ----

app = None  # Initialized after lifespan function definition

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8888/search")
GLOBAL_VPN_PROXY = os.environ.get("AGENT_VPN_PROXY", None)

LATEST_LLM_CONFIG = {
    "baseUrl": "http://localhost:11434/v1",
    "modelId": "gemma4:12b",
    "apiKey": "",
}

last_activity = time.time()
active_missions: Dict[str, dict] = {}


# ---- WebSocket Manager ----

class ConnectionManager:
    """Manages WebSocket connections for real-time agent logging."""

    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, client_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[client_id] = websocket

    def disconnect(self, client_id: str):
        self.active_connections.pop(client_id, None)

    async def broadcast(self, client_id: str, message: str):
        if client_id in self.active_connections:
            try:
                await self.active_connections[client_id].send_text(message)
            except Exception:
                pass


manager = ConnectionManager()


# ---- Request Models ----

class ResearchRequest(BaseModel):
    query: str
    top_n: int = 5
    client_id: str = "default"
    persist: bool = False
    stealth: Dict = {}
    domain: str = "general"
    brain_only: bool = False
    tor_routing: bool = False
    incognito: bool = False
    llm_config: Dict = {}


class SearchRequest(BaseModel):
    q: str
    engines: str = "google,bing,duckduckgo"
    format: str = "json"


class ScrapeRequest(BaseModel):
    url: str
    query: str = ""
    client_id: str = "default"


class ExecRequest(BaseModel):
    code: str
    timeout: int = 30
    client_id: str = "default"


class ActionStep(BaseModel):
    action: str
    selector: str = ""
    value: str = ""
    delay: float = 0.5
    x: Optional[float] = None
    y: Optional[float] = None


class MultiActionRequest(BaseModel):
    url: str
    steps: List[ActionStep]
    client_id: str = "default"
    session_id: str = None


class LoginRequest(BaseModel):
    url: str
    username: str
    password: str
    client_id: str = "default"


class MissionRequest(BaseModel):
    query: str
    client_id: str = "default"


class MissionStopRequest(BaseModel):
    mission_id: str
    client_id: str = "default"


class ToolSaveRequest(BaseModel):
    name: str
    description: str
    code: str
    client_id: str = "default"


class ToolExecRequest(BaseModel):
    name: str
    kwargs: Dict = {}
    client_id: str = "default"


class DynamicApiRequest(BaseModel):
    url: str
    method: str = "GET"
    headers: Dict = {}
    payload: Dict = {}
    client_id: str = "default"


# Phase 2: Risk Shield, Mission Scheduler, Shadow Browser

class ShieldRequest(BaseModel):
    url: str
    real_time: bool = True


class ShieldBatchRequest(BaseModel):
    urls: List[str]
    real_time: bool = False


class MissionScheduleRequest(BaseModel):
    query: str
    schedule: str = None
    priority: int = 1
    trigger_conditions: str = None
    client_id: str = "default"


class ShadowInterestRequest(BaseModel):
    name: str
    keywords: List[str]
    seed_urls: List[str] = []
    priority: int = 1


# Phase 3: Vision, Form Filler, Local Connector

class VisionGroundRequest(BaseModel):
    url: str = ""
    image_data: str = ""  # base64 encoded screenshot
    client_id: str = "default"


class FormDetectRequest(BaseModel):
    url: str
    html: str = ""


class ObsidianRequest(BaseModel):
    title: str
    content: str
    folder: str = "Research"
    vault_path: str = None


class ReminderRequest(BaseModel):
    title: str
    notes: str = ""
    due_date: str = ""
    list_name: str = "Jambubrowser"


class LocalNoteRequest(BaseModel):
    title: str
    content: str
    sources: List[str] = []


# Phase 4: Knowledge Graph, P2P, Multimodal

class KnowledgeGraphIngestRequest(BaseModel):
    text: str
    url: str = ""


class P2PQueryRequest(BaseModel):
    node_id: str
    query: str


class MultimodalImageRequest(BaseModel):
    image_data: str
    filename: str = "image.png"
    task: str = "analyze"


class MultimodalFileRequest(BaseModel):
    file_data: str
    filename: str


class MultimodalTextRequest(BaseModel):
    text: str


# Phase 5: Skill Synthesis, Fingerprint Rotation

class SkillSynthesizeRequest(BaseModel):
    url: str
    error_message: str
    page_snippet: str = ""
    target_description: str = ""


class FingerprintGenerateRequest(BaseModel):
    os_family: str = None


# ---- Lifecycle ----

from contextlib import asynccontextmanager
import gc

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Modern lifespan context manager - replaces deprecated on_event."""
    init_db()
    print("🚀 Jambubrowser Engine v2.0 started on port 8001")

    # Background tasks
    tasks = []

    async def memory_audit():
        try:
            while True:
                await asyncio.sleep(600)
                gc.collect()
                try:
                    await manager.broadcast("all", "🧹 Periodic memory audit complete.")
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass

    async def curiosity_loop():
        try:
            while True:
                await asyncio.sleep(60)
                try:
                    from backend.core.database import get_db_cursor
                    with get_db_cursor() as cursor:
                        cursor.execute("SELECT text FROM documents ORDER BY RANDOM() LIMIT 1")
                        row = cursor.fetchone()
                        if row:
                            topic = row[0][:50]
                            await manager.broadcast("all",
                                f"🧪 Curiosity: Exploring subtopic from vault via {LATEST_LLM_CONFIG.get('modelId', 'local')}")
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass

    tasks.append(asyncio.create_task(memory_audit()))
    tasks.append(asyncio.create_task(curiosity_loop()))

    yield  # Application runs here

    # Shutdown cleanup
    for task in tasks:
        task.cancel()
    try:
        from backend.modules.browser import cleanup_browser
        await cleanup_browser()
    except Exception:
        pass
    try:
        from backend.modules.missions import get_scheduler
        get_scheduler().stop()
    except Exception:
        pass
    try:
        from backend.modules.shadow_browser import get_shadow_browser
        await get_shadow_browser().close()
    except Exception:
        pass
    try:
        from backend.modules.risk_shield import get_shield
        await get_shield().close()
    except Exception:
        pass


app = FastAPI(title="Jambubrowser Engine v2.0", lifespan=lifespan)

from backend.core.rate_limiter import RateLimitMiddleware, get_limiter
limiter = get_limiter()
limiter.set_endpoint_limit("/research", 2.0, 5)
limiter.set_endpoint_limit("/exec", 5.0, 10)
app.add_middleware(RateLimitMiddleware, limiter=limiter)


# ---- WebSocket ----

@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await manager.connect(client_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(client_id)


# ===================================================================
# SYSTEM ENDPOINTS
# ===================================================================

@app.get("/health")
async def health():
    """System health with real-time metrics."""
    mem = psutil.virtual_memory()
    return {
        "status": "online",
        "message": "Jambubrowser v2.0 is ready.",
        "ram_used_gb": round(mem.used / (1024 ** 3), 2),
        "ram_total_gb": round(mem.total / (1024 ** 3), 2),
        "cpu_percent": psutil.cpu_percent(interval=None),
    }


@app.get("/stats")
async def get_stats():
    """Database and system statistics."""
    db_info = db_stats()
    return {
        "doc_count": db_info["documents"],
        "active_missions": db_info["active_missions"],
        "custom_tools": db_info["custom_tools"],
        "credentials": db_info["credentials"],
        "browser_sessions": db_info["browser_sessions"],
    }


# ===================================================================
# RESEARCH ENDPOINTS
# ===================================================================

@app.post("/research")
async def research(req: ResearchRequest):
    """Primary autonomous research endpoint with swarm, scrape, and RAG."""
    cid = req.client_id
    global last_activity, LATEST_LLM_CONFIG
    last_activity = time.time()

    try:
        LATEST_LLM_CONFIG = req.llm_config

        # Brain-only mode
        if req.brain_only:
            return await _brain_only_research(req.query)

        # Step 1: Expand search queries
        expanded = await _expand_query(req.query, cid, req.llm_config)

        # Step 2: Multi-engine search
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
            engines = "google,bing,duckduckgo,wikipedia"
            if req.domain == "finance":
                engines = "yahoo finance,google"
            if req.tor_routing:
                engines += ",ahmia,torch"

            async with httpx.AsyncClient() as client:
                tasks = [
                    client.get(SEARXNG_URL, params={"q": q, "format": "json", "engines": engines}, timeout=15.0)
                    for q in expanded
                ]
                resps = await asyncio.gather(*tasks, return_exceptions=True)
                for r in resps:
                    if isinstance(r, Exception):
                        continue
                    if r.status_code == 200:
                        try:
                            all_res.extend(r.json().get("results", []))
                        except Exception:
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

        if not search_results:
            return {"context": "", "sources": [], "doc_count": 0}

        # Security screening
        safe_urls = []
        for r in search_results:
            is_risky = await _assess_url_risk(r["url"], cid, req.llm_config)
            if not is_risky:
                safe_urls.append(r["url"])

        # Step 3: Scrape with stealth
        proxy = "socks5://127.0.0.1:9050" if req.tor_routing else None
        effective_proxy = proxy or req.stealth.get("proxy") or GLOBAL_VPN_PROXY

        try:
            from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
            from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
            from crawl4ai.content_filter_strategy import BM25ContentFilter

            markdown_strategy = DefaultMarkdownGenerator(
                content_filter=BM25ContentFilter(user_query=req.query, bm25_threshold=0.4),
                options={"ignore_links": True, "ignore_images": True, "skip_internal_links": True, "strip_comments": True},
            )
            browser_config = BrowserConfig(proxy=effective_proxy, headless=True)
            run_config = CrawlerRunConfig(
                markdown_generator=markdown_strategy,
                wait_until="networkidle",
                magic=True,
                screenshot=True,
                delay_before_return_html=1.0,
            )

            if req.incognito or effective_proxy:
                async with AsyncWebCrawler(config=browser_config) as temp_crawler:
                    tasks = [temp_crawler.arun(url=url, config=run_config) for url in safe_urls]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
            else:
                async with AsyncWebCrawler(config=browser_config) as crawler:
                    tasks = [crawler.arun(url=url, config=run_config) for url in safe_urls]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
        except ImportError:
            # Fallback to basic HTTP fetch if crawl4ai not available
            results = []
            async with httpx.AsyncClient() as client:
                for url in safe_urls:
                    try:
                        resp = await client.get(url, timeout=15.0, follow_redirects=True)
                        results.append(resp)
                    except Exception:
                        results.append(Exception(f"Failed to fetch {url}"))

        crawled = []
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                continue
            if hasattr(res, "success") and res.success:
                crawled.append({"url": safe_urls[i], "markdown": res.markdown})
            elif hasattr(res, "text"):
                crawled.append({"url": safe_urls[i], "markdown": res.text[:50000]})

        # Step 4: Index into local brain
        try:
            from sentence_transformers import SentenceTransformer
            import numpy as np
            import sqlite_vec

            model = SentenceTransformer("all-MiniLM-L6-v2")

            with get_db_cursor() as cursor:
                if not req.persist:
                    cursor.execute("DELETE FROM documents")
                    cursor.execute("DELETE FROM vec_documents")

                for item in crawled:
                    from backend.core.database import smart_chunking
                    for chunk in smart_chunking(item["markdown"]):
                        chash = hashlib.sha256(chunk.encode()).hexdigest()
                        cursor.execute("SELECT embedding FROM embedding_cache WHERE hash = ?", (chash,))
                        row = cursor.fetchone()
                        emb_bytes = row[0] if row else model.encode(chunk).astype(np.float32).tobytes()

                        if not row:
                            cursor.execute("INSERT OR IGNORE INTO embedding_cache VALUES (?, ?)", (chash, emb_bytes))

                        cursor.execute(
                            "INSERT INTO documents (url, text) VALUES (?, ?)",
                            (item["url"], chunk),
                        )
                        cursor.execute(
                            "INSERT INTO vec_documents (id, embedding) VALUES (?, ?)",
                            (cursor.lastrowid, emb_bytes),
                        )

                # Vector search for final context
                query_vec = model.encode(req.query).astype(np.float32).tobytes()
                cursor.execute(
                    "SELECT d.text, d.url FROM vec_documents v JOIN documents d ON v.id = d.id WHERE v.embedding MATCH ? AND k = 8",
                    (query_vec,),
                )
                rows = cursor.fetchall()
        except ImportError:
            rows = []
            for item in crawled:
                rows.append((item["markdown"][:500], item["url"]))

        return {
            "context": "\n\n".join([f"Source: {r[1]}\n{r[0]}" for r in rows]),
            "sources": list(set([r[1] for r in rows])),
            "doc_count": len(crawled),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/search")
async def search(q: str, engines: str = "google,bing,duckduckgo", format: str = "json"):
    """Raw multi-engine metasearch without scraping."""
    try:
        results = await multi_engine_search(q, engines)
        return {"results": results, "query": q}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/scrape")
async def scrape(req: ScrapeRequest):
    """Single-page scraping endpoint."""
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
            return {"success": True, "url": req.url, "markdown": result.markdown[:50000], "title": result.metadata.get("title", "") if result.metadata else ""}
        return {"success": False, "url": req.url, "error": "Failed to scrape page"}
    except ImportError:
        async with httpx.AsyncClient() as client:
            resp = await client.get(req.url, timeout=15.0, follow_redirects=True)
            return {"success": True, "url": req.url, "markdown": resp.text[:50000], "title": ""}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===================================================================
# SANDBOX EXECUTION
# ===================================================================

@app.post("/exec")
async def execute_code(req: ExecRequest):
    """
    Execute Python code in a sandboxed environment.
    Uses Docker when available, falls back to subprocess isolation.
    """
    try:
        result = await execute_sandboxed(req.code, req.timeout)
        await manager.broadcast(req.client_id, f"⚡ Sandbox ({result['sandbox_type']}): Execution completed in {result['execution_time']}s")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===================================================================
# BROWSER AUTOMATION
# ===================================================================

@app.post("/act")
async def perform_actions(req: MultiActionRequest):
    """Execute browser actions (click, type, scroll, click_xy)."""
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

        js_lines = []
        for step in req.steps:
            if step.action == "click":
                js_lines.append(f"document.querySelector('{step.selector}').click();")
            elif step.action == "type":
                js_lines.append(f"document.querySelector('{step.selector}').value = '{step.value}';")
            elif step.action == "scroll":
                js_lines.append(f"window.scrollBy(0, {step.value});")
            elif step.action == "click_xy":
                js_lines.append(
                    f"{{ const vx = window.innerWidth * {step.x / 100}; "
                    f"const vy = window.innerHeight * {step.y / 100}; "
                    f"const el = document.elementFromPoint(vx, vy); if(el) el.click(); }}"
                )

        browser_config = BrowserConfig(headless=True)
        run_config = CrawlerRunConfig(wait_until="networkidle")

        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await crawler.arun(
                url=req.url,
                js_code=f"(async () => {{ {' '.join(js_lines)} }})()",
                config=run_config,
            )
            return {"status": "success", "markdown": result.markdown[:10000] if result.success else ""}
    except ImportError:
        return {"status": "error", "message": "crawl4ai not installed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/workflow/execute")
async def execute_workflow(req: MultiActionRequest):
    """Execute a multi-step browser workflow."""
    return await perform_actions(req)


# ===================================================================
# CREDENTIAL VAULT
# ===================================================================

@app.post("/login")
async def perform_login(req: LoginRequest):
    """
    Autonomous login using the Credential Vault.
    Stores credentials encrypted and attempts login.
    """
    try:
        vault = get_vault()

        # Store the credential
        from urllib.parse import urlparse
        parsed = urlparse(req.url)
        domain = parsed.hostname or req.url

        vault.store_credential(
            domain=domain,
            username=req.username,
            password=req.password,
            url_pattern=f"*{domain}*",
        )

        await manager.broadcast(req.client_id, f"🔐 Credential stored for {domain}")

        # Attempt to use the credential for login
        try:
            from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

            browser_config = BrowserConfig(headless=True)
            run_config = CrawlerRunConfig(wait_until="networkidle")

            async with AsyncWebCrawler(config=browser_config) as crawler:
                # Try to fill login form
                js_code = (
                    f"(async () => {{"
                    f"  const userField = document.querySelector('input[type=\"email\"], input[type=\"text\"], input[name=\"username\"], input[name=\"email\"]');"
                    f"  const passField = document.querySelector('input[type=\"password\"]');"
                    f"  const submitBtn = document.querySelector('button[type=\"submit\"], input[type=\"submit\"]');"
                    f"  if (userField) userField.value = '{req.username}';"
                    f"  if (passField) passField.value = '{req.password}';"
                    f"  if (submitBtn) submitBtn.click();"
                    f"}})()"
                )
                result = await crawler.arun(url=req.url, js_code=js_code, config=run_config)

            return {
                "status": "success",
                "domain": domain,
                "message": f"Login attempted for {domain}",
                "page_title": result.metadata.get("title", "") if result.success and result.metadata else "",
            }
        except ImportError:
            return {"status": "success", "domain": domain, "message": f"Credential stored for {domain}. Login automation requires crawl4ai."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/vault/domains")
async def list_vault_domains():
    """List all domains with stored credentials."""
    vault = get_vault()
    return {"domains": vault.list_domains()}


@app.get("/vault/credential")
async def get_vault_credential(url: str):
    """Find the best matching credential for a URL."""
    vault = get_vault()
    cred = vault.find_best_credential(url)
    if cred:
        return {"found": True, "domain": cred["domain"], "username": cred["username"]}
    return {"found": False}


# ===================================================================
# VISION & PERCEPTION
# ===================================================================

@app.post("/vision/grounding")
async def vision_grounding(req: ScrapeRequest):
    """Visual grounding: analyze page and suggest interactive elements."""
    cid = req.client_id
    await manager.broadcast(cid, "👁️ Performing visual grounding pass...")

    base_url = LATEST_LLM_CONFIG.get("baseUrl", "http://localhost:8080/v1")
    model_id = LATEST_LLM_CONFIG.get("modelId", "gemma-4-12b")

    try:
        # Fetch page structure for analysis
        async with httpx.AsyncClient() as cl:
            resp = await cl.get(req.url, timeout=10.0, follow_redirects=True)
            page_text = resp.text[:5000] if resp.status_code == 200 else ""

        prompt = (
            "Analyze this page structure and suggest 3 high-impact actions (click, type, scroll) "
            "to extract information. Return JSON: [{label, action, selector}]. "
            f"Page snippet: {page_text[:3000]}"
        )

        async with httpx.AsyncClient() as cl:
            ai_resp = await cl.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {LATEST_LLM_CONFIG.get('apiKey', '')}"},
                json={"model": model_id, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2},
                timeout=15.0,
            )
            content = ai_resp.json()["choices"][0]["message"]["content"]
            # Try to parse JSON from response
            try:
                suggestions = json.loads(content)
            except json.JSONDecodeError:
                suggestions = [
                    {"label": "🔍 Explore Page", "action": "click", "selector": "a:first-of-type"},
                    {"label": "📊 Extract Content", "action": "scrape", "url": req.url},
                    {"label": "⏬ Scroll for More", "action": "scroll", "value": "500"},
                ]

        return {"suggestions": suggestions}
    except Exception:
        return {
            "suggestions": [
                {"label": "🔍 Explore Page", "action": "click", "selector": "a:first-of-type"},
                {"label": "📊 Extract Content", "action": "scrape", "url": req.url},
                {"label": "⏬ Scroll for More", "action": "scroll", "value": "500"},
            ]
        }


# ===================================================================
# MEMORY & KNOWLEDGE
# ===================================================================

@app.get("/memory/recall")
async def recall_memory(query: str):
    """Cross-session semantic recall from the knowledge vault."""
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np

        model = SentenceTransformer("all-MiniLM-L6-v2")

        with get_db_cursor() as cursor:
            query_vec = model.encode(query).astype(np.float32).tobytes()
            cursor.execute(
                "SELECT d.text, d.url FROM vec_documents v JOIN documents d ON v.id = d.id WHERE v.embedding MATCH ? AND k = 10",
                (query_vec,),
            )
            rows = cursor.fetchall()

        return {
            "memory": [
                {"text": r[0][:300], "url": r[1]}
                for r in rows
            ]
        }
    except ImportError:
        return {"memory": []}
    except Exception as e:
        return {"memory": [], "error": str(e)}


@app.get("/graph_data")
async def get_graph_data():
    """Generate node/edge data for 3D brain visualization."""
    with get_db_cursor() as cursor:
        cursor.execute("SELECT id, text, url FROM documents ORDER BY id DESC LIMIT 50")
        docs = cursor.fetchall()

    nodes = [{"id": d[0], "label": d[1][:30] + "...", "url": d[2], "val": 1} for d in docs]
    edges = []
    for i in range(len(docs)):
        for j in range(i + 1, len(docs)):
            wi = set(docs[i][1].lower().split())
            wj = set(docs[j][1].lower().split())
            if len(wi & wj) > 5:
                edges.append({"source": docs[i][0], "target": docs[j][0]})

    return {"nodes": nodes, "edges": edges}


# ===================================================================
# MISSIONS
# ===================================================================

@app.post("/mission")
async def start_mission(req: MissionRequest):
    """Register a background research mission."""
    mid = hashlib.md5(req.query.encode()).hexdigest()[:8]
    with get_db_cursor() as cursor:
        cursor.execute(
            "INSERT OR REPLACE INTO missions (id, query, status, last_run, next_run, schedule) VALUES (?, ?, 'active', ?, ?, 'none')",
            (mid, req.query, time.time(), 0),
        )
    return {"mission_id": mid, "status": "active"}


@app.post("/mission/stop")
async def stop_mission(req: MissionStopRequest):
    """Stop a background research mission."""
    with get_db_cursor() as cursor:
        cursor.execute("UPDATE missions SET status = 'stopped' WHERE id = ?", (req.mission_id,))
    return {"mission_id": req.mission_id, "status": "stopped"}


# ===================================================================
# TOOL MANAGEMENT
# ===================================================================

@app.post("/tool/save")
async def save_custom_tool(req: ToolSaveRequest):
    """Persist an agent-written Python skill."""
    safe_name = re.sub(r"[^a-zA-Z0-9_]", "", req.name)
    tools_dir = os.path.join(os.path.dirname(__file__), "..", "tools")
    os.makedirs(tools_dir, exist_ok=True)
    file_path = os.path.join(tools_dir, f"{safe_name}.py")

    with open(file_path, "w") as f:
        f.write(req.code)

    with get_db_cursor() as cursor:
        cursor.execute(
            "INSERT OR REPLACE INTO custom_tools VALUES (?, ?, ?, ?)",
            (safe_name, req.description, file_path, time.time()),
        )

    return {"status": "success", "name": safe_name}


@app.get("/tools")
async def list_tools():
    """List all saved agent skills."""
    with get_db_cursor() as cursor:
        cursor.execute("SELECT name, description, created_at FROM custom_tools")
        return {
            "tools": [
                {"name": r[0], "description": r[1], "created": r[2]}
                for r in cursor.fetchall()
            ]
        }


@app.post("/tool/exec")
async def execute_custom_tool(req: ToolExecRequest):
    """Run a saved agent skill."""
    tools_dir = os.path.join(os.path.dirname(__file__), "..", "tools")
    file_path = os.path.join(tools_dir, f"{req.name}.py")

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"Tool '{req.name}' not found")

    spec = importlib.util.spec_from_file_location(req.name, file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if asyncio.iscoroutinefunction(module.run):
        result = await module.run(**req.kwargs)
    else:
        result = module.run(**req.kwargs)

    return {"output": str(result)}


# ===================================================================
# API DISCOVERY
# ===================================================================

@app.post("/discover_api")
async def discover_api(req: ScrapeRequest):
    """Scan a URL for OpenAPI/Swagger specifications."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(req.url, timeout=10.0)
            if "openapi" in resp.text.lower() or "swagger" in resp.text.lower():
                try:
                    return {"spec": resp.json(), "url": req.url}
                except Exception:
                    return {"spec": resp.text[:5000], "url": req.url}
            return {"error": "No API spec found"}
        except Exception as e:
            return {"error": str(e)}


@app.post("/api/call")
async def call_dynamic_api(req: DynamicApiRequest):
    """Execute a structured request against a discovered API."""
    async with httpx.AsyncClient() as client:
        resp = await client.request(
            req.method, req.url,
            json=req.payload if req.payload else None,
            headers=req.headers,
            timeout=30.0,
        )
        try:
            data = resp.json()
        except Exception:
            data = resp.text[:10000]
        return {"status": resp.status_code, "data": data}


# ===================================================================
# P2P MESH (STUBS)
# ===================================================================

@app.get("/peers/discover")
async def discover_peers():
    """UDP discovery of other Jambu nodes on the network."""
    return {"peers": []}


@app.post("/peer/sync")
async def peer_sync(req: ResearchRequest):
    """Anonymized research vector exchange."""
    return {"results": []}


# ===================================================================
# HELPER FUNCTIONS
# ===================================================================

async def _brain_only_research(query: str) -> dict:
    """Search only the local knowledge vault."""
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np

        model = SentenceTransformer("all-MiniLM-L6-v2")
        query_vec = model.encode(query).astype(np.float32).tobytes()

        with get_db_cursor() as cursor:
            cursor.execute(
                "SELECT d.text, d.url FROM vec_documents v JOIN documents d ON v.id = d.id WHERE v.embedding MATCH ? AND k = 15",
                (query_vec,),
            )
            rows = cursor.fetchall()

        scored = sorted(
            [
                (sum(1 for w in set(query.lower().split()) if w in r[0].lower()), r[0], r[1])
                for r in rows
            ],
            reverse=True,
        )[:8]

        return {
            "context": "\n\n".join([f"Source: {r[2]}\n{r[1]}" for r in scored]),
            "sources": list(set([r[2] for r in scored])),
            "doc_count": 0,
        }
    except ImportError:
        return {"context": "", "sources": [], "doc_count": 0}


async def _expand_query(query: str, client_id: str, llm_config: dict) -> list:
    """Use LLM to generate diverse search queries."""
    base_url = llm_config.get("baseUrl", "http://localhost:8080/v1")
    model_id = llm_config.get("modelId", "gemma-4-12b")

    prompt = f"Diverse search queries for: '{query}'. Return exactly 3 lines, one query per line."
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                json={"model": model_id, "messages": [{"role": "user", "content": prompt}]},
                timeout=10.0,
            )
            return resp.json()["choices"][0]["message"]["content"].strip().split("\n")
        except Exception:
            return [query]


async def _assess_url_risk(url: str, client_id: str, llm_config: dict) -> bool:
    """Pre-scan URL for security risks."""
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
    """Fetch papers from ArXiv API."""
    url = f"http://export.arxiv.org/api/query?search_query=all:{query}&max_results=3"
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
    """Fetch repos from GitHub API."""
    url = f"https://api.github.com/search/repositories?q={query}&per_page=3"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers={"Accept": "application/vnd.github.v3+json"}, timeout=15.0)
        return [
            {"url": i["html_url"], "markdown": i.get("description", "")}
            for i in resp.json().get("items", [])
        ]


# ===================================================================
# PHASE 2: RISK SHIELD ENDPOINTS
# ===================================================================

@app.post("/shield/check")
async def shield_check(req: ShieldRequest):
    """Assess the risk of a URL using all available sources."""
    try:
        from backend.modules.risk_shield import get_shield
        result = await get_shield().assess_url(req.url, real_time=req.real_time)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/shield/batch")
async def shield_batch(req: ShieldBatchRequest):
    """Batch risk assessment for multiple URLs."""
    try:
        from backend.modules.risk_shield import get_shield
        results = await get_shield().batch_assess(req.urls)
        return {"results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/shield/stats")
async def shield_stats():
    """Get risk shield cache statistics."""
    from backend.modules.risk_shield import get_shield
    return get_shield().get_cache_stats()


# ===================================================================
# PHASE 2: ADVANCED MISSION SCHEDULER
# ===================================================================

@app.post("/mission/schedule")
async def schedule_mission(req: MissionScheduleRequest):
    """Schedule an advanced mission with cron expression and trigger conditions."""
    try:
        from backend.modules.missions import get_scheduler, parse_cron, get_next_run
        scheduler = get_scheduler()
        mission = await scheduler.add_mission(
            query=req.query, schedule=req.schedule, priority=req.priority,
            trigger_conditions=req.trigger_conditions,
        )
        next_run = get_next_run(req.schedule) if req.schedule else None
        return {
            "mission_id": mission.id, "status": mission.status,
            "query": mission.query, "schedule": mission.schedule,
            "priority": mission.priority, "next_run": next_run,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/mission/list")
async def list_missions(status: str = None):
    """List all scheduled missions."""
    from backend.modules.missions import get_scheduler
    scheduler = get_scheduler()
    await scheduler.load_from_db()
    return {"missions": scheduler.list_missions(status)}


@app.post("/mission/start-scheduler")
async def start_mission_scheduler():
    """Start the background mission scheduler loop."""
    from backend.modules.missions import get_scheduler
    scheduler = get_scheduler()
    await scheduler.load_from_db()
    from backend.modules.notifications import get_notifier
    notifier = get_notifier()
    scheduler.set_notification_handler(
        on_complete=lambda m, r: notifier.send_mission_alert(m.query[:50], r[:200]),
        on_finding=lambda m, r: notifier.send_mission_alert(m.query[:50], r[:200]),
    )
    asyncio.create_task(scheduler.run_loop())
    return {"status": "started", "missions_loaded": len(scheduler.list_missions())}


@app.post("/mission/stop-scheduler")
async def stop_mission_scheduler():
    """Stop the background mission scheduler."""
    from backend.modules.missions import get_scheduler
    get_scheduler().stop()
    return {"status": "stopped"}


# ===================================================================
# PHASE 2: SHADOW BROWSER
# ===================================================================

@app.post("/shadow/start")
async def start_shadow_browser():
    """Start the autonomous shadow browser background loop."""
    from backend.modules.shadow_browser import get_shadow_browser
    shadow = get_shadow_browser()
    asyncio.create_task(shadow.run_loop())
    return {"status": "started"}


@app.get("/shadow/stats")
async def shadow_browser_stats():
    """Get shadow browser statistics."""
    from backend.modules.shadow_browser import get_shadow_browser
    return get_shadow_browser().get_stats()


@app.get("/shadow/interests")
async def shadow_interests():
    """List shadow browser interest profiles."""
    from backend.modules.shadow_browser import get_shadow_browser
    return {"interests": get_shadow_browser().get_interests()}


@app.post("/shadow/interests")
async def add_shadow_interest(req: ShadowInterestRequest):
    """Add a new interest topic to the shadow browser."""
    from backend.modules.shadow_browser import get_shadow_browser, InterestTopic
    topic = InterestTopic(name=req.name, keywords=req.keywords,
                          seed_urls=req.seed_urls, priority=req.priority)
    get_shadow_browser().add_interest(topic)
    return {"status": "added", "name": req.name}


@app.delete("/shadow/interests/{name}")
async def remove_shadow_interest(name: str):
    """Remove an interest topic from the shadow browser."""
    from backend.modules.shadow_browser import get_shadow_browser
    get_shadow_browser().remove_interest(name)
    return {"status": "removed", "name": name}


@app.post("/shadow/stop")
async def stop_shadow_browser():
    """Stop the shadow browser background loop."""
    from backend.modules.shadow_browser import get_shadow_browser
    get_shadow_browser().stop()
    return {"status": "stopped"}


# ===================================================================
# PHASE 2: NOTIFICATIONS
# ===================================================================

@app.get("/notifications/history")
async def notification_history(category: str = None, limit: int = 20):
    """Get notification history, optionally filtered by category."""
    from backend.modules.notifications import get_notifier
    return {"notifications": get_notifier().get_history(category=category, limit=limit)}


@app.post("/notifications/send")
async def send_notification_endpoint(
    title: str, message: str, urgency: str = "normal",
    category: str = "general", action_url: str = "",
):
    """Send a test/system notification."""
    from backend.modules.notifications import send_notification
    notif = await send_notification(title=title, message=message,
                                     urgency=urgency, category=category,
                                     action_url=action_url)
    return {"status": "sent", "id": notif.id, "delivered": notif.delivered}


# ===================================================================
# PHASE 3: VISION, FORM FILLER, LOCAL CONNECTOR
# ===================================================================

@app.post("/vision/analyze")
async def vision_analyze(req: VisionGroundRequest):
    """Analyze a screenshot using the vision model. Returns grounded UI elements."""
    import base64
    from backend.modules.vision import get_vision_model, VisionGrounder
    try:
        if req.image_data:
            image_bytes = base64.b64decode(req.image_data)
        else:
            from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
            browser_config = BrowserConfig(headless=True)
            run_config = CrawlerRunConfig(screenshot=True, wait_until="networkidle")
            async with AsyncWebCrawler(config=browser_config) as crawler:
                result = await crawler.arun(url=req.url, config=run_config)
                if not result.success:
                    raise HTTPException(status_code=500, detail="Screenshot failed")
                image_bytes = base64.b64decode(result.screenshot) if isinstance(result.screenshot, str) else result.screenshot
        model = get_vision_model()
        grounder = VisionGrounder(model)
        analysis = await grounder.ground_page(image_bytes, req.url)
        suggestions = grounder.elements_to_suggestions(analysis)
        return {
            "url": req.url, "elements_found": len(analysis.elements),
            "processing_time": analysis.processing_time, "model_used": analysis.model_used,
            "suggestions": suggestions,
            "elements": [{"label": e.label, "type": e.element_type, "selector": e.selector,
                          "position": e.bounding_box, "confidence": e.confidence,
                          "action": e.suggested_action} for e in analysis.elements[:10]],
        }
    except ImportError:
        return {
            "url": req.url, "elements_found": 0, "fallback": True,
            "suggestions": [
                {"label": "Explore Page", "action": "click", "selector": "a:first-of-type"},
                {"label": "Extract Content", "action": "scrape", "url": req.url},
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/forms/detect")
async def detect_forms(req: FormDetectRequest):
    """Detect and classify forms on a page. Match with vault credentials."""
    from backend.modules.form_filler import get_form_filler
    try:
        if not req.html:
            async with httpx.AsyncClient() as client:
                resp = await client.get(req.url, timeout=15.0, follow_redirects=True)
                html = resp.text
        else:
            html = req.html
        return get_form_filler().detect_and_match(html, req.url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/forms/fill-script")
async def generate_fill_script(req: FormDetectRequest):
    """Generate JS to fill a detected form with vault credentials."""
    from backend.modules.form_filler import get_form_filler
    try:
        filler = get_form_filler()
        html = req.html
        if not html:
            async with httpx.AsyncClient() as c:
                html = (await c.get(req.url, timeout=15.0, follow_redirects=True)).text
        result = filler.detect_and_match(html, req.url)
        scripts = []
        for form in result.get('forms', []):
            if form.get('auto_fillable'):
                fill_data = {f['selector']: f['value'] for f in form['fields'] if f.get('value')}
                scripts.append({
                    'form_selector': form['form_selector'], 'form_type': form['form_type'],
                    'fields_filled': len(fill_data),
                    'js_script': filler.generate_js_fill_script(fill_data, form.get('has_submit', True)),
                })
        return {"scripts": scripts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/local/obsidian/create")
async def obsidian_create(req: ObsidianRequest):
    """Create a new note in the Obsidian vault."""
    from backend.modules.local_connector import get_obsidian
    return get_obsidian(req.vault_path).create_note(req.title, req.content, req.folder)


@app.post("/local/obsidian/append")
async def obsidian_append(req: ObsidianRequest):
    """Append content to an existing Obsidian note."""
    from backend.modules.local_connector import get_obsidian
    return get_obsidian(req.vault_path).append_to_note(req.title, req.content)


@app.get("/local/obsidian/read")
async def obsidian_read(title: str, vault_path: str = None):
    """Read an Obsidian note by title."""
    from backend.modules.local_connector import get_obsidian
    return get_obsidian(vault_path).read_note(title)


@app.get("/local/obsidian/search")
async def obsidian_search(query: str, max_results: int = 10, vault_path: str = None):
    """Search the Obsidian vault."""
    from backend.modules.local_connector import get_obsidian
    return get_obsidian(vault_path).search_vault(query, max_results)


@app.get("/local/obsidian/stats")
async def obsidian_stats(vault_path: str = None):
    """Get Obsidian vault statistics."""
    from backend.modules.local_connector import get_obsidian
    return get_obsidian(vault_path).get_stats()


@app.post("/local/reminders/create")
async def reminders_create(req: ReminderRequest):
    """Create a macOS Reminder."""
    from backend.modules.local_connector import get_reminders
    return get_reminders().create_reminder(req.title, req.notes, req.due_date, req.list_name)


@app.post("/local/clipboard/copy")
async def clipboard_copy(text: str):
    """Copy text to system clipboard."""
    from backend.modules.local_connector import get_clipboard
    return get_clipboard().copy(text)


@app.get("/local/clipboard/paste")
async def clipboard_paste():
    """Get system clipboard contents."""
    from backend.modules.local_connector import get_clipboard
    return get_clipboard().paste()


@app.post("/local/notes/save")
async def save_research_note(req: LocalNoteRequest):
    """Save research as a local markdown file."""
    from backend.modules.local_connector import get_filesystem
    return get_filesystem().save_research(req.title, req.content, req.sources)


# ===================================================================
# PHASE 4: KNOWLEDGE GRAPH
# ===================================================================

@app.post("/knowledge/ingest")
async def knowledge_ingest(req: KnowledgeGraphIngestRequest):
    """Ingest text into the knowledge graph, extracting entities and relations."""
    from backend.modules.knowledge_graph import get_knowledge_graph
    graph = get_knowledge_graph()
    result = graph.ingest_document(req.text, req.url)
    return result


@app.get("/knowledge/graph")
async def knowledge_graph_data(max_nodes: int = 100):
    """Get knowledge graph data for 3D visualization."""
    from backend.modules.knowledge_graph import get_knowledge_graph
    return get_knowledge_graph().get_graph_data(max_nodes)


@app.get("/knowledge/search")
async def knowledge_search(query: str, limit: int = 20):
    """Search for entities in the knowledge graph."""
    from backend.modules.knowledge_graph import get_knowledge_graph
    return {"entities": get_knowledge_graph().search_entities(query, limit)}


@app.get("/knowledge/entity/{entity_id}")
async def knowledge_entity(entity_id: str):
    """Get an entity and its relationships."""
    from backend.modules.knowledge_graph import get_knowledge_graph
    return get_knowledge_graph().get_entity_relations(entity_id)


@app.get("/knowledge/clusters")
async def knowledge_clusters(max_clusters: int = 10):
    """Get topic clusters from the knowledge graph."""
    from backend.modules.knowledge_graph import get_knowledge_graph
    return {"clusters": get_knowledge_graph().get_topic_clusters(max_clusters)}


@app.get("/knowledge/stats")
async def knowledge_stats():
    """Get knowledge graph statistics."""
    from backend.modules.knowledge_graph import get_knowledge_graph
    return get_knowledge_graph().get_stats()


# ===================================================================
# PHASE 4: P2P DISCOVERY
# ===================================================================

@app.get("/p2p/info")
async def p2p_node_info():
    """Get this node's information for peer discovery."""
    from backend.modules.p2p_discovery import get_p2p
    return get_p2p().get_node_info()


@app.post("/p2p/discover")
async def p2p_discover():
    """Trigger peer discovery on the local network."""
    from backend.modules.p2p_discovery import get_p2p
    p2p = get_p2p()
    peers = await p2p.discover_peers()
    return {"peers": [p.to_dict() for p in peers], "count": len(peers)}


@app.get("/p2p/peers")
async def p2p_list_peers(online_only: bool = False):
    """List all known peers."""
    from backend.modules.p2p_discovery import get_p2p
    return {"peers": get_p2p().get_peers(online_only)}


@app.post("/p2p/query")
async def p2p_query_peer(req: P2PQueryRequest):
    """Query a specific peer for research results."""
    from backend.modules.p2p_discovery import get_p2p
    result = await get_p2p().query_peer(req.node_id, req.query)
    return result or {"error": "Peer unavailable"}


@app.post("/p2p/start-discovery")
async def p2p_start_discovery():
    """Start the background peer discovery loop."""
    from backend.modules.p2p_discovery import get_p2p
    p2p = get_p2p()
    asyncio.create_task(p2p.run_discovery_loop())
    return {"status": "started"}


@app.get("/p2p/stats")
async def p2p_stats():
    """Get P2P network statistics."""
    from backend.modules.p2p_discovery import get_p2p
    return get_p2p().get_stats()


# Peer handler endpoints (for other nodes to call)
@app.get("/peer/info")
async def peer_info_handler():
    """Respond to peer info requests from other nodes."""
    from backend.modules.p2p_discovery import get_p2p
    return get_p2p().get_node_info()


@app.post("/peer/query")
async def peer_query_handler(request: dict):
    """Handle research queries from peer nodes via Federated RAG protocol."""
    from backend.modules.federated_rag import get_federated_rag
    return await get_federated_rag().handle_federated_query_request(request)


# ===================================================================
# PHASE 4: MULTIMODAL INPUT
# ===================================================================

@app.post("/multimodal/image")
async def multimodal_image(req: MultimodalImageRequest):
    """Process an image: OCR, analysis, or data extraction."""
    import base64
    from backend.modules.multimodal_input import get_processor
    try:
        image_bytes = base64.b64decode(req.image_data)
        processor = get_processor()
        result = await processor.process_image(image_bytes, req.filename, req.task)
        return {
            "input_type": result.input_type,
            "extracted_text": result.extracted_text[:5000],
            "structured_data": result.structured_data,
            "summary": result.summary,
            "confidence": result.confidence,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/multimodal/file")
async def multimodal_file(req: MultimodalFileRequest):
    """Process a file: CSV, JSON, markdown, code, or text."""
    import base64
    from backend.modules.multimodal_input import get_processor
    try:
        file_bytes = base64.b64decode(req.file_data)
        processor = get_processor()
        result = await processor.process_file(file_bytes, req.filename)
        return {
            "input_type": result.input_type,
            "extracted_text": result.extracted_text[:5000],
            "structured_data": result.structured_data,
            "summary": result.summary,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/multimodal/text")
async def multimodal_text(req: MultimodalTextRequest):
    """Process pasted text: URL detection, code recognition, query parsing."""
    from backend.modules.multimodal_input import get_processor
    try:
        processor = get_processor()
        result = await processor.process_text_input(req.text)
        return {
            "input_type": result.input_type,
            "extracted_text": result.extracted_text[:5000],
            "structured_data": result.structured_data,
            "summary": result.summary,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===================================================================
# PHASE 5: SKILL SYNTHESIS
# ===================================================================

@app.post("/skill/synthesize")
async def synthesize_skill(req: SkillSynthesizeRequest):
    """Autonomously synthesize a Python skill to fix a scraping failure."""
    from backend.modules.skill_synthesizer import get_synthesizer
    synthesizer = get_synthesizer()
    result = await synthesizer.analyze_and_synthesize(
        url=req.url, error_message=req.error_message,
        page_snippet=req.page_snippet,
        target_description=req.target_description,
    )
    return result


@app.get("/skill/list-synthesized")
async def list_synthesized_skills():
    """List all autonomously synthesized skills."""
    from backend.modules.skill_synthesizer import get_synthesizer
    return {"skills": get_synthesizer().list_synthesized()}


# ===================================================================
# PHASE 5: FINGERPRINT ROTATION
# ===================================================================

@app.post("/fingerprint/generate")
async def generate_fingerprint(req: FingerprintGenerateRequest):
    """Generate a new unique browser fingerprint profile."""
    from backend.modules.fingerprint_rotator import get_rotator
    rotator = get_rotator()
    profile = rotator.generate_profile(req.os_family)
    return {
        "profile": profile.to_dict(),
        "playwright_config": rotator.get_profile_for_playwright(profile.profile_id),
    }


@app.get("/fingerprint/list")
async def list_fingerprints():
    """List all generated fingerprint profiles."""
    from backend.modules.fingerprint_rotator import get_rotator
    return {"profiles": get_rotator().list_profiles()}


@app.get("/fingerprint/profile/{profile_id}")
async def get_fingerprint(profile_id: str):
    """Get a specific fingerprint profile by ID."""
    from backend.modules.fingerprint_rotator import get_rotator
    rotator = get_rotator()
    profile = rotator.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {
        "profile": profile.to_dict(),
        "playwright_config": rotator.get_profile_for_playwright(profile_id),
        "proxy_routing": rotator.get_proxy_routing_config(profile_id),
    }


@app.post("/fingerprint/rotate")
async def rotate_fingerprint(current_profile_id: str = None):
    """Generate a new fingerprint deliberately different from current."""
    from backend.modules.fingerprint_rotator import get_rotator
    rotator = get_rotator()
    profile = rotator.rotate_profile(current_profile_id)
    return {
        "profile": profile.to_dict(),
        "playwright_config": rotator.get_profile_for_playwright(profile.profile_id),
    }


# ===================================================================
# FEDERATED RAG
# ===================================================================

@app.post("/federated/query")
async def federated_query(query: str, min_relevance: float = 0.5, max_results: int = 10):
    """Send an anonymized query to trusted peers for federated knowledge sharing."""
    from backend.modules.federated_rag import get_federated_rag
    frag = get_federated_rag()
    fquery = frag.create_query(query, min_relevance, max_results)
    results = await frag.query_peers(fquery)
    total = sum(len(r) for r in results.values())
    return {"query_id": fquery.query_id, "peers_queried": len(results), "total_results": total}


@app.get("/federated/stats")
async def federated_stats():
    """Get federated RAG statistics."""
    from backend.modules.federated_rag import get_federated_rag
    return get_federated_rag().get_stats()


@app.post("/media/youtube")
async def youtube_analyze(url: str, summarize: bool = False):
    """Analyze a YouTube video: transcript, metadata, chapters, and optional summary."""
    from backend.modules.youtube import get_youtube_analyzer
    analyzer = get_youtube_analyzer()
    video_id = analyzer.extract_video_id(url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")
    llm_config = LATEST_LLM_CONFIG if summarize else None
    video = await analyzer.analyze(url, llm_config)
    return video.to_dict()


@app.get("/media/youtube/transcript")
async def youtube_transcript(url: str):
    """Get only the transcript of a YouTube video."""
    from backend.modules.youtube import get_youtube_analyzer
    analyzer = get_youtube_analyzer()
    video_id = analyzer.extract_video_id(url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")
    transcript = await analyzer.get_transcript(video_id)
    return {
        "video_id": video_id,
        "transcript": [{"text": s.text, "start": s.start, "duration": s.duration} for s in transcript],
        "full_text": " ".join(s.text for s in transcript),
    }


@app.get("/media/youtube/search")
async def youtube_search_transcript(url: str, query: str):
    """Search within a YouTube video's transcript."""
    from backend.modules.youtube import get_youtube_analyzer
    analyzer = get_youtube_analyzer()
    video_id = analyzer.extract_video_id(url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")
    results = await analyzer.search_transcript(video_id, query)
    return {
        "video_id": video_id,
        "query": query,
        "matches": len(results),
        "segments": [{"text": r.text, "start": r.start} for r in results[:10]],
    }


# ===================================================================
# MODEL MANAGER - GEMMA 4 LOCAL INTELLIGENCE
# ===================================================================

@app.get("/models/available")
async def list_available_models():
    """List all available Gemma 4 models with specs."""
    from backend.modules.model_manager import get_model_manager
    return {"models": get_model_manager().get_available_models()}


@app.get("/models/installed")
async def list_installed_models():
    """List all installed models across all providers."""
    from backend.modules.model_manager import get_model_manager
    manager = get_model_manager()
    models = await manager.list_all_models()
    return {
        "models": [
            {
                "name": m.name, "family": m.family, "size": m.size,
                "provider": m.provider, "status": m.status,
                "modified_at": m.modified_at,
            }
            for m in models
        ],
    }


@app.get("/models/status")
async def model_status(model: str = None):
    """Get status of a specific model or the default model."""
    from backend.modules.model_manager import get_model_manager
    manager = get_model_manager()
    model_name = model or manager.get_default_model()
    return await manager.get_model_status(model_name)


@app.post("/models/pull")
async def pull_model(model: str = None):
    """Pull a Gemma 4 model via Ollama. Default: gemma4:12b."""
    from backend.modules.model_manager import get_model_manager
    manager = get_model_manager()
    model_name = model or manager.get_default_model()
    return await manager.pull_model(model_name)


@app.get("/models/recommend")
async def recommend_model():
    """Get recommended Gemma 4 model based on available system RAM."""
    from backend.modules.model_manager import get_model_manager
    return await get_model_manager().recommend_model()


@app.post("/models/setup")
async def setup_gemma4(model_size: str = "12b"):
    """One-click Gemma 4 setup: detects provider and pulls recommended model."""
    from backend.modules.model_manager import get_model_manager
    return await get_model_manager().setup_gemma4(model_size)


@app.get("/models/providers")
async def check_providers():
    """Check which LLM providers are available (Ollama, llama.cpp)."""
    from backend.modules.model_manager import get_model_manager
    manager = get_model_manager()
    return {
        "ollama": await manager.is_ollama_running(),
        "llamacpp": await manager.is_llamacpp_running(),
        "recommended": "ollama",
    }


@app.get("/llm/config")
async def get_llm_config_info():
    """Get current LLM configuration with auto-detection."""
    from backend.core.llm_config import get_llm_config
    config = get_llm_config()
    return {
        "current": LATEST_LLM_CONFIG,
        "provider": config.detect_provider(),
        "default_model": "gemma4:12b",
        "system_info": config.get_system_info(),
    }


# ===================================================================
# PHASE 4 — Consensus Engine (Multi-Machine Decision Making)
# ===================================================================

from backend.modules.consensus_engine import ConsensusEngine
_consensus = ConsensusEngine()


class ConsensusProposeRequest(BaseModel):
    title: str
    description: str = ""
    options: List[str] = ["Yes", "No"]
    required_nodes: int = 3


@app.post("/consensus/propose")
async def consensus_propose(req: ConsensusProposeRequest):
    """Create a new consensus proposal for multi-node decision making."""
    return _consensus.create_proposal(req.title, req.description, req.options, req.required_nodes)


@app.get("/consensus/list")
async def consensus_list(status: Optional[str] = None):
    """List all consensus proposals."""
    return {"proposals": _consensus.list_proposals(status)}


@app.get("/consensus/proposal/{proposal_id}")
async def consensus_get(proposal_id: str):
    """Get details of a specific proposal."""
    return _consensus.get_proposal(proposal_id)


class ConsensusVoteRequest(BaseModel):
    proposal_id: str
    node_id: str
    choice: str
    confidence: float = 1.0
    reasoning: str = ""


@app.post("/consensus/vote")
async def consensus_vote(req: ConsensusVoteRequest):
    """Cast a vote on a proposal."""
    return _consensus.vote(req.proposal_id, req.node_id, req.choice, req.confidence, req.reasoning)


@app.get("/consensus/tally/{proposal_id}")
async def consensus_tally(proposal_id: str):
    """Tally votes for a proposal."""
    return _consensus.tally_votes(proposal_id)


@app.get("/consensus/check/{proposal_id}")
async def consensus_check(proposal_id: str):
    """Check if consensus has been reached."""
    return _consensus.check_consensus(proposal_id)


@app.post("/consensus/close/{proposal_id}")
async def consensus_close(proposal_id: str):
    """Close a proposal and record the result."""
    return _consensus.close_proposal(proposal_id)


# ===================================================================
# ENTRY POINT
# ===================================================================

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8001)
