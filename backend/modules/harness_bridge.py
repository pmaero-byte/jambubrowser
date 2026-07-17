"""
Harness Bridge — Jambubrowser ↔ Harness Integration
=====================================================
Connects Jambubrowser to the Harness meta-agent gateway,
enabling multi-AI-agent research, Playwright-grade browser
automation, multi-model LLM access, shared context, and
workflow orchestration.

Harness Gateway: http://localhost:9090
HarnessGPT Bridge: http://localhost:9090/v1 (LLM proxy)

Capabilities exposed:
- Multi-agent research swarm (Hermes + Claude + OpenCode in parallel)
- Playwright MCP browser automation (replace Crawl4AI)
- Multi-provider LLM (local Gemma 4 + cloud models)
- Shared persistent context across agents
- YAML workflow automation for research pipelines
- Telemetry and observability spans
"""

import asyncio
import json
import logging
import time
import os
from typing import Optional, List, Dict
from dataclasses import dataclass, field

import httpx

try:
    from backend.core.socks import make_async_client
except ImportError:
    make_async_client = httpx.AsyncClient

from backend.core.database import get_db_cursor
from backend.engine_runtime import (
    broadcast_agent_state,
    broadcast_agent_telemetry,
)

log = logging.getLogger("jambu.harness_bridge")


# ---- Configuration ----

HARNESS_GATEWAY = os.environ.get("HARNESS_GATEWAY_URL", "http://localhost:9090")
HARNESS_BRIDGE_ENABLED = os.environ.get("HARNESS_BRIDGE_ENABLED", "true").lower() == "true"

DEFAULT_CONNECTORS = ["hermes", "claude", "opencode"]


@dataclass
class HarnessResult:
    """Result from a Harness operation."""
    source: str  # connector name or "harness"
    content: str
    latency_ms: float
    success: bool
    metadata: dict = field(default_factory=dict)


