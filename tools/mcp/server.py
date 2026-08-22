"""
Jambubrowser MCP Server
=======================

Exposes all Jambubrowser features as MCP tools for AI assistants.
Wraps the FastAPI backend running on localhost:8001.

Usage:
    python3 -m tools.mcp.server                    # stdio transport
    JAMBU_MCP_TRANSPORT=http python3 -m tools.mcp.server  # HTTP/SSE
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

JAMBU_BACKEND = os.environ.get("JAMBU_BACKEND_URL", "http://localhost:8001")
REQUEST_TIMEOUT = float(os.environ.get("JAMBU_MCP_TIMEOUT", "30"))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("jambu_mcp")

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

app = Server("jambubrowser")

# ---------------------------------------------------------------------------
# HTTP client helper
# ---------------------------------------------------------------------------


async def _get(path: str, params: dict | None = None) -> dict:
    """GET request to Jambubrowser backend."""
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(f"{JAMBU_BACKEND}{path}", params=params)
        resp.raise_for_status()
        return resp.json()


async def _post(path: str, data: dict | None = None) -> dict:
    """POST request to Jambubrowser backend."""
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.post(f"{JAMBU_BACKEND}{path}", json=data or {})
        resp.raise_for_status()
        return resp.json()


async def _put(path: str, data: dict | None = None) -> dict:
    """PUT request to Jambubrowser backend."""
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.put(f"{JAMBU_BACKEND}{path}", json=data or {})
        resp.raise_for_status()
        return resp.json()


async def _delete(path: str, params: dict | None = None) -> dict:
    """DELETE request to Jambubrowser backend."""
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.delete(f"{JAMBU_BACKEND}{path}", params=params)
        resp.raise_for_status()
        return resp.json()


def _ok(data: Any) -> list[TextContent]:
    """Wrap result as MCP TextContent."""
    text = json.dumps(data, indent=2, default=str) if not isinstance(data, str) else data
    return [TextContent(type="text", text=text)]


def _err(msg: str) -> list[TextContent]:
    """Wrap error as MCP TextContent."""
    return [TextContent(type="text", text=f"ERROR: {msg}")]


# ---------------------------------------------------------------------------
# Tool definitions — comprehensive coverage of all Jambubrowser features
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    # ── System ──────────────────────────────────────────────────────────
    Tool(
        name="jambu_health",
        description="Check Jambubrowser backend health status (CPU, RAM, uptime).",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_stats",
        description="Get database statistics: document count, active missions, credentials, browser sessions.",
        inputSchema={"type": "object", "properties": {}},
    ),

    # ── Research ────────────────────────────────────────────────────────
    Tool(
        name="jambu_search",
        description="Search the web via multi-engine search (SearXNG → DuckDuckGo → Google). Returns ranked results with titles, URLs, and snippets.",
        inputSchema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "engine": {"type": "string", "enum": ["auto", "searxng", "duckduckgo", "google"], "default": "auto"},
            },
        },
    ),
    Tool(
        name="jambu_scrape",
        description="Scrape a URL and return clean markdown content. Uses Playwright for JS-rendered pages.",
        inputSchema={
            "type": "object",
            "required": ["url"],
            "properties": {
                "url": {"type": "string", "description": "URL to scrape"},
            },
        },
    ),
    Tool(
        name="jambu_research",
        description="Run autonomous research on a topic. Decomposes query, searches, scrapes, and synthesizes findings.",
        inputSchema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "description": "Research question or topic"},
                "brain_only": {"type": "boolean", "default": False, "description": "If true, only search local knowledge (no web access)"},
                "llm_provider": {"type": "string", "default": "auto", "description": "LLM provider for synthesis"},
            },
        },
    ),

    # ── Browser Automation ──────────────────────────────────────────────
    Tool(
        name="jambu_browser_act",
        description="Perform browser actions: navigate, click, type, scroll on a web page.",
        inputSchema={
            "type": "object",
            "required": ["url", "actions"],
            "properties": {
                "url": {"type": "string", "description": "Target URL"},
                "actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": ["navigate", "click", "type", "scroll", "wait"]},
                            "selector": {"type": "string"},
                            "value": {"type": "string"},
                        },
                    },
                    "description": "List of actions to perform",
                },
            },
        },
    ),
    Tool(
        name="jambu_browser_login",
        description="Login to a website using credentials from the encrypted vault.",
        inputSchema={
            "type": "object",
            "required": ["url", "username"],
            "properties": {
                "url": {"type": "string", "description": "Login URL"},
                "username": {"type": "string", "description": "Username or email"},
                "password": {"type": "string", "description": "Password (or retrieve from vault)"},
            },
        },
    ),

    # ── Privacy ─────────────────────────────────────────────────────────
    Tool(
        name="jambu_privacy_report",
        description="Get full privacy report: mode, PII detections, blocked requests, audit stats.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_privacy_set_mode",
        description="Set privacy mode: standard, enhanced, maximum, or local_only.",
        inputSchema={
            "type": "object",
            "required": ["mode"],
            "properties": {
                "mode": {"type": "string", "enum": ["standard", "enhanced", "maximum", "local_only"]},
            },
        },
    ),
    Tool(
        name="jambu_privacy_check_url",
        description="Check if a URL is allowed under the current privacy mode.",
        inputSchema={
            "type": "object",
            "required": ["url"],
            "properties": {
                "url": {"type": "string", "description": "URL to check"},
            },
        },
    ),

    # ── Audit ───────────────────────────────────────────────────────────
    Tool(
        name="jambu_audit_stats",
        description="Get audit log statistics: total entries, categories, retention period.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_audit_log",
        description="Retrieve audit log entries with optional category filter.",
        inputSchema={
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": ["all", "research", "browser", "credential", "network", "privacy", "system", "error"], "default": "all"},
                "limit": {"type": "integer", "default": 50},
            },
        },
    ),
    Tool(
        name="jambu_audit_verify",
        description="Verify the tamper-evident hash chain integrity of the audit log.",
        inputSchema={"type": "object", "properties": {}},
    ),

    # ── Credential Vault ────────────────────────────────────────────────
    Tool(
        name="jambu_vault_status",
        description="Check vault lock status and access log.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_vault_unlock",
        description="Unlock the credential vault with master password.",
        inputSchema={
            "type": "object",
            "required": ["password"],
            "properties": {
                "password": {"type": "string", "description": "Master password"},
            },
        },
    ),
    Tool(
        name="jambu_vault_lock",
        description="Lock the credential vault.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_vault_domains",
        description="List all domains with stored credentials.",
        inputSchema={"type": "object", "properties": {}},
    ),

    # ── Security ────────────────────────────────────────────────────────
    Tool(
        name="jambu_security_verify",
        description="Verify supply chain integrity: check hashes of all Python dependencies.",
        inputSchema={"type": "object", "properties": {}},
    ),

    # ── Fingerprint ─────────────────────────────────────────────────────
    Tool(
        name="jambu_fingerprint_generate",
        description="Generate a new browser fingerprint profile for session isolation.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "default": "default"},
            },
        },
    ),
    Tool(
        name="jambu_fingerprint_rotate",
        description="Rotate to a new fingerprint for the current session.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "default": "default"},
            },
        },
    ),

    # ── Knowledge Graph ─────────────────────────────────────────────────
    Tool(
        name="jambu_knowledge_ingest",
        description="Ingest content into the knowledge graph with entity extraction.",
        inputSchema={
            "type": "object",
            "required": ["text"],
            "properties": {
                "text": {"type": "string", "description": "Text content to ingest"},
                "url": {"type": "string", "default": "", "description": "Source URL (optional)"},
            },
        },
    ),
    Tool(
        name="jambu_knowledge_graph",
        description="Get knowledge graph visualization data: nodes and edges.",
        inputSchema={"type": "object", "properties": {}},
    ),

    # ── Missions ────────────────────────────────────────────────────────
    Tool(
        name="jambu_mission_schedule",
        description="Schedule a recurring research mission.",
        inputSchema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "description": "Research query"},
                "schedule": {"type": "string", "default": "none", "description": "Cron expression or 'none'"},
                "priority": {"type": "integer", "default": 1},
            },
        },
    ),
    Tool(
        name="jambu_mission_list",
        description="List all scheduled missions with status and run history.",
        inputSchema={"type": "object", "properties": {}},
    ),

    # ── Consensus ───────────────────────────────────────────────────────
    Tool(
        name="jambu_consensus_propose",
        description="Create a proposal for multi-node consensus voting.",
        inputSchema={
            "type": "object",
            "required": ["title", "description"],
            "properties": {
                "title": {"type": "string", "description": "Proposal title"},
                "description": {"type": "string", "description": "Proposal details"},
                "proposer": {"type": "string", "default": "mcp"},
            },
        },
    ),
    Tool(
        name="jambu_consensus_vote",
        description="Cast a vote on an existing proposal.",
        inputSchema={
            "type": "object",
            "required": ["proposal_id", "vote"],
            "properties": {
                "proposal_id": {"type": "string", "description": "Proposal ID"},
                "vote": {"type": "string", "enum": ["approve", "reject", "abstain"]},
                "voter": {"type": "string", "default": "mcp"},
            },
        },
    ),

    # ── MLX (Apple Silicon LLM) ────────────────────────────────────────
    Tool(
        name="jambu_mlx_status",
        description="Get MLX provider status: server running, available models, cached models.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_mlx_generate",
        description="Generate text using MLX local LLM (Gemma 3 on Apple Silicon).",
        inputSchema={
            "type": "object",
            "required": ["prompt"],
            "properties": {
                "prompt": {"type": "string", "description": "Text prompt"},
                "model": {"type": "string", "default": "gemma3:12b", "description": "Model ID"},
                "max_tokens": {"type": "integer", "default": 512},
                "temperature": {"type": "number", "default": 0.7},
            },
        },
    ),

    # ── Memory (v3) ────────────────────────────────────────────────────
    Tool(
        name="jambu_memory_profile",
        description="Get or update user memory profile (interests, expertise, preferences).",
        inputSchema={
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "default": "default"},
                "display_name": {"type": "string"},
                "interests": {"type": "array", "items": {"type": "string"}},
                "expertise": {"type": "array", "items": {"type": "string"}},
            },
        },
    ),
    Tool(
        name="jambu_memory_sessions",
        description="List memory sessions with recent activity.",
        inputSchema={
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "default": "default"},
                "limit": {"type": "integer", "default": 10},
            },
        },
    ),
    Tool(
        name="jambu_memory_store",
        description="Store a semantic memory entry (fact, preference, observation).",
        inputSchema={
            "type": "object",
            "required": ["content"],
            "properties": {
                "content": {"type": "string", "description": "Memory content to store"},
                "user_id": {"type": "string", "default": "default"},
                "category": {"type": "string", "default": "general"},
                "importance": {"type": "number", "default": 0.5, "minimum": 0, "maximum": 1},
            },
        },
    ),
    Tool(
        name="jambu_memory_recall",
        description="Recall relevant memories for a query using hybrid retrieval (vector + recency + FTS).",
        inputSchema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "description": "Recall query"},
                "user_id": {"type": "string", "default": "default"},
                "limit": {"type": "integer", "default": 5},
            },
        },
    ),
    Tool(
        name="jambu_memory_stats",
        description="Get memory system statistics: total entries, by category, by user.",
        inputSchema={
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "default": "default"},
            },
        },
    ),

    # ── LLM (v2) ───────────────────────────────────────────────────────
    Tool(
        name="jambu_llm_chat",
        description="Send a chat message to any LLM provider (Anthropic, OpenAI, Ollama, MLX, MiniMax, Mock).",
        inputSchema={
            "type": "object",
            "required": ["messages"],
            "properties": {
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string", "enum": ["user", "assistant", "system"]},
                            "content": {"type": "string"},
                        },
                    },
                    "description": "Chat messages",
                },
                "provider": {"type": "string", "default": "auto", "description": "LLM provider"},
                "model": {"type": "string", "description": "Override model name"},
                "stream": {"type": "boolean", "default": False},
            },
        },
    ),

    # ── Agent (v2) ──────────────────────────────────────────────────────
    Tool(
        name="jambu_agent_run",
        description="Run the ReAct agent loop: plan → execute → verify → replan. Streams SSE events with tool calls and reasoning.",
        inputSchema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "description": "Research query or task"},
                "provider": {"type": "string", "default": "auto", "description": "LLM provider"},
                "max_steps": {"type": "integer", "default": 10, "description": "Max reasoning steps"},
                "user_id": {"type": "string", "default": "default"},
            },
        },
    ),

    # ── Vision ──────────────────────────────────────────────────────────
    Tool(
        name="jambu_vision_ocr",
        description="Extract text from an image using OCR.",
        inputSchema={
            "type": "object",
            "required": ["image_url"],
            "properties": {
                "image_url": {"type": "string", "description": "URL or base64 of image"},
            },
        },
    ),
    Tool(
        name="jambu_vision_ui_elements",
        description="Detect UI elements in a screenshot for automation.",
        inputSchema={
            "type": "object",
            "required": ["image_url"],
            "properties": {
                "image_url": {"type": "string", "description": "URL or base64 of screenshot"},
            },
        },
    ),

    # ── Computer Control ────────────────────────────────────────────────
    Tool(
        name="jambu_computer_capture",
        description="Capture the current screen (macOS only).",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_computer_mouse",
        description="Control mouse: click, move, drag at screen coordinates.",
        inputSchema={
            "type": "object",
            "required": ["action", "x", "y"],
            "properties": {
                "action": {"type": "string", "enum": ["click", "double_click", "right_click", "move", "drag"]},
                "x": {"type": "integer", "description": "X coordinate"},
                "y": {"type": "integer", "description": "Y coordinate"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
            },
        },
    ),
    Tool(
        name="jambu_computer_keyboard",
        description="Type text or press a key on the keyboard.",
        inputSchema={
            "type": "object",
            "required": ["text"],
            "properties": {
                "text": {"type": "string", "description": "Text to type or key name (e.g., 'Enter', 'Cmd+c')"},
            },
        },
    ),

    # ── Knowledge Graph (extended) ──────────────────────────────────────
    Tool(
        name="jambu_knowledge_search",
        description="Search entities in the knowledge graph.",
        inputSchema={"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}},
    ),
    Tool(
        name="jambu_knowledge_clusters",
        description="Get topic clusters from the knowledge graph.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_knowledge_entity",
        description="Get a specific entity and its relationships.",
        inputSchema={"type": "object", "required": ["entity_id"], "properties": {"entity_id": {"type": "string"}}},
    ),
    Tool(
        name="jambu_knowledge_stats",
        description="Get knowledge graph statistics.",
        inputSchema={"type": "object", "properties": {}},
    ),

    # ── V2 Memory (extended) ────────────────────────────────────────────
    Tool(
        name="jambu_memory_session_detail",
        description="Fetch a specific memory session by ID.",
        inputSchema={"type": "object", "required": ["session_id"], "properties": {
            "session_id": {"type": "string"}, "user_id": {"type": "string", "default": "default"},
        }},
    ),
    Tool(
        name="jambu_memory_session_update",
        description="Update a memory session (creates if missing).",
        inputSchema={"type": "object", "required": ["session_id"], "properties": {
            "session_id": {"type": "string"}, "user_id": {"type": "string", "default": "default"},
            "summary": {"type": "string"}, "key_facts": {"type": "array", "items": {"type": "string"}},
        }},
    ),
    Tool(
        name="jambu_memory_forget",
        description="Delete (forget) a semantic memory entry.",
        inputSchema={"type": "object", "required": ["mem_id"], "properties": {
            "mem_id": {"type": "string"}, "user_id": {"type": "string", "default": "default"},
        }},
    ),
    Tool(
        name="jambu_memory_procedural",
        description="List learned procedural patterns (action→outcome mappings).",
        inputSchema={"type": "object", "properties": {"user_id": {"type": "string", "default": "default"}}},
    ),
    Tool(
        name="jambu_memory_procedural_record",
        description="Record the outcome of an action attempt for procedural learning.",
        inputSchema={"type": "object", "required": ["action", "outcome"], "properties": {
            "user_id": {"type": "string", "default": "default"},
            "action": {"type": "string"}, "outcome": {"type": "string"}, "context": {"type": "string"},
        }},
    ),

    # ── V2 Agent/LLM (extended) ─────────────────────────────────────────
    Tool(
        name="jambu_list_agent_tools",
        description="List tools available to the ReAct agent.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_agent_history",
        description="Recent agent run history.",
        inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "default": 10}}},
    ),
    Tool(
        name="jambu_list_llm_providers",
        description="List available LLM providers and their default models.",
        inputSchema={"type": "object", "properties": {}},
    ),

    # ── Goal Orchestrator ───────────────────────────────────────────────
    Tool(
        name="jambu_goal_set",
        description="Set the browser's sovereign goal.",
        inputSchema={"type": "object", "required": ["goal"], "properties": {
            "goal": {"type": "string"}, "priority": {"type": "string", "default": "medium"},
        }},
    ),
    Tool(
        name="jambu_goal_active",
        description="Get the currently active sovereign goal.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_goal_list",
        description="List all goals (filterable by status).",
        inputSchema={"type": "object", "properties": {"status": {"type": "string"}}},
    ),
    Tool(
        name="jambu_goal_achieve",
        description="Mark the active goal as achieved.",
        inputSchema={"type": "object", "required": ["goal_id"], "properties": {
            "goal_id": {"type": "string"}, "notes": {"type": "string"},
        }},
    ),
    Tool(
        name="jambu_goal_block",
        description="Mark a goal as blocked.",
        inputSchema={"type": "object", "required": ["goal_id", "reason"], "properties": {
            "goal_id": {"type": "string"}, "reason": {"type": "string"},
        }},
    ),
    Tool(
        name="jambu_goal_approach",
        description="Record a new approach attempt for a goal.",
        inputSchema={"type": "object", "required": ["goal_id", "approach"], "properties": {
            "goal_id": {"type": "string"}, "approach": {"type": "string"},
        }},
    ),
    Tool(
        name="jambu_goal_approach_update",
        description="Update an approach with results/learning.",
        inputSchema={"type": "object", "required": ["approach_id", "result"], "properties": {
            "approach_id": {"type": "string"}, "result": {"type": "string"}, "learning": {"type": "string"},
        }},
    ),
    Tool(
        name="jambu_goal_approaches",
        description="Get approaches for a goal.",
        inputSchema={"type": "object", "required": ["goal_id"], "properties": {"goal_id": {"type": "string"}}},
    ),
    Tool(
        name="jambu_goal_fallback",
        description="Generate fallback strategies when a goal is blocked.",
        inputSchema={"type": "object", "required": ["goal_id"], "properties": {"goal_id": {"type": "string"}}},
    ),
    Tool(
        name="jambu_goal_inject",
        description="Preview the goal-injected prompt.",
        inputSchema={"type": "object", "required": ["goal_id"], "properties": {"goal_id": {"type": "string"}}},
    ),
    Tool(
        name="jambu_goal_context",
        description="Get condensed goal context for LLM.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_goal_learnings",
        description="Query RAG for past iteration learnings.",
        inputSchema={"type": "object", "properties": {"query": {"type": "string"}}},
    ),

    # ── Consensus (extended) ────────────────────────────────────────────
    Tool(
        name="jambu_consensus_list",
        description="List all consensus proposals.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_consensus_proposal",
        description="Get a specific consensus proposal by ID.",
        inputSchema={"type": "object", "required": ["proposal_id"], "properties": {"proposal_id": {"type": "string"}}},
    ),
    Tool(
        name="jambu_consensus_tally",
        description="Tally votes on a proposal.",
        inputSchema={"type": "object", "required": ["proposal_id"], "properties": {"proposal_id": {"type": "string"}}},
    ),
    Tool(
        name="jambu_consensus_check",
        description="Check if consensus has been reached on a proposal.",
        inputSchema={"type": "object", "required": ["proposal_id"], "properties": {"proposal_id": {"type": "string"}}},
    ),
    Tool(
        name="jambu_consensus_close",
        description="Close a consensus proposal.",
        inputSchema={"type": "object", "required": ["proposal_id"], "properties": {
            "proposal_id": {"type": "string"}, "decision": {"type": "string"},
        }},
    ),

    # ── Shield ──────────────────────────────────────────────────────────
    Tool(
        name="jambu_shield_check",
        description="Assess risk of a URL using the risk shield.",
        inputSchema={"type": "object", "required": ["url"], "properties": {
            "url": {"type": "string"}, "context": {"type": "string"},
        }},
    ),
    Tool(
        name="jambu_shield_batch",
        description="Batch risk assessment for multiple URLs.",
        inputSchema={"type": "object", "required": ["urls"], "properties": {
            "urls": {"type": "array", "items": {"type": "string"}},
        }},
    ),
    Tool(
        name="jambu_shield_stats",
        description="Get risk shield cache statistics.",
        inputSchema={"type": "object", "properties": {}},
    ),

    # ── Shadow Agent ────────────────────────────────────────────────────
    Tool(
        name="jambu_shadow_start",
        description="Start the autonomous shadow browser background loop.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_shadow_stop",
        description="Stop the shadow browser background loop.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_shadow_stats",
        description="Get shadow browser statistics.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_shadow_interests_get",
        description="List interest profiles for the shadow browser.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_shadow_interests_set",
        description="Set interest topics for the shadow browser.",
        inputSchema={"type": "object", "required": ["interests"], "properties": {
            "interests": {"type": "array", "items": {"type": "string"}},
        }},
    ),

    # ── Local Tools ─────────────────────────────────────────────────────
    Tool(
        name="jambu_obsidian_create",
        description="Create a new Obsidian note.",
        inputSchema={"type": "object", "required": ["title", "content"], "properties": {
            "title": {"type": "string"}, "content": {"type": "string"}, "folder": {"type": "string"},
        }},
    ),
    Tool(
        name="jambu_obsidian_append",
        description="Append content to an existing Obsidian note.",
        inputSchema={"type": "object", "required": ["title", "content"], "properties": {
            "title": {"type": "string"}, "content": {"type": "string"},
        }},
    ),
    Tool(
        name="jambu_obsidian_read",
        description="Read an Obsidian note by title.",
        inputSchema={"type": "object", "required": ["title"], "properties": {"title": {"type": "string"}}},
    ),
    Tool(
        name="jambu_obsidian_search",
        description="Search the Obsidian vault.",
        inputSchema={"type": "object", "required": ["q"], "properties": {"q": {"type": "string"}}},
    ),
    Tool(
        name="jambu_obsidian_stats",
        description="Get Obsidian vault statistics.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_clipboard_copy",
        description="Copy text to the system clipboard.",
        inputSchema={"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}},
    ),
    Tool(
        name="jambu_clipboard_paste",
        description="Get the current system clipboard contents.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_notes_save",
        description="Save research as a local markdown file.",
        inputSchema={"type": "object", "required": ["title", "content"], "properties": {
            "title": {"type": "string"}, "content": {"type": "string"},
            "format": {"type": "string", "default": "markdown"},
        }},
    ),
    Tool(
        name="jambu_reminders_create",
        description="Create a macOS Reminder.",
        inputSchema={"type": "object", "required": ["title"], "properties": {
            "title": {"type": "string"}, "notes": {"type": "string"}, "due_date": {"type": "string"},
        }},
    ),

    # ── Media / YouTube ─────────────────────────────────────────────────
    Tool(
        name="jambu_youtube_analyze",
        description="Analyze a YouTube video (transcript + metadata + optional summary).",
        inputSchema={"type": "object", "required": ["url"], "properties": {
            "url": {"type": "string"}, "summarize": {"type": "boolean", "default": True},
        }},
    ),
    Tool(
        name="jambu_youtube_transcript",
        description="Get the transcript of a YouTube video.",
        inputSchema={"type": "object", "required": ["url"], "properties": {"url": {"type": "string"}}},
    ),
    Tool(
        name="jambu_youtube_search",
        description="Search within a YouTube video's transcript.",
        inputSchema={"type": "object", "required": ["url", "q"], "properties": {
            "url": {"type": "string"}, "q": {"type": "string"},
        }},
    ),

    # ── Models / MLX (extended) ─────────────────────────────────────────
    Tool(
        name="jambu_mlx_models",
        description="List available MLX models (definitions + cached).",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_mlx_server_start",
        description="Start the MLX LM server.",
        inputSchema={"type": "object", "properties": {
            "model": {"type": "string"}, "port": {"type": "integer", "default": 8080},
        }},
    ),
    Tool(
        name="jambu_mlx_server_stop",
        description="Stop the MLX LM server.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_mlx_model_download",
        description="Download an MLX model from HuggingFace.",
        inputSchema={"type": "object", "required": ["model"], "properties": {"model": {"type": "string"}}},
    ),
    Tool(
        name="jambu_models_available",
        description="List available Gemma 3 models with specs.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_models_installed",
        description="List installed models across all providers.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_models_status",
        description="Get status of a specific model.",
        inputSchema={"type": "object", "required": ["model"], "properties": {"model": {"type": "string"}}},
    ),
    Tool(
        name="jambu_models_pull",
        description="Pull a model via Ollama.",
        inputSchema={"type": "object", "required": ["model"], "properties": {"model": {"type": "string"}}},
    ),
    Tool(
        name="jambu_models_recommend",
        description="Recommend a model based on system RAM.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_models_setup",
        description="One-click model setup.",
        inputSchema={"type": "object", "properties": {"model": {"type": "string"}}},
    ),
    Tool(
        name="jambu_models_providers",
        description="Check which LLM providers are available.",
        inputSchema={"type": "object", "properties": {}},
    ),

    # ── Multimodal ──────────────────────────────────────────────────────
    Tool(
        name="jambu_multimodal_image",
        description="Process an image (OCR/analysis/extraction).",
        inputSchema={"type": "object", "required": ["image_url"], "properties": {
            "image_url": {"type": "string"}, "prompt": {"type": "string"},
        }},
    ),
    Tool(
        name="jambu_multimodal_text",
        description="Process pasted text (URL detection, code recognition).",
        inputSchema={"type": "object", "required": ["text"], "properties": {
            "text": {"type": "string"}, "prompt": {"type": "string"},
        }},
    ),
    Tool(
        name="jambu_multimodal_file",
        description="Process a file (CSV, JSON, markdown, code, text).",
        inputSchema={"type": "object", "required": ["file_path"], "properties": {
            "file_path": {"type": "string"}, "prompt": {"type": "string"},
        }},
    ),

    # ── P2P / Peers ─────────────────────────────────────────────────────
    Tool(
        name="jambu_p2p_info",
        description="Get this node's info for peer discovery.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_p2p_discover",
        description="Trigger peer discovery on the LAN.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_p2p_peers",
        description="List all known peers.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_p2p_query",
        description="Query a specific peer.",
        inputSchema={"type": "object", "required": ["peer_id", "query"], "properties": {
            "peer_id": {"type": "string"}, "query": {"type": "string"},
        }},
    ),
    Tool(
        name="jambu_p2p_start_discovery",
        description="Start background peer discovery loop.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_p2p_stats",
        description="Get P2P network statistics.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_peer_info",
        description="Get peer info handler (for other nodes).",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_peer_query",
        description="Federated RAG query from a peer.",
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string"}, "context": {"type": "string"},
        }},
    ),
    Tool(
        name="jambu_peer_sync",
        description="Anonymized research vector exchange with a peer.",
        inputSchema={"type": "object", "required": ["data"], "properties": {"data": {"type": "object"}}},
    ),

    # ── Harness ─────────────────────────────────────────────────────────
    Tool(
        name="jambu_harness_status",
        description="Harness gateway availability and connector list.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_harness_research",
        description="Delegate research to Harness multi-agent swarm.",
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string"},
            "connectors": {"type": "array", "items": {"type": "string"}},
        }},
    ),
    Tool(
        name="jambu_harness_research_single",
        description="Delegate to a single Harness connector.",
        inputSchema={"type": "object", "required": ["query", "connector"], "properties": {
            "query": {"type": "string"}, "connector": {"type": "string"},
        }},
    ),
    Tool(
        name="jambu_harness_browse",
        description="Use Harness Playwright MCP for browser automation.",
        inputSchema={"type": "object", "required": ["url"], "properties": {
            "url": {"type": "string"},
            "actions": {"type": "array", "items": {"type": "object"}},
        }},
    ),
    Tool(
        name="jambu_harness_llm",
        description="Send LLM request through Harness bridge.",
        inputSchema={"type": "object", "required": ["prompt"], "properties": {
            "prompt": {"type": "string"}, "model": {"type": "string"}, "provider": {"type": "string"},
        }},
    ),
    Tool(
        name="jambu_harness_context_store",
        description="Store context in Harness shared memory.",
        inputSchema={"type": "object", "required": ["key", "value"], "properties": {
            "key": {"type": "string"}, "value": {"type": "string"},
            "metadata": {"type": "object"},
        }},
    ),
    Tool(
        name="jambu_harness_context_search",
        description="Search Harness shared memory.",
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string"}, "limit": {"type": "integer", "default": 10},
        }},
    ),

    # ── Plugins ─────────────────────────────────────────────────────────
    Tool(
        name="jambu_plugins_list",
        description="List all available plugins.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_plugins_get",
        description="Get detailed info about a plugin.",
        inputSchema={"type": "object", "required": ["plugin_name"], "properties": {"plugin_name": {"type": "string"}}},
    ),
    Tool(
        name="jambu_plugins_execute",
        description="Execute a plugin by name.",
        inputSchema={"type": "object", "required": ["plugin_name"], "properties": {
            "plugin_name": {"type": "string"}, "args": {"type": "object"},
        }},
    ),
    Tool(
        name="jambu_plugins_chain",
        description="Execute a chain of plugins with output passing.",
        inputSchema={"type": "object", "required": ["plugins"], "properties": {
            "plugins": {"type": "array", "items": {"type": "string"}},
            "input_data": {"type": "object"},
        }},
    ),

    # ── Forms ───────────────────────────────────────────────────────────
    Tool(
        name="jambu_forms_detect",
        description="Detect and classify forms on a page, match with vault credentials.",
        inputSchema={"type": "object", "required": ["url"], "properties": {"url": {"type": "string"}}},
    ),
    Tool(
        name="jambu_forms_fill_script",
        description="Generate JavaScript to fill a page's login form with vault credentials.",
        inputSchema={"type": "object", "required": ["url"], "properties": {
            "url": {"type": "string", "description": "Page URL whose login form should be filled"},
        }},
    ),

    # ── Notifications ───────────────────────────────────────────────────
    Tool(
        name="jambu_notifications_history",
        description="Get notification history.",
        inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "default": 20}}},
    ),
    Tool(
        name="jambu_notifications_send",
        description="Send a test/system notification.",
        inputSchema={"type": "object", "required": ["title", "message"], "properties": {
            "title": {"type": "string"}, "message": {"type": "string"},
            "level": {"type": "string", "default": "info"},
        }},
    ),

    # ── Fingerprint (extended) ──────────────────────────────────────────
    Tool(
        name="jambu_fingerprint_list",
        description="List all generated fingerprint profiles.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_fingerprint_profile",
        description="Get a specific fingerprint profile by ID.",
        inputSchema={"type": "object", "required": ["profile_id"], "properties": {"profile_id": {"type": "string"}}},
    ),

    # ── Federated RAG ───────────────────────────────────────────────────
    Tool(
        name="jambu_federated_query",
        description="Send anonymized query to trusted peers.",
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string"}, "trust_level": {"type": "string", "default": "medium"},
        }},
    ),
    Tool(
        name="jambu_federated_stats",
        description="Get federated RAG statistics.",
        inputSchema={"type": "object", "properties": {}},
    ),

    # ── Skill Synthesis ─────────────────────────────────────────────────
    Tool(
        name="jambu_skill_synthesize",
        description="Autonomously synthesize a Python skill from a failure.",
        inputSchema={"type": "object", "required": ["description", "code"], "properties": {
            "description": {"type": "string"}, "code": {"type": "string"}, "tests": {"type": "string"},
        }},
    ),
    Tool(
        name="jambu_skill_list",
        description="List all synthesized skills.",
        inputSchema={"type": "object", "properties": {}},
    ),

    # ── Mission Control (extended) ──────────────────────────────────────
    Tool(
        name="jambu_mission_create",
        description="Register a background research mission.",
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string"}, "schedule": {"type": "string"},
            "priority": {"type": "integer", "default": 1},
        }},
    ),
    Tool(
        name="jambu_mission_stop",
        description="Stop a background mission.",
        inputSchema={"type": "object", "required": ["mission_id"], "properties": {"mission_id": {"type": "string"}}},
    ),
    Tool(
        name="jambu_mission_start_scheduler",
        description="Start the background mission scheduler loop.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_mission_stop_scheduler",
        description="Stop the background mission scheduler loop.",
        inputSchema={"type": "object", "properties": {}},
    ),

    # ── Tool Management ─────────────────────────────────────────────────
    Tool(
        name="jambu_tool_save",
        description="Persist an agent-written Python skill.",
        inputSchema={"type": "object", "required": ["name", "code"], "properties": {
            "name": {"type": "string"}, "code": {"type": "string"}, "description": {"type": "string"},
        }},
    ),
    Tool(
        name="jambu_tools_list",
        description="List all saved agent skills.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_tool_exec",
        description="Run a saved agent skill.",
        inputSchema={"type": "object", "required": ["name"], "properties": {
            "name": {"type": "string"}, "args": {"type": "object"},
        }},
    ),

    # ── System / Analytics ──────────────────────────────────────────────
    Tool(
        name="jambu_analytics_summary",
        description="Get analytics summary.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_benchmark",
        description="Run a simple system benchmark.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_vault_credential",
        description="Find the best matching credential for a URL.",
        inputSchema={"type": "object", "required": ["url"], "properties": {"url": {"type": "string"}}},
    ),
    Tool(
        name="jambu_security_verify_package",
        description="Verify a specific Python package's integrity.",
        inputSchema={"type": "object", "required": ["package_name"], "properties": {"package_name": {"type": "string"}}},
    ),
    Tool(
        name="jambu_llm_config",
        description="Get current LLM configuration with auto-detection.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_graph_data",
        description="Get node/edge data for 3D brain visualization.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_vision_verify",
        description="Verify screen state matches expected description.",
        inputSchema={"type": "object", "required": ["image_data", "expected"], "properties": {
            "image_data": {"type": "string"}, "expected": {"type": "string"},
        }},
    ),
    Tool(
        name="jambu_vision_analyze",
        description="Analyze image with vision model: returns UI elements + suggestions.",
        inputSchema={"type": "object", "required": ["image_data"], "properties": {
            "image_data": {"type": "string"}, "prompt": {"type": "string"},
        }},
    ),
    Tool(
        name="jambu_vision_grounding",
        description="Analyze page and suggest interactive elements via LLM.",
        inputSchema={"type": "object", "required": ["image_data"], "properties": {
            "image_data": {"type": "string"}, "task": {"type": "string"},
        }},
    ),
    Tool(
        name="jambu_computer_launch",
        description="Launch a macOS app by name.",
        inputSchema={"type": "object", "required": ["app_name"], "properties": {"app_name": {"type": "string"}}},
    ),
    Tool(
        name="jambu_computer_apps",
        description="List installed macOS applications.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="jambu_workflow_execute",
        description="Execute a multi-step browser workflow.",
        inputSchema={"type": "object", "required": ["url", "steps"], "properties": {
            "url": {"type": "string"},
            "steps": {"type": "array", "items": {"type": "object"}},
        }},
    ),
    Tool(
        name="jambu_memory_recall_legacy",
        description="Legacy cross-session semantic recall from the local knowledge vault.",
        inputSchema={"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}},
    ),
]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


async def _handle(tool_name: str, args: dict) -> list[TextContent]:
    """Route tool call to the appropriate API endpoint."""
    try:
        if tool_name == "jambu_health":
            return _ok(await _get("/health"))

        elif tool_name == "jambu_stats":
            return _ok(await _get("/stats"))

        # Research
        elif tool_name == "jambu_search":
            return _ok(await _get("/search", {"q": args["query"], "engine": args.get("engine", "auto")}))

        elif tool_name == "jambu_scrape":
            return _ok(await _post("/scrape", {"url": args["url"]}))

        elif tool_name == "jambu_research":
            return _ok(await _post("/research", {"query": args["query"], "brain_only": args.get("brain_only", False), "llm_provider": args.get("llm_provider", "auto")}))

        # Browser
        elif tool_name == "jambu_browser_act":
            return _ok(await _post("/act", {"url": args["url"], "actions": args["actions"]}))

        elif tool_name == "jambu_browser_login":
            return _ok(await _post("/login", {"url": args["url"], "username": args["username"], "password": args.get("password", "")}))

        # Privacy
        elif tool_name == "jambu_privacy_report":
            return _ok(await _get("/privacy/report"))

        elif tool_name == "jambu_privacy_set_mode":
            return _ok(await _post("/privacy/mode", {"mode": args["mode"]}))

        elif tool_name == "jambu_privacy_check_url":
            return _ok(await _get("/privacy/check", {"url": args["url"]}))

        # Audit
        elif tool_name == "jambu_audit_stats":
            return _ok(await _get("/audit/stats"))

        elif tool_name == "jambu_audit_log":
            params = {"limit": args.get("limit", 50)}
            if args.get("category", "all") != "all":
                params["category"] = args["category"]
            return _ok(await _get("/audit/log", params))

        elif tool_name == "jambu_audit_verify":
            return _ok(await _get("/audit/verify"))

        # Vault
        elif tool_name == "jambu_vault_status":
            return _ok(await _get("/vault/status"))

        elif tool_name == "jambu_vault_unlock":
            return _ok(await _post("/vault/unlock", {"master_password": args["password"]}))

        elif tool_name == "jambu_vault_lock":
            return _ok(await _post("/vault/lock"))

        elif tool_name == "jambu_vault_domains":
            return _ok(await _get("/vault/domains"))

        # Security
        elif tool_name == "jambu_security_verify":
            return _ok(await _get("/security/verify"))

        # Fingerprint
        elif tool_name == "jambu_fingerprint_generate":
            return _ok(await _post("/fingerprint/generate", {"session_id": args.get("session_id", "default")}))

        elif tool_name == "jambu_fingerprint_rotate":
            return _ok(await _post("/fingerprint/rotate", {"session_id": args.get("session_id", "default")}))

        # Knowledge
        elif tool_name == "jambu_knowledge_ingest":
            return _ok(await _post("/knowledge/ingest", {"text": args["text"], "url": args.get("url", "")}))

        elif tool_name == "jambu_knowledge_graph":
            return _ok(await _get("/knowledge/graph"))

        # Missions
        elif tool_name == "jambu_mission_schedule":
            return _ok(await _post("/mission/schedule", {"query": args["query"], "schedule": args.get("schedule", "none"), "priority": args.get("priority", 1)}))

        elif tool_name == "jambu_mission_list":
            return _ok(await _get("/mission/list"))

        # Consensus
        elif tool_name == "jambu_consensus_propose":
            return _ok(await _post("/consensus/propose", {"title": args["title"], "description": args["description"], "proposer": args.get("proposer", "mcp")}))

        elif tool_name == "jambu_consensus_vote":
            return _ok(await _post("/consensus/vote", {"proposal_id": args["proposal_id"], "vote": args["vote"], "voter": args.get("voter", "mcp")}))

        # MLX
        elif tool_name == "jambu_mlx_status":
            return _ok(await _get("/mlx/status"))

        elif tool_name == "jambu_mlx_generate":
            return _ok(await _post("/mlx/generate", {
                "prompt": args["prompt"],
                "model": args.get("model", "gemma3:12b"),
                "max_tokens": args.get("max_tokens", 512),
                "temperature": args.get("temperature", 0.7),
            }))

        # Memory
        elif tool_name == "jambu_memory_profile":
            data = {"user_id": args.get("user_id", "default")}
            if "display_name" in args:
                data["display_name"] = args["display_name"]
            if "interests" in args:
                data["interests"] = args["interests"]
            if "expertise" in args:
                data["expertise"] = args["expertise"]
            if len(data) > 1:
                return _ok(await _put("/v2/memory/profile", data))
            return _ok(await _get("/v2/memory/profile", {"user_id": args.get("user_id", "default")}))

        elif tool_name == "jambu_memory_sessions":
            return _ok(await _get("/v2/memory/sessions", {"user_id": args.get("user_id", "default"), "limit": args.get("limit", 10)}))

        elif tool_name == "jambu_memory_store":
            return _ok(await _post("/v2/memory/store", {
                "content": args["content"],
                "user_id": args.get("user_id", "default"),
                "category": args.get("category", "general"),
                "importance": args.get("importance", 0.5),
            }))

        elif tool_name == "jambu_memory_recall":
            return _ok(await _post("/v2/memory/recall", {
                "query": args["query"],
                "user_id": args.get("user_id", "default"),
                "k": args.get("limit", 5),
            }))

        elif tool_name == "jambu_memory_stats":
            return _ok(await _get("/v2/memory/stats", {"user_id": args.get("user_id", "default")}))

        # LLM
        elif tool_name == "jambu_llm_chat":
            return _ok(await _post("/v2/llm/chat", {
                "messages": args["messages"],
                "provider": args.get("provider", "auto"),
                "model": args.get("model"),
                "stream": args.get("stream", False),
            }))

        # Agent
        elif tool_name == "jambu_agent_run":
            return _ok(await _post("/v2/agent/run", {
                "query": args["query"],
                "provider": args.get("provider", "auto"),
                "max_steps": args.get("max_steps", 10),
                "user_id": args.get("user_id", "default"),
            }))

        # Vision
        elif tool_name == "jambu_vision_ocr":
            return _ok(await _post("/vision/ocr", {"image_data": args["image_url"]}))

        elif tool_name == "jambu_vision_ui_elements":
            return _ok(await _post("/vision/ui-elements", {"image_data": args["image_url"]}))

        # Computer
        elif tool_name == "jambu_computer_capture":
            return _ok(await _get("/computer/capture"))

        elif tool_name == "jambu_computer_mouse":
            return _ok(await _post("/computer/mouse", {
                "action": args["action"],
                "x": args["x"],
                "y": args["y"],
                "button": args.get("button", "left"),
            }))

        elif tool_name == "jambu_computer_keyboard":
            return _ok(await _post("/computer/keyboard", {"text": args["text"]}))

        # Knowledge (extended)
        elif tool_name == "jambu_knowledge_search":
            return _ok(await _get("/knowledge/search", {"query": args["query"]}))
        elif tool_name == "jambu_knowledge_clusters":
            return _ok(await _get("/knowledge/clusters"))
        elif tool_name == "jambu_knowledge_entity":
            return _ok(await _get(f"/knowledge/entity/{args['entity_id']}"))
        elif tool_name == "jambu_knowledge_stats":
            return _ok(await _get("/knowledge/stats"))

        # V2 Memory (extended)
        elif tool_name == "jambu_memory_session_detail":
            return _ok(await _get(f"/v2/memory/session/{args['session_id']}", {"user_id": args.get("user_id", "default")}))
        elif tool_name == "jambu_memory_session_update":
            data = {"user_id": args.get("user_id", "default")}
            if "summary" in args: data["summary"] = args["summary"]
            if "key_facts" in args: data["key_facts"] = args["key_facts"]
            return _ok(await _put(f"/v2/memory/session/{args['session_id']}", data))
        elif tool_name == "jambu_memory_forget":
            return _ok(await _delete(f"/v2/memory/{args['mem_id']}", {"user_id": args.get("user_id", "default")}))
        elif tool_name == "jambu_memory_procedural":
            return _ok(await _get("/v2/memory/procedural", {"user_id": args.get("user_id", "default")}))
        elif tool_name == "jambu_memory_procedural_record":
            return _ok(await _post("/v2/memory/procedural/record", {
                "user_id": args.get("user_id", "default"), "action": args["action"],
                "outcome": args["outcome"], "context": args.get("context", ""),
            }))

        # V2 Agent/LLM (extended)
        elif tool_name == "jambu_list_agent_tools":
            return _ok(await _get("/v2/agent/tools"))
        elif tool_name == "jambu_agent_history":
            return _ok(await _get("/v2/agent/history", {"limit": args.get("limit", 10)}))
        elif tool_name == "jambu_list_llm_providers":
            return _ok(await _get("/v2/llm/providers"))

        # Goal Orchestrator
        elif tool_name == "jambu_goal_set":
            return _ok(await _post("/goal/set", {"goal": args["goal"], "priority": args.get("priority", "medium")}))
        elif tool_name == "jambu_goal_active":
            return _ok(await _get("/goal/active"))
        elif tool_name == "jambu_goal_list":
            params = {}
            if args.get("status"): params["status"] = args["status"]
            return _ok(await _get("/goal/list", params))
        elif tool_name == "jambu_goal_achieve":
            return _ok(await _post("/goal/achieve", {"goal_id": args["goal_id"], "notes": args.get("notes", "")}))
        elif tool_name == "jambu_goal_block":
            return _ok(await _post("/goal/block", {"goal_id": args["goal_id"], "reason": args["reason"]}))
        elif tool_name == "jambu_goal_approach":
            return _ok(await _post("/goal/approach", {"goal_id": args["goal_id"], "approach": args["approach"]}))
        elif tool_name == "jambu_goal_approach_update":
            return _ok(await _post("/goal/approach/update", {
                "approach_id": args["approach_id"], "result": args["result"],
                "learning": args.get("learning", ""),
            }))
        elif tool_name == "jambu_goal_approaches":
            return _ok(await _get("/goal/approaches", {"goal_id": args["goal_id"]}))
        elif tool_name == "jambu_goal_fallback":
            return _ok(await _get("/goal/fallback", {"goal_id": args["goal_id"]}))
        elif tool_name == "jambu_goal_inject":
            return _ok(await _post("/goal/inject", {"goal_id": args["goal_id"]}))
        elif tool_name == "jambu_goal_context":
            return _ok(await _get("/goal/context"))
        elif tool_name == "jambu_goal_learnings":
            params = {}
            if args.get("query"): params["query"] = args["query"]
            return _ok(await _get("/goal/learnings", params))

        # Consensus (extended)
        elif tool_name == "jambu_consensus_list":
            return _ok(await _get("/consensus/list"))
        elif tool_name == "jambu_consensus_proposal":
            return _ok(await _get(f"/consensus/proposal/{args['proposal_id']}"))
        elif tool_name == "jambu_consensus_tally":
            return _ok(await _get(f"/consensus/tally/{args['proposal_id']}"))
        elif tool_name == "jambu_consensus_check":
            return _ok(await _get(f"/consensus/check/{args['proposal_id']}"))
        elif tool_name == "jambu_consensus_close":
            return _ok(await _post(f"/consensus/close/{args['proposal_id']}", {"decision": args.get("decision", "")}))

        # Shield
        elif tool_name == "jambu_shield_check":
            return _ok(await _post("/shield/check", {"url": args["url"], "context": args.get("context", "")}))
        elif tool_name == "jambu_shield_batch":
            return _ok(await _post("/shield/batch", {"urls": args["urls"]}))
        elif tool_name == "jambu_shield_stats":
            return _ok(await _get("/shield/stats"))

        # Shadow Agent
        elif tool_name == "jambu_shadow_start":
            return _ok(await _post("/shadow/start"))
        elif tool_name == "jambu_shadow_stop":
            return _ok(await _post("/shadow/stop"))
        elif tool_name == "jambu_shadow_stats":
            return _ok(await _get("/shadow/stats"))
        elif tool_name == "jambu_shadow_interests_get":
            return _ok(await _get("/shadow/interests"))
        elif tool_name == "jambu_shadow_interests_set":
            return _ok(await _post("/shadow/interests", {"interests": args["interests"]}))

        # Local Tools
        elif tool_name == "jambu_obsidian_create":
            return _ok(await _post("/local/obsidian/create", {"title": args["title"], "content": args["content"], "folder": args.get("folder", "")}))
        elif tool_name == "jambu_obsidian_append":
            return _ok(await _post("/local/obsidian/append", {"title": args["title"], "content": args["content"]}))
        elif tool_name == "jambu_obsidian_read":
            return _ok(await _get("/local/obsidian/read", {"title": args["title"]}))
        elif tool_name == "jambu_obsidian_search":
            return _ok(await _get("/local/obsidian/search", {"q": args["q"]}))
        elif tool_name == "jambu_obsidian_stats":
            return _ok(await _get("/local/obsidian/stats"))
        elif tool_name == "jambu_clipboard_copy":
            return _ok(await _post("/local/clipboard/copy", {"text": args["text"]}))
        elif tool_name == "jambu_clipboard_paste":
            return _ok(await _get("/local/clipboard/paste"))
        elif tool_name == "jambu_notes_save":
            return _ok(await _post("/local/notes/save", {"title": args["title"], "content": args["content"], "format": args.get("format", "markdown")}))
        elif tool_name == "jambu_reminders_create":
            return _ok(await _post("/local/reminders/create", {"title": args["title"], "notes": args.get("notes", ""), "due_date": args.get("due_date", "")}))

        # Media / YouTube
        elif tool_name == "jambu_youtube_analyze":
            return _ok(await _post("/media/youtube", {"url": args["url"], "summarize": args.get("summarize", True)}))
        elif tool_name == "jambu_youtube_transcript":
            return _ok(await _get("/media/youtube/transcript", {"url": args["url"]}))
        elif tool_name == "jambu_youtube_search":
            return _ok(await _get("/media/youtube/search", {"url": args["url"], "q": args["q"]}))

        # Models / MLX (extended)
        elif tool_name == "jambu_mlx_models":
            return _ok(await _get("/mlx/models"))
        elif tool_name == "jambu_mlx_server_start":
            return _ok(await _post("/mlx/server/start", {"model": args.get("model", ""), "port": args.get("port", 8080)}))
        elif tool_name == "jambu_mlx_server_stop":
            return _ok(await _post("/mlx/server/stop"))
        elif tool_name == "jambu_mlx_model_download":
            return _ok(await _post("/mlx/models/download", {"model": args["model"]}))
        elif tool_name == "jambu_models_available":
            return _ok(await _get("/models/available"))
        elif tool_name == "jambu_models_installed":
            return _ok(await _get("/models/installed"))
        elif tool_name == "jambu_models_status":
            return _ok(await _get("/models/status", {"model": args["model"]}))
        elif tool_name == "jambu_models_pull":
            return _ok(await _post("/models/pull", {"model": args["model"]}))
        elif tool_name == "jambu_models_recommend":
            return _ok(await _get("/models/recommend"))
        elif tool_name == "jambu_models_setup":
            return _ok(await _post("/models/setup", {"model": args.get("model", "")}))
        elif tool_name == "jambu_models_providers":
            return _ok(await _get("/models/providers"))

        # Multimodal
        elif tool_name == "jambu_multimodal_image":
            return _ok(await _post("/multimodal/image", {"image_url": args["image_url"], "prompt": args.get("prompt", "")}))
        elif tool_name == "jambu_multimodal_text":
            return _ok(await _post("/multimodal/text", {"text": args["text"], "prompt": args.get("prompt", "")}))
        elif tool_name == "jambu_multimodal_file":
            return _ok(await _post("/multimodal/file", {"file_path": args["file_path"], "prompt": args.get("prompt", "")}))

        # P2P / Peers
        elif tool_name == "jambu_p2p_info":
            return _ok(await _get("/p2p/info"))
        elif tool_name == "jambu_p2p_discover":
            return _ok(await _post("/p2p/discover"))
        elif tool_name == "jambu_p2p_peers":
            return _ok(await _get("/p2p/peers"))
        elif tool_name == "jambu_p2p_query":
            return _ok(await _post("/p2p/query", {"peer_id": args["peer_id"], "query": args["query"]}))
        elif tool_name == "jambu_p2p_start_discovery":
            return _ok(await _post("/p2p/start-discovery"))
        elif tool_name == "jambu_p2p_stats":
            return _ok(await _get("/p2p/stats"))
        elif tool_name == "jambu_peer_info":
            return _ok(await _get("/peer/info"))
        elif tool_name == "jambu_peer_query":
            return _ok(await _post("/peer/query", {"query": args["query"], "context": args.get("context", "")}))
        elif tool_name == "jambu_peer_sync":
            return _ok(await _post("/peer/sync", {"data": args["data"]}))

        # Harness
        elif tool_name == "jambu_harness_status":
            return _ok(await _get("/harness/status"))
        elif tool_name == "jambu_harness_research":
            return _ok(await _post("/harness/research", {"query": args["query"], "connectors": args.get("connectors", [])}))
        elif tool_name == "jambu_harness_research_single":
            return _ok(await _post("/harness/research/single", {"query": args["query"], "connector": args["connector"]}))
        elif tool_name == "jambu_harness_browse":
            return _ok(await _post("/harness/browse", {"url": args["url"], "actions": args.get("actions", [])}))
        elif tool_name == "jambu_harness_llm":
            return _ok(await _post("/harness/llm", {"prompt": args["prompt"], "model": args.get("model", ""), "provider": args.get("provider", "")}))
        elif tool_name == "jambu_harness_context_store":
            return _ok(await _post("/harness/context/store", {"key": args["key"], "value": args["value"], "metadata": args.get("metadata", {})}))
        elif tool_name == "jambu_harness_context_search":
            return _ok(await _post("/harness/context/search", {"query": args["query"], "limit": args.get("limit", 10)}))

        # Plugins
        elif tool_name == "jambu_plugins_list":
            return _ok(await _get("/plugins/list"))
        elif tool_name == "jambu_plugins_get":
            return _ok(await _get(f"/plugins/{args['plugin_name']}"))
        elif tool_name == "jambu_plugins_execute":
            return _ok(await _post("/plugins/execute", {"plugin_name": args["plugin_name"], "args": args.get("args", {})}))
        elif tool_name == "jambu_plugins_chain":
            return _ok(await _post("/plugins/chain", {"plugins": args["plugins"], "input_data": args.get("input_data", {})}))

        # Forms
        elif tool_name == "jambu_forms_detect":
            return _ok(await _post("/forms/detect", {"url": args["url"]}))
        elif tool_name == "jambu_forms_fill_script":
            return _ok(await _post("/forms/fill-script", {"url": args["url"]}))

        # Notifications
        elif tool_name == "jambu_notifications_history":
            return _ok(await _get("/notifications/history", {"limit": args.get("limit", 20)}))
        elif tool_name == "jambu_notifications_send":
            return _ok(await _post("/notifications/send", {"title": args["title"], "message": args["message"], "level": args.get("level", "info")}))

        # Fingerprint (extended)
        elif tool_name == "jambu_fingerprint_list":
            return _ok(await _get("/fingerprint/list"))
        elif tool_name == "jambu_fingerprint_profile":
            return _ok(await _get(f"/fingerprint/profile/{args['profile_id']}"))

        # Federated RAG
        elif tool_name == "jambu_federated_query":
            return _ok(await _post("/federated/query", {"query": args["query"], "trust_level": args.get("trust_level", "medium")}))
        elif tool_name == "jambu_federated_stats":
            return _ok(await _get("/federated/stats"))

        # Skill Synthesis
        elif tool_name == "jambu_skill_synthesize":
            return _ok(await _post("/skill/synthesize", {"description": args["description"], "code": args["code"], "tests": args.get("tests", "")}))
        elif tool_name == "jambu_skill_list":
            return _ok(await _get("/skill/list-synthesized"))

        # Mission Control (extended)
        elif tool_name == "jambu_mission_create":
            return _ok(await _post("/mission", {"query": args["query"], "schedule": args.get("schedule", ""), "priority": args.get("priority", 1)}))
        elif tool_name == "jambu_mission_stop":
            return _ok(await _post("/mission/stop", {"mission_id": args["mission_id"]}))
        elif tool_name == "jambu_mission_start_scheduler":
            return _ok(await _post("/mission/start-scheduler"))
        elif tool_name == "jambu_mission_stop_scheduler":
            return _ok(await _post("/mission/stop-scheduler"))

        # Tool Management
        elif tool_name == "jambu_tool_save":
            return _ok(await _post("/tool/save", {"name": args["name"], "code": args["code"], "description": args.get("description", "")}))
        elif tool_name == "jambu_tools_list":
            return _ok(await _get("/tools"))
        elif tool_name == "jambu_tool_exec":
            return _ok(await _post("/tool/exec", {"name": args["name"], "args": args.get("args", {})}))

        # System / Analytics
        elif tool_name == "jambu_analytics_summary":
            return _ok(await _get("/analytics/summary"))
        elif tool_name == "jambu_benchmark":
            return _ok(await _get("/benchmark"))
        elif tool_name == "jambu_vault_credential":
            return _ok(await _get("/vault/credential", {"url": args["url"]}))
        elif tool_name == "jambu_security_verify_package":
            return _ok(await _get("/security/verify/package", {"package_name": args["package_name"]}))
        elif tool_name == "jambu_llm_config":
            return _ok(await _get("/llm/config"))
        elif tool_name == "jambu_graph_data":
            return _ok(await _get("/graph_data"))
        elif tool_name == "jambu_vision_verify":
            return _ok(await _post("/vision/verify", {"image_data": args["image_data"], "expected": args["expected"]}))
        elif tool_name == "jambu_vision_analyze":
            return _ok(await _post("/vision/analyze", {"image_data": args["image_data"], "prompt": args.get("prompt", "")}))
        elif tool_name == "jambu_vision_grounding":
            return _ok(await _post("/vision/grounding", {"image_data": args["image_data"], "task": args.get("task", "")}))
        elif tool_name == "jambu_computer_launch":
            return _ok(await _post("/computer/launch", {"app_name": args["app_name"]}))
        elif tool_name == "jambu_computer_apps":
            return _ok(await _get("/computer/apps"))
        elif tool_name == "jambu_workflow_execute":
            return _ok(await _post("/workflow/execute", {"url": args["url"], "steps": args["steps"]}))
        elif tool_name == "jambu_memory_recall_legacy":
            return _ok(await _get("/memory/recall", {"query": args["query"]}))

        else:
            return _err(f"Unknown tool: {tool_name}")

    except httpx.HTTPStatusError as e:
        return _err(f"HTTP {e.response.status_code}: {e.response.text[:500]}")
    except httpx.ConnectError:
        return _err(f"Cannot connect to Jambubrowser backend at {JAMBU_BACKEND}. Is it running?")
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# MCP handlers
# ---------------------------------------------------------------------------


@app.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    logger.info("Tool call: %s(%s)", name, json.dumps(arguments, default=str)[:200])
    return await _handle(name, arguments)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main():
    """Run MCP server over stdio."""
    async with stdio_server() as (read_stream, write_stream):
        logger.info("Jambubrowser MCP server starting (backend: %s)", JAMBU_BACKEND)
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
