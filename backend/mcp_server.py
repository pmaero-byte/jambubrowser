"""
Jambubrowser MCP Server
=======================
FastMCP server exposing the full Jambubrowser engine as MCP tools.
External agents (Claude, Cursor, etc.) can use these tools to perform
autonomous research, browser automation, and knowledge management.

35 MCP tools covering:
- Research & Search (5 tools)
- Browser Actions (5 tools)
- Vision & Perception (2 tools)
- Memory & Knowledge (3 tools)
- Tools & Skills (2 tools)
- System (4 tools: check_engine_health, get_system_stats, start_mission, stop_mission)
- DecentraCode Mesh (5 tools: dcm_status, dcm_infer, dcm_models, dcm_earnings, dcm_settlement_log)
- MeshPay (2 tools: meshpay_audit, meshpay_anchor)
- Browser Sessions (7 tools: browser_session_open|snapshot|act|run|receipts|close + browser_test_flow)
- Agent Evaluation (2 tools: agent_eval_certify, agent_eval_verify)
"""

import asyncio
import json
import os
import httpx
from mcp.server.fastmcp import FastMCP

from backend import __version__

# Initialize FastMCP server for Jambubrowser
mcp = FastMCP(f"Jambubrowser Sovereign Engine v{__version__}")

# Engine URL is overridable via JAMBU_ENGINE_URL (useful for tests that spawn
# the engine on a free port). Default matches the conventional dev port.
ENGINE_URL = os.environ.get("JAMBU_ENGINE_URL", "http://localhost:8001")
DEFAULT_TIMEOUT = 60.0


