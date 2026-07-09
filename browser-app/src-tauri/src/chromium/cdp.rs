//! Chrome DevTools Protocol (CDP) client.
//!
//! Communicates with a running Chromium instance via WebSocket.
//! Uses per-call connections for simplicity — each CDP command opens
//! a fresh WebSocket, sends the JSON-RPC message, reads the response,
//! and closes. This is fast enough for <20 tabs and avoids complex
//! connection-pool management.

use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio_tungstenite::{connect_async, tungstenite::Message};

use super::tab::Tab;

/// CDP client connected to a Chromium browser instance.
pub struct CdpClient {
    /// Base URL for Chrome's HTTP debugger API (e.g. http://127.0.0.1:9222)
    http_base: String,
}

impl CdpClient {
    /// Connect to an already-running Chromium instance on the given debug port.
    /// Reads `/json/version` to verify the browser is reachable.
    pub async fn connect(debug_port: u16) -> Result<Self, String> {
        let http_base = format!("http://127.0.0.1:{debug_port}", debug_port = debug_port);
        let version_url = format!("{http_base}/json/version");
        let resp = reqwest::get(&version_url)
            .await
            .map_err(|e| format!("Chrome not reachable on port {debug_port}: {e}"))?;

        if !resp.status().is_success() {
            return Err(format!(
                "Chrome returned {} on port {debug_port}",
                resp.status()
            ));
        }

        Ok(Self { http_base })
    }

    // ── HTTP API (browser-level) ──────────────────────────────────

    /// Create a new tab (target) and navigate to `url`.
    /// Uses Chrome's `PUT /json/new?url=...` HTTP API.
    pub async fn new_tab(&self, url: &str) -> Result<Tab, String> {
        let encoded = urlencoding(url);
        let create_url = format!("{}/json/new?{}", self.http_base, encoded);

        let client = reqwest::Client::new();
        let resp = client
            .put(&create_url)
            .send()
            .await
            .map_err(|e| format!("Failed to create tab: {e}"))?;

        let json: Value = resp
            .json()
            .await
            .map_err(|e| format!("Failed to parse new tab response: {e}"))?;

        let target_id = json["id"]
            .as_str()
            .ok_or_else(|| format!("No target id in response: {json}"))?
            .to_string();
        let ws_url = json["webSocketDebuggerUrl"]
            .as_str()
            .ok_or_else(|| format!("No webSocketDebuggerUrl in response: {json}"))?
            .to_string();

        let tab_id = format!("tab-{}", &target_id[..8.min(target_id.len())]);
        let initial_url = json["url"]
            .as_str()
            .unwrap_or(url)
            .to_string();

        Ok(Tab::new(tab_id, target_id, ws_url, initial_url))
    }

    /// Close a tab by its CDP target ID.
    pub async fn close_tab(&self, target_id: &str) -> Result<(), String> {
        let close_url = format!("{}/json/close/{}", self.http_base, target_id);
        reqwest::get(&close_url)
            .await
            .map_err(|e| format!("Failed to close tab {target_id}: {e}"))?;
        Ok(())
    }

    // ── WebSocket commands (per-target) ──────────────────────────

    /// Navigate a tab to a new URL.
    pub async fn navigate(&self, tab: &Tab, url: &str) -> Result<(), String> {
        let result = self
            .send_cdp(&tab.ws_url, "Page.navigate", json!({"url": url}))
            .await?;
        // Check for error in the CDP result
        if let Some(error_text) = result.get("errorText").and_then(|v| v.as_str()) {
            return Err(format!("Navigation failed: {error_text}"));
        }
        Ok(())
    }

    /// Reload the current page in a tab.
    pub async fn reload(&self, tab: &Tab) -> Result<(), String> {
        self.send_cdp(&tab.ws_url, "Page.reload", json!({"ignoreCache": false}))
            .await?;
        Ok(())
    }

