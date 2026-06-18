"""
Tool registry — typed, JSON-Schema-driven definitions of capabilities the
agent can invoke. Each tool has a name, description, parameter schema, and
an async handler.

Tools can be registered globally (default registry) or per-agent. The agent
loop serializes tool specs into the LLM's native tool-use format (Anthropic
or OpenAI) and dispatches calls back through the registry.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("jambu.agent.tools")


class RiskLevel(str, Enum):
    LOW = "low"           # read-only, no side effects
    MEDIUM = "medium"     # side effects within scope (e.g., save file to vault)
    HIGH = "high"         # cross-system side effects (e.g., post to social)

log = logging.getLogger("jambu.agent.tools")


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict       # JSON Schema for parameters
    returns: dict = field(default_factory=lambda: {"type": "object"})
    requires_network: bool = False
    risk_level: RiskLevel = RiskLevel.LOW
    examples: list[dict] = field(default_factory=list)


@dataclass
class ToolResult:
    success: bool
    data: Any = None
    error: Optional[str] = None
    duration_ms: float = 0.0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"success": self.success, "duration_ms": self.duration_ms}
        if self.data is not None:
            out["data"] = self.data
        if self.error:
            out["error"] = self.error
        if self.metadata:
            out["metadata"] = self.metadata
        return out


Handler = Callable[..., Awaitable[Any]]


class Tool:
    """A registered tool: spec + async handler."""
    def __init__(self, spec: ToolSpec, handler: Handler):
        self.spec = spec
        self.handler = handler
        self.call_count = 0
        self.success_count = 0
        self.failure_count = 0
        self.total_duration_ms = 0.0

    async def __call__(self, **kwargs) -> ToolResult:
        self.call_count += 1
        started = time.monotonic()
        try:
            data = await self.handler(**kwargs)
            self.success_count += 1
            duration = (time.monotonic() - started) * 1000
            self.total_duration_ms += duration
            return ToolResult(success=True, data=data, duration_ms=duration)
        except Exception as e:
            self.failure_count += 1
            duration = (time.monotonic() - started) * 1000
            self.total_duration_ms += duration
            log.warning("Tool %s failed: %s\n%s", self.spec.name, e, traceback.format_exc())
            return ToolResult(success=False, error=str(e), duration_ms=duration)


class ToolRegistry:
    """Holds named tools + their handlers. Singleton by default."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    # -- registration --------------------------------------------------------

    def register(
        self,
        name: str,
        handler: Handler,
        *,
        description: str = "",
        parameters: Optional[dict] = None,
        requires_network: bool = False,
        risk_level: RiskLevel = RiskLevel.LOW,
        examples: Optional[list[dict]] = None,
        returns: Optional[dict] = None,
    ) -> Tool:
        """Register a tool from a handler function. Parameters are auto-derived
        from the function signature if not given explicitly. Idempotent — re-registering
        a tool with the same name is a no-op.
        """
        if name in self._tools:
            return self._tools[name]
        spec_params = parameters or _params_from_signature(handler)
        spec = ToolSpec(
            name=name,
            description=description or (handler.__doc__ or "").split("\n")[0].strip(),
            parameters=spec_params,
            returns=returns or {"type": "object"},
            requires_network=requires_network,
            risk_level=risk_level,
            examples=examples or [],
        )
        tool = Tool(spec, handler)
        self._tools[name] = tool
        return tool

    def register_tool(self, tool: Tool) -> None:
        if tool.spec.name in self._tools:
            return  # idempotent registration
        self._tools[tool.spec.name] = tool

    # -- lookup --------------------------------------------------------------

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"Tool {name!r} not registered. Known: {sorted(self._tools)}")
        return self._tools[name]

    def has(self, name: str) -> bool:
        return name in self._tools

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def list_names(self) -> list[str]:
        return sorted(self._tools.keys())

    # -- dispatch ------------------------------------------------------------

    async def execute(self, name: str, **kwargs) -> ToolResult:
        if name not in self._tools:
            return ToolResult(success=False, error=f"Unknown tool: {name}")
        # Drop kwargs the tool's signature doesn't accept. LLMs sometimes
        # add "helpful" parameters (recency, region, ...) that the tool
        # doesn't declare. Rejecting them crashed the whole tool call;
        # silently dropping them lets the tool run with what it has and
        # we log a debug line so unexpected drift is visible.
        sig = inspect.signature(self._tools[name].handler)
        accepted = set(sig.parameters.keys())
        # VAR_KEYWORD (**kwargs) accepts everything — don't filter.
        has_var_keyword = any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in sig.parameters.values()
        )
        if not has_var_keyword:
            unknown = set(kwargs) - accepted
            if unknown:
                log.debug("dropping unknown kwargs for tool %s: %s", name, sorted(unknown))
                kwargs = {k: v for k, v in kwargs.items() if k in accepted}
        return await self._tools[name](**kwargs)

    # -- serialization for LLM tool-use -------------------------------------

    def to_openai_tools(self) -> list[dict]:
        """Convert all tools to OpenAI function-calling format."""
        out: list[dict] = []
        for tool in self._tools.values():
            out.append({
                "type": "function",
                "function": {
                    "name": tool.spec.name,
                    "description": tool.spec.description,
                    "parameters": tool.spec.parameters,
                },
            })
        return out

    def to_anthropic_tools(self) -> list[dict]:
        """Convert all tools to Anthropic tool-use format."""
        out: list[dict] = []
        for tool in self._tools.values():
            out.append({
                "name": tool.spec.name,
                "description": tool.spec.description,
                "input_schema": tool.spec.parameters,
            })
        return out

    # -- observability -------------------------------------------------------

    def stats(self) -> list[dict]:
        out = []
        for tool in self._tools.values():
            n = tool.call_count
            out.append({
                "name": tool.spec.name,
                "calls": n,
                "success": tool.success_count,
                "failure": tool.failure_count,
                "avg_ms": (tool.total_duration_ms / n) if n else 0.0,
                "risk": tool.spec.risk_level.value,
            })
        return out


