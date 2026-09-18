//! Native tab views — the second half of dual-mode tabs.
//!
//! The default pane renders the CDP Chromium over a JPEG screencast (full
//! automation/audit parity). A *native* view instead embeds the OS system
//! webview (WKWebView / WebView2 / WebKitGTK) as a child of the app window,
//! positioned over the viewport rect. Native views feel real — caret,
//! selection, context menus, downloads — but they are a different engine:
//! no CDP input, no audits, no fingerprint scripts. The UI must say so.
//!
//! Requires Tauri's `unstable` feature (`Window::add_child`).

use std::collections::HashMap;
use std::sync::Mutex;

use tauri::{
    LogicalPosition, LogicalSize, State, Webview, WebviewUrl, Window,
};

/// Live native views keyed by browser tab id.
#[derive(Default)]
pub struct NativeViewRegistry(pub Mutex<HashMap<String, Webview>>);

fn label_for(tab_id: &str) -> String {
    format!("native-{tab_id}")
}

/// Show (creating if needed) the native view for a tab, positioned over the
/// given viewport rect in CSS pixels. Navigates when the URL differs.
/// Returns the view's current URL.
#[tauri::command]
pub async fn browser_native_view(
    window: Window,
    tab_id: String,
    url: String,
    x: f64,
    y: f64,
    width: f64,
    height: f64,
    registry: State<'_, NativeViewRegistry>,
) -> Result<String, String> {
    let target: url::Url = url
        .parse()
        .map_err(|e| format!("Invalid URL {url:?}: {e}"))?;

    // Reuse the existing child when one is alive for this tab.
    if let Some(view) = registry.0.lock().unwrap().get(&tab_id).cloned() {
        if view.url().map(|u| u.as_str() != target.as_str()).unwrap_or(true) {
            view.navigate(target)
                .map_err(|e| format!("Native view navigate failed: {e}"))?;
        }
        set_rect(&view, x, y, width, height)?;
        view.show().map_err(|e| format!("Native view show failed: {e}"))?;
        return current_url(&view);
    }

    let builder = tauri::webview::WebviewBuilder::new(
        label_for(&tab_id),
        WebviewUrl::External(target),
    );
    let view = window
        .add_child(
            builder,
            LogicalPosition::new(x, y),
            LogicalSize::new(width.max(1.0), height.max(1.0)),
        )
        .map_err(|e| format!("Native view creation failed: {e}"))?;
    registry.0.lock().unwrap().insert(tab_id, view.clone());
    current_url(&view)
}

/// Move/resize a tab's native view (viewport rect in CSS pixels).
#[tauri::command]
pub async fn browser_native_set_rect(
    tab_id: String,
    x: f64,
    y: f64,
    width: f64,
    height: f64,
    registry: State<'_, NativeViewRegistry>,
) -> Result<(), String> {
    let view = registry
        .0
        .lock()
        .unwrap()
        .get(&tab_id)
        .cloned()
        .ok_or_else(|| format!("No native view for tab: {tab_id}"))?;
    set_rect(&view, x, y, width, height)
}

/// Current URL of a tab's native view (address-bar sync).
#[tauri::command]
pub async fn browser_native_url(
    tab_id: String,
    registry: State<'_, NativeViewRegistry>,
) -> Result<String, String> {
    let view = registry
        .0
        .lock()
        .unwrap()
        .get(&tab_id)
        .cloned()
        .ok_or_else(|| format!("No native view for tab: {tab_id}"))?;
    current_url(&view)
}

/// Destroy a tab's native view.
#[tauri::command]
pub async fn browser_native_close(
    tab_id: String,
    registry: State<'_, NativeViewRegistry>,
) -> Result<(), String> {
    if let Some(view) = registry.0.lock().unwrap().remove(&tab_id) {
        view.close()
            .map_err(|e| format!("Native view close failed: {e}"))?;
    }
    Ok(())
}

/// Drive the native child's own history (reload/back/forward). The system
/// webview keeps its own session history, so these run as in-page JS.
#[tauri::command]
pub async fn browser_native_action(
    tab_id: String,
    action: String,
    registry: State<'_, NativeViewRegistry>,
) -> Result<(), String> {
    let view = registry
        .0
        .lock()
        .unwrap()
        .get(&tab_id)
        .cloned()
        .ok_or_else(|| format!("No native view for tab: {tab_id}"))?;
    let js = match action.as_str() {
        "reload" => "location.reload()",
        "back" => "history.back()",
        "forward" => "history.forward()",
        other => return Err(format!("Unsupported native action: {other}")),
    };
    view.eval(js)
        .map_err(|e| format!("Native view {action} failed: {e}"))
}

fn set_rect(view: &Webview, x: f64, y: f64, width: f64, height: f64) -> Result<(), String> {
    view.set_position(LogicalPosition::new(x, y))
        .map_err(|e| format!("Native view move failed: {e}"))?;
    view.set_size(LogicalSize::new(width.max(1.0), height.max(1.0)))
        .map_err(|e| format!("Native view resize failed: {e}"))?;
    Ok(())
}

fn current_url(view: &Webview) -> Result<String, String> {
    view.url()
        .map(|u| u.to_string())
        .map_err(|e| format!("Native view URL failed: {e}"))
}
