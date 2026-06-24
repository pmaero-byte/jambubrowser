"""V2 LLM chat and Agent endpoints."""
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter(tags=["v2"])


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
    from backend.agent import Agent
    from backend.memory import get_memory, retrieve_relevant, format_context

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

    agent = Agent(max_steps=req.max_steps, max_tokens=req.max_tokens, max_seconds=req.max_seconds)

    if req.stream:
        async def gen():
            try:
                async for event in agent.run(req.query, user_id=req.user_id, context=context_str):
                    yield event.to_sse()
            except Exception as e:
                yield f"event: error\ndata: {{\"error\": \"{e}\"}}\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")
    else:
        result = await agent.run_to_completion(req.query, user_id=req.user_id, context=context_str)
        return result.to_dict()


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
    return {"runs": [], "note": "agent runs are ephemeral; query audit_log for persistence"}
