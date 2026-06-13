"""V1 Harness Gateway compatibility endpoints."""
import asyncio
import json
import time
import uuid
from typing import Optional, List, Dict, Any
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.core.database import (
    memory_add, memory_search, memory_list, memory_delete,
    session_create, session_update, session_list, session_get,
    record_task_metric, record_tool_usage, get_analytics_summary,
)
from backend.modules.search import multi_engine_search
from backend.core.sandbox import execute_sandboxed

router = APIRouter(tags=["v1"])


class RunRequest(BaseModel):
    prompt: str
    tool: Optional[str] = None
    session_id: Optional[str] = None


class RunStreamRequest(BaseModel):
    prompt: str
    tool: Optional[str] = None
    session_id: Optional[str] = None


@router.post("/v1/run")
async def v1_run(req: RunRequest):
    """Harness Gateway compatibility: execute a task."""
    start = time.time()
    session_id = req.session_id or str(uuid.uuid4())

    session_create(session_id, f"Task-{session_id[:8]}")

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
        from backend.modules.scraper import scrape_url
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
        endpoint, method = "/exec", "POST"
        result = await execute_sandboxed(req.prompt, 30)
        status = "success"

    duration_ms = int((time.time() - start) * 1000)

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


@router.post("/v1/run/stream")
async def v1_run_stream(req: RunStreamRequest):
    """Harness Gateway compatibility: SSE streaming task execution."""
    async def event_stream():
        session_id = req.session_id or str(uuid.uuid4())
        session_create(session_id, f"Stream-{session_id[:8]}")

        yield f"data: {json.dumps({'type': 'session', 'session_id': session_id})}\n\n"
        yield f"data: {json.dumps({'type': 'start', 'session_id': session_id})}\n\n"

        start = time.time()
        prompt_lower = req.prompt.lower()

        if any(kw in prompt_lower for kw in ["search", "find"]):
            result = await multi_engine_search(req.prompt)
        elif any(kw in prompt_lower for kw in ["research", "analyze"]):
            result = await multi_engine_search(req.prompt)
        else:
            result = await execute_sandboxed(req.prompt, 30)

        result_str = json.dumps(str(result))
        for i in range(0, len(result_str), 100):
            chunk = result_str[i:i+100]
            yield f"data: {json.dumps({'type': 'chunk', 'data': chunk})}\n\n"
            await asyncio.sleep(0.01)

        duration_ms = int((time.time() - start) * 1000)
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        task_data = json.dumps({"task_id": task_id, "status": "success", "duration_ms": duration_ms})
        yield f"data: {json.dumps({'type': 'task', 'data': json.loads(task_data)})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@router.get("/v1/sessions")
async def v1_sessions_list(limit: int = 20):
    """List recent sessions."""
    return {"sessions": session_list(limit)}


@router.get("/v1/sessions/{sid}")
async def v1_session_get(sid: str):
    """Get a specific session."""
    return session_get(sid)


@router.get("/v1/models")
async def v1_models():
    """List available models (v1 compatibility)."""
    from backend.llm import get_registry
    reg = get_registry()
    return {"models": reg.list_available()}


@router.get("/v1/connectors")
async def v1_connectors():
    """List available connectors (v1 compatibility / tool list)."""
    from backend.agent.tools import get_registry
    from backend.agent.builtin_tools import register_builtin_tools
    reg = get_registry()
    register_builtin_tools(reg)
    return {
        "connectors": [
            {
                "name": t.spec.name,
                "description": t.spec.description,
                "parameters": t.spec.parameters,
            }
            for t in reg.list()
        ]
    }


@router.get("/v1/health/detailed")
async def v1_health_detailed():
    """Detailed health check with system metrics."""
    import psutil
    start = time.time()
    mem = psutil.virtual_memory()
    return {
        "status": "online",
        "version": "2.0.0",
        "cpu_percent": psutil.cpu_percent(interval=0),
        "ram_used_gb": round(mem.used / (1024 ** 3), 2),
        "ram_total_gb": round(mem.total / (1024 ** 3), 2),
        "response_time_ms": round((time.time() - start) * 1000, 2),
        "engine_version": "2.0.0",
    }
