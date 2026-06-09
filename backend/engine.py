"""
Jambubrowser Action Engine v2.0
===============================
Sovereign Autonomous Research Engine.
Connects the UI to the internet, memory, sandboxed execution,
credential vault, and AI reasoning.

Security Features:
- Privacy-first browser automation
- Request/response sanitization
- Audit logging for all actions
- Local-only mode enforcement
- Supply chain verification

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
- /privacy/report              : Privacy status report
- /audit/log                   : Audit trail access
- /security/verify             : Supply chain verification
"""

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Dict, Optional, Any
import uvicorn
import asyncio
import time
import hashlib
import json
import os
import uuid
import psutil
import re
import httpx
import xml.etree.ElementTree as ET
import importlib.util

# --- Importing Modular Parts ---
from backend.core.database import init_db, get_db, get_db_cursor, get_stats as db_stats, clear_memory
from backend.core.sandbox import execute_sandboxed
from backend.core.vault import get_vault
from backend.core.privacy import PrivacyMode, get_privacy_manager, sanitize_content_for_storage
from backend.core.audit import get_audit_logger, ActionCategory
from backend.core.supply_chain import get_verifier
from backend.modules.search import multi_engine_search, filter_trusted_results
from backend.modules.scraper import get_sovereign_crawler, get_scrape_config, is_special_media, get_special_content, scrape_url
from backend.modules.browser import SessionMode, PrivacyLevel, get_browser_manager

# ---- App Init ----

app = None  # Initialized after lifespan function definition

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8888/search")
GLOBAL_VPN_PROXY = os.environ.get("AGENT_VPN_PROXY", None)

LATEST_LLM_CONFIG = {
    "provider": "ollama",
    "baseUrl": "http://localhost:11434/v1",
    "modelId": "gemma4:12b-it-qat",
    "apiKey": "",
}

CLOUD_PROVIDERS = {
    "minimax": {
        "baseUrl": "https://api.minimax.io/v1",
        "modelId": "MiniMax-M2.7",
        "apiKey": os.environ.get("MINIMAX_API_KEY", ""),
    },
    "mlx": {
        "baseUrl": "http://127.0.0.1:8080/v1",
        "modelId": "gemma4:12b",
        "apiKey": "",
    },
}


def _resolve_llm_config(cfg: dict) -> dict:
    """Merge caller config with the matching cloud preset if provider is set."""
    merged = dict(LATEST_LLM_CONFIG)
    if cfg:
        merged.update({k: v for k, v in cfg.items() if v})
    provider = merged.get("provider", "ollama")
    if provider in CLOUD_PROVIDERS:
        merged.update(CLOUD_PROVIDERS[provider])
    return merged


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip_think(text: str) -> str:
    return _THINK_RE.sub("", text).strip()