# ---------------------------------------------------------------------------
# Parameter inference
# ---------------------------------------------------------------------------

def _params_from_signature(fn) -> dict:
    """Derive a JSON Schema from the function signature.

    Supports:
    - Type hints (str, int, float, bool, list, dict, Optional[X] -> X)
    - Default values
    - Annotated descriptions from `Annotated[X, "description"]`
    """
    sig = inspect.signature(fn)
    properties: dict[str, dict] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        ann = param.annotation
        schema = _annotation_to_schema(ann)
        # Use Annotated[X, "desc"] to attach descriptions
        if _has_description(ann):
            schema["description"] = _get_description(ann)
        if param.default is inspect.Parameter.empty:
            required.append(name)
        else:
            schema["default"] = param.default
        properties[name] = schema
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _annotation_to_schema(ann) -> dict:
    """Map a Python type annotation to a JSON Schema type."""
    import typing
    origin = typing.get_origin(ann)
    args = typing.get_args(ann)
    if ann is str or ann is bytes:
        return {"type": "string"}
    if ann is int:
        return {"type": "integer"}
    if ann is float:
        return {"type": "number"}
    if ann is bool:
        return {"type": "boolean"}
    if ann is dict or origin is dict:
        return {"type": "object"}
    if ann is list or origin is list:
        if args:
            inner = _annotation_to_schema(args[0])
            return {"type": "array", "items": inner}
        return {"type": "array"}
    if origin is typing.Union or origin is typing.Optional:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _annotation_to_schema(non_none[0])
        return {"oneOf": [_annotation_to_schema(a) for a in non_none]}
    if origin is typing.Literal:
        return {"enum": list(args)}
    return {"type": "string"}  # fallback


def _has_description(ann) -> bool:
    import typing
    return typing.get_origin(ann) is typing.Annotated


def _get_description(ann) -> str:
    import typing
    return typing.get_args(ann)[1]


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_REGISTRY: Optional[ToolRegistry] = None


def get_registry() -> ToolRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ToolRegistry()
    return _REGISTRY


def reset_registry() -> ToolRegistry:
    global _REGISTRY
    _REGISTRY = ToolRegistry()
    return _REGISTRY
