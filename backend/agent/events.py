"""
SSE event types for the agent loop.

The agent emits structured events as it runs, so the frontend can render a
live timeline of what the agent is doing. Events are also serialized over
HTTP Server-Sent Events from `/v2/agent/run?stream=true`.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class EventType(str, Enum):
    RUN_STARTED = "run_started"
    PLAN_CREATED = "plan_created"
    STEP_STARTED = "step_started"
    TOOL_CALLED = "tool_called"
    TOOL_FAILED = "tool_failed"
    STEP_VERIFIED = "step_verified"
    REPLANNED = "replanned"
    ANSWER_READY = "answer_ready"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    LOG = "log"


@dataclass
class AgentEvent:
    type: EventType
    run_id: str = ""
    timestamp: float = field(default_factory=time.time)
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "data": self.data,
        }

    def to_sse(self) -> str:
        """Serialize as a Server-Sent Event line."""
        return f"event: {self.type.value}\ndata: {json.dumps(self.to_dict())}\n\n"

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


# Convenience constructors

def run_started(run_id: str, query: str, user_id: str) -> AgentEvent:
    return AgentEvent(EventType.RUN_STARTED, run_id=run_id, data={"query": query, "user_id": user_id})


def plan_created(run_id: str, plan: dict) -> AgentEvent:
    return AgentEvent(EventType.PLAN_CREATED, run_id=run_id, data={"plan": plan})


def step_started(run_id: str, step: dict) -> AgentEvent:
    return AgentEvent(EventType.STEP_STARTED, run_id=run_id, data={"step": step})


def tool_called(run_id: str, tool: str, args: dict, result: dict) -> AgentEvent:
    return AgentEvent(EventType.TOOL_CALLED, run_id=run_id, data={"tool": tool, "args": args, "result": result})


def tool_failed(run_id: str, tool: str, args: dict, error: str) -> AgentEvent:
    return AgentEvent(EventType.TOOL_FAILED, run_id=run_id, data={"tool": tool, "args": args, "error": error})


def step_verified(run_id: str, step: dict, verdict: dict) -> AgentEvent:
    return AgentEvent(EventType.STEP_VERIFIED, run_id=run_id, data={"step": step, "verdict": verdict})


def replanned(run_id: str, reason: str, new_plan: dict) -> AgentEvent:
    return AgentEvent(EventType.REPLANNED, run_id=run_id, data={"reason": reason, "new_plan": new_plan})


def answer_ready(run_id: str, answer: str, sources: list[str], usage: Optional[dict] = None) -> AgentEvent:
    return AgentEvent(EventType.ANSWER_READY, run_id=run_id, data={"answer": answer, "sources": sources, "usage": usage or {}})


def run_completed(run_id: str, duration_ms: float, total_steps: int, total_tokens: int, total_cost: float) -> AgentEvent:
    return AgentEvent(EventType.RUN_COMPLETED, run_id=run_id, data={
        "duration_ms": duration_ms, "total_steps": total_steps,
        "total_tokens": total_tokens, "total_cost_usd": total_cost,
    })


def run_failed(run_id: str, error: str) -> AgentEvent:
    return AgentEvent(EventType.RUN_FAILED, run_id=run_id, data={"error": error})


def log_event(run_id: str, level: str, message: str) -> AgentEvent:
    return AgentEvent(EventType.LOG, run_id=run_id, data={"level": level, "message": message})


# Module-level event sink (used by tests)
_LAST_EVENT: Optional[AgentEvent] = None


def emit_event(event: AgentEvent) -> None:
    """Set the last event for test inspection. Real consumers use the
    async iterator returned by Agent.run()."""
    global _LAST_EVENT
    _LAST_EVENT = event
