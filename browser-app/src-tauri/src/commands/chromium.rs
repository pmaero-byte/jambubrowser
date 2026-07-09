//! Tauri IPC commands for browser operations.
//!
//! These commands are callable from the React frontend via `invoke()`.
//! They delegate to the shared `ChromiumManager` held in app state.

use crate::AppState;
use crate::chromium::audit::AuditReport;
use crate::chromium::extensions::Extension;
use crate::chromium::tab::TabInfo;
use serde_json::Value;
use tauri::State;

// ── Commands ─────────────────────────────────────────────────────

/// Create a new tab and navigate to `url`. Returns tab info.
#[tauri::command]
pub async fn browser_new_tab(
    url: String,
    state: State<'_, AppState>,
) -> Result<TabInfo, String> {
    let mut mgr = state.chromium.lock().await;
    let mgr = mgr
        .as_mut()
        .ok_or("Browser engine not initialized")?;
    mgr.create_tab(&url).await
}

/// Navigate an existing tab to a new URL.
#[tauri::command]
pub async fn browser_navigate(
    tab_id: String,
    url: String,
    state: State<'_, AppState>,
) -> Result<(), String> {
    let mgr = state.chromium.lock().await;
    let mgr = mgr.as_ref().ok_or("Browser engine not initialized")?;
    mgr.navigate(&tab_id, &url).await
}

/// Reload the current page in a tab.
#[tauri::command]
pub async fn browser_reload(
    tab_id: String,
    state: State<'_, AppState>,
) -> Result<(), String> {
    let mgr = state.chromium.lock().await;
    let mgr = mgr.as_ref().ok_or("Browser engine not initialized")?;
    mgr.reload(&tab_id).await
}

/// Go back in a tab's history.
#[tauri::command]
pub async fn browser_go_back(
    tab_id: String,
    state: State<'_, AppState>,
) -> Result<(), String> {
    let mgr = state.chromium.lock().await;
    let mgr = mgr.as_ref().ok_or("Browser engine not initialized")?;
    mgr.go_back(&tab_id).await
}

/// Go forward in a tab's history.
#[tauri::command]
pub async fn browser_go_forward(
    tab_id: String,
    state: State<'_, AppState>,
) -> Result<(), String> {
    let mgr = state.chromium.lock().await;
    let mgr = mgr.as_ref().ok_or("Browser engine not initialized")?;
    mgr.go_forward(&tab_id).await
}

/// Close a tab.
#[tauri::command]
pub async fn browser_close_tab(
    tab_id: String,
    state: State<'_, AppState>,
) -> Result<(), String> {
    let mut mgr = state.chromium.lock().await;
    let mgr = mgr
        .as_mut()
        .ok_or("Browser engine not initialized")?;
    mgr.close_tab(&tab_id).await
}

/// Capture a screenshot of a tab (returns base64 PNG data URL).
#[tauri::command]
pub async fn browser_capture_screenshot(
    tab_id: String,
    state: State<'_, AppState>,
) -> Result<String, String> {
    let mgr = state.chromium.lock().await;
    let mgr = mgr.as_ref().ok_or("Browser engine not initialized")?;
    let b64 = mgr.capture_screenshot(&tab_id).await?;
    Ok(format!("data:image/png;base64,{b64}"))
}

/// Execute JavaScript in a tab and return the result.
#[tauri::command]
pub async fn browser_evaluate(
    tab_id: String,
    expression: String,
    state: State<'_, AppState>,
) -> Result<String, String> {
    let mgr = state.chromium.lock().await;
    let mgr = mgr.as_ref().ok_or("Browser engine not initialized")?;
    mgr.evaluate(&tab_id, &expression).await
}

/// List all open tabs.
#[tauri::command]
pub async fn browser_list_tabs(
    state: State<'_, AppState>,
) -> Result<Vec<TabInfo>, String> {
    let mgr = state.chromium.lock().await;
    let mgr = mgr.as_ref().ok_or("Browser engine not initialized")?;
    Ok(mgr.list_tabs())
}

/// Get the current URL and title of a tab.
#[tauri::command]
pub async fn browser_get_tab_info(
    tab_id: String,
    state: State<'_, AppState>,
) -> Result<TabInfo, String> {
    let mgr = state.chromium.lock().await;
    let mgr = mgr.as_ref().ok_or("Browser engine not initialized")?;
    mgr.get_tab(&tab_id)
        .ok_or_else(|| format!("Tab not found: {tab_id}"))
}

/// Get all cookies for a tab.
#[tauri::command]
pub async fn browser_get_cookies(
    tab_id: String,
    state: State<'_, AppState>,
) -> Result<Value, String> {
    let mgr = state.chromium.lock().await;
    let mgr = mgr.as_ref().ok_or("Browser engine not initialized")?;
    mgr.get_cookies(&tab_id).await
}

/// Clear all cookies for a tab.
#[tauri::command]
pub async fn browser_clear_cookies(
    tab_id: String,
    state: State<'_, AppState>,
) -> Result<(), String> {
    let mgr = state.chromium.lock().await;
    let mgr = mgr.as_ref().ok_or("Browser engine not initialized")?;
    mgr.clear_cookies(&tab_id).await
}

/// Delete a specific cookie from a tab.
#[tauri::command]
pub async fn browser_delete_cookie(
    tab_id: String,
    name: String,
    domain: String,
    state: State<'_, AppState>,
) -> Result<(), String> {
    let mgr = state.chromium.lock().await;
    let mgr = mgr.as_ref().ok_or("Browser engine not initialized")?;
    mgr.delete_cookie(&tab_id, &name, &domain).await
}

/// List all discovered browser extensions.
#[tauri::command]
pub async fn browser_list_extensions(
    state: State<'_, AppState>,
) -> Result<Vec<Extension>, String> {
    let mgr = state.chromium.lock().await;
    let mgr = mgr.as_ref().ok_or("Browser engine not initialized")?;
    Ok(mgr.list_extensions())
}

/// Run a page audit on the active tab (perf, a11y, SEO, security).
#[tauri::command]
pub async fn browser_run_audit(
    tab_id: String,
    state: State<'_, AppState>,
) -> Result<AuditReport, String> {
    let mgr = state.chromium.lock().await;
    let mgr = mgr.as_ref().ok_or("Browser engine not initialized")?;
    mgr.run_audit(&tab_id).await
}
