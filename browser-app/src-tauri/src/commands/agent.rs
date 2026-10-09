//! Tauri IPC commands: the local-agent bridge.
//!
//! These commands let a program driving the desktop app (an MCP client, a
//! CLI agent, a test harness) ask the app itself what it can do, instead of
//! guessing. Everything here is a thin projection of state the Rust shell
//! and its Python sidecar already hold:
//!
//! - [`agent_bridge_status`] — one JSON blob: engine health, tab count,
//!   MCP surface (tool count + profile), use-case catalog.
//! - [`agent_session_open`] / [`agent_session_act`] / [`agent_session_close`] —
//!   browser sessions with the same rails the MCP tools enforce (allowlist
//!   fail-closed, approval gates, PII scrub), proxied to the sidecar engine.
//! - [`agent_run_audit`] / [`agent_run_research`] — dispatch the two most
//!   common agent jobs (web-app audit, deep research) to the engine.
//!
//! The MCP stdio launcher (`bin/jambubrowser-mcp`) remains the primary
//! agent path — 52 tools, no app window needed. This module is the
//! *in-app* counterpart: an agent that already has a Tauri window (or the
//! `jambu` CLI pointed at one) gets the same capabilities through `invoke`.
use crate::AppState;
use serde_json::{json, Value};
use tauri::State;

/// Use cases the desktop app exposes to local AI agents.
///
/// Kept in sync with `docs/MCP_TOOLS.md` families and the `jambu` CLI
/// groups. An agent reads this list from [`agent_bridge_status`] and picks
/// the verb it needs instead of probing endpoints.
pub const AGENT_USE_CASES: &[(&str, &str, &str)] = &[
    (
        "audit",
        "AI-employee web audit (security, performance, a11y, SEO, UX, code quality)",
        "agent_run_audit | MCP: browser_test_flow / browser_test_matrix | CLI: jambu audit <url>",
    ),
    (
        "browse",
        "Agent-driven browsing with rails (allowlist, approvals, PII scrub, receipts)",
        "agent_session_open/act/close | MCP: browser_session_* | engine: /browser/sessions",
    ),
    (
        "research",
        "Multi-engine web + academic + code search, deep-research swarm",
        "agent_run_research | MCP: research_web / deep_research | engine: /research/*",
    ),
    (
        "memory",
        "4-store memory (profile/session/semantic/procedural) + knowledge graph",
        "MCP: recall_memory / query_brain / get_brain_stats | engine: /v2/memory/*",
    ),
    (
        "mesh",
        "DecentraCode mesh inference, settlement log, mesh status",
        "MCP: dcm_status / dcm_infer / dcm_models | CLI: jambu dcm ...",
    ),
    (
        "simulate",
        "Replicated simulation compute with numeric-agreement settlement",
        "MCP: simulation_submit / simulation_jobs / simulation_quote | CLI: jambu sim ...",
    ),
    (
        "privacy",
        "Dynamic VPN egress (pool status, endpoint selection, health probe)",
        "MCP: vpn_status / vpn_select / vpn_probe | CLI: jambu vpn ...",
    ),
    (
        "qa",
        "Managed QA cases, NL test plans, case x dataset x viewport matrices",
        "MCP: browser_test_plan / browser_test_matrix | CLI: jambu test ...",
    ),
    (
        "missions",
        "Long-running background research missions",
        "MCP: start_mission / stop_mission | engine: /missions/*",
    ),
    (
        "eval",
        "Signed eval certificates (frozen spec hash, Ed25519 verify)",
        "MCP: agent_eval_certify / agent_eval_verify | engine: /eval/*",
    ),
];

async fn engine_get(backend: &str, path: &str) -> Result<Value, String> {
    reqwest::get(format!("{backend}{path}"))
        .await
        .map_err(|e| format!("engine unreachable: {e}"))?
        .json::<Value>()
        .await
        .map_err(|e| format!("engine parse: {e}"))
}

/// One-blob capability report for a local agent.
///
/// Returns engine health, open tab count, the MCP surface reachable at the
/// sidecar (`/mcp/tools/docs?format=json`), and the use-case catalog, so a
/// model can decide what to do next from a single `invoke` call.
#[tauri::command]
pub async fn agent_bridge_status(state: State<'_, AppState>) -> Result<Value, String> {
    let backend = state.backend_url.clone();
    let health: Value = engine_get(&backend, "/health")
        .await
        .unwrap_or_else(|e| json!({"status": "offline", "error": e}));
    // Newer engines gate /mcp/tools/docs behind the MCP token; the public
    // server card carries the same count + names, so prefer it second.
    let tools: Value = match engine_get(&backend, "/mcp/tools/docs?format=json").await {
        Ok(t) if t.get("count").is_some() => t,
        _ => engine_get(&backend, "/.well-known/mcp-server-card.json")
            .await
            .ok()
            .and_then(|card| card.get("tools").cloned())
            .unwrap_or_else(|| json!({"count": 0, "tools": []})),
    };
    let tabs: Vec<Value> = {
        let mgr = state.chromium.lock().await;
        match mgr.as_ref() {
            Some(m) => m
                .list_tabs()
                .iter()
                .map(|t| json!({"id": t.id, "url": t.url, "title": t.title}))
                .collect(),
            None => vec![],
        }
    };
    Ok(json!({
        "app": "Jambubrowser",
        "engine": health,
        "engine_url": backend,
        "tabs": {"count": tabs.len(), "open": tabs},
        "mcp": {
            "stdio_launcher": "bin/jambubrowser-mcp",
            "http": format!("{backend}/mcp/"),
            "server_card": format!("{backend}/.well-known/mcp-server-card.json"),
            "tools": tools,
        },
        "use_cases": AGENT_USE_CASES
            .iter()
            .map(|(id, what, via)| json!({"id": id, "what": what, "via": via}))
            .collect::<Vec<_>>(),
    }))
}

