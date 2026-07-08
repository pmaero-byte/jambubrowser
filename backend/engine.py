"""
Jambubrowser Action Engine v2.0
================================
Application factory — imports route modules and wires up middleware.

Routes have been split into domain modules under backend/routes/.
Shared runtime state lives in backend/engine_runtime.py.
"""

import asyncio
import gc
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

# Ensure an event loop exists for this thread.  Python 3.9's asyncio.run()
# destroys the loop when it finishes (set_event_loop(None)), and some
# dependencies (FastAPI / Starlette internals) call get_event_loop() at
# module load time, so if engine.py is imported after asyncio.run() has
# been called — e.g. by a test that ran earlier in the suite — the import
# would crash with "There is no current event loop in thread 'MainThread'".
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

# Load .env before any environment-dependent import or config read.
# This ensures JAMBU_LLM_PROVIDER, MINIMAX_API_KEY, etc. are available
# when the engine is started directly (e.g. `python3 -m uvicorn ...`).
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("jambu.engine")

# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

_SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8888/search")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Modern lifespan context manager — replaces deprecated on_event."""
    from backend.core.database import init_db
    init_db()
    _warn_missing_runtime_deps()
    log.info("Jambubrowser Engine v2.0 started on port 8001")

    from backend.engine_runtime import safe_task, manager
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
                    from backend.engine_runtime import LATEST_LLM_CONFIG
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

    # Shutdown cleanup. Inlined in the post-yield for-loop because
    # @asynccontextmanager wraps this async generator and Python's parser
    # accepts `await` only inside try/loop bodies in that context.
    for task in tasks:
        task.cancel()
    for mod_name in ["browser", "missions", "shadow_browser", "risk_shield"]:
        try:
            mod = __import__(f"backend.modules.{mod_name}", fromlist=["cleanup"])
            if mod_name == "browser":
                await mod.cleanup_browser()
            elif mod_name == "missions":
                mod.get_scheduler().stop()
            elif mod_name == "shadow_browser":
                await mod.get_shadow_browser().close()
            elif mod_name == "risk_shield":
                await mod.get_shield().close()
        except Exception:
            pass


# (The previous _shutdown_modules helper was removed because the bare
# `await _shutdown_modules(tasks)` line at module-asyncgen level was
# rejected by Python's parser. The awaits must live inside the for-body
# above, which is what the original code did.)


# ---------------------------------------------------------------------------
# App creation
# ---------------------------------------------------------------------------

app = FastAPI(title="Jambubrowser Engine v2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:1420", "http://localhost:3000",
        "http://localhost:5173", "http://localhost:5174", "tauri://localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=3600,
)


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


def _is_debug() -> bool:
    return os.environ.get("JAMBU_DEBUG", "false").lower() in ("true", "1", "yes")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log.error("Unhandled exception: %s", exc)
    from backend.core.request_id import get_request_id
    request_id = get_request_id(request.scope) if hasattr(request, "scope") else ""
    content = {
        "detail": "Internal server error",
        "path": request.url.path,
    }
    if request_id:
        content["request_id"] = request_id
    if _is_debug():
        content["error"] = str(exc)
    return JSONResponse(status_code=500, content=content)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    from backend.core.request_id import get_request_id
    request_id = get_request_id(request.scope) if hasattr(request, "scope") else ""
    content = {"detail": exc.detail, "path": request.url.path}
    if request_id:
        content["request_id"] = request_id
    return JSONResponse(status_code=exc.status_code, content=content)


# ---------------------------------------------------------------------------
# Rate limiter — per-endpoint limits
# ---------------------------------------------------------------------------

from backend.core.rate_limiter import RateLimitMiddleware, get_limiter
from backend.core.security_headers import SecurityHeadersMiddleware
from backend.core.body_size_limit import BodySizeLimitMiddleware
from backend.core.trusted_host import TrustedHostMiddleware
from backend.core.request_id import RequestIDMiddleware
from backend.core.request_timeout import RequestTimeoutMiddleware
from backend.core.access_log import AccessLogMiddleware