async def _call_engine(
    method: str,
    path: str,
    json_data: dict = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Internal helper to call the engine API."""
    async with httpx.AsyncClient() as client:
        try:
            if method == "GET":
                resp = await client.get(
                    f"{ENGINE_URL}{path}",
                    params=json_data,
                    timeout=timeout,
                )
            elif method == "POST":
                resp = await client.post(
                    f"{ENGINE_URL}{path}",
                    json=json_data or {},
                    timeout=timeout,
                )
            else:
                return {"error": f"Unsupported method: {method}"}

            if resp.status_code == 200:
                return resp.json()
            # Surface the engine's own error detail (e.g. DCM's missing-runtime
            # explanation from /dcm/infer) instead of a bare status code.
            detail = None
            try:
                body = resp.json()
                if isinstance(body, dict):
                    detail = body.get("detail") or body.get("error")
            except Exception:
                detail = None
            if detail:
                return {"error": f"Engine HTTP {resp.status_code}: {detail}"}
            return {"error": f"Engine returned status {resp.status_code}"}
        except httpx.TimeoutException:
            return {"error": f"Request timed out after {timeout}s"}
        except httpx.ConnectError:
            return {"error": "Engine is not running. Start it with: python engine.py"}
        except Exception as e:
            return {"error": str(e)}


# ===================================================================
# RESEARCH & SEARCH TOOLS
# ===================================================================

@mcp.tool()
async def research_web(query: str, tor: bool = False) -> str:
    """
    Perform an autonomous research mission using the Jambubrowser swarm.
    Decomposes query into parallel sub-tasks and synthesizes findings.

    Args:
        query: The research question or topic
        tor: Route through Tor for anonymity (default: False)
    """
    result = await _call_engine("POST", "/research", {
        "query": query,
        "tor_routing": tor,
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Research failed: {result['error']}"
    return f"RESEARCH RESULTS:\n\n{result.get('context', 'No results found.')}\n\nSources: {json.dumps(result.get('sources', []))}"


@mcp.tool()
async def search_multi_engine(
    query: str,
    engines: str = "google,bing,duckduckgo",
) -> str:
    """
    Search across multiple engines without scraping pages.
    Returns raw search results with URLs and snippets.

    Args:
        query: Search query
        engines: Comma-separated engine list (default: google,bing,duckduckgo)
    """
    result = await _call_engine("GET", "/search", {
        "q": query,
        "engines": engines,
    })
    if "error" in result:
        return f"Search failed: {result['error']}"
    results = result.get("results", [])
    if not results:
        return "No results found."
    lines = [f"# Search results for: {query}\n"]
    for i, r in enumerate(results[:10], 1):
        lines.append(f"{i}. **{r.get('title', 'Untitled')}**\n   {r.get('url', '')}\n   {r.get('content', '')[:200]}\n")
    return "\n".join(lines)


@mcp.tool()
async def search_academic(query: str) -> str:
    """
    Search ArXiv for academic papers on a topic.
    Returns paper titles, abstracts, and links.

    Args:
        query: Research topic to search for
    """
    result = await _call_engine("POST", "/research", {
        "query": query,
        "domain": "academic",
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Academic search failed: {result['error']}"
    return f"ACADEMIC RESULTS:\n\n{result.get('context', 'No papers found.')}\n\nSources: {json.dumps(result.get('sources', []))}"


@mcp.tool()
async def search_code(query: str) -> str:
    """
    Search GitHub for code repositories matching a query.
    Returns repo names, descriptions, and links.

    Args:
        query: Code or project topic to search for
    """
    result = await _call_engine("POST", "/research", {
        "query": query,
        "domain": "coding",
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Code search failed: {result['error']}"
    return f"CODE RESULTS:\n\n{result.get('context', 'No repos found.')}\n\nSources: {json.dumps(result.get('sources', []))}"


@mcp.tool()
async def deep_research(query: str, rounds: int = 3) -> str:
    """
    Perform multi-round recursive research that builds on previous findings.
    More thorough than single-pass research.

    Args:
        query: The research topic
        rounds: Number of recursive research rounds (default: 3, max: 5)
    """
    rounds = min(rounds, 5)
    all_context = []
    current_query = query

    for r in range(rounds):
        result = await _call_engine("POST", "/research", {
            "query": current_query,
            "persist": True,
            "client_id": "mcp",
        })
        if "error" not in result:
            all_context.append(f"--- Round {r + 1} ---\n{result.get('context', '')}")
            # Refine query for next round based on findings
            if result.get("context"):
                current_query = f"{query} additional details: {result['context'][:200]}"

    return "DEEP RESEARCH RESULTS:\n\n" + "\n\n".join(all_context)


# ===================================================================
# BROWSER ACTION TOOLS
# ===================================================================

@mcp.tool()
async def scrape_page(url: str, session_id: str = None) -> str:
    """
    Scrape a webpage and return its text content as clean text.
    Includes page title, main content, and a screenshot.

    Args:
        url: The webpage URL to scrape
        session_id: Optional browser session ID for stateful navigation
    """
    result = await _call_engine("POST", "/scrape", {
        "url": url,
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Scrape failed: {result['error']}"
    content = result.get("context", result.get("markdown", "No content extracted."))
    return f"PAGE CONTENT ({url}):\n\n{content[:10000]}"


@mcp.tool()
async def click_element(url: str, selector: str, session_id: str = None) -> str:
    """
    Click an element on a webpage using a CSS selector.
    Returns the page state after clicking.

    Args:
        url: The page URL
        selector: CSS selector for the element to click
        session_id: Optional browser session ID
    """
    result = await _call_engine("POST", "/act", {
        "url": url,
        "steps": [{"action": "click", "selector": selector}],
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Click failed: {result['error']}"
    return f"Clicked '{selector}' on {url}. Result: {result.get('markdown', 'Action completed.')[:5000]}"


@mcp.tool()
async def type_text(url: str, selector: str, text: str, session_id: str = None) -> str:
    """
    Type text into an input field on a webpage.

    Args:
        url: The page URL
        selector: CSS selector for the input field
        text: Text to type
        session_id: Optional browser session ID
    """
    result = await _call_engine("POST", "/act", {
        "url": url,
        "steps": [{"action": "type", "selector": selector, "value": text}],
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Type failed: {result['error']}"
    return f"Typed '{text}' into '{selector}' on {url}."


@mcp.tool()
async def take_screenshot(url: str, full_page: bool = False, session_id: str = None) -> str:
    """
    Take a screenshot of a webpage. Returns base64-encoded PNG.

    Args:
        url: The page URL to screenshot
        full_page: Capture the full scrollable page (default: viewport only)
        session_id: Optional browser session ID
    """
    result = await _call_engine("POST", "/scrape", {
        "url": url,
        "query": "screenshot",
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Screenshot failed: {result['error']}"
    return f"Screenshot captured for {url}. Content length: {len(result.get('context', ''))} chars."


@mcp.tool()
async def navigate_browser(url: str, session_id: str = None) -> str:
    """
    Navigate the browser to a URL. Use before other browser actions
    to establish the page context.

    Args:
        url: The URL to navigate to
        session_id: Optional browser session ID
    """
    result = await _call_engine("POST", "/scrape", {
        "url": url,
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Navigation failed: {result['error']}"
    return f"Navigated to {url}. Page title: {result.get('title', 'Unknown')}"


# ===================================================================
# VISION & PERCEPTION TOOLS
# ===================================================================

@mcp.tool()
async def visual_grounding(url: str) -> str:
    """
    Analyze a webpage visually and identify interactive elements
    (buttons, forms, links). Returns suggested actions the agent can take.

    Args:
        url: The page URL to analyze visually
    """
    result = await _call_engine("POST", "/vision/grounding", {
        "url": url,
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Vision grounding failed: {result['error']}"

    suggestions = result.get("suggestions", [])
    if not suggestions:
        return "No interactive elements identified."

    lines = ["# Visual Grounding Analysis", f"URL: {url}", ""]
    for i, s in enumerate(suggestions, 1):
        lines.append(
            f"{i}. {s.get('label', 'Action')} | "
            f"Type: {s.get('action', 'unknown')} | "
            f"Selector: {s.get('selector', 'viewport')}"
        )
    return "\n".join(lines)


@mcp.tool()
async def analyze_screenshot(image_data: str) -> str:
    """
    Analyze a screenshot or image using the vision model.
    Describe what the agent sees in the image.

    Args:
        image_data: Base64-encoded image data
    """
    result = await _call_engine("POST", "/vision/analyze", {
        "image": image_data,
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Image analysis failed: {result['error']}"
    return result.get("analysis", "No analysis available.")


# ===================================================================
# MEMORY & KNOWLEDGE TOOLS
# ===================================================================

@mcp.tool()
async def query_brain(query: str) -> str:
    """
    Search the local knowledge vault (vector search) for relevant
    previously-researched information.

    Args:
        query: What to search for in the knowledge vault
    """
    result = await _call_engine("GET", "/memory/recall", {"query": query})
    if "error" in result:
        return f"Brain query failed: {result['error']}"

    memories = result.get("memory", [])
    if not memories:
        return "No relevant memories found in the knowledge vault."

    lines = [f"# Knowledge Vault Results for: {query}\n"]
    for i, m in enumerate(memories[:10], 1):
        lines.append(f"{i}. {m.get('text', '')[:300]}")
        if m.get("url"):
            lines.append(f"   Source: {m['url']}")
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
async def recall_memory(query: str) -> str:
    """
    Cross-session semantic recall. Finds information from past
    research sessions that relates to the current query.

    Args:
        query: Context to find related past research for
    """
    result = await _call_engine("GET", "/memory/recall", {"query": query})
    if "error" in result:
        return f"Memory recall failed: {result['error']}"

    memories = result.get("memory", [])
    if not memories:
        return "No cross-session memories found."

    lines = ["# Cross-Session Memory Recall\n"]
    for i, m in enumerate(memories[:5], 1):
        lines.append(f"{i}. {m.get('text', '')[:200]}")
    return "\n".join(lines)


@mcp.tool()
async def get_brain_stats() -> str:
    """
    Get statistics about the local knowledge vault:
    document count, active missions, stored tools, credentials.
    """
    result = await _call_engine("GET", "/stats")
    if "error" in result:
        return f"Stats check failed: {result['error']}"
    return (
        f"Knowledge Vault Stats:\n"
        f"- Documents indexed: {result.get('doc_count', 0)}\n"
        f"- Database path: rag_data.db"
    )


# ===================================================================
# TOOLS & SKILLS TOOLS
# ===================================================================

@mcp.tool()
async def list_custom_tools() -> str:
    """
    List all saved agent-generated tools and skills stored
    in the toolbox.
    """
    result = await _call_engine("GET", "/tools")
    if "error" in result:
        return f"Tool listing failed: {result['error']}"

    tools = result.get("tools", [])
    if not tools:
        return "No custom tools in the toolbox yet."

    lines = ["# Custom Toolbox\n"]
    for i, t in enumerate(tools, 1):
        lines.append(f"{i}. **{t.get('name', 'unknown')}**: {t.get('description', 'No description')}")
    return "\n".join(lines)


@mcp.tool()
async def execute_tool(name: str, kwargs: str = "{}") -> str:
    """
    Execute a previously saved custom tool/script.

    Args:
        name: Name of the tool to execute
        kwargs: JSON string of keyword arguments to pass to the tool
    """
    try:
        parsed_kwargs = json.loads(kwargs)
    except json.JSONDecodeError:
        return f"Invalid kwargs JSON: {kwargs}"

    result = await _call_engine("POST", "/tool/exec", {
        "name": name,
        "kwargs": parsed_kwargs,
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Tool execution failed: {result['error']}"
    return f"Tool '{name}' executed. Output: {result.get('output', 'No output.')}"


# ===================================================================
# SYSTEM TOOLS
# ===================================================================

@mcp.tool()
async def check_engine_health() -> str:
    """
    Check if the Jambubrowser engine is running and healthy.
    Returns engine status and system metrics.
    """
    result = await _call_engine("GET", "/health")
    if "error" in result:
        return f"Engine Offline: {result['error']}"

    return (
        f"Engine Status: {result.get('status', 'unknown')}\n"
        f"Message: {result.get('message', 'No message')}\n"
        f"RAM Used: {result.get('ram_used_gb', 'N/A')} GB\n"
        f"CPU: {result.get('cpu_percent', 'N/A')}%"
    )


@mcp.tool()
async def get_system_stats() -> str:
    """
    Get detailed system statistics: CPU usage, RAM, document count,
    active missions, and database size.
    """
    health = await _call_engine("GET", "/health")
    stats = await _call_engine("GET", "/stats")

    lines = ["# System Statistics\n"]
    if "error" not in health:
        lines.append(f"- Status: {health.get('status', 'unknown')}")
        lines.append(f"- RAM: {health.get('ram_used_gb', 'N/A')}/{health.get('ram_total_gb', 'N/A')} GB")
        lines.append(f"- CPU: {health.get('cpu_percent', 'N/A')}%")
    if "error" not in stats:
        lines.append(f"- Documents: {stats.get('doc_count', 0)}")
    return "\n".join(lines)


@mcp.tool()
async def start_mission(query: str, schedule: str = None) -> str:
    """
    Register a long-running background research mission.
    The engine will periodically research this topic and report findings.

    Args:
        query: The research topic to monitor
        schedule: Cron-style schedule (e.g., '0 */6 * * *' for every 6 hours)
    """
    result = await _call_engine("POST", "/mission", {
        "query": query,
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Mission registration failed: {result['error']}"
    return (
        f"Mission registered!\n"
        f"Mission ID: {result.get('mission_id', 'unknown')}\n"
        f"Query: {query}\n"
        f"Status: Active"
    )


@mcp.tool()
async def stop_mission(mission_id: str) -> str:
    """
    Stop a running background research mission.

    Args:
        mission_id: The mission ID to stop (from start_mission)
    """
    result = await _call_engine("POST", "/mission/stop", {
        "mission_id": mission_id,
        "client_id": "mcp",
    })
    if "error" in result:
        return f"Mission stop failed: {result['error']}"
    return f"Mission {mission_id} stopped."


# ===================================================================
# DECENTRACODE MESH (DCM) TOOLS
# ===================================================================

@mcp.tool()
async def dcm_status() -> str:
    """
    Check the local DecentraCode Mesh (DCM) node: reachability, inference
    runtimes, available models, and connected peers.
    """
    result = await _call_engine("GET", "/dcm/status", timeout=20.0)
    if "error" in result:
        return f"DCM status failed: {result['error']}"
    if not result.get("reachable"):
        return (
            "DCM node unreachable. Start it with: "
            "cd decentracode/backend && npm start"
        )

    lines = ["# DecentraCode Mesh Node\n"]
    inf = result.get("inference_status") or {}
    if isinstance(inf, dict):
        # A top-level "error" only concerns the *default* runtime; a ready
        # secondary (MoE sidecar) still means the node can serve inference.
        top_ready = bool(inf.get("engine_ready", inf.get("ready")))
        moe = inf.get("moe") or {}
        moe_ready = bool(moe.get("ready") or moe.get("available"))
        if top_ready:
            lines.append(f"- Inference: {inf.get('runtime', 'runtime')} ready")
        elif moe_ready:
            lines.append(
                f"- Inference: {moe.get('runtime', 'MoE')} ready"
                + (f" ({inf.get('runtime', 'default')}: {str(inf.get('error'))[:60]})"
                   if inf.get("error") else "")
            )
        else:
            lines.append(
                f"- Inference: not ready — {str(inf.get('error') or 'no runtime')[:80]}"
            )
    elif inf.get("error"):
        lines.append(f"- Inference: unavailable — {inf['error']}")

    models = result.get("models")
    if isinstance(models, list):
        available = [m for m in models if m.get("status") in ("available", "ready")]
        lines.append(f"- Models: {len(available)}/{len(models)} available")

    mesh = result.get("mesh_status") or {}
    if isinstance(mesh, dict) and not mesh.get("error"):
        peers = mesh.get("peers")
        n = len(peers) if isinstance(peers, (list, dict)) else (peers or 0)
        lines.append(f"- Mesh peers: {n}")

    return "\n".join(lines)


@mcp.tool()
async def dcm_infer(prompt: str, model: str = "", max_tokens: int = 64) -> str:
    """
    Run a prompt on the local DecentraCode Mesh (distributed inference).

    Args:
        prompt: The prompt to run
        model: Optional DCM model id (e.g. 'qwen1.5-moe-a2.7b'); empty uses the node default
        max_tokens: Maximum tokens to generate (1-4096)
    """
    payload = {"prompt": prompt, "max_tokens": max_tokens}
    if model:
        payload["model"] = model
    result = await _call_engine("POST", "/dcm/infer", payload, timeout=180.0)
    if "error" in result:
        return f"DCM inference failed: {result['error']}"
    usage = result.get("usage") or {}
    return (
        f"{result.get('content', '').strip()}\n\n"
        f"_model: {result.get('model', '?')} · "
        f"{usage.get('completion_tokens', 0)} tokens · "
        f"{result.get('latency_ms', 0):.0f}ms_"
    )


@mcp.tool()
async def dcm_models() -> str:
    """
    List the local DecentraCode Mesh model catalog with availability and
    runtime per model.
    """
    result = await _call_engine("GET", "/dcm/models", timeout=20.0)
    if "error" in result:
        return f"DCM models failed: {result['error']}"
    models = result.get("models", [])
    if not models:
        return "DCM node returned no models."
    lines = [f"# DCM Models ({len(models)})\n"]
    for m in models:
        status = m.get("status", "?")
        mark = "✅" if status in ("available", "ready") else "·"
        lines.append(
            f"- {mark} `{m.get('id')}` — {m.get('name', '')} "
            f"({m.get('runtime', '?')}, {status})"
        )
    return "\n".join(lines)


@mcp.tool()
async def dcm_earnings(did: str) -> str:
    """
    Show accrued DCT earnings for a provider DID on the local DCM node.

    Args:
        did: Provider DID (e.g. 'did:dcm:...' or the node's registered DID)
    """
    result = await _call_engine("GET", f"/dcm/earnings/{did}", timeout=20.0)
    if "error" in result:
        return f"DCM earnings failed: {result['error']}"
    lines = [f"# DCT Earnings — {did}\n"]
    for key in ("pendingDct", "paidDct", "totalDct", "settledDct", "withdrawnDct"):
        if key in result:
            lines.append(f"- {key}: {result[key]}")
    if len(lines) == 1:
        lines.append(f"```json\n{json.dumps(result, indent=2)[:800]}\n```")
    return "\n".join(lines)


@mcp.tool()
async def dcm_settlement_log(limit: int = 20) -> str:
    """
    Fetch the DCM node's hash-chained settlement receipts (billing audit
    trail: usage, inference-charge, simulation-charge, settlement).

    Args:
        limit: Number of receipts to fetch (1-500)
    """
    result = await _call_engine(
        "GET", "/dcm/settlement-log", {"limit": limit}, timeout=20.0,
    )
    if "error" in result:
        return f"DCM settlement log failed: {result['error']}"
    entries = result.get("entries") or result.get("log") or []
    # DCM nests the chain verdict under "verification"; accept both shapes.
    verification = result.get("verification") or {}
    valid = result.get("valid", verification.get("valid"))
    head = f"# DCM Settlement Log — {len(entries)} receipt(s)"
    if valid is not None:
        head += f" · chain valid: {valid}"
    totals = verification.get("totals") or {}
    lines = [head + "\n"]
    if totals:
        lines.append(
            "- totals: "
            + ", ".join(f"{k}={v}" for k, v in totals.items())
        )
    for e in entries[:limit]:
        kind = e.get("kind") or e.get("type") or "?"
        amount = e.get("amountDct", e.get("amount", e.get("dct", "")))
        when = e.get("timestamp", "")
        lines.append(f"- `{kind}` {amount} — {when}")
    if not entries:
        lines.append("(no receipts yet)")
    return "\n".join(lines)


# ===================================================================
# MESHPAY TOOLS (USDC settlement for mesh compute)
# ===================================================================

@mcp.tool()
async def meshpay_audit(limit: int = 200) -> str:
    """
    Independently audit the DCM settlement receipt chain and preview the
    USDC payout plan. Replays the hash chain with MeshPay's own verifier
    and compares it to DCM's verdict.

    Args:
        limit: Receipts to audit (1-200)
    """
    result = await _call_engine("GET", "/meshpay/audit", {"limit": limit}, timeout=30.0)
    if "error" in result:
        return f"MeshPay audit failed: {result['error']}"

    v = result.get("verification") or {}
    lines = ["# MeshPay Audit\n"]
    lines.append(f"- Receipts checked: {v.get('checked', 0)}")
    lines.append(f"- Chain valid (independent): {v.get('valid')}")
    if v.get("broken_at") is not None:
        lines.append(f"- Broken at: #{v['broken_at']} — {v.get('broken_reason')}")
    if result.get("agreement") is not None:
        lines.append(f"- Agrees with DCM's own verdict: {result['agreement']}")
    epochs = result.get("epochs") or []
    lines.append(f"- Epochs: {len(epochs)}")
    payout = result.get("payout") or {}
    totals = payout.get("totals") or {}
    if totals:
        lines.append(
            f"- Latest epoch plan: {totals.get('grossDct')} DCT gross → "
            f"{totals.get('usdc')} USDC net (fee {payout.get('protocol_fee_pct')})"
        )
        providers = payout.get("providers") or []
        for p in providers[:5]:
            lines.append(
                f"  - {p['nodeId']}: {p['netDct']} DCT → {p['usdc']} USDC"
            )
    return "\n".join(lines)


@mcp.tool()
async def meshpay_anchor(epoch_index: int = -1, epoch_size: int = 50) -> str:
    """
    Anchor an epoch's Merkle receipt root (Solana memo program on the
    configured cluster, or the explicit mock transport). Returns the
    signature and explorer link when a real cluster is configured.

    Args:
        epoch_index: Epoch to anchor (-1 = latest)
        epoch_size: Receipts per epoch
    """
    result = await _call_engine("POST", "/meshpay/anchor", {
        "epoch_index": epoch_index,
        "epoch_size": epoch_size,
        "limit": 200,
    }, timeout=60.0)
    if "error" in result:
        return f"MeshPay anchor failed: {result['error']}"
    lines = ["# MeshPay Anchor\n"]
    lines.append(f"- Epoch: {result.get('epoch', {}).get('index')} "
                 f"({result.get('epoch', {}).get('receipts')} receipts)")
    lines.append(f"- Root: `{result.get('root')}`")
    lines.append(f"- Transport: {result.get('transport')} "
                 f"(cluster: {result.get('cluster')})")
    lines.append(f"- Signature: `{result.get('signature')}`")
    if result.get("explorer_url"):
        lines.append(f"- Explorer: {result['explorer_url']}")
    else:
        lines.append("- (mock transport — no chain transaction; nothing to explore)")
    return "\n".join(lines)


# ===================================================================
# BROWSER SESSIONS (hardened agent browsing)
# ===================================================================

@mcp.tool()
async def browser_session_open(allow_domains: str, require_approval: bool = True) -> str:
    """
    Open an isolated browser session for agent-driven work, restricted to a
    domain allowlist. Navigations outside it are refused; irreversible-looking
    actions need ``approve=true``; PII is scrubbed from snapshots.

    Args:
        allow_domains: Comma-separated domains the session may visit (subdomains allowed)
        require_approval: Require approve=true for input actions inside the allowlist
    """
    domains = [d.strip() for d in (allow_domains or "").split(",") if d.strip()]
    if not domains:
        return "allow_domains must be a non-empty comma-separated list (sessions fail closed)."
    result = await _call_engine("POST", "/browser/sessions", {
        "allow_domains": domains, "require_approval": require_approval,
    }, timeout=60.0)
    if "error" in result:
        return f"Session open failed: {result['error']}"
    return (
        f"Session {result['session_id']} open\n"
        f"- allowlisted: {', '.join(result['allow_domains'])}\n"
        f"- approval required: {result['require_approval']}\n"
        f"- snapshot next: browser_session_snapshot"
    )


@mcp.tool()
async def browser_session_snapshot(session_id: str) -> str:
    """
    Perception step: accessibility-style snapshot with a typed element
    catalog (refs @e1…). Act on refs, never on selector guesses.

    Args:
        session_id: Session from browser_session_open
    """
    result = await _call_engine(
        "GET", f"/browser/sessions/{session_id}/snapshot", timeout=60.0,
    )
    if "error" in result:
        return f"Snapshot failed: {result['error']}"
    lines = [f"# {result.get('title', '')} — {result.get('url', '')}\n"]
    for e in (result.get("elements") or [])[:25]:
        risk = f" ⚠{e['risk']}" if e.get("risk") else ""
        lines.append(f"- `{e['ref']}` {e.get('tag')} {e.get('name', '')[:60]}{risk}")
    if result.get("count", 0) > 25:
        lines.append(f"… and {result['count'] - 25} more")
    if result.get("text"):
        lines.append(f"\nPage text (scrubbed):\n{result['text'][:600]}")
    return "\n".join(lines)


@mcp.tool()
async def browser_session_act(session_id: str, action: str, ref: str,
                              text: str = "", approve: bool = False) -> str:
    """
    Deterministic dispatch by catalog ref. Refusals are explicit: blocked
    domains, unknown refs, and actions needing approval (risky elements such
    as delete/pay/send always require approve=true).

    Args:
        session_id: Session id
        action: "click" or "type"
        ref: Element ref from the last snapshot (e.g. @e3)
        text: Text to type (for action="type")
        approve: Explicit approval for input/risky actions
    """
    result = await _call_engine("POST", f"/browser/sessions/{session_id}/act", {
        "action": action, "ref": ref, "text": text, "approve": approve,
    }, timeout=60.0)
    if "error" in result:
        return f"Action refused/failed: {result['error']}"
    return f"ok — {result.get('outcome')} at {result.get('url')} (step {result.get('step', {}).get('seq')})"


@mcp.tool()
async def browser_session_receipts(session_id: str) -> str:
    """
    Hash-chained receipt log for a session (every action, blocked or not),
    with the Merkle root that can be signed into an evidence bundle.

    Args:
        session_id: Session id
    """
    result = await _call_engine(
        "GET", f"/browser/sessions/{session_id}/receipts", timeout=30.0,
    )
    if "error" in result:
        return f"Receipts failed: {result['error']}"
    lines = [f"# Receipts — {result.get('count', 0)} step(s)",
             f"merkle_root: `{result.get('merkle_root')}`\n"]
    for s in (result.get("steps") or [])[-10:]:
        lines.append(
            f"- #{s['seq']} {s['action']} [{s['outcome']}] {s.get('ref') or ''} {s.get('detail', '')[:60]}"
        )
    return "\n".join(lines)


@mcp.tool()
async def browser_session_close(session_id: str) -> str:
    """
    Close a browser session (ephemeral context is torn down).

    Args:
        session_id: Session id
    """
    result = await _call_engine(
        "DELETE", f"/browser/sessions/{session_id}", timeout=30.0,
    )
    if "error" in result:
        return f"Close failed: {result['error']}"
    return f"Session {session_id} closed ({result.get('steps', 0)} steps recorded)."


# ===================================================================
# TOKEN-EFFICIENT BROWSER TESTING (one call for a whole flow)
# ===================================================================

def _render_flow(result: dict) -> str:
    """Compact pass/fail digest of a flow report (token-lean)."""
    icon = "PASS" if result.get("ok") else "FAIL"
    lines = [
        f"# Browser test {icon} — {result.get('passed', 0)}/{result.get('total', 0)} steps "
        f"in {result.get('duration_ms', 0)}ms",
        f"final: {result.get('title', '') or '(untitled)'} — {result.get('final_url', '')}",
    ]
    for step in result.get("steps") or []:
        mark = "ok " if step.get("status") == "passed" else "FAIL"
        bit = f"{mark} #{step.get('i')} {step.get('action')}"
        if step.get("detail"):
            bit += f" — {step['detail']}"
        if step.get("status") == "failed":
            bit += f" — {step.get('reason')}: {step.get('error')}"
        lines.append(bit)
        for cand in step.get("candidates") or []:
            lines.append(f"      candidate {cand.get('ref')}: {cand.get('name')}")
        cause = step.get("cause") or {}
        if cause:
            parts = []
            if cause.get("dom"):
                d = cause["dom"]
                parts.append(f"dom +{d.get('added', 0)}/-{d.get('removed', 0)}/~{d.get('changed', 0)}")
            if cause.get("failed_requests"):
                parts.append(f"{len(cause['failed_requests'])} failed req")
            if cause.get("console_errors"):
                parts.append(f"{len(cause['console_errors'])} console error")
            if parts:
                lines.append(f"      cause: {'; '.join(parts)}")
    mapped = result.get("console_errors_source") or []
    for item in mapped[:5]:
        if item.get("source"):
            lines.append(f"console {item['source']}:{item.get('source_line')} — {item.get('text', '')[:120]}")
    errors = result.get("console_errors") or []
    if errors:
        lines.append(f"console errors ({len(errors)}):")
        lines.extend(f"  - {e[:160]}" for e in errors[:5])
    failed = result.get("failed_requests") or []
    if failed:
        lines.append(f"failed requests ({len(failed)}):")
        lines.extend(f"  - {r.get('method')} {r.get('url', '')[:110]} — {r.get('failure', '')[:70]}"
                     for r in failed[:5])
    bad = result.get("bad_responses") or []
    if bad:
        lines.append(f"HTTP >=400 ({len(bad)}):")
        lines.extend(f"  - {r.get('status')} {r.get('method')} {r.get('url', '')[:110]}"
                     for r in bad[:5])
    artifacts = result.get("artifacts") or {}
    if artifacts:
        lines.append("artifacts: " + ", ".join(f"{k}={v}" for k, v in artifacts.items()))
    return "\n".join(lines)


@mcp.tool()
async def browser_test_flow(url: str, steps: str = "[]", allow_domains: str = "",
                            local: bool = False, approve: bool = False,
                            stop_on_failure: bool = False,
                            network: str = "", trace: bool = False,
                            har: bool = False, video: bool = False,
                            resolve_sources: bool = False,
                            storage_state: str = "") -> str:
    """
    Test a web app end-to-end in ONE call: opens a browser session, runs a
    declarative step list (navigate / click / type / press / wait / assert_*),
    and returns a compact pass/fail report with console errors and failed
    requests already attached. Prefer this over open→snapshot→act loops to
    save tool calls.

    Set local=true for localhost / private dev servers (e.g. http://localhost:3000).

    Args:
        url: Starting URL (also the default allowlist host), e.g. http://localhost:3000
        steps: JSON array of step objects. Actions: navigate{url}, click{target|ref},
            type{target|ref,value}, press{key,target?}, hover, select{value},
            check/uncheck, reload, back, forward, wait{selector|text|url_contains},
            screenshot, assert_visible/assert_not_visible/assert_text{value}/
            assert_text_equals/assert_value/assert_url/assert_title/assert_count/
            assert_checked/assert_unchecked/assert_enabled/assert_disabled/
            assert_console_clean/assert_no_failed_requests/assert_no_a11y_violations/
            assert_lcp/assert_fcp/assert_load/assert_dom_nodes/assert_transfer_kb/
            assert_made_request{value}/assert_no_request{value}.
            'target' matches element text by exact name, unique substring, or "role name".
        allow_domains: Optional comma-separated allowlist (defaults to url host)
        local: Allow loopback/private hosts (local dev testing)
        approve: Approve risky/input actions for every step (delete/pay/send…)
        stop_on_failure: Stop at the first failed step
        network: Optional JSON request-interception policy:
            {"mocks":[{"url":"**/api/user","json":{...},"status":200}],
             "fail":["**/analytics/**"],
             "delay":[{"url":"**/slow","ms":3000}],"offline":false}
        trace: Capture a Playwright trace artifact (screenshots+snapshots)
        har: Capture a HAR network archive
        video: Capture a video recording
        resolve_sources: Map console errors through source maps to original files
        storage_state: Optional JSON storage state ({cookies,origins}) to seed auth
    """
    import json as _json

    def _load(raw, default):
        if not raw:
            return default
        try:
            return _json.loads(raw) if isinstance(raw, str) else raw
        except _json.JSONDecodeError:
            return default

    try:
        parsed = _json.loads(steps) if isinstance(steps, str) else steps
    except _json.JSONDecodeError as exc:
        return f"steps is not valid JSON: {exc}"
    domains = [d.strip() for d in (allow_domains or "").split(",") if d.strip()]
    result = await _call_engine("POST", "/browser/sessions/run", {
        "url": url, "steps": parsed or [], "allow_domains": domains,
        "local": local, "approve": approve, "stop_on_failure": stop_on_failure,
        "network": _load(network, None), "trace": trace, "har": har, "video": video,
        "resolve_sources": resolve_sources, "storage_state": _load(storage_state, None),
    }, timeout=300.0)
    if "error" in result:
        return f"Test flow failed: {result['error']}"
    return _render_flow(result)


@mcp.tool()
async def browser_session_run(session_id: str, steps: str,
                              approve: bool = False,
                              stop_on_failure: bool = False,
                              network: str = "",
                              resolve_sources: bool = False) -> str:
    """
    Run a declarative step flow against an existing browser session and return
    a compact pass/fail report (one call instead of many snapshot/act calls).

    Args:
        session_id: Session from browser_session_open
        steps: JSON array of step objects (see browser_test_flow for actions)
        approve: Approve risky/input actions for every step
        stop_on_failure: Stop at the first failed step
        network: Optional JSON request-interception policy (see browser_test_flow)
        resolve_sources: Map console errors through source maps
    """
    import json as _json

    try:
        parsed = _json.loads(steps) if isinstance(steps, str) else steps
        net = _json.loads(network) if network else None
    except _json.JSONDecodeError as exc:
        return f"steps/network is not valid JSON: {exc}"
    result = await _call_engine("POST", f"/browser/sessions/{session_id}/run", {
        "steps": parsed or [], "approve": approve,
        "stop_on_failure": stop_on_failure, "network": net,
        "resolve_sources": resolve_sources,
    }, timeout=300.0)
    if "error" in result:
        return f"Flow failed: {result['error']}"
    return _render_flow(result)


@mcp.tool()
async def browser_test_plan(url: str, goal: str, kind: str = "",
                            use_llm: bool = False) -> str:
    """
    Author a browser test flow from a natural-language goal (does NOT run it).
    Returns ready-to-run steps for browser_test_flow. Use this to turn
    "test login and the dashboard" into a concrete flow, then run it.

    Args:
        url: App URL, e.g. http://localhost:3000
        goal: What to test, e.g. "test login with a valid user"
        kind: Force a template: smoke|login|signup|checkout|search|accessibility|performance|responsive
        use_llm: Refine the plan with the configured LLM (default: template only)
    """
    result = await _call_engine("POST", "/browser/sessions/plan", {
        "url": url, "goal": goal, "kind": kind or None, "use_llm": use_llm,
    }, timeout=120.0)
    if "error" in result:
        return f"Plan failed: {result['error']}"
    lines = [
        f"# Test plan — {result.get('kind')} ({result.get('source')})",
        f"goal: {goal or '(none)'}",
    ]
    if result.get("placeholders"):
        lines.append(f"placeholders to fill: {', '.join(result['placeholders'])}")
    lines.append("steps:")
    lines.append(json.dumps(result.get("steps") or [], indent=1))
    lines.append(f"note: {result.get('notes', '')}")
    return "\n".join(lines)


@mcp.tool()
async def browser_test_matrix(url: str, steps: str, matrix: str = "",
                              local: bool = False, approve: bool = False,
                              network: str = "") -> str:
    """
    Run the same test flow across viewports/locales concurrently (responsive
    and cross-locale checks) in ONE call, returning a per-variant digest.

    Args:
        url: App URL
        steps: JSON array of step objects (see browser_test_flow)
        matrix: Optional JSON array of variants, e.g.
            [{"name":"desktop","viewport":{"width":1280,"height":800}},
             {"name":"mobile","viewport":{"width":390,"height":844},"locale":"en-GB"}]
            Defaults to desktop + mobile.
        local: Allow loopback/private hosts
        approve: Approve risky/input actions
        network: Optional JSON request-interception policy
    """
    import json as _json

    try:
        parsed = _json.loads(steps) if isinstance(steps, str) else steps
        variants = _json.loads(matrix) if matrix else None
        net = _json.loads(network) if network else None
    except _json.JSONDecodeError as exc:
        return f"steps/matrix/network is not valid JSON: {exc}"
    result = await _call_engine("POST", "/browser/sessions/matrix", {
        "url": url, "steps": parsed or [], "matrix": variants,
        "local": local, "approve": approve, "network": net,
    }, timeout=600.0)
    if "error" in result:
        return f"Matrix run failed: {result['error']}"
    lines = [
        f"# Matrix {'PASS' if result.get('ok') else 'FAIL'} — "
        f"{result.get('summary', {}).get('passed')}/{result.get('summary', {}).get('variants')} variants",
    ]
    for v in result.get("variants") or []:
        mark = "ok " if v.get("ok") else "FAIL"
        bit = f"{mark} {v.get('variant')}: {v.get('passed')}/{v.get('total')} steps"
        if v.get("error"):
            bit += f" — {v['error']}"
        lines.append(bit)
        for fs in v.get("failed_steps") or []:
            lines.append(f"      #{fs.get('i')} {fs.get('action')}: {fs.get('reason')} {fs.get('error', '')[:80]}")
        if v.get("console_errors"):
            lines.append(f"      console errors: {len(v['console_errors'])}")
    return "\n".join(lines)


@mcp.tool()
async def browser_export_playwright(steps: str, name: str = "jambubrowser flow",
                                    base_url: str = "") -> str:
    """
    Export a declarative flow as a Playwright Test (.spec.ts) file, so it can
    run in the developer's own CI without lock-in.

    Args:
        steps: JSON array of step objects (as used by browser_test_flow)
        name: Test name
        base_url: Optional Playwright baseURL
    """
    import json as _json

    try:
        parsed = _json.loads(steps) if isinstance(steps, str) else steps
    except _json.JSONDecodeError as exc:
        return f"steps is not valid JSON: {exc}"
    result = await _call_engine("POST", "/browser/sessions/export", {
        "steps": parsed or [], "name": name, "base_url": base_url,
    }, timeout=60.0)
    if "error" in result:
        return f"Export failed: {result['error']}"
    return result.get("code", "")


@mcp.tool()
async def browser_task(url: str, goal: str, inputs: str = "{}",
                       local: bool = True, approve: bool = False) -> str:
    """
    One-call meta-tool: turn a goal into a test flow and run it. Plans the
    flow (template/LLM), substitutes any {{placeholders}} from `inputs`, then
    executes it and returns the pass/fail report. Use this when you don't
    want to author steps yourself.

    Args:
        url: App URL, e.g. http://localhost:3000
        goal: What to test, e.g. "test login with a valid user"
        inputs: JSON object filling placeholders, e.g. {"email":"a@b.com","password":"..."}
        local: Allow loopback/private hosts (default true)
        approve: Approve risky/input actions for every step
    """
    import json as _json

    try:
        values = _json.loads(inputs) if isinstance(inputs, str) else (inputs or {})
    except _json.JSONDecodeError as exc:
        return f"inputs is not valid JSON: {exc}"

    plan = await _call_engine("POST", "/browser/sessions/plan", {
        "url": url, "goal": goal,
    }, timeout=120.0)
    if "error" in plan:
        return f"Plan failed: {plan['error']}"

    steps = plan.get("steps") or []
    missing = []
    for step in steps:
        for key, val in list(step.items()):
            if isinstance(val, str) and "{{" in val:
                for name, replacement in values.items():
                    val = val.replace("{{" + name + "}}", str(replacement))
                step[key] = val
                for ph in plan.get("placeholders") or []:
                    if "{{" + ph + "}}" in val:
                        missing.append(ph)
    missing = sorted(set(missing))
    if missing:
        return (
            f"# Plan ready ({plan.get('kind')}) — needs inputs: {', '.join(missing)}\n"
            f"Call browser_task again with inputs={{\"{missing[0]}\": \"...\"}}\n\n"
            + _json.dumps(steps, indent=1)
        )

    result = await _call_engine("POST", "/browser/sessions/run", {
        "url": url, "steps": steps, "local": local, "approve": approve,
    }, timeout=300.0)
    if "error" in result:
        return f"Run failed: {result['error']}"
    return f"(planned: {plan.get('kind')})\n" + _render_flow(result)


# ===================================================================
# AGENT EVALUATION CERTIFICATES
# ===================================================================

@mcp.tool()
async def agent_eval_certify(suite: str, provider: str = "",
                             pass_threshold: float = 0.8) -> str:
    """
    Run an eval suite under a frozen spec and issue a signed certificate.
    The spec (task list + scoring + provider) is hashed before the run, so
    dropping failed tasks afterwards is detectable; verdicts are PASS, FAIL,
    INCONCLUSIVE (harness errors) or INVALID (coverage mismatch).

    Args:
        suite: Suite name, e.g. "smoke" (see the GET /eval/suites list)
        provider: LLM provider under test (empty = engine default)
        pass_threshold: Pass rate required for PASS (0-1)
    """
    result = await _call_engine("POST", "/eval/certificates", {
        "suite": suite, "provider": provider or None,
        "pass_threshold": pass_threshold,
    }, timeout=600.0)
    if "error" in result:
        return f"Certification failed: {result['error']}"
    verdict = (result.get("payload") or {}).get("verdict") or {}
    summary = verdict.get("summary") or {}
    lines = [
        f"# Certificate #{result.get('id')} — {result.get('kind')}",
        f"- suite: {suite} | verdict: **{verdict.get('verdict')}**",
        f"- pass rate: {summary.get('pass_rate')} "
        f"({summary.get('passed')}/{summary.get('committed')} passed, "
        f"{summary.get('error')} errors)",
        f"- spec_hash: `{(result.get('payload') or {}).get('spec_hash')}`",
    ]
    for reason in verdict.get("reasons") or []:
        lines.append(f"- reason: {reason}")
    return "\n".join(lines)


@mcp.tool()
async def agent_eval_verify(certificate_id: int) -> str:
    """
    Verify a certificate's signature and recompute its verdict from the
    embedded results (a signed certificate whose verdict doesn't follow
    from its data is rejected).

    Args:
        certificate_id: Bundle id from agent_eval_certify
    """
    result = await _call_engine(
        "GET", f"/eval/certificates/{certificate_id}", timeout=60.0,
    )
    if "error" in result:
        return f"Verification failed: {result['error']}"
    verification = result.get("verification") or {}
    lines = [f"# Certificate #{certificate_id} verification"]
    for name, ok in (verification.get("checks") or {}).items():
        lines.append(f"- [{'PASS' if ok else 'FAIL'}] {name}")
    lines.append(f"\n{'VALID' if verification.get('valid') else 'INVALID'}"
                 + (f" — {verification.get('reason')}" if verification.get("reason") else ""))
    return "\n".join(lines)


# ===================================================================
# ENTRY POINT
# ===================================================================

# Tool profiles: `JAMBU_MCP_PROFILE=curated` keeps the surface compact for
# better tool selection and drops arbitrary-execution tools — remote
# deployments (backend/mcp_http.py) should default to curated; local stdio
# installs keep the full surface.
CURATED_EXCLUDES = ("execute_tool",)

# The `developer` profile keeps only the high-level browser-testing verbs, so
# an agent choosing among tools pays the smallest possible selection cost.
DEVELOPER_TOOLS = {
    "browser_task",
    "browser_test_flow",
    "browser_test_plan",
    "browser_test_matrix",
    "browser_session_run",
    "browser_export_playwright",
    "check_engine_health",
}


def apply_tool_profile(profile: str) -> list[str]:
    """Remove non-profile tools from the live registry. Returns removals."""
    if profile == "curated":
        removed = []
        for name in CURATED_EXCLUDES:
            try:
                mcp.remove_tool(name)
                removed.append(name)
            except Exception:  # tool already absent
                pass
        return removed
    if profile == "developer":
        try:
            names = [t.name for t in mcp._tool_manager.list_tools()]
        except Exception:
            names = list(DEVELOPER_TOOLS)
        removed = []
        for name in names:
            if name in DEVELOPER_TOOLS:
                continue
            try:
                mcp.remove_tool(name)
                removed.append(name)
            except Exception:
                pass
        return removed
    return []


apply_tool_profile(os.environ.get("JAMBU_MCP_PROFILE", "full"))


if __name__ == "__main__":
    mcp.run()
