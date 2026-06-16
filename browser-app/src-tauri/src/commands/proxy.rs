/// UI Command: Proxy localhost requests to the Python backend
///
/// The Tauri WebView CSP does not allow arbitrary `connect-src` to
/// `127.0.0.1:*`. This command lets the React frontend call the FastAPI
/// backend through Rust, which is allowed by the shell capabilities.

use reqwest::{Client, Method};
use serde::Deserialize;
use std::collections::HashMap;

#[derive(Debug, Deserialize)]
pub struct ProxyRequest {
    url: String,
    method: String,
    #[serde(default)]
    headers: HashMap<String, String>,
    #[serde(default)]
    body: Option<String>,
}

#[derive(Debug, serde::Serialize)]
pub struct ProxyResponse {
    status: u16,
    headers: HashMap<String, String>,
    body: String,
}

#[tauri::command]
pub async fn proxy_localhost(request: ProxyRequest) -> Result<ProxyResponse, String> {
    let method = request
        .method
        .parse::<Method>()
        .map_err(|e| format!("Invalid HTTP method: {e}"))?;

    let client = Client::builder()
        .timeout(std::time::Duration::from_secs(60))
        .build()
        .map_err(|e| format!("Failed to build HTTP client: {e}"))?;

    let mut req = client.request(method, &request.url);
    for (k, v) in request.headers {
        req = req.header(k, v);
    }
    if let Some(body) = request.body {
        req = req.body(body);
    }

    let resp = req
        .send()
        .await
        .map_err(|e| format!("Proxy request failed: {e}"))?;

    let status = resp.status().as_u16();
    let mut headers = HashMap::new();
    for (k, v) in resp.headers().iter() {
        if let Ok(v) = v.to_str() {
            headers.insert(k.as_str().to_string(), v.to_string());
        }
    }

    let body = resp
        .text()
        .await
        .map_err(|e| format!("Failed to read response body: {e}"))?;

    Ok(ProxyResponse {
        status,
        headers,
        body,
    })
}