async def _call_llm(prompt: str, system: Optional[str] = None, *, max_tokens: int = 500, temperature: float = 0.3, timeout: float = 10.0) -> tuple[str, dict]:
    """Unified LLM call. Returns (answer_text, usage_dict). Provider-aware."""
    cfg = _resolve_llm_config({})
    provider = cfg.get("provider", "ollama")
    if provider in ("local", "ollama"):
        provider = "ollama"
    base_url = cfg.get("baseUrl", "http://localhost:11434/v1").rstrip("/")
    model_id = cfg.get("modelId", "gemma4:12b-it-qat")
    api_key = cfg.get("apiKey", "")

    if provider == "none":
        raise RuntimeError("No LLM provider configured")

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with httpx.AsyncClient() as client:
        if provider == "ollama":
            health_url = f"{base_url.removesuffix('/v1')}/api/tags"
            try:
                health = await client.get(health_url, timeout=3.0)
                if health.status_code != 200:
                    raise RuntimeError(f"Ollama not responding at {health_url}")
            except httpx.ConnectError:
                raise RuntimeError(f"Ollama not available at {health_url}. Start Ollama and try again.")
            except httpx.TimeoutException:
                raise RuntimeError(f"Ollama timeout at {health_url}. Server may be starting.")

            url = f"{base_url.removesuffix('/v1')}/api/generate"
            payload = {
                "model": model_id,
                "prompt": (f"{system}\n\n{prompt}" if system else prompt),
                "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            }
        else:
            if provider == "mlx" and timeout < 60.0:
                timeout = 60.0  # MLX server may need more time for cold starts
            url = f"{base_url}/chat/completions"
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            payload = {"model": model_id, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}

        print(f"[LLM] → {provider} {model_id} prompt_len={len(prompt)} url={url}")
        resp = await client.post(url, json=payload, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            raise RuntimeError(f"LLM {provider} {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        if provider == "ollama":
            text = data.get("response", "")
            usage = {"prompt_tokens": data.get("prompt_eval_count", 0), "completion_tokens": data.get("eval_count", 0)}
        else:
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            usage = data.get("usage", {})

        stripped = _strip_think(text)
        print(f"[LLM] provider={provider} model={model_id} text_len={len(text)} stripped_len={len(stripped)} usage={usage}")
        return stripped, usage

START_TIME = time.time()

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
            except Exception as e:
                print(f"[ws] broadcast to {client_id} failed: {e!r}")
                self.disconnect(client_id)

    async def broadcast_all(self, message: str):
        for ws in list(self.active_connections.values()):
            try:
                await ws.send_text(message)
            except Exception as e:
                print(f"[ws] broadcast_all failed: {e!r}")


manager = ConnectionManager()


# ---- Agent State Tracking ----

active_tasks: Dict[str, str] = {}
cancel_flags: Dict[str, asyncio.Event] = {}
_task_token_starts: Dict[str, float] = {}
_task_token_counts: Dict[str, int] = {}


def safe_task(coro, label: str = "background") -> asyncio.Task:
    """Wrap a coroutine in a task that logs exceptions instead of swallowing them."""
    task = asyncio.create_task(coro)
    def _done(t: asyncio.Task) -> None:
        try:
            t.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            print(f"[safe_task:{label}] unhandled exception: {exc!r}")
    task.add_done_callback(_done)
    return task


def _new_task_id() -> str:
    return uuid.uuid4().hex[:8]


async def broadcast_agent_state(client_id: str, state: str, zone: Optional[str] = None) -> None:
    payload = {
        "type": "agent.state",
        "state": state,
        "zone": zone,
        "task_id": active_tasks.get(client_id),
        "timestamp": time.time(),
    }
    await manager.broadcast_all(json.dumps(payload))


async def broadcast_agent_telemetry(
    client_id: str,
    action: str,
    file_path: Optional[str] = None,
    tokens_generated: Optional[int] = None,
    tokens_per_sec: Optional[float] = None,
    context_size: Optional[int] = None,
) -> None:
    payload = {
        "type": "agent.telemetry",
        "model": LATEST_LLM_CONFIG.get("modelId", "gemma4:12b-it-qat"),
        "action": action,
        "file_path": file_path,
        "tokens_generated": tokens_generated,
        "tokens_per_sec": tokens_per_sec,
        "context_size": context_size,
        "timestamp": time.time(),
    }
    await manager.broadcast_all(json.dumps(payload))


async def broadcast_agent_reasoning(client_id: str, delta: str) -> None:
    payload = {
        "type": "agent.reasoning",
        "delta": delta,
        "task_id": active_tasks.get(client_id),
        "timestamp": time.time(),
    }
    await manager.broadcast_all(json.dumps(payload))


async def broadcast_task_start(client_id: str, query: str, task_id: str) -> None:
    active_tasks[client_id] = task_id
    cancel_flags[task_id] = asyncio.Event()
    _task_token_starts[task_id] = time.time()
    _task_token_counts[task_id] = 0
    payload = {
        "type": "agent.task_start",
        "task_id": task_id,
        "query": query,
        "timestamp": time.time(),
    }
    await manager.broadcast_all(json.dumps(payload))


async def broadcast_task_end(
    client_id: str,
    task_id: str,
    status: str,
    result_preview: Optional[str] = None,
) -> None:
    elapsed = time.time() - _task_token_starts.get(task_id, time.time())
    final_tokens = _task_token_counts.get(task_id, 0)
    tps = (final_tokens / elapsed) if elapsed > 0 and final_tokens > 0 else None
    payload = {
        "type": "agent.task_end",
        "task_id": task_id,
        "status": status,
        "result_preview": (result_preview[:200] if result_preview else None),
        "tokens_generated": final_tokens,
        "tokens_per_sec": round(tps, 2) if tps else None,
        "elapsed_sec": round(elapsed, 2),
        "timestamp": time.time(),
    }
    await manager.broadcast_all(json.dumps(payload))
    if active_tasks.get(client_id) == task_id:
        active_tasks.pop(client_id, None)
    cancel_flags.pop(task_id, None)
    _task_token_starts.pop(task_id, None)
    _task_token_counts.pop(task_id, None)


def is_cancelled(task_id: Optional[str]) -> bool:
    if not task_id:
        return False
    flag = cancel_flags.get(task_id)
    return bool(flag and flag.is_set())


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
    llm_provider: str = "ollama"


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

    tasks.append(safe_task(memory_audit(), "memory_audit"))
    tasks.append(safe_task(curiosity_loop(), "curiosity_loop"))

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

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:1420", "http://localhost:3000", "http://localhost:5173", "tauri://localhost"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi import Request
from fastapi.responses import JSONResponse

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler for unhandled errors."""
    print(f"[ERROR] Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "error": str(exc),
            "path": request.url.path
        }
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """HTTP exception handler with consistent error format."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "path": request.url.path
        }
    )

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


@app.websocket("/ws/audit")
async def audit_websocket(websocket: WebSocket):
    """WebSocket endpoint for live audit log updates."""
    await websocket.accept()
    try:
        # Send current audit stats
        audit_logger = get_audit_logger()
        stats = audit_logger.get_statistics()
        await websocket.send_json({"type": "stats", "data": stats})
        
        # Keep connection alive and send periodic updates
        while True:
            # Wait for client messages (ping/pong)
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # Send updated stats every 30 seconds
                stats = audit_logger.get_statistics()
                await websocket.send_json({"type": "stats", "data": stats})
    except Exception as e:
        print(f"[ws] audit connection closed: {e}")


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
# PRIVACY & SECURITY ENDPOINTS
# ===================================================================

@app.get("/privacy/report")
async def privacy_report():
    """
    Get comprehensive privacy report.
    Shows all privacy protections and their status.
    """
    privacy_mgr = get_privacy_manager()
    audit_logger = get_audit_logger()

    return {
        "privacy": privacy_mgr.get_privacy_report(),
        "audit": audit_logger.get_statistics(),
        "vault_status": "locked" if get_vault().is_locked else "unlocked",
    }


@app.get("/privacy/check")
async def check_url_privacy(url: str):
    """Check if a URL is allowed under current privacy mode."""
    privacy_mgr = get_privacy_manager()
    allowed = privacy_mgr.check_url_allowed(url)

    return {
        "url": url,
        "allowed": allowed,
        "mode": privacy_mgr.mode.value,
    }


@app.get("/audit/stats")
async def audit_stats():
    """Get audit statistics."""
    audit_logger = get_audit_logger()
    return audit_logger.get_statistics()


@app.get("/audit/log")
async def audit_log(category: str = None, limit: int = 100):
    """Get audit log entries."""
    audit_logger = get_audit_logger()
    entries = audit_logger.get_entries(category=category, limit=limit)
    return {"entries": entries, "total": len(entries)}


@app.get("/audit/verify")
async def verify_audit_chain():
    """Verify the integrity of the audit log chain."""
    audit_logger = get_audit_logger()
    is_valid, message = audit_logger.verify_chain_integrity()
    return {"valid": is_valid, "message": message}


@app.get("/security/verify")
async def verify_security():
    """Verify supply chain integrity."""
    verifier = get_verifier()
    report = verifier.get_verification_report()
    return report


@app.get("/security/verify/package")
async def verify_package(package_name: str):
    """Verify a specific package's integrity."""
    verifier = get_verifier()
    info = verifier.verify_package(package_name)
    return {
        "name": info.name,
        "version": info.version,
        "verified": info.verified,
        "hash": info.actual_hash[:16] + "..." if info.actual_hash else None,
    }


@app.get("/browser/privacy")
async def browser_privacy_summary():
    """Get privacy summary of all browser sessions."""
    manager = get_browser_manager()
    return manager.get_privacy_summary()


class PrivacyModeRequest(BaseModel):
    mode: str = "enhanced"


@app.post("/privacy/mode")
async def set_privacy_mode(req: PrivacyModeRequest):
    """Set the privacy mode for new sessions."""
    try:
        mode = PrivacyMode(req.mode)
        privacy_mgr = get_privacy_manager(mode)
        return {
            "success": True,
            "mode": mode.value,
            "message": f"Privacy mode set to {mode.value}",
        }
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid privacy mode: {req.mode}. Use: standard, enhanced, maximum, local_only"
        )


class InterruptRequest(BaseModel):
    new_instruction: str = ""
    client_id: str = "default"


@app.post("/interrupt/{task_id}")
async def interrupt_task(task_id: str, req: InterruptRequest):
    """Cancel the active task and optionally inject a new instruction."""
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


# ===================================================================
# RESEARCH ENDPOINTS
# ===================================================================

