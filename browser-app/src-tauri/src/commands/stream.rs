//! Streaming proxy command.
//!
//! `proxy_localhost` buffers the entire response body, which silently
//! breaks SSE: agent runs and audits arrive only after they complete.
//! `proxy_stream` forwards response chunks to the frontend as they
//! arrive over a Tauri IPC channel, so `localFetchStream` (api.ts) can
//! expose a real `ReadableStream` inside the WebView.

use crate::commands::proxy::ProxyRequest;
use base64::Engine;
use reqwest::{Client, Method, Response};
use std::collections::HashMap;
use std::sync::Mutex;
use std::time::Duration;
use tauri::ipc::Channel;
use tauri::{AppHandle, Manager, State};

/// One message on a proxy stream.
#[derive(Clone, serde::Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum StreamEvent {
    /// Response headers received. Sent before any body chunk so the
    /// frontend can construct its `Response` with the real status code.
    Init {
        status: u16,
        headers: HashMap<String, String>,
    },
    /// Base64-encoded body bytes. Bytes (not text) so UTF-8 sequences
    /// split across chunks reassemble correctly in the frontend decoder.
    Chunk { data: String },
    /// Stream completed.
    End,
    /// Stream failed before completion; `message` is user-presentable.
    Error { message: String },
}

/// In-flight streams keyed by stream id, so a cancel command can abort
/// the underlying task when the frontend aborts its AbortController.
#[derive(Default)]
pub struct StreamRegistry(pub Mutex<HashMap<String, tauri::async_runtime::JoinHandle<()>>>);

/// Start a streaming request. Returns as soon as the request is issued;
/// all response data flows through `on_event`. The server's status code
/// and headers arrive in the first `Init` event.
#[tauri::command]
pub async fn proxy_stream(
    app: AppHandle,
    request: ProxyRequest,
    stream_id: String,
    on_event: Channel<StreamEvent>,
) -> Result<(), String> {
    let method = request
        .method
        .parse::<Method>()
        .map_err(|e| format!("Invalid HTTP method: {e}"))?;

    // No overall timeout: SSE runs (agent, audit) legitimately take
    // minutes. Connect timeouts still protect against dead endpoints.
    let client = Client::builder()
        .connect_timeout(Duration::from_secs(10))
        .build()
        .map_err(|e| format!("Failed to build HTTP client: {e}"))?;

    let mut req = client.request(method, &request.url);
    for (k, v) in &request.headers {
        req = req.header(k, v);
    }
    if let Some(body) = &request.body {
        req = req.body(body.clone());
    }

    let id = stream_id.clone();
    let cleanup_handle = app.clone();
    let handle = tauri::async_runtime::spawn(async move {
        match req.send().await {
            Ok(resp) => {
                if let Err(message) = forward_response(resp, &on_event).await {
                    let _ = on_event.send(StreamEvent::Error { message });
                }
            }
            Err(e) => {
                let _ = on_event.send(StreamEvent::Error {
                    message: format!("Proxy request failed: {e}"),
                });
            }
        }
        // Drop this stream from the registry so it doesn't leak.
        if let Some(state) = cleanup_handle.try_state::<StreamRegistry>() {
            state.0.lock().unwrap().remove(&id);
        }
    });

    app.state::<StreamRegistry>()
        .0
        .lock()
        .unwrap()
        .insert(stream_id, handle);
    Ok(())
}

/// Abort an in-flight stream. Safe to call for unknown/already-finished
/// ids (no-op).
#[tauri::command]
pub fn proxy_stream_cancel(stream_id: String, registry: State<'_, StreamRegistry>) {
    if let Some(handle) = registry.0.lock().unwrap().remove(&stream_id) {
        handle.abort();
    }
}

/// Read the response chunk by chunk, forwarding each one to the channel.
/// `Response::chunk()` needs no extra reqwest features and is bounded by
/// the server's write cadence — exactly what SSE wants.
async fn forward_response(
    mut resp: Response,
    on_event: &Channel<StreamEvent>,
) -> Result<(), String> {
    let status = resp.status().as_u16();
    let mut headers = HashMap::new();
    for (k, v) in resp.headers().iter() {
        if let Ok(v) = v.to_str() {
            headers.insert(k.as_str().to_string(), v.to_string());
        }
    }
    on_event
        .send(StreamEvent::Init { status, headers })
        .map_err(|e| format!("stream init: {e}"))?;

    while let Some(chunk) = resp
        .chunk()
        .await
        .map_err(|e| format!("stream read: {e}"))?
    {
        let data = base64::engine::general_purpose::STANDARD.encode(&chunk);
        on_event
            .send(StreamEvent::Chunk { data })
            .map_err(|e| format!("stream chunk: {e}"))?;
    }

    let _ = on_event.send(StreamEvent::End);
    Ok(())
}