limiter = get_limiter()
# Heavy compute — tight limits
limiter.set_endpoint_limit("/research", 2.0, 5)
limiter.set_endpoint_limit("/scrape", 5.0, 10)
limiter.set_endpoint_limit("/exec", 5.0, 10)
limiter.set_endpoint_limit("/act", 5.0, 10)
limiter.set_endpoint_limit("/workflow", 3.0, 5)
# Browser automation
limiter.set_endpoint_limit("/login", 3.0, 6)
limiter.set_endpoint_limit("/discover_api", 3.0, 6)
# Vision / MLX — resource heavy
limiter.set_endpoint_limit("/vision", 10.0, 15)
limiter.set_endpoint_limit("/multimodal", 10.0, 15)
limiter.set_endpoint_limit("/mlx/generate", 5.0, 8)
# Knowledge / memory writes
limiter.set_endpoint_limit("/knowledge/ingest", 10.0, 20)
limiter.set_endpoint_limit("/memory/recall", 30.0, 50)
# Missions / scheduling
limiter.set_endpoint_limit("/mission", 10.0, 20)
limiter.set_endpoint_limit("/shield", 20.0, 30)
# All other POST endpoints — moderate default
limiter.set_endpoint_limit("default_post", 30.0, 60)

app.add_middleware(RateLimitMiddleware, limiter=limiter)
app.add_middleware(BodySizeLimitMiddleware, max_bytes=2 * 1024 * 1024)
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(RequestTimeoutMiddleware, timeout_seconds=30.0, exclude_paths=[
    "/research", "/scrape", "/exec", "/act", "/workflow", "/v2/",
    "/mlx/", "/mission", "/knowledge/ingest", "/login", "/discover_api",
    "/audit/", "/proxy",
])
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(TrustedHostMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(AccessLogMiddleware)


# ---------------------------------------------------------------------------
# Import & register route modules
# ---------------------------------------------------------------------------

from backend.routes.system import router as system_router
from backend.routes.ws import router as ws_router
from backend.routes.research import router as research_router
from backend.routes.browser import router as browser_router
from backend.routes.vault import router as vault_router
from backend.routes.knowledge import router as knowledge_router
from backend.routes.memory import router as memory_router
from backend.routes.local import router as local_router
from backend.routes.missions import router as missions_router
from backend.routes.tools import router as tools_router
from backend.routes.models import router as models_router
from backend.routes.p2p import router as p2p_router
from backend.routes.goals import router as goals_router
from backend.routes.consensus import router as consensus_router
from backend.routes.harness import router as harness_router
from backend.routes.v1 import router as v1_router
from backend.routes.v2 import router as v2_router
from backend.routes.multimodal import router as multimodal_router
from backend.routes.fingerprint import router as fingerprint_router
from backend.routes.media import router as media_router
from backend.routes.audit import router as audit_router
from backend.routes.api_keys import router as api_keys_router
from backend.routes.billing import router as billing_router
from backend.routes.teams import router as teams_router
from backend.routes.proxy import router as proxy_router
from backend.routes.mcp import router as mcp_router

app.include_router(system_router)
app.include_router(ws_router)
app.include_router(research_router)
app.include_router(browser_router)
app.include_router(vault_router)
app.include_router(knowledge_router)
app.include_router(memory_router)
app.include_router(local_router)
app.include_router(missions_router)
app.include_router(tools_router)
app.include_router(models_router)
app.include_router(p2p_router)
app.include_router(goals_router)
app.include_router(consensus_router)
app.include_router(harness_router)
app.include_router(v1_router)
app.include_router(v2_router)
app.include_router(multimodal_router)
app.include_router(fingerprint_router)
app.include_router(media_router)
app.include_router(audit_router)
app.include_router(api_keys_router)
app.include_router(billing_router)
app.include_router(teams_router)
app.include_router(proxy_router)
app.include_router(mcp_router)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)


# ---------------------------------------------------------------------------
# Runtime dep check (called from lifespan on startup)
# ---------------------------------------------------------------------------

def _warn_missing_runtime_deps() -> None:
    """Warn loudly (but don't crash) for missing optional deps that would
    otherwise 500 the first time a user calls the endpoint.

    Currently checked:
    - markdownify : required by /scrape and /act (returns 500 if missing)
    """
    for mod_name, import_path, what in (
        ("markdownify", "markdownify", "scrape + act (returns 500 if missing)"),
    ):
        try:
            __import__(import_path)
        except ImportError:
            log.warning(
                "MISSING DEP '%s' — required for: %s. "
                "Run: pip install %s",
                mod_name, what, mod_name,
            )