@app.post("/research")
async def research(req: ResearchRequest):
    """Primary autonomous research endpoint with swarm, scrape, and RAG."""
    cid = req.client_id
    global last_activity, LATEST_LLM_CONFIG
    last_activity = time.time()

    task_id = _new_task_id()
    await broadcast_task_start(cid, req.query, task_id)

    try:
        if req.llm_provider and req.llm_provider != "ollama":
            preset = CLOUD_PROVIDERS.get(req.llm_provider, {})
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

        # Step 1: Expand search queries
        expanded = await _expand_query(req.query, cid, req.llm_config)

        # Step 2: Multi-engine search with fallback
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
            # Use multi_engine_search with DuckDuckGo fallback
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
                    print(f"[search] error for query '{q}': {e!r}")
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
                    except Exception as e:
                        print(f"[fetch] {url} failed: {e!r}")
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
                from backend.core.vector_search import search_similar
                rows = search_similar(query_vec, k=8)
        except ImportError:
            rows = []
            for item in crawled:
                rows.append((item["markdown"][:500], item["url"]))

        # Step 6: LLM Synthesis — generate proper response from context
        context_text = "\n\n".join([f"Source: {r[1]}\n{r[0]}" for r in rows])
        sources_list = list(set([r[1] for r in rows]))
        answer = context_text[:500] if context_text else "No results found."

        if is_cancelled(task_id):
            await broadcast_task_end(cid, task_id, status="cancelled")
            return {"answer": "[INTERRUPTED]", "context": "", "sources": [], "doc_count": 0}

        if context_text and rows:
            await broadcast_agent_state(cid, "writing", zone="desk")
            provider_label = _resolve_llm_config({}).get("provider", "ollama")
            await broadcast_agent_telemetry(
                cid,
                action=f"Synthesizing answer via {provider_label}",
                context_size=len(context_text) // 4,
            )
            try:
                answer_text, usage = await _call_llm(
                    prompt=f"Based on this research context, provide a concise, well-structured answer to: '{req.query}'\n\nContext:\n{context_text[:3000]}",
                    max_tokens=500,
                    temperature=0.3,
                    timeout=30.0,
                )
                if answer_text:
                    answer = answer_text
                completion_tokens = usage.get("completion_tokens", 0) or len(answer.split())
                _task_token_counts[task_id] = _task_token_counts.get(task_id, 0) + completion_tokens
                elapsed = time.time() - _task_token_starts.get(task_id, time.time())
                tps = _task_token_counts[task_id] / elapsed if elapsed > 0 else 0
                await broadcast_agent_telemetry(
                    cid,
                    action="LLM synthesis complete",
                    tokens_generated=_task_token_counts[task_id],
                    tokens_per_sec=round(tps, 2),
                    context_size=len(context_text) // 4,
                )
                await broadcast_agent_reasoning(cid, answer[:160])
                await manager.broadcast(req.client_id, f"🧠 LLM synthesis complete ({provider_label}).")
            except Exception as e:
                await manager.broadcast(req.client_id, f"⚠️ LLM call failed: {str(e)[:120]}")

        await broadcast_task_end(cid, task_id, status="completed", result_preview=answer)

        return {
            "answer": answer,
            "context": context_text,
            "sources": sources_list,
            "doc_count": len(crawled),
        }

    except Exception as e:
        await broadcast_task_end(cid, task_id, status="failed", result_preview=str(e))
        await broadcast_agent_state(cid, "error")
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
    """Single-page scraping endpoint with audit logging."""
    audit = get_audit_logger()
    
    # Log the scrape attempt
    audit.log(
        category=ActionCategory.BROWSER,
        action="scrape",
        details={
            "url": req.url,
            "query": req.query,
        },
        session_id=req.client_id if hasattr(req, 'client_id') else None,
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
            
            # Log successful scrape
            audit.log(
                category=ActionCategory.BROWSER,
                action="scrape_success",
                details={
                    "url": req.url,
                    "content_length": len(sanitized_content),
                    "pii_removed": len(sanitization_result.pii_removed),
                    "engine": "crawl4ai",
                },
                session_id=req.client_id if hasattr(req, 'client_id') else None,
            )
            
            return {
                "success": True,
                "url": req.url,
                "markdown": sanitized_content,
                "title": result.metadata.get("title", "") if result.metadata else "",
            }
        return {"success": False, "url": req.url, "error": "Failed to scrape page"}
    except ImportError:
        # Fallback to Playwright
        pass
    except Exception as e:
        # Log the error but continue to fallback
        print(f"crawl4ai error: {e}")
    
    # Playwright fallback
    try:
        from backend.modules.playwright_scraper import scrape_with_playwright
        
        result = await scrape_with_playwright(req.url)
        
        if result["success"]:
            content = result["content"]
            sanitized_content, sanitization_result = sanitize_content_for_storage(content)
            
            # Log successful scrape
            audit.log(
                category=ActionCategory.BROWSER,
                action="scrape_success",
                details={
                    "url": req.url,
                    "content_length": len(sanitized_content),
                    "pii_removed": len(sanitization_result.pii_removed),
                    "engine": "playwright",
                },
                session_id=req.client_id if hasattr(req, 'client_id') else None,
            )
            
            return {
                "success": True,
                "url": req.url,
                "markdown": sanitized_content,
                "title": result.get("title", ""),
            }
        return {"success": False, "url": req.url, "error": result.get("error", "Failed to scrape page")}
    except Exception as e:
        # Log the error
        audit.log(
            category=ActionCategory.ERROR,
            action="scrape_error",
            details={"url": req.url, "error": str(e)},
            session_id=req.client_id if hasattr(req, 'client_id') else None,
        )
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
    if not req.code or not req.code.strip():
        return {"success": False, "output": "", "error": "Empty code - nothing to execute",
                "execution_time": 0, "exit_code": -1, "sandbox_type": "subprocess"}
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
    """Execute browser actions (click, type, scroll, click_xy) with audit logging."""
    audit = get_audit_logger()
    
    # Log the action attempt
    audit.log(
        category=ActionCategory.BROWSER,
        action="perform_actions",
        details={
            "url": req.url,
            "steps_count": len(req.steps),
            "actions": [step.action for step in req.steps],
        },
        session_id=req.client_id,
    )
    
    # Convert steps to action dicts
    actions = []
    for step in req.steps:
        action_dict = {"action": step.action}
        if hasattr(step, 'selector') and step.selector:
            action_dict["selector"] = step.selector
        if hasattr(step, 'value') and step.value:
            action_dict["value"] = step.value
        if hasattr(step, 'x') and step.x is not None:
            action_dict["x"] = step.x
        if hasattr(step, 'y') and step.y is not None:
            action_dict["y"] = step.y
        actions.append(action_dict)
    
    # Try crawl4ai first, fallback to Playwright
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
            
            # Sanitize content before returning
            content = result.markdown[:10000] if result.success else ""
            sanitized_content, sanitization_result = sanitize_content_for_storage(content)
            
            # Log successful action
            audit.log(
                category=ActionCategory.BROWSER,
                action="perform_actions_success",
                details={
                    "url": req.url,
                    "content_length": len(sanitized_content),
                    "pii_removed": len(sanitization_result.pii_removed),
                    "engine": "crawl4ai",
                },
                session_id=req.client_id,
            )
            
            return {"status": "success", "markdown": sanitized_content}
    except ImportError:
        # Fallback to Playwright
        pass
    except Exception as e:
        # Log the error but continue to fallback
        print(f"crawl4ai error: {e}")
    
    # Playwright fallback
    try:
        from backend.modules.playwright_scraper import perform_actions_with_playwright
        
        result = await perform_actions_with_playwright(req.url, actions)
        
        if result["success"]:
            content = result["content"]
            sanitized_content, sanitization_result = sanitize_content_for_storage(content)
            
            # Log successful action
            audit.log(
                category=ActionCategory.BROWSER,
                action="perform_actions_success",
                details={
                    "url": req.url,
                    "content_length": len(sanitized_content),
                    "pii_removed": len(sanitization_result.pii_removed),
                    "engine": "playwright",
                },
                session_id=req.client_id,
            )
            
            return {"status": "success", "markdown": sanitized_content}
        return {"status": "error", "message": result.get("error", "Failed to perform actions")}
    except Exception as e:
        # Log the error
        audit.log(
            category=ActionCategory.ERROR,
            action="perform_actions_error",
            details={"url": req.url, "error": str(e)},
            session_id=req.client_id,
        )
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
    audit = get_audit_logger()
    
    # Log the login attempt
    audit.log(
        category=ActionCategory.CREDENTIAL,
        action="login_attempt",
        details={
            "url": req.url,
            "username": req.username,
        },
        session_id=req.client_id,
    )
    
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

            # Log successful login
            audit.log(
                category=ActionCategory.CREDENTIAL,
                action="login_success",
                details={
                    "domain": domain,
                    "url": req.url,
                },
                session_id=req.client_id,
            )

            return {
                "status": "success",
                "domain": domain,
                "message": f"Login attempted for {domain}",
                "page_title": result.metadata.get("title", "") if result.success and result.metadata else "",
            }
        except ImportError:
            return {"status": "success", "domain": domain, "message": f"Credential stored for {domain}. Login automation requires crawl4ai."}
    except Exception as e:
        # Log the error
        audit.log(
            category=ActionCategory.ERROR,
            action="login_error",
            details={"url": req.url, "error": str(e)},
            session_id=req.client_id,
        )
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


class VaultUnlockRequest(BaseModel):
    master_password: str = ""


@app.post("/vault/unlock")
async def vault_unlock(req: VaultUnlockRequest):
    """Unlock the credential vault with master password."""
    vault = get_vault()
    success = vault.unlock(req.master_password)
    if success:
        return {"success": True, "message": "Vault unlocked"}
    return {"success": False, "error": "Invalid password or vault is locked out"}


@app.post("/vault/lock")
async def vault_lock():
    """Lock the credential vault."""
    vault = get_vault()
    vault.lock()
    return {"success": True, "message": "Vault locked"}


@app.get("/vault/status")
async def vault_status():
    """Get vault lock status."""
    vault = get_vault()
    return {
        "locked": vault.is_locked,
        "access_log": vault.get_access_log()[-10:],
    }


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
            from backend.core.vector_search import search_similar
            rows = search_similar(query_vec, k=10)

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
        from backend.core.vector_search import search_similar, is_sqlite_vec_available

        model = SentenceTransformer("all-MiniLM-L6-v2")
        query_vec = model.encode(query).astype(np.float32).tobytes()

        # Use vector_search module which handles both sqlite-vec and fallback
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
                print(f"[brain_only] LLM synthesis failed: {e!r}")

        return {
            "answer": answer,
            "context": context_text,
            "sources": sources_list,
            "doc_count": 0,
        }
    except ImportError:
        return {"answer": "", "context": "", "sources": [], "doc_count": 0}


async def _expand_query(query: str, client_id: str, llm_config: dict) -> list:
    """Use LLM to generate diverse search queries."""
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
    if not req.url or not req.url.startswith(("http://", "https://")):
        return {"url": req.url, "risk_level": "invalid", "blocked": True,
                "consensus_score": 1.0, "reason": "Invalid or empty URL",
                "checks": [{"source": "heuristic", "risk_level": "invalid", "score": 1.0,
                           "details": "URL is empty or has invalid scheme"}]}
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
    safe_task(scheduler.run_loop(), "scheduler")
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
    safe_task(shadow.run_loop(), "shadow")
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
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(req.url, timeout=10.0, follow_redirects=True)
                    html = resp.text
            except Exception:
                return {"url": req.url, "forms_found": 0, "forms": [], "error": "Could not fetch URL"}
        else:
            html = req.html
        if not html or not html.strip():
            return {"url": req.url, "forms_found": 0, "forms": []}
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
# PHASE 2 — COMPUTER USE LAYER
# OS-level control: screen capture, mouse, keyboard, app launch
# ===================================================================

import subprocess, base64, platform

@app.get("/computer/capture")
async def computer_capture(region: str = "full"):
    """Capture screen region. region: 'full' | 'frontmost'.
    Returns base64-encoded PNG. Requires macOS accessibility permissions."""
    import tempfile, os
    if platform.system() != "Darwin":
        return {"error": "Screen capture only supported on macOS"}
    tmp = tempfile.mktemp(suffix=".png")
    try:
        if region == "frontmost":
            result = subprocess.run(["screencapture", "-x", "-l", 
                          str(_get_frontmost_window_id()), tmp],
                         capture_output=True, timeout=10)
        else:
            result = subprocess.run(["screencapture", "-x", tmp],
                         capture_output=True, timeout=10)
        
        if result.returncode != 0:
            return {"error": "Screen capture failed. Grant accessibility permissions in System Settings → Privacy & Security → Accessibility",
                    "details": result.stderr.decode() if result.stderr else ""}
        
        if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
            return {"error": "Screen capture produced no output. Check accessibility permissions."}
        
        with open(tmp, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        return {"success": True, "image_data": data, "format": "png", "region": region}
    except PermissionError:
        return {"error": "Permission denied. Grant accessibility permissions in System Settings → Privacy & Security → Accessibility"}
    except subprocess.TimeoutExpired:
        return {"error": "Screen capture timed out"}
    except Exception as e:
        return {"error": f"Screen capture failed: {str(e)}"}
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _get_frontmost_window_id() -> int:
    """Get the window ID of the frontmost application."""
    try:
        result = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to get id of first window of first process whose frontmost is true'],
            capture_output=True, text=True, timeout=5
        )
        return int(result.stdout.strip())
    except Exception:
        return 0


@app.post("/computer/mouse")
async def computer_mouse(action: str, x: int = 0, y: int = 0, button: str = "left"):
    """Mouse control. action: 'move' | 'click' | 'doubleclick' | 'rightclick' | 'drag'.
    Uses macOS osascript (no external deps)."""
    if platform.system() != "Darwin":
        return {"error": "Mouse control only supported on macOS"}
    
    if action == "move":
        script = f'tell application "System Events" to set position of front window to {{{x}, {y}}}'
    elif action == "click":
        script = f'do shell script "cliclick c:{x},{y}"'
    elif action == "doubleclick":
        script = f'do shell script "cliclick dc:{x},{y}"'
    elif action == "rightclick":
        script = f'do shell script "cliclick rc:{x},{y}"'
    elif action == "drag":
        script = f'do shell script "cliclick dd:{x},{y} du:{x+100},{y+100}"'
    else:
        return {"error": f"Unknown action: {action}"}
    
    try:
        result = subprocess.run(["osascript", "-e", script],
                              capture_output=True, text=True, timeout=10)
        return {"success": result.returncode == 0, "action": action, "x": x, "y": y}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/computer/keyboard")
async def computer_keyboard(text: str = "", key: str = "", modifiers: list = []):
    """Keyboard input. Use 'text' to type strings, 'key' for special keys.
    modifiers: ['command', 'shift', 'option', 'control']"""
    if platform.system() != "Darwin":
        return {"error": "Keyboard control only supported on macOS"}
    
    if text:
        # Type text character by character via clipboard (faster than keystroke)
        script = f'''
        set the clipboard to "{text}"
        tell application "System Events" to keystroke "v" using command down
        '''
    elif key:
        mod_str = " using {" + ", ".join(f"{m} down" for m in (modifiers or [])) + "}" if modifiers else ""
        script = f'tell application "System Events" to key code {_key_to_code(key)}{mod_str}'
    else:
        return {"error": "Provide 'text' or 'key'"}
    
    try:
        result = subprocess.run(["osascript", "-e", script],
                              capture_output=True, text=True, timeout=10)
        return {"success": result.returncode == 0, "text": text, "key": key}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _key_to_code(key: str) -> int:
    """Map key names to macOS key codes."""
    codes = {
        "return": 36, "enter": 76, "tab": 48, "space": 49,
        "delete": 51, "escape": 53, "up": 126, "down": 125,
        "left": 123, "right": 124, "home": 115, "end": 119,
        "pageup": 116, "pagedown": 121, "f1": 122, "f2": 120,
        "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98,
        "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
    }
    return codes.get(key.lower(), 0)


@app.post("/computer/launch")
async def computer_launch(app_name: str):
    """Launch a macOS application by name."""
    if platform.system() != "Darwin":
        return {"error": "App launch only supported on macOS"}
    try:
        subprocess.Popen(["open", "-a", app_name])
        return {"success": True, "app": app_name}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/computer/apps")
async def computer_list_apps():
    """List installed macOS applications."""
    if platform.system() != "Darwin":
        return {"error": "App listing only supported on macOS"}
    try:
        result = subprocess.run(
            ["mdfind", "kMDItemKind == 'Application'"],
            capture_output=True, text=True, timeout=10
        )
        apps = [line.split("/")[-1].replace(".app", "") 
                for line in result.stdout.strip().split("\n") if line]
        return {"apps": sorted(apps)[:50], "total": len(apps)}
    except Exception as e:
        return {"error": str(e)}


# ===================================================================
# PHASE 3 — VISION ENGINE
# OCR, UI element detection, screen state verification
# ===================================================================

class VisionOCRRequest(BaseModel):
    image_data: str  # base64-encoded image
    language: str = "eng"

class VisionUIRequest(BaseModel):
    image_data: str  # base64-encoded image

class VisionVerifyRequest(BaseModel):
    image_data: str  # base64-encoded image
    expected: str    # what we expect to see


@app.post("/vision/ocr")
async def vision_ocr(req: VisionOCRRequest):
    """Extract all text from a screenshot using LLM vision.
    Sends image to local Ollama LLM for text extraction."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{LATEST_LLM_CONFIG['baseUrl']}/chat/completions",
                json={
                    "model": LATEST_LLM_CONFIG["modelId"],
                    "messages": [
                        {"role": "system", "content": "Extract ALL readable text from this image. Return only the text, preserving layout."},
                        {"role": "user", "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{req.image_data}"}},
                            {"type": "text", "text": "Extract all text from this image."}
                        ]}
                    ],
                    "stream": False
                },
                timeout=60
            )
        result = response.json()
        text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        return {"text": text, "language": req.language}
    except Exception as e:
        return {"text": "", "error": str(e)}


@app.post("/vision/ui-elements")
async def vision_ui_elements(req: VisionUIRequest):
    """Detect UI elements (buttons, fields, links) in a screenshot.
    Returns structured JSON of interactive elements."""
    try:
        response = httpx.post(
            f"{LATEST_LLM_CONFIG['baseUrl']}/chat/completions",
            json={
                "model": LATEST_LLM_CONFIG["modelId"],
                "messages": [
                    {"role": "system", "content": """Identify all interactive UI elements in this screenshot. 
Return as JSON array: [{"type": "button|textfield|link|dropdown|checkbox", "label": "...", "bounds": [x,y,w,h], "action": "what it does"}]
Only include elements you can clearly identify."""},
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{req.image_data}"}},
                        {"type": "text", "text": "Identify all interactive UI elements."}
                    ]}
                ],
                "stream": False
            },
            timeout=60
        )
        result = response.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "[]")
        # Try to parse JSON from response
        import json
        try:
            elements = json.loads(content)
        except json.JSONDecodeError:
            elements = [{"raw": content}]
        return {"elements": elements, "count": len(elements)}
    except Exception as e:
        return {"elements": [], "error": str(e)}


@app.post("/vision/verify")
async def vision_verify(req: VisionVerifyRequest):
    """Verify screen state matches expected description.
    Returns match confidence and actual vs expected."""
    try:
        response = httpx.post(
            f"{LATEST_LLM_CONFIG['baseUrl']}/chat/completions",
            json={
                "model": LATEST_LLM_CONFIG["modelId"],
                "messages": [
                    {"role": "system", "content": """Compare the expected screen state with what you actually see.
Return JSON: {"match": true/false, "confidence": 0.0-1.0, "actual": "what you see", "differences": ["list of differences"]}"""},
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{req.image_data}"}},
                        {"type": "text", "text": f"Expected: {req.expected}\n\nDoes the screen match this description?"}
                    ]}
                ],
                "stream": False
            },
            timeout=60
        )
        result = response.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        import json
        try:
            verification = json.loads(content)
        except json.JSONDecodeError:
            verification = {"match": False, "raw": content}
        return verification
    except Exception as e:
        return {"match": False, "error": str(e)}


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
    safe_task(p2p.run_discovery_loop(), "p2p_discovery")
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
        "success": True,
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
        "success": True,
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
    """Check which LLM providers are available (Ollama, llama.cpp, MLX)."""
    from backend.modules.model_manager import get_model_manager
    from backend.modules.mlx_provider import is_mlx_available, is_mlx_server_running
    manager = get_model_manager()
    mlx_avail = is_mlx_available()
    return {
        "ollama": await manager.is_ollama_running(),
        "llamacpp": await manager.is_llamacpp_running(),
        "mlx": mlx_avail,
        "mlx_server": is_mlx_server_running() if mlx_avail else False,
        "recommended": "mlx" if mlx_avail else "ollama",
    }


@app.get("/mlx/status")
async def mlx_status():
    """Get MLX provider status."""
    from backend.modules.mlx_provider import get_provider_info, is_mlx_available
    if not is_mlx_available():
        return {"available": False, "message": "mlx-lm not installed. Run: pip install mlx-lm"}
    return get_provider_info()


@app.post("/mlx/server/start")
async def mlx_start(model: str = "gemma4:12b", port: int = 8080):
    """Start MLX LM server."""
    from backend.modules.mlx_provider import mlx_start_server, is_mlx_available
    if not is_mlx_available():
        raise HTTPException(status_code=400, detail="mlx-lm not installed. Run: pip install mlx-lm")
    result = await mlx_start_server(model=model, port=port)
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message", "Failed to start MLX server"))
    return result


@app.post("/mlx/server/stop")
async def mlx_stop():
    """Stop MLX LM server."""
    from backend.modules.mlx_provider import mlx_stop_server
    return mlx_stop_server()


@app.get("/mlx/models")
async def mlx_models():
    """List available MLX models (both definitions and locally cached)."""
    from backend.modules.mlx_provider import get_available_mlx_models, mlx_list_cached_models
    return {
        "available": get_available_mlx_models(),
        "cached": await mlx_list_cached_models(),
    }


@app.post("/mlx/models/download")
async def mlx_download(model_id: str = "gemma4:12b"):
    """Download an MLX model from HuggingFace."""
    from backend.modules.mlx_provider import mlx_download_model, is_mlx_available
    if not is_mlx_available():
        raise HTTPException(status_code=400, detail="mlx-lm not installed")
    result = await mlx_download_model(model_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message", "Download failed"))
    return result


@app.post("/mlx/generate")
async def mlx_generate_endpoint(prompt: str, system: Optional[str] = None, model: str = "gemma4:12b", max_tokens: int = 500, temperature: float = 0.3):
    """Direct MLX inference without server."""
    from backend.modules.mlx_provider import mlx_generate, is_mlx_available
    if not is_mlx_available():
        raise HTTPException(status_code=400, detail="mlx-lm not installed")
    text, usage = await mlx_generate(prompt=prompt, system=system, model=model, max_tokens=max_tokens, temperature=temperature)
    return {"response": text, "usage": usage}


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
    return await _consensus.create_proposal(req.title, req.description, req.options, req.required_nodes)


@app.get("/consensus/list")
async def consensus_list(status: Optional[str] = None):
    """List all consensus proposals."""
    return {"proposals": _consensus.list_proposals(status)}


@app.get("/consensus/proposal/{proposal_id}")
async def consensus_get(proposal_id: str):
    """Get details of a specific proposal."""
    result = _consensus.get_proposal(proposal_id)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error", "Proposal not found"))
    return result


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
# HARNESS BRIDGE - MULTI-AGENT ORCHESTRATION
# ===================================================================

@app.get("/harness/status")
async def harness_status():
    """Check Harness gateway availability and list connectors."""
    from backend.modules.harness_bridge import get_harness_bridge
    return await get_harness_bridge().get_status()


@app.post("/harness/research")
async def harness_research(query: str, use_swarm: bool = True, domain: str = "general"):
    """Delegate research to Harness multi-agent swarm."""
    from backend.modules.harness_bridge import get_harness_bridge
    bridge = get_harness_bridge()
    if not await bridge.is_available():
        return {"status": "unavailable", "fallback": True,
                "message": "Harness not running. Using built-in research engine."}
    return await bridge.jambu_research(query, use_swarm, domain)


@app.post("/harness/research/single")
async def harness_research_single(query: str, connector: str = "hermes"):
    """Delegate research to a single Harness connector."""
    from backend.modules.harness_bridge import get_harness_bridge
    return await get_harness_bridge().research_single(query, connector)


@app.post("/harness/browse")
async def harness_browse(url: str, action: str = "scrape",
                          selector: str = None, value: str = None):
    """Use Harness Playwright MCP for browser automation."""
    from backend.modules.harness_bridge import get_harness_bridge
    return await get_harness_bridge().browse(url, action, selector, value)


@app.post("/harness/llm")
async def harness_llm(prompt: str, model: str = "gemma4:12b",
                       temperature: float = 0.7):
    """Send LLM request through Harness bridge (local + cloud models)."""
    from backend.modules.harness_bridge import get_harness_bridge
    return await get_harness_bridge().llm_chat(prompt, model, temperature=temperature)


@app.post("/harness/context/store")
async def harness_store_context(key: str, value: str, tags: str = ""):
    """Store context in Harness shared memory."""
    from backend.modules.harness_bridge import get_harness_bridge
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    return await get_harness_bridge().store_context(key, value, tag_list)


@app.post("/harness/context/search")
async def harness_search_context(query: str):
    """Search Harness shared memory for relevant context."""
    from backend.modules.harness_bridge import get_harness_bridge
    return await get_harness_bridge().search_context(query)


# ===================================================================
# PHASE 1 — Harness Gateway Compatibility Layer
# Bridges Jambubrowser with Harness_App's meta-orchestrator
# ===================================================================

from backend.core.database import (
    memory_add, memory_search, memory_list, memory_delete,
    session_create, session_update, session_list, session_get,
    record_task_metric, record_tool_usage, get_analytics_summary,
)
from backend.modules.search import multi_engine_search
from backend.modules.skill_synthesizer import get_synthesizer
import uuid

# ── Pydantic Models ──────────────────────────────────────────────────────

class RunRequest(BaseModel):
    prompt: str
    tool: Optional[str] = None
    session_id: Optional[str] = None

class RunStreamRequest(BaseModel):
    prompt: str
    tool: Optional[str] = None
    session_id: Optional[str] = None

class MemoryEntry(BaseModel):
    category: str = "general"
    key: str
    value: str
    importance: Optional[float] = 0.5

class MemorySearch(BaseModel):
    query: str
    limit: Optional[int] = 10

class SessionCreate(BaseModel):
    name: Optional[str] = None

# ── /v1/run — Harness-compatible task execution ──────────────────────────

@app.post("/v1/run")
async def v1_run(req: RunRequest):
    """
    Harness Gateway compatibility: execute a task.
    Maps prompt to best Jambubrowser endpoint, returns structured result.
    """
    start = time.time()
    session_id = req.session_id or str(uuid.uuid4())

    # Create or update session
    session_create(session_id, f"Task-{session_id[:8]}")

    # Route prompt to best endpoint
    prompt_lower = req.prompt.lower()
    task_id = f"task_{uuid.uuid4().hex[:8]}"

    if any(kw in prompt_lower for kw in ["search", "find", "look up"]):
        endpoint, method = "/search", "POST"
        result = await multi_engine_search(req.prompt)
        status = "success"
    elif any(kw in prompt_lower for kw in ["scrape", "fetch", "get content from"]):
        endpoint, method = "/scrape", "POST"
        import re
        url_match = re.search(r"https?://[^\s]+", req.prompt)
        url = url_match.group(0) if url_match else "https://example.com"
        result = await scrape_url(url, req.prompt)
        status = "success"
    elif any(kw in prompt_lower for kw in ["remember", "know", "learned", "stored"]):
        endpoint, method = "/v1/memory", "POST"
        result = memory_search(req.prompt, limit=10)
        status = "success"
    elif any(kw in prompt_lower for kw in ["research", "analyze", "investigate"]):
        endpoint, method = "/research", "POST"
        result = await multi_engine_search(req.prompt)
        status = "success"
    else:
        # Default: exec via sandbox
        endpoint, method = "/exec", "POST"
        result = await execute_sandboxed(req.prompt, 30)
        status = "success"

    duration_ms = int((time.time() - start) * 1000)

    # Record metrics
    record_task_metric(endpoint, method, status, duration_ms, session_id=session_id)
    session_update(session_id, [endpoint], 1, duration_ms)

    return {
        "session_id": session_id,
        "tasks": [{
            "task_id": task_id,
            "status": status,
            "output": result,
            "error": None,
            "duration_ms": duration_ms
        }]
    }


# ── /v1/run/stream — SSE streaming ───────────────────────────────────────

@app.post("/v1/run/stream")
async def v1_run_stream(req: RunStreamRequest):
    """
    Harness Gateway compatibility: SSE streaming task execution.
    Streams output token-by-token.
    """
    import asyncio

    async def event_stream():
        session_id = req.session_id or str(uuid.uuid4())
        session_create(session_id, f"Stream-{session_id[:8]}")

        yield f"data: {json.dumps({'type': 'session', 'session_id': session_id})}\n\n"
        yield f"data: {json.dumps({'type': 'start', 'session_id': session_id})}\n\n"

        start = time.time()

        # Route to endpoint (all are async, must await)
        prompt_lower = req.prompt.lower()
        if any(kw in prompt_lower for kw in ["search", "find"]):
            result = await multi_engine_search(req.prompt)
        elif any(kw in prompt_lower for kw in ["research", "analyze"]):
            result = await multi_engine_search(req.prompt)
        else:
            result = await execute_sandboxed(req.prompt, 30)

        # Stream chunks
        result_str = json.dumps(str(result))
        for i in range(0, len(result_str),100):
            chunk = result_str[i:i+100]
            yield f"data: {json.dumps({'type': 'chunk', 'data': chunk})}\n\n"
            await asyncio.sleep(0.01)

        duration_ms = int((time.time() - start) * 1000)
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        task_data = json.dumps({"task_id": task_id, "status": "success", "duration_ms": duration_ms})
        yield f"data: {json.dumps({'type': 'task', 'data': json.loads(task_data)})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


# ── /v1/memory — Harness-compatible memory ───────────────────────────────

@app.post("/v1/memory")
async def v1_memory_add(req: MemoryEntry):
    """Harness-compatible: add a memory entry."""
    entry_id = memory_add(req.category, req.key, req.value, req.importance or 0.5)
    return {"id": entry_id, "category": req.category, "key": req.key, "value": req.value}


@app.get("/v1/memory")
async def v1_memory_list(category: Optional[str] = None, limit: int = 50):
    """Harness-compatible: list memory entries."""
    return {"results": memory_list(category, limit)}


@app.post("/v1/memory/search")
async def v1_memory_search(req: MemorySearch):
    """Harness-compatible: FTS5 full-text memory search."""
    results = memory_search(req.query, req.limit or 10)
    return {"results": results}


@app.delete("/v1/memory/{entry_id}")
async def v1_memory_delete(entry_id: int):
    """Harness-compatible: delete a memory entry."""
    deleted = memory_delete(entry_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory entry not found")
    return {"deleted": True, "id": entry_id}


# ── /v1/sessions — Harness-compatible session management ────────────────

@app.get("/v1/sessions")
async def v1_sessions_list(limit: int = 20):
    """Harness-compatible: list recent sessions."""
    return {"sessions": session_list(limit)}


@app.get("/v1/sessions/{sid}")
async def v1_session_get(sid: str):
    """Harness-compatible: get session detail."""
    session = session_get(sid)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


# ── /v1/models — Harness-compatible model listing ───────────────────────

@app.get("/v1/models")
async def v1_models():
    """Harness-compatible: list available models."""
    return {
        "models": [
            {"id": "gemma4:12b", "object": "model", "owned_by": "local"},
            {"id": "llama3.1:8b", "object": "model", "owned_by": "local"},
            {"id": "mistral:7b", "object": "model", "owned_by": "local"},
        ]
    }


# ── /v1/connectors — Harness-compatible connector listing ────────────────

@app.get("/v1/connectors")
async def v1_connectors():
    """Harness-compatible: list connectors with health status."""
    return {
        "connectors": [
            {
                "name": "jambubrowser",
                "available": True,
                "capabilities": [
                    "research", "web_automation", "vision", "knowledge",
                    "browser_control", "local_compute", "credential_vault",
                    "p2p_federation", "multimodal", "skill_forge"
                ]
            }
        ]
    }


# ── /v1/health/detailed — Extended health ───────────────────────────────

@app.get("/v1/health/detailed")
async def v1_health_detailed():
    """Harness-compatible: detailed health with connector status."""
    uptime = int(time.time() - START_TIME) if START_TIME else 0
    return {
        "status": "ok",
        "version": "2.0.0",
        "uptime_s": uptime,
        "connectors": [
            {
                "name": "jambubrowser",
                "available": True,
                "capabilities": [
                    "research", "web_automation", "vision", "knowledge",
                    "browser_control", "local_compute", "credential_vault",
                    "p2p_federation", "multimodal", "skill_forge",
                    "consensus", "shadow_browser"
                ]
            }
        ]
    }


# ── /analytics/summary — Analytics endpoint ─────────────────────────────

@app.get("/analytics/summary")
async def analytics_summary(days: int = 7):
    """Return analytics summary (matches Harness's analytics engine)."""
    return get_analytics_summary(days)


@app.get("/benchmark")
async def benchmark():
    """Simple system benchmark for the browser-app frontend."""
    import time
    start = time.time()
    mem = psutil.virtual_memory()
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "ram_used_gb": round(mem.used / (1024**3), 2),
        "ram_total_gb": round(mem.total / (1024**3), 2),
        "response_time_ms": round((time.time() - start) * 1000, 2),
        "engine_version": "2.0.0",
    }


@app.get("/peers/tools")
async def peers_tools():
    """List tools available from peer nodes on the mesh."""
    return {"tools": []}


@app.get("/peers/tools/pull")
async def peers_tools_pull(name: str):
    """Pull a tool from a peer node on the mesh."""
    return {"status": "not_found", "name": name,
            "message": "No peers connected. Enable P2P discovery first."}


# ===================================================================
# PHASE 5 — PLUGIN SYSTEM
# Extensible task execution with sandboxed plugins
# ===================================================================

from backend.plugins.manager import get_plugin_manager, PluginResult

class PluginExecuteRequest(BaseModel):
    plugin_name: str
    params: Dict[str, Any] = {}
    timeout: int = 60

class PluginChainRequest(BaseModel):
    steps: List[Dict[str, Any]]


@app.get("/plugins/list")
async def list_plugins():
    """List all available plugins."""
    manager = get_plugin_manager()
    return {"plugins": manager.list_plugins(), "count": len(manager.list_plugins())}


@app.get("/plugins/{plugin_name}")
async def get_plugin_info(plugin_name: str):
    """Get detailed info about a specific plugin."""
    manager = get_plugin_manager()
    plugin = manager.get(plugin_name)
    if not plugin:
        raise HTTPException(status_code=404, detail=f"Plugin not found: {plugin_name}")
    return {
        "name": plugin.name,
        "description": plugin.description,
        "version": plugin.version,
        "capabilities": plugin.capabilities,
        "requires_network": plugin.requires_network,
    }


@app.post("/plugins/execute")
async def execute_plugin(req: PluginExecuteRequest):
    """Execute a plugin by name with given parameters."""
    manager = get_plugin_manager()
    result = await manager.execute(req.plugin_name, req.params)
    return {
        "success": result.success,
        "data": result.data,
        "error": result.error,
        "duration_ms": result.duration_ms,
        "plugin_name": result.plugin_name,
        "metadata": result.metadata,
    }


@app.post("/plugins/chain")
async def execute_plugin_chain(req: PluginChainRequest):
    """Execute a chain of plugins, passing output between them."""
    manager = get_plugin_manager()
    results = await manager.execute_chain(req.steps)
    return {
        "results": [
            {
                "success": r.success,
                "data": r.data,
                "error": r.error,
                "duration_ms": r.duration_ms,
                "plugin_name": r.plugin_name,
            }
            for r in results
        ],
        "total_steps": len(results),
        "all_success": all(r.success for r in results),
    }


# GOAL ORCHESTRATOR — SOVEREIGN GOAL-DRIVEN AGENT
# ===================================================================

class GoalSetRequest(BaseModel):
    title: str
    description: str
    success_criteria: List[str] = []
    constraints: List[str] = []
    priority: int = 3


class ApproachRecordRequest(BaseModel):
    goal_id: str = None
    strategy: str
    hypothesis: str = ""
    iteration: int = None


class ApproachUpdateRequest(BaseModel):
    approach_id: str
    result: str  # success, falsified, partial
    evidence: str = ""
    learning: str = ""
    next_target: str = ""


@app.post("/goal/set")
async def goal_set(req: GoalSetRequest):
    """Set the browser's sovereign goal. Injected into all subsequent prompts."""
    from backend.modules.goal_orchestrator import get_goal_orchestrator
    orch = get_goal_orchestrator()
    goal = orch.set_goal(req.title, req.description,
                          req.success_criteria, req.constraints, req.priority)
    return {"status": "goal_set", "goal": {
        "id": goal.id, "title": goal.title, "status": goal.status,
        "priority": goal.priority, "approaches_tried": goal.approaches_tried,
    }}


@app.get("/goal/active")
async def goal_active():
    """Get the currently active sovereign goal."""
    from backend.modules.goal_orchestrator import get_goal_orchestrator
    orch = get_goal_orchestrator()
    goal = orch.get_active_goal()
    if not goal:
        return {"active": False, "message": "No active goal. Set one with POST /goal/set"}
    return {"active": True, "goal": {
        "id": goal.id, "title": goal.title, "description": goal.description,
        "status": goal.status, "priority": goal.priority,
        "approaches_tried": goal.approaches_tried,
        "approaches_succeeded": goal.approaches_succeeded,
        "success_criteria": goal.success_criteria,
        "constraints": goal.constraints,
    }}


@app.get("/goal/list")
async def goal_list(status: str = None):
    """List all goals, optionally filtered by status."""
    from backend.modules.goal_orchestrator import get_goal_orchestrator
    goals = get_goal_orchestrator().list_goals(status)
    return {"goals": [
        {"id": g.id, "title": g.title, "status": g.status,
         "priority": g.priority, "approaches_tried": g.approaches_tried}
        for g in goals
    ]}


@app.post("/goal/achieve")
async def goal_achieve(goal_id: str = None):
    """Mark the active goal as achieved."""
    from backend.modules.goal_orchestrator import get_goal_orchestrator
    success = get_goal_orchestrator().achieve_goal(goal_id)
    return {"status": "achieved" if success else "not_found"}


@app.post("/goal/block")
async def goal_block(goal_id: str = None, reason: str = ""):
    """Mark a goal as blocked."""
    from backend.modules.goal_orchestrator import get_goal_orchestrator
    get_goal_orchestrator().block_goal(goal_id, reason)
    return {"status": "blocked"}


@app.post("/goal/approach")
async def goal_record_approach(req: ApproachRecordRequest):
    """Record a new approach attempt toward the active goal."""
    from backend.modules.goal_orchestrator import get_goal_orchestrator
    orch = get_goal_orchestrator()
    approach = orch.record_approach(req.goal_id, req.strategy,
                                     req.hypothesis, req.iteration)
    return {"status": "recorded", "approach": {
        "id": approach.id, "iteration": approach.iteration,
        "strategy": approach.strategy[:100],
    }}


@app.post("/goal/approach/update")
async def goal_update_approach(req: ApproachUpdateRequest):
    """Update an approach with results, learning, and next target."""
    from backend.modules.goal_orchestrator import get_goal_orchestrator
    success = get_goal_orchestrator().update_approach(
        req.approach_id, req.result, req.evidence, req.learning, req.next_target)
    return {"status": "updated" if success else "not_found"}


@app.get("/goal/approaches")
async def goal_approaches(goal_id: str = None, limit: int = 10):
    """Get approaches for a goal."""
    from backend.modules.goal_orchestrator import get_goal_orchestrator
    approaches = get_goal_orchestrator().get_approaches(goal_id, limit)
    return {"approaches": [
        {"id": a.id, "iteration": a.iteration, "strategy": a.strategy[:100],
         "result": a.result, "learning": a.learning[:200],
         "next_target": a.next_target[:200]}
        for a in approaches
    ]}


@app.get("/goal/fallback")
async def goal_fallback(goal_id: str = None):
    """Generate fallback strategies when current approach is blocked."""
    from backend.modules.goal_orchestrator import get_goal_orchestrator
    return {"fallback": get_goal_orchestrator().generate_fallback(goal_id)}


@app.post("/goal/inject")
async def goal_inject(user_query: str):
    """Preview the goal-injected prompt that would be sent to the LLM."""
    from backend.modules.goal_orchestrator import get_goal_orchestrator
    injected = get_goal_orchestrator().inject_goal_context(user_query)
    return {"original": user_query, "injected": injected}


@app.get("/goal/context")
async def goal_context():
    """Get condensed goal context for LLM system prompts."""
    from backend.modules.goal_orchestrator import get_goal_orchestrator
    return {"context": get_goal_orchestrator().get_goal_context_for_llm()}


@app.get("/goal/learnings")
async def goal_learnings(query: str, limit: int = 10):
    """Query RAG knowledge vault for past iteration learnings."""
    from backend.modules.goal_orchestrator import get_goal_orchestrator
    return {"learnings": get_goal_orchestrator().query_learnings(query, limit)}


# ===================================================================
# ENTRY POINT
# ===================================================================

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8001)
