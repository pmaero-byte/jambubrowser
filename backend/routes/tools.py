"""Custom tool, plugin, skill, and API discovery endpoints."""
import os
import re
import json
import asyncio
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, validator

from backend.core.database import get_db_cursor
from backend.core.security import is_safe_url
from backend.routes.research import ScrapeRequest

router = APIRouter(tags=["tools"])


# ── Custom Tools ──


class ToolSaveRequest(BaseModel):
    name: str
    code: str
    description: str = ""
    client_id: str = "default"


class ToolExecRequest(BaseModel):
    name: str
    kwargs: Dict = {}
    client_id: str = "default"


@router.post("/tool/save")
async def save_custom_tool(req: ToolSaveRequest):
    """Persist an agent-written Python skill."""
    safe_name = re.sub(r"[^a-zA-Z0-9_]", "", req.name)
    tools_dir = os.path.join(os.path.dirname(__file__), "..", "..", "tools")
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


import time


@router.get("/tools")
async def list_tools():
    """List all saved agent skills."""
    with get_db_cursor() as cursor:
        cursor.execute("SELECT name, description, created_at FROM custom_tools")
        return {
            "tools": [
                {"name": row[0], "description": row[1], "created_at": row[2]}
                for row in cursor.fetchall()
            ]
        }


@router.post("/tool/exec")
async def execute_custom_tool(req: ToolExecRequest):
    """Execute a saved agent skill by name with arguments."""
    import importlib
    tools_dir = os.path.join(os.path.dirname(__file__), "..", "..", "tools")
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


# ── API Discovery ──


class DynamicApiRequest(BaseModel):
    url: str
    method: str = "GET"
    headers: dict = {}
    body: Optional[dict] = None
    client_id: str = "default"

    @validator("url")
    def validate_url(cls, v):
        if not is_safe_url(v):
            raise ValueError("Invalid or blocked URL")
        return v


@router.post("/discover_api")
async def discover_api(req: ScrapeRequest):
    """Scan a URL for OpenAPI/Swagger specifications."""
    import httpx
    async with httpx.AsyncClient(timeout=10.0) as client:
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


@router.post("/api/call")
async def call_dynamic_api(req: DynamicApiRequest):
    """Execute a structured request against a discovered API."""
    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.request(
            req.method, req.url,
            json=req.body if req.body else None,
            headers=req.headers,
            timeout=30.0,
        )
        try:
            data = resp.json()
        except Exception:
            data = resp.text[:10000]
        return {"status": resp.status_code, "data": data}


# ── Analytics ──


@router.get("/analytics/summary")
async def analytics_summary(days: int = 7):
    """Get analytics summary from the database."""
    from backend.core.database import get_analytics_summary
    return get_analytics_summary(days)


@router.get("/benchmark")
async def benchmark():
    """Run a simple system benchmark."""
    import time
    import psutil

    start = time.time()
    # CPU benchmark
    count = 0
    for i in range(1000000):
        count += 1
    cpu_time = time.time() - start

    mem = psutil.virtual_memory()
    return {
        "cpu_benchmark_ops_per_sec": round(1000000 / cpu_time),
        "ram_used_gb": round(mem.used / (1024**3), 2),
        "ram_percent": mem.percent,
        "cpu_percent": psutil.cpu_percent(interval=0.5),
        "python_version": __import__("sys").version,
    }


# ── Plugins ──


class PluginExecuteRequest(BaseModel):
    plugin_name: str
    params: Dict[str, Any] = {}
    timeout: int = 60


class PluginChainRequest(BaseModel):
    steps: List[Dict[str, Any]]


@router.get("/plugins/list")
async def list_plugins():
    """List all available plugins."""
    from backend.plugins.manager import get_plugin_manager
    mgr = get_plugin_manager()
    return {"plugins": mgr.list_plugins(), "count": len(mgr.list_plugins())}


@router.get("/plugins/{plugin_name}")
async def get_plugin_info(plugin_name: str):
    """Get detailed info about a specific plugin."""
    from backend.plugins.manager import get_plugin_manager
    mgr = get_plugin_manager()
    plugin = mgr.get(plugin_name)
    if not plugin:
        raise HTTPException(status_code=404, detail=f"Plugin not found: {plugin_name}")
    return {
        "name": plugin.name,
        "description": plugin.description,
        "version": plugin.version,
        "capabilities": plugin.capabilities,
        "requires_network": plugin.requires_network,
    }


@router.post("/plugins/execute")
async def execute_plugin(req: PluginExecuteRequest):
    """Execute a plugin by name with given parameters."""
    from backend.plugins.manager import get_plugin_manager
    mgr = get_plugin_manager()
    result = await mgr.execute(req.plugin_name, req.params)
    return {
        "success": result.success,
        "data": result.data,
        "error": result.error,
        "duration_ms": result.duration_ms,
        "plugin_name": result.plugin_name,
        "metadata": result.metadata,
    }


@router.post("/plugins/chain")
async def execute_plugin_chain(req: PluginChainRequest):
    """Execute a chain of plugins, passing output between them."""
    from backend.plugins.manager import get_plugin_manager
    mgr = get_plugin_manager()
    results = await mgr.execute_chain(req.steps)
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


# ── Skills ──


class SkillSynthesizeRequest(BaseModel):
    description: str
    code: str = ""
    tests: str = ""


@router.post("/skill/synthesize")
async def synthesize_skill(req: SkillSynthesizeRequest):
    """Autonomously synthesize a Python skill from a failure."""
    from backend.modules.skill_synthesizer import get_synthesizer
    synth = get_synthesizer()
    result = await synth.synthesize(req.description, req.code, req.tests)
    return result


@router.get("/skill/list-synthesized")
async def list_synthesized_skills():
    """List all synthesized skills."""
    from backend.modules.skill_synthesizer import get_synthesizer
    synth = get_synthesizer()
    return {"skills": synth.list_skills()}


# ── Peer Tools ──


@router.get("/peers/tools")
async def peers_tools():
    """List tools available from peer nodes on the mesh."""
    return {"tools": []}


@router.get("/peers/tools/pull")
async def peers_tools_pull(name: str):
    """Pull a tool from a peer node on the mesh."""
    return {"status": "not_found", "name": name,
            "message": "No peers connected. Enable P2P discovery first."}
