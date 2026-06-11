"""
Mock LLM provider — deterministic, no network, used for tests and offline demos.

Returns canned responses or echoes the user message with a prefix. Supports
tool calls when the user message contains a JSON command like `{"tool": "..."}`.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, AsyncIterator, Optional

from ..base import (
    ChatMessage,
    ChatResponse,
    ProviderError,
    StreamChunk,
    Usage,
    estimate_cost_for_model,
)
from ..config import LLMConfig

NAME = "mock"
MODELS = ["mock-echo", "mock-llm-1"]
supports_tools = True


def _detect_tool_call(content: str) -> Optional[dict]:
    """If the content looks like `{"tool": "...", "args": {...}}`, return it."""
    content = content.strip()
    if not content.startswith("{"):
        return None
    try:
        obj = json.loads(content)
    except json.JSONDecodeError:
        return None
    if isinstance(obj, dict) and "tool" in obj:
        return obj
    return None


class MockProvider:
    name = NAME
    models = MODELS
    supports_tools = True

    def __init__(self, config: LLMConfig):
        self.config = config
        self.call_count = 0
        self.history: list[list[ChatMessage]] = []

    async def health(self) -> bool:
        return True

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        tools: Optional[list[dict]] = None,
        timeout: float = 5.0,
    ) -> ChatResponse:
        self.call_count += 1
        self.history.append(list(messages))
        last_user = next((m for m in reversed(messages) if m.role.value == "user"), None)
        content = last_user.content if last_user else ""

        # Tool-call mode: detect JSON in user message
        tool_calls: list[dict] = []
        text = ""
        cmd = _detect_tool_call(content)
        if cmd:
            tool_calls.append({
                "id": f"call_{self.call_count}",
                "name": cmd["tool"],
                "input": cmd.get("args", {}),
            })
        else:
            text = f"[mock:{model or 'mock-echo'}] You said: {content[:200]}"
            if tools:
                # Auto-suggest a tool if tools are available
                first = tools[0]
                tool_calls.append({
                    "id": f"call_{self.call_count}",
                    "name": first["name"],
                    "input": {},
                })

        prompt_tokens = sum(len(m.content.split()) for m in messages) * 2
        completion_tokens = len(text.split()) * 2 if text else 16
        usage = Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
        usage.cost_usd = 0.0
        return ChatResponse(
            content=text,
            model=model or "mock-echo",
            provider=self.name,
            usage=usage,
            finish_reason="tool_use" if tool_calls else "stop",
            tool_calls=tool_calls,
            raw={"mock": True, "call": self.call_count},
            latency_ms=2.0,
        )

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        model: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        tools: Optional[list[dict]] = None,
        timeout: float = 5.0,
    ) -> AsyncIterator[StreamChunk]:
        resp = await self.chat(messages, model=model, max_tokens=max_tokens, tools=tools, timeout=timeout)
        # Stream the text in small chunks
        if resp.content:
            for i in range(0, len(resp.content), 8):
                yield StreamChunk(delta=resp.content[i:i + 8])
                await asyncio.sleep(0.005)
        if resp.tool_calls:
            for tc in resp.tool_calls:
                yield StreamChunk(delta="", tool_calls=[tc])
                await asyncio.sleep(0.005)
        yield StreamChunk(delta="", finish_reason="stop", usage=resp.usage)

    def estimate_cost(self, usage: Usage, model: Optional[str] = None) -> float:
        return 0.0


def register(registry) -> None:
    registry.register_factory(NAME, MockProvider)