class HarnessBridge:
    """
    Unified bridge to the Harness meta-agent gateway.
    Auto-detects Harness availability and provides fallback behavior.
    """

    def __init__(self, gateway_url: str = None):
        self.gateway_url = (gateway_url or HARNESS_GATEWAY).rstrip('/')
        self._http_client: Optional[httpx.AsyncClient] = None
        self._available: Optional[bool] = None
        self._last_check: float = 0
        self._check_ttl: float = 30  # Re-check availability every 30s

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = make_async_client(timeout=60.0)
        return self._http_client

    async def is_available(self) -> bool:
        """Check if the Harness gateway is reachable."""
        if self._available is not None and time.time() - self._last_check < self._check_ttl:
            return self._available

        client = await self._get_client()
        try:
            resp = await client.get(f"{self.gateway_url}/health", timeout=3.0)
            self._available = resp.status_code == 200
        except Exception:
            self._available = False

        self._last_check = time.time()
        return self._available

    async def get_status(self) -> dict:
        """Get Harness gateway status and available connectors."""
        if not await self.is_available():
            return {
                "available": False,
                "message": "Harness gateway is not reachable at " + self.gateway_url,
                "action": "Start Harness: cd ~/Harness_App/harness && make run",
                "install": "If not installed: git clone https://github.com/pmaero-byte/harness.git",
                "gateway_url": self.gateway_url,
            }

        client = await self._get_client()
        connectors = {}
        try:
            resp = await client.get(f"{self.gateway_url}/v1/connectors", timeout=5.0)
            if resp.status_code == 200:
                connectors = resp.json()
        except Exception:
            pass

        return {
            "available": True,
            "gateway_url": self.gateway_url,
            "connectors": connectors,
            "health": "online",
        }

    # ---- Multi-Agent Research Swarm ----

    async def research_swarm(self, query: str,
                              connectors: List[str] = None,
                              judge: bool = True,
                              client_id: str = "default") -> Dict:
        """
        Delegate research to Harness's multi-agent swarm.
        Sends the query to all specified AI agents in parallel,
        with optional judging of the best result.

        Args:
            query: Research question or task
            connectors: List of Harness connectors (hermes, claude, codex, opencode)
            judge: Whether to have Harness judge and pick the best result
            client_id: WebSocket client_id for state/telemetry broadcasts

        Returns:
            Dict with results from each agent + judge verdict
        """
        if not await self.is_available():
            return {"status": "unavailable", "message": "Harness gateway not reachable"}

        connectors = connectors or DEFAULT_CONNECTORS
        await broadcast_agent_state(client_id, "searching", zone="pile")
        await broadcast_agent_telemetry(
            client_id,
            action=f"Dispatching swarm to {len(connectors)} connectors",
        )

        client = await self._get_client()
        try:
            resp = await client.post(
                f"{self.gateway_url}/v1/run/parallel",
                json={
                    "prompt": query,
                    "connectors": connectors,
                    "judge": judge,
                },
                timeout=300.0,
            )

            if resp.status_code == 200:
                data = resp.json()
                await broadcast_agent_state(client_id, "reading", zone="cabinet")
                await broadcast_agent_telemetry(
                    client_id, action="Swarm synthesis received"
                )
                return {
                    "status": "success",
                    "query": query,
                    "connectors_used": connectors,
                    "judge_used": judge,
                    "results": data,
                }

            await broadcast_agent_state(client_id, "error")
            await broadcast_agent_telemetry(
                client_id, action=f"Swarm error: HTTP {resp.status_code}"
            )
            return {
                "status": "error",
                "message": f"Harness returned status {resp.status_code}",
                "detail": resp.text[:500],
            }

        except httpx.TimeoutException:
            await broadcast_agent_state(client_id, "error")
            await broadcast_agent_telemetry(client_id, action="Swarm timeout")
            return {"status": "timeout", "message": "Research swarm timed out"}
        except Exception as e:
            await broadcast_agent_state(client_id, "error")
            await broadcast_agent_telemetry(
                client_id, action=f"Swarm exception: {str(e)[:80]}"
            )
            return {"status": "error", "message": str(e)[:200]}

    async def research_single(self, query: str, connector: str = "hermes",
                              client_id: str = "default") -> Dict:
        """
        Delegate research to a single Harness connector.

        Args:
            query: Research question
            connector: Which AI agent to use

        Returns:
            Dict with the agent's response
        """
        if not await self.is_available():
            return {"status": "unavailable"}

        client = await self._get_client()
        try:
            resp = await client.post(
                f"{self.gateway_url}/v1/run",
                params={"connector": connector},
                json={"prompt": query},
                timeout=120.0,
            )

            if resp.status_code == 200:
                return {"status": "success", "connector": connector, "result": resp.json()}

            return {"status": "error", "message": f"Status {resp.status_code}"}

        except Exception as e:
            return {"status": "error", "message": str(e)[:200]}

    async def research_stream(self, query: str, connector: str = "hermes"):
        """
        Stream research results via SSE from Harness.

        Yields text chunks as they arrive.
        """
        if not await self.is_available():
            yield "Harness gateway not available"
            return

        client = await self._get_client()
        try:
            async with client.stream(
                "POST",
                f"{self.gateway_url}/v1/run/stream",
                params={"connector": connector},
                json={"prompt": query},
                timeout=120.0,
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:].strip()
                        if data and data != "[DONE]":
                            try:
                                chunk = json.loads(data)
                                yield chunk.get("content", "")
                            except json.JSONDecodeError:
                                yield data
        except Exception as e:
            yield f"Stream error: {str(e)[:100]}"

    # ---- Playwright MCP Browser Automation ----

    async def browse(self, url: str, action: str = "scrape",
                      selector: str = None, value: str = None,
                      client_id: str = "default") -> Dict:
        """
        Use Harness's Playwright MCP for browser automation.
        Replaces Crawl4AI for production-grade browsing.

        Args:
            url: Target URL
            action: 'scrape', 'click', 'type', 'screenshot', 'navigate'
            selector: CSS selector for click/type actions
            value: Text value for type action

        Returns:
            Dict with page content or action result
        """
        if not await self.is_available():
            return {"status": "unavailable", "fallback": "Use built-in Crawl4AI or Playwright"}

        prompts = {
            "scrape": f"Navigate to {url}, wait for it to fully load, extract all main content as clean text. Return the content.",
            "click": f"Navigate to {url}, click element '{selector}', return the resulting page content.",
            "type": f"Navigate to {url}, type '{value}' into '{selector}', return the resulting page content.",
            "screenshot": f"Navigate to {url}, take a full-page screenshot, return what you see on the page.",
            "navigate": f"Navigate to {url} and return the page title and main headings.",
        }

        prompt = prompts.get(action, prompts["scrape"])

        await broadcast_agent_state(client_id, "searching", zone="pile")
        await broadcast_agent_telemetry(
            client_id, action=f"Browser action: {action}", file_path=url
        )

        client = await self._get_client()
        try:
            resp = await client.post(
                f"{self.gateway_url}/v1/run",
                params={"connector": "mcp"},
                json={"prompt": prompt},
                timeout=60.0,
            )

            if resp.status_code == 200:
                await broadcast_agent_state(client_id, "reading", zone="cabinet")
                await broadcast_agent_telemetry(client_id, action=f"Browser {action} complete")
                return {
                    "status": "success",
                    "url": url,
                    "action": action,
                    "content": resp.json(),
                }

            await broadcast_agent_state(client_id, "error")
            await broadcast_agent_telemetry(
                client_id, action=f"Browser {action} error: HTTP {resp.status_code}"
            )
            return {
                "status": "error",
                "message": f"Status {resp.status_code}",
            }

        except Exception as e:
            await broadcast_agent_state(client_id, "error")
            await broadcast_agent_telemetry(
                client_id, action=f"Browser {action} exception: {str(e)[:80]}"
            )
            return {"status": "error", "message": str(e)[:200]}

    # ---- LLM Access via harnessGPT Bridge ----

    async def llm_chat(self, prompt: str, model: str = "gemma4:12b",
                        system_msg: str = "You are a helpful research assistant.",
                        temperature: float = 0.7) -> Dict:
        """
        Send a chat request through Harness's LLM bridge.
        Supports local and cloud models through a unified API.

        Args:
            prompt: User message
            model: Model ID (gemma4:12b, gpt-4o, claude-3.5-sonnet, etc.)
            system_msg: System instructions
            temperature: Creativity (0.0-1.0)

        Returns:
            Dict with the model's response
        """
        if not await self.is_available():
            return {"status": "unavailable", "message": "Harness gateway not reachable"}

        client = await self._get_client()
        try:
            resp = await client.post(
                f"{self.gateway_url}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": temperature,
                },
                timeout=60.0,
            )

            if resp.status_code == 200:
                data = resp.json()
                return {
                    "status": "success",
                    "model": model,
                    "content": data["choices"][0]["message"]["content"],
                    "usage": data.get("usage", {}),
                }

            return {"status": "error", "message": f"Status {resp.status_code}"}

        except Exception as e:
            return {"status": "error", "message": str(e)[:200]}

    # ---- Shared Context Store ----

    async def store_context(self, key: str, value: str,
                             tags: List[str] = None) -> Dict:
        """
        Store context in Harness's shared memory.
        All connected agents can access this context.

        Args:
            key: Context identifier
            value: Context content
            tags: Optional tags for categorization
        """
        if not await self.is_available():
            return {"status": "unavailable"}

        client = await self._get_client()
        try:
            resp = await client.post(
                f"{self.gateway_url}/v1/memory",
                json={"key": key, "value": value, "tags": tags or []},
                timeout=10.0,
            )
            return {"status": "stored" if resp.status_code == 200 else "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)[:100]}

    async def search_context(self, query: str) -> Dict:
        """
        Search Harness's shared memory for relevant context.
        """
        if not await self.is_available():
            return {"status": "unavailable", "results": []}

        client = await self._get_client()
        try:
            resp = await client.post(
                f"{self.gateway_url}/v1/memory/search",
                json={"query": query},
                timeout=10.0,
            )
            if resp.status_code == 200:
                return {"status": "success", "results": resp.json()}
            return {"status": "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)[:100]}

    # ---- Workflow Execution ----

    async def execute_workflow(self, workflow_id: str) -> Dict:
        """
        Execute a pre-defined Harness workflow.
        Useful for complex multi-step research pipelines.
        """
        if not await self.is_available():
            return {"status": "unavailable"}

        client = await self._get_client()
        try:
            resp = await client.post(
                f"{self.gateway_url}/v1/workflows/{workflow_id}/run",
                timeout=300.0,
            )
            return {"status": "success" if resp.status_code == 200 else "error",
                    "workflow_id": workflow_id}
        except Exception as e:
            return {"status": "error", "message": str(e)[:100]}

    # ---- Jambubrowser Research Integration ----

    async def jambu_research(self, query: str,
                              use_swarm: bool = True,
                              domain: str = "general",
                              client_id: str = "default") -> Dict:
        """
        Jambubrowser's primary research entry point via Harness.
        Combines multi-agent swarm with domain-specific routing.

        Args:
            query: Research question
            use_swarm: Use parallel agents (True) or single agent (False)
            domain: Research domain for routing (general, academic, coding, security)
            client_id: WebSocket client_id for state/telemetry broadcasts

        Returns:
            Synthesized research results
        """
        if not await self.is_available():
            await broadcast_agent_state(client_id, "error")
            await broadcast_agent_telemetry(
                client_id, action="Harness unavailable, fallback to built-in"
            )
            return {
                "status": "unavailable",
                "message": "Harness not available. Using built-in research engine.",
                "query": query,
            }

        domain_connectors = {
            "academic": ["hermes", "claude"],
            "coding": ["hermes", "codex"],
            "security": ["hermes", "opencode"],
            "general": DEFAULT_CONNECTORS,
        }

        connectors = domain_connectors.get(domain, DEFAULT_CONNECTORS)
        await broadcast_agent_telemetry(
            client_id,
            action=f"Routing to {domain} swarm ({len(connectors)} connectors)",
        )

        if use_swarm:
            result = await self.research_swarm(query, connectors, judge=True, client_id=client_id)
        else:
            result = await self.research_single(query, connectors[0], client_id=client_id)

        # Store the research context in shared memory
        if result.get("status") == "success":
            await self.store_context(
                key=f"jambu_research_{query[:50]}",
                value=json.dumps(result)[:5000],
                tags=["jambubrowser", domain, "research"],
            )

        return result

    async def close(self):
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None


# Module-level singleton
_bridge: Optional[HarnessBridge] = None


def get_harness_bridge() -> HarnessBridge:
    global _bridge
    if _bridge is None:
        _bridge = HarnessBridge()
    return _bridge
