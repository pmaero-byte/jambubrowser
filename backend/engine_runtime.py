"""
Jambubrowser Engine Runtime
===========================
Shared state, helpers, and infrastructure that all route modules depend on.

Extracted from engine.py to enable domain-split route modules.
"""

import asyncio
import json
import logging
import os
import re
import time
import uuid
from typing import Any, Dict, Optional

from fastapi import WebSocket

log = logging.getLogger("jambu.runtime")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8888/search")
GLOBAL_VPN_PROXY = os.environ.get("AGENT_VPN_PROXY", None)

LATEST_LLM_CONFIG: dict = {
    "provider": "ollama",
    "baseUrl": "http://localhost:11434/v1",
    "modelId": "gemma4:12b-it-qat",
    "apiKey": "",
}

CLOUD_PROVIDERS: dict = {
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

START_TIME = time.time()
last_activity = time.time()
active_missions: Dict[str, dict] = {}

# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _resolve_llm_config(cfg: dict) -> dict:
    """Merge caller config with the matching cloud preset if provider is set."""
    merged = dict(LATEST_LLM_CONFIG)
    if cfg:
        merged.update({k: v for k, v in cfg.items() if v})
    provider = merged.get("provider", "ollama")
    if provider in CLOUD_PROVIDERS:
        merged.update(CLOUD_PROVIDERS[provider])
    return merged


def _strip_think(text: str) -> str:
    return _THINK_RE.sub("", text).strip()


async def _call_llm(
    prompt: str,
    system: Optional[str] = None,
    *,
    max_tokens: int = 500,
    temperature: float = 0.3,
    timeout: float = 10.0,
) -> tuple[str, dict]:
    """Unified LLM call. Returns (answer_text, usage_dict). Provider-aware.

    Thin shim that delegates to the backend.llm layer. Preserves the
    original (text, usage_dict) signature so 60+ existing call-sites keep
    working unchanged. Respects caller-provided provider config first, falls
    back to env-driven default.
    """
    cfg = _resolve_llm_config({})
    from backend.llm import ChatMessage, Role, get_default

    messages: list[ChatMessage] = []
    if system:
        messages.append(ChatMessage(role=Role.SYSTEM, content=system))
    messages.append(ChatMessage(role=Role.USER, content=prompt))

    provider_name = cfg.get("provider")
    if provider_name in ("local", "ollama", ""):
        provider_name = "ollama"
    if provider_name in (None, "auto"):
        provider_name = None  # let registry pick

    if provider_name == "mlx" and timeout < 60.0:
        timeout = 60.0

    try:
        from backend.llm import get_registry

        reg = get_registry()
        resp = await reg.chat(
            messages,
            provider=provider_name,
            model=cfg.get("modelId") or None,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        )
        stripped = _strip_think(resp.content)
        usage = {
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
            "total_tokens": resp.usage.total_tokens,
            "cost_usd": resp.usage.cost_usd,
            "provider": resp.provider,
            "model": resp.model,
        }
        return stripped, usage
    except Exception as e:
        return f"[LLM error: {e}]", {"error": str(e)}


# ---------------------------------------------------------------------------
# WebSocket connection manager
# ---------------------------------------------------------------------------

import re

_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_\-:.]{1,64}$")
_MAX_CONNECTIONS_PER_IP = int(os.environ.get("WS_MAX_CONNECTIONS_PER_IP", "8"))
_MAX_TOTAL_CONNECTIONS = int(os.environ.get("WS_MAX_TOTAL_CONNECTIONS", "256"))


def _is_valid_client_id(client_id: str) -> bool:
    """Validate a WebSocket client_id. Restrictive character set, length cap."""
    return bool(client_id) and bool(_CLIENT_ID_RE.match(client_id))


def _peer_ip(websocket) -> str:
    """Extract the peer's IP from a WebSocket connection."""
    try:
        if websocket.client and isinstance(websocket.client, (list, tuple)) and websocket.client:
            return str(websocket.client[0])
    except Exception:
        pass
    return "unknown"


