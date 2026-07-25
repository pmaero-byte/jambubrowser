"""V2 LLM chat and Agent endpoints."""
import logging
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.engine_runtime import (
    _new_task_id,
    broadcast_agent_state,
    broadcast_agent_telemetry,
    broadcast_task_start,
    broadcast_task_end,
)

router = APIRouter(tags=["v2"])
log = logging.getLogger("jambu.routes.v2")


class LLMChatRequest(BaseModel):
    messages: List[Dict[str, Any]]
    provider: Optional[str] = None
    model: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    tools: Optional[List[Dict[str, Any]]] = None
    stream: bool = False


@router.post("/v2/llm/chat")
async def llm_chat(req: LLMChatRequest):
    """Unified chat against the LLM layer."""
    from backend.llm import ChatMessage, Role, get_registry
    msgs = []
    for m in req.messages:
        role = m.get("role", "user")
        try:
            role_enum = Role(role)
        except ValueError:
            role_enum = Role.USER
        msgs.append(ChatMessage(
            role=role_enum,
            content=m.get("content", ""),
            name=m.get("name"),
            tool_call_id=m.get("tool_call_id"),
            tool_calls=m.get("tool_calls"),
        ))

    if req.stream:
        async def gen():
            try:
                async for chunk in get_registry().stream(
                    msgs, provider=req.provider, model=req.model,
                    max_tokens=req.max_tokens, temperature=req.temperature,
                    tools=req.tools,
                ):
                    yield f"data: {chunk.delta}\n\n"
            except Exception as e:
                yield f"data: [error] {e}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    try:
        from backend.llm import normalize_llm_response
        resp = await get_registry().chat(
            msgs, provider=req.provider, model=req.model,
            max_tokens=req.max_tokens, temperature=req.temperature, tools=req.tools,
        )
        resp.content = normalize_llm_response(resp.content)
        return resp.to_dict() if hasattr(resp, "to_dict") else {
            "content": resp.content, "model": resp.model, "provider": resp.provider,
            "usage": resp.usage.__dict__, "finish_reason": resp.finish_reason,
            "latency_ms": resp.latency_ms,
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM call failed: {e}")


@router.get("/v2/llm/providers")
async def llm_providers():
    """List available LLM providers and their default models."""
    from backend.llm import get_registry, get_config
    reg = get_registry()
    return {
        "default_provider": get_config().default_provider,
        "fallback_chain": get_config().fallback_chain,
        "providers": reg.list_available(),
        "models": {
            name: (reg.get(name).models if reg.has(name) else [])
            for name in reg.list_available()
        },
    }


# ── MoA preset configuration ────────────────────────────────────────────────


@router.get("/v2/llm/moa/presets")
async def moa_get_presets():
    """Return the currently active MoA preset set (override > env > defaults)."""
    from backend.llm.providers.moa import get_active_presets, _OVERRIDE_PRESETS
    return {
        "presets": get_active_presets(),
        "has_override": _OVERRIDE_PRESETS is not None,
    }


@router.post("/v2/llm/moa/presets")
async def moa_set_presets(body: Dict[str, Any]):
    """Install a runtime MoA preset override. Replaces env var + defaults.

    The body must be a JSON object keyed by preset name; each value is a
    preset definition with at least an ``aggregator`` field.
    """
    presets = body.get("presets")
    if not isinstance(presets, dict) or not presets:
        raise HTTPException(
            status_code=400,
            detail="body.presets must be a non-empty JSON object keyed by preset name",
        )
    try:
        from backend.llm.providers.moa import set_presets
        set_presets(presets)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "installed", "preset_count": len(presets)}


@router.delete("/v2/llm/moa/presets")
async def moa_clear_presets():
    """Remove the runtime override so providers fall back to env / defaults."""
    from backend.llm.providers.moa import clear_presets
    clear_presets()
    return {"status": "cleared"}


class AgentRunRequest(BaseModel):
    query: str
    user_id: str = "default"
    max_steps: int = 10
    max_tokens: int = 30000
    max_seconds: float = 120.0
    stream: bool = True


@router.post("/v2/agent/run")
async def agent_run(req: AgentRunRequest):
    """Run the ReAct/Plan-Execute agent loop."""
    from backend.agent import get_agent
    from backend.memory import get_memory, retrieve_relevant, format_context

    task_id = _new_task_id()
    await broadcast_task_start(req.user_id, req.query, task_id)
    await broadcast_agent_state(req.user_id, "thinking")
    await broadcast_agent_telemetry(req.user_id, action="Running ReAct agent loop")

    try:
        hits = retrieve_relevant(req.query, user_id=req.user_id, k=5)
        context_str = format_context(hits) if hits else ""
        profile = get_memory().get_profile(req.user_id)
        if profile.work_context or profile.interests:
            user_ctx = (
                f"User profile: {', '.join(profile.interests) if profile.interests else '(none)'}. "
                f"Context: {profile.work_context or '(none)'}."
            )
            context_str = (context_str + "\n\n" + user_ctx).strip()
    except Exception:
        context_str = ""

    agent = get_agent()
    # Budgets are passed per run (not set on the shared singleton) so each
    # request's limits apply only to its own run and concurrent requests
    # cannot clobber each other.
    budgets = {
        "max_steps": req.max_steps,
        "max_tokens": req.max_tokens,
        "max_seconds": req.max_seconds,
    }

    if req.stream:
        async def gen():
            try:
                async for event in agent.run(req.query, user_id=req.user_id, context=context_str, **budgets):
                    yield event.to_sse()
                await broadcast_agent_state(req.user_id, "reading", zone="cabinet")
                await broadcast_task_end(req.user_id, task_id, status="completed")
            except Exception as e:
                log.warning("[v2/agent/run] stream failed: %r", e)
                await broadcast_task_end(req.user_id, task_id, status="failed", result_preview=str(e))
                raise
            finally:
                await broadcast_agent_state(req.user_id, "idle")
        return StreamingResponse(gen(), media_type="text/event-stream")
    else:
        try:
            result = await agent.run_to_completion(req.query, user_id=req.user_id, context=context_str, **budgets)
            await broadcast_agent_state(req.user_id, "reading", zone="cabinet")
            await broadcast_task_end(
                req.user_id, task_id, status="completed", result_preview=result.answer[:200]
            )
            return result.to_dict()
        except Exception as e:
            log.warning("[v2/agent/run] run_to_completion failed: %r", e)
            await broadcast_task_end(req.user_id, task_id, status="failed", result_preview=str(e))
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            await broadcast_agent_state(req.user_id, "idle")


@router.get("/v2/agent/tools")
async def agent_tools():
    """List tools available to the agent."""
    from backend.agent.tools import get_registry
    from backend.agent.builtin_tools import register_builtin_tools
    reg = get_registry()
    register_builtin_tools(reg)
    return {
        "tools": [
            {
                "name": t.spec.name,
                "description": t.spec.description,
                "parameters": t.spec.parameters,
                "requires_network": t.spec.requires_network,
                "risk_level": t.spec.risk_level.value,
            }
            for t in reg.list()
        ],
        "stats": reg.stats(),
    }


@router.get("/v2/agent/history")
async def agent_history(limit: int = 10):
    """Return recent agent run history."""
    from backend.agent import get_agent
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
    runs = get_agent().history[-limit:]
    return {
        "runs": [r.to_dict() for r in runs],
        "count": len(runs),
        "total_in_history": len(get_agent().history),
    }