    /// Go back in tab history.
    pub async fn go_back(&self, tab: &Tab) -> Result<(), String> {
        // CDP uses Page.navigateToHistoryEntry with a delta of -1
        // First, get the current history index
        let history = self
            .send_cdp(
                &tab.ws_url,
                "Page.getNavigationHistory",
                json!({}),
            )
            .await?;
        let current_idx = history["currentIndex"].as_i64().unwrap_or(0);
        if current_idx <= 0 {
            return Err("No previous page in history".into());
        }
        // Navigate to the previous entry
        self.send_cdp(
            &tab.ws_url,
            "Page.navigateToHistoryEntry",
            json!({"entryId": current_idx - 1}),
        )
        .await?;
        Ok(())
    }

    /// Go forward in tab history.
    pub async fn go_forward(&self, tab: &Tab) -> Result<(), String> {
        let history = self
            .send_cdp(
                &tab.ws_url,
                "Page.getNavigationHistory",
                json!({}),
            )
            .await?;
        let current_idx = history["currentIndex"].as_i64().unwrap_or(0);
        let entries = history["entries"].as_array().map(|a| a.len()).unwrap_or(0) as i64;
        if current_idx + 1 >= entries {
            return Err("No next page in history".into());
        }
        self.send_cdp(
            &tab.ws_url,
            "Page.navigateToHistoryEntry",
            json!({"entryId": current_idx + 1}),
        )
        .await?;
        Ok(())
    }

    /// Execute JavaScript in the tab and return the result.
    pub async fn evaluate(&self, tab: &Tab, expression: &str) -> Result<String, String> {
        let result = self
            .send_cdp(
                &tab.ws_url,
                "Runtime.evaluate",
                json!({"expression": expression, "returnByValue": true}),
            )
            .await?;
        let value = result
            .get("result")
            .and_then(|r| r.get("value"))
            .map(|v| v.to_string())
            .unwrap_or_else(|| "undefined".to_string());
        Ok(value)
    }

    /// Capture a screenshot of the tab (returns base64-encoded PNG).
    pub async fn capture_screenshot(&self, tab: &Tab) -> Result<String, String> {
        let result = self
            .send_cdp(
                &tab.ws_url,
                "Page.captureScreenshot",
                json!({"format": "png"}),
            )
            .await?;
        let data = result["data"]
            .as_str()
            .ok_or("No screenshot data")?
            .to_string();
        Ok(data)
    }

    // ── Internal helpers ─────────────────────────────────────────

    /// Send a single CDP command over WebSocket and return the result.
    async fn send_cdp(
        &self,
        ws_url: &str,
        method: &str,
        params: Value,
    ) -> Result<Value, String> {
        let (mut ws, _) = connect_async(ws_url)
            .await
            .map_err(|e| format!("WebSocket connect failed: {e}"))?;

        let msg = json!({
            "id": 1,
            "method": method,
            "params": params,
        });

        ws.send(Message::Text(msg.to_string()))
            .await
            .map_err(|e| format!("WS send failed: {e}"))?;

        // Read until we get the response for our id
        while let Some(msg) = ws.next().await {
            let msg = msg.map_err(|e| format!("WS read error: {e}"))?;
            if let Message::Text(text) = msg {
                let v: Value =
                    serde_json::from_str(&text).map_err(|e| format!("JSON parse error: {e}"))?;
                // Match our request id
                if v.get("id").and_then(|i| i.as_u64()) == Some(1) {
                    if let Some(err) = v.get("error") {
                        let msg = err["message"].as_str().unwrap_or("Unknown CDP error");
                        return Err(format!("CDP error ({method}): {msg}"));
                    }
                    return Ok(v["result"].clone());
                }
                // Ignore events (messages without an `id` matching ours)
            }
        }

        Err("WebSocket connection closed before response".into())
    }
}

/// Simple URL encoding for the `url` query parameter in CDP HTTP requests.
fn urlencoding(s: &str) -> String {
    // Only encode what's needed for the `url=` query param
    // Chrome's /json/new?url=... expects the URL as-is for most cases
    // but we need to handle special chars
    let encoded = s
        .replace('%', "%25")
        .replace('&', "%26")
        .replace('#', "%23");
    format!("url={encoded}")
}