/// Open a railed browser session for agent-driven work.
///
/// Thin proxy over `POST /browser/sessions` on the sidecar engine: the
/// session fails closed without a non-empty domain allowlist, and the
/// engine enforces approvals, PII scrubbing, and hash-chained receipts.
#[tauri::command]
pub async fn agent_session_open(
    allow_domains: Vec<String>,
    require_approval: Option<bool>,
    state: State<'_, AppState>,
) -> Result<Value, String> {
    let backend = state.backend_url.clone();
    reqwest::Client::new()
        .post(format!("{backend}/browser/sessions"))
        .json(&json!({
            "allow_domains": allow_domains,
            "require_approval": require_approval.unwrap_or(true),
        }))
        .send()
        .await
        .map_err(|e| format!("engine unreachable: {e}"))?
        .json::<Value>()
        .await
        .map_err(|e| format!("engine parse: {e}"))
}

/// Act inside a railed browser session (`navigate`/`click`/`type`/… by ref).
///
/// Thin proxy over `POST /browser/sessions/{id}/act`. Refusals come back
/// as the engine's 403 payload (`blocked_domain`, `approval_required`,
/// `unknown_ref`) — never as a transport error.
#[tauri::command]
#[allow(clippy::too_many_arguments)]
pub async fn agent_session_act(
    session_id: String,
    action: String,
    ref_: Option<String>,
    text: Option<String>,
    url: Option<String>,
    approve: Option<bool>,
    state: State<'_, AppState>,
) -> Result<Value, String> {
    let backend = state.backend_url.clone();
    let mut body = json!({"action": action});
    if let Some(r) = ref_ {
        body["ref"] = json!(r);
    }
    if let Some(t) = text {
        body["text"] = json!(t);
    }
    if let Some(u) = url {
        body["url"] = json!(u);
    }
    if let Some(a) = approve {
        body["approve"] = json!(a);
    }
    reqwest::Client::new()
        .post(format!("{backend}/browser/sessions/{session_id}/act"))
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("engine unreachable: {e}"))?
        .json::<Value>()
        .await
        .map_err(|e| format!("engine parse: {e}"))
}

/// Close a railed browser session (ephemeral context is torn down).
#[tauri::command]
pub async fn agent_session_close(
    session_id: String,
    state: State<'_, AppState>,
) -> Result<Value, String> {
    let backend = state.backend_url.clone();
    reqwest::Client::new()
        .delete(format!("{backend}/browser/sessions/{session_id}"))
        .send()
        .await
        .map_err(|e| format!("engine unreachable: {e}"))?
        .json::<Value>()
        .await
        .map_err(|e| format!("engine parse: {e}"))
}

/// Dispatch an AI-employee web audit (quick or full) via the sidecar engine.
///
/// `POST /audit/run` streams SSE; `POST /audit/quick` forces quick mode.
/// Both are proxied as JSON here — a caller that wants the live stream
/// should use `proxy_stream` against the same path instead.
#[tauri::command]
pub async fn agent_run_audit(
    url: String,
    mode: Option<String>,
    state: State<'_, AppState>,
) -> Result<Value, String> {
    let backend = state.backend_url.clone();
    let mode = mode.unwrap_or_else(|| "quick".to_string());
    let path = if mode == "quick" { "/audit/quick" } else { "/audit/run" };
    reqwest::Client::new()
        .post(format!("{backend}{path}"))
        .json(&json!({"url": url, "mode": mode}))
        .send()
        .await
        .map_err(|e| format!("engine unreachable: {e}"))?
        .json::<Value>()
        .await
        .map_err(|e| format!("engine parse: {e}"))
}

/// Dispatch a research job via the sidecar engine (`POST /research`).
#[tauri::command]
pub async fn agent_run_research(
    query: String,
    domain: Option<String>,
    state: State<'_, AppState>,
) -> Result<Value, String> {
    let backend = state.backend_url.clone();
    reqwest::Client::new()
        .post(format!("{backend}/research"))
        .json(&json!({
            "query": query,
            "domain": domain.unwrap_or_else(|| "general".to_string()),
        }))
        .send()
        .await
        .map_err(|e| format!("engine unreachable: {e}"))?
        .json::<Value>()
        .await
        .map_err(|e| format!("engine parse: {e}"))
}