class ConnectionManager:
    """Manages WebSocket connections for real-time agent logging.

    Security features:
    - Validates `client_id` format before accepting (prevents path injection).
    - Tracks connections per peer IP; rejects if an IP exceeds the limit
      (prevents connection-storm / memory exhaustion DoS).
    - Replaces stale connection for the same client_id cleanly (old socket
      is closed to prevent resource leaks).
    - Caps total connections globally to prevent runaway growth.
    """

    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self._ip_counts: Dict[str, int] = {}

    def _register_ip(self, ip: str) -> bool:
        """Increment IP connection count. False if over limit."""
        if ip == "unknown":
            return True
        count = self._ip_counts.get(ip, 0) + 1
        if count > _MAX_CONNECTIONS_PER_IP:
            return False
        self._ip_counts[ip] = count
        return True

    def _release_ip(self, ip: str) -> None:
        if ip == "unknown":
            return
        current = self._ip_counts.get(ip, 0)
        if current <= 1:
            self._ip_counts.pop(ip, None)
        else:
            self._ip_counts[ip] = current - 1

    def has_capacity(self) -> bool:
        """True if there's room for one more total connection."""
        return len(self.active_connections) < _MAX_TOTAL_CONNECTIONS

    def get_stats(self) -> dict:
        """Return current connection statistics."""
        return {
            "active_connections": len(self.active_connections),
            "max_total": _MAX_TOTAL_CONNECTIONS,
            "max_per_ip": _MAX_CONNECTIONS_PER_IP,
            "unique_ips": len(self._ip_counts),
            "ip_counts": dict(self._ip_counts),
        }

    async def connect(self, client_id: str, websocket: WebSocket) -> bool:
        """Accept a new WebSocket connection. Returns False if rejected."""
        if not _is_valid_client_id(client_id):
            log.warning("[ws] rejected connection: invalid client_id %r", client_id[:80])
            return False

        if not self.has_capacity():
            log.warning("[ws] rejected connection: total cap %d reached",
                        _MAX_TOTAL_CONNECTIONS)
            return False

        ip = _peer_ip(websocket)
        if not self._register_ip(ip):
            log.warning("[ws] rejected connection: per-IP cap %d for %s",
                        _MAX_CONNECTIONS_PER_IP, ip)
            return False

        old = self.active_connections.get(client_id)
        if old is not None and old is not websocket:
            try:
                await old.close(code=1000, reason="replaced")
            except Exception:
                pass

        await websocket.accept()
        self.active_connections[client_id] = websocket
        return True

    def disconnect(self, client_id: str) -> None:
        ws = self.active_connections.pop(client_id, None)
        if ws is not None:
            self._release_ip(_peer_ip(ws))

    async def broadcast(self, client_id: str, message: str) -> None:
        if client_id in self.active_connections:
            try:
                await self.active_connections[client_id].send_text(message)
            except Exception as e:
                log.warning("[ws] broadcast to %s failed: %r", client_id, e)
                self.disconnect(client_id)

    async def broadcast_all(self, message: str) -> None:
        for ws in list(self.active_connections.values()):
            try:
                await ws.send_text(message)
            except Exception as e:
                log.warning("[ws] broadcast_all failed: %r", e)


manager = ConnectionManager()

# ---------------------------------------------------------------------------
# Agent state tracking
# ---------------------------------------------------------------------------

active_tasks: Dict[str, str] = {}
cancel_flags: Dict[str, asyncio.Event] = {}
_task_token_starts: Dict[str, float] = {}
_task_token_counts: Dict[str, int] = {}


def safe_task(coro: Any, label: str = "background") -> asyncio.Task:
    task = asyncio.create_task(coro)

    def _done(t: asyncio.Task) -> None:
        try:
            t.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.error("[safe_task:%s] unhandled exception: %r", label, exc)

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
    return flag is not None and flag.is_set()
