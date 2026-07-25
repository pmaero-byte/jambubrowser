//! Chrome DevTools Protocol (CDP) client.
//!
//! Communicates with a running Chromium instance via WebSocket.
//! Uses per-call connections for simplicity — each CDP command opens
//! a fresh WebSocket, sends the JSON-RPC message, reads the response,
//! and closes. This is fast enough for <20 tabs and avoids complex
//! connection-pool management.

use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio::time::{sleep, Duration};
use tokio_tungstenite::{connect_async, tungstenite::Message};

use super::tab::Tab;

/// Performance metrics returned by CDP Performance.getMetrics.
#[derive(Debug, Clone, Default, serde::Serialize)]
pub struct PerfMetrics {
    pub dom_content_loaded_ms: Option<f64>,
    pub load_complete_ms: Option<f64>,
    pub first_paint_ms: Option<f64>,
    pub first_contentful_paint_ms: Option<f64>,
    pub dom_nodes: Option<u64>,
    pub layout_count: Option<u64>,
    pub js_heap_used_mb: Option<f64>,
}

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
        // Wait for the page to finish loading (up to 10 s)
        self.wait_for_page_load(tab, 10_000).await.ok();
        Ok(())
    }

    /// Reload the current page in a tab.
    pub async fn reload(&self, tab: &Tab) -> Result<(), String> {
        self.send_cdp(&tab.ws_url, "Page.reload", json!({"ignoreCache": false}))
            .await?;
        // Wait for the page to finish loading (up to 10 s)
        self.wait_for_page_load(tab, 10_000).await.ok();
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

    /// Wait for the page to finish loading (document.readyState === "complete").
    pub async fn wait_for_page_load(&self, tab: &Tab, timeout_ms: u64) -> Result<(), String> {
        for _ in 0..timeout_ms / 100 {
            let state = self
                .evaluate(tab, "document.readyState")
                .await
                .unwrap_or_default()
                .trim_matches('"')
                .to_string();
            if state == "complete" {
                return Ok(());
            }
            sleep(Duration::from_millis(100)).await;
        }
        Err(format!("Page did not finish loading within {timeout_ms}ms"))
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

    /// Get the current page title from the tab via DOM.
    pub async fn get_page_title(&self, tab: &Tab) -> Result<String, String> {
        self.evaluate(tab, "document.title").await
            .map(|v| v.trim_matches('"').to_string())
    }

    /// Get the current page URL from the tab via DOM.
    pub async fn get_page_url(&self, tab: &Tab) -> Result<String, String> {
        self.evaluate(tab, "window.location.href").await
            .map(|v| v.trim_matches('"').to_string())
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

    // ── Input dispatch (mouse / keyboard) ────────────────────────

    /// Dispatch a mouse event via Input.dispatchMouseEvent.
    ///
    /// `event_type` is one of `mousePressed`, `mouseReleased`, `mouseMoved`,
    /// `mouseWheel`. Coordinates are CSS pixels in the page's viewport (the
    /// frontend translates from scaled screenshot coordinates). `button` is
    /// `left` / `right` / `middle` / `none`; wheel events carry `delta_x` /
    /// `delta_y` and use `button: "none"`, `click_count: 0`.
    #[allow(clippy::too_many_arguments)]
    pub async fn dispatch_mouse_event(
        &self,
        tab: &Tab,
        event_type: &str,
        x: f64,
        y: f64,
        button: &str,
        click_count: i32,
        delta_x: f64,
        delta_y: f64,
    ) -> Result<(), String> {
        let mut params = json!({
            "type": event_type,
            "x": x,
            "y": y,
            "button": button,
            "clickCount": click_count,
        });
        if event_type == "mouseWheel" {
            params["deltaX"] = json!(delta_x);
            params["deltaY"] = json!(delta_y);
        }
        self.send_cdp(&tab.ws_url, "Input.dispatchMouseEvent", params)
            .await?;
        Ok(())
    }

    /// Dispatch a key event via Input.dispatchKeyEvent.
    ///
    /// `event_type` is `rawKeyDown` (non-text keys), `keyUp`, or `char`
    /// (text-producing keys — `text` carries the character). `modifiers` is
    /// the CDP bitmask: Alt=1, Ctrl=2, Meta=4, Shift=8.
    pub async fn dispatch_key_event(
        &self,
        tab: &Tab,
        event_type: &str,
        key: &str,
        code: &str,
        text: Option<&str>,
        windows_virtual_key_code: Option<i32>,
        modifiers: i32,
    ) -> Result<(), String> {
        let mut params = json!({
            "type": event_type,
            "key": key,
            "code": code,
            "modifiers": modifiers,
        });
        if let Some(text) = text {
            params["text"] = json!(text);
        }
        if let Some(vk) = windows_virtual_key_code {
            params["windowsVirtualKeyCode"] = json!(vk);
            params["nativeVirtualKeyCode"] = json!(vk);
        }
        self.send_cdp(&tab.ws_url, "Input.dispatchKeyEvent", params)
            .await?;
        Ok(())
    }

    /// Insert text via Input.insertText — IME-safe text entry that goes
    /// through the page's focused editable element without synthesizing
    /// individual key events.
    pub async fn insert_text(&self, tab: &Tab, text: &str) -> Result<(), String> {
        self.send_cdp(
            &tab.ws_url,
            "Input.insertText",
            json!({"text": text}),
        )
        .await?;
        Ok(())
    }

    // ── Privacy & fingerprinting ──────────────────────────────────

    /// Block a list of URL patterns via Network.setBlockedURLs.
    pub async fn set_blocked_urls(&self, tab: &Tab, patterns: &[String]) -> Result<(), String> {
        // Enable network domain first
        self.send_cdp(&tab.ws_url, "Network.enable", json!({})).await?;
        self.send_cdp(
            &tab.ws_url,
            "Network.setBlockedURLs",
            json!({"urls": patterns}),
        )
        .await?;
        Ok(())
    }

    /// Inject a script that runs before every new document (for fingerprint protection).
    pub async fn add_script_on_new_document(
        &self,
        tab: &Tab,
        script: &str,
    ) -> Result<(), String> {
        self.send_cdp(
            &tab.ws_url,
            "Page.addScriptToEvaluateOnNewDocument",
            json!({"source": script, "worldName": "jambu-privacy"}),
        )
        .await?;
        Ok(())
    }

    /// Get all cookies for the current page.
    pub async fn get_cookies(&self, tab: &Tab) -> Result<serde_json::Value, String> {
        self.send_cdp(&tab.ws_url, "Network.getCookies", json!({})).await
    }

    /// Delete cookies matching a name and domain.
    pub async fn delete_cookies(
        &self,
        tab: &Tab,
        name: &str,
        domain: &str,
    ) -> Result<(), String> {
        self.send_cdp(
            &tab.ws_url,
            "Network.deleteCookies",
            json!({"name": name, "domain": domain}),
        )
        .await?;
        Ok(())
    }

    /// Clear all cookies for the current page.
    pub async fn clear_cookies(&self, tab: &Tab) -> Result<(), String> {
        // Get all cookies first, then delete each
        let cookies = self.get_cookies(tab).await?;
        if let Some(list) = cookies.get("cookies").and_then(|c| c.as_array()) {
            for cookie in list {
                let name = cookie["name"].as_str().unwrap_or("");
                let domain = cookie["domain"].as_str().unwrap_or("");
                let _ = self.delete_cookies(tab, name, domain).await;
            }
        }
        Ok(())
    }

    /// Get performance metrics via CDP Performance.getMetrics.
    pub async fn get_performance_metrics(&self, tab: &Tab) -> Result<PerfMetrics, String> {
        let result = self
            .send_cdp(&tab.ws_url, "Performance.getMetrics", json!({}))
            .await?;

        let metrics = &result["metrics"];
        let get_num = |name: &str| -> Option<f64> {
            metrics.as_array()?.iter()
                .find(|m| m["name"].as_str() == Some(name))
                .and_then(|m| m["value"].as_f64())
        };

        Ok(PerfMetrics {
            dom_content_loaded_ms: get_num("DomContentLoaded"),
            load_complete_ms: get_num("LoadEventFired"),
            first_paint_ms: get_num("FirstPaint"),
            first_contentful_paint_ms: get_num("FirstContentfulPaint"),
            dom_nodes: get_num("Nodes").map(|v| v as u64),
            layout_count: get_num("LayoutCount").map(|v| v as u64),
            js_heap_used_mb: get_num("JSHeapUsedSize").map(|v| v / (1024.0 * 1024.0)),
        })
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
