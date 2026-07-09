use serde::Serialize;

/// Represents a single browser tab backed by a Chromium target.
#[derive(Debug, Clone, Serialize)]
pub struct Tab {
    /// Jambu-assigned tab ID (short, UI-friendly)
    pub id: String,
    /// CDP target ID assigned by Chromium
    pub target_id: String,
    /// CDP WebSocket debugger URL for this specific target
    pub ws_url: String,
    /// Current URL loaded in this tab
    pub url: String,
    /// Page title (updated via CDP events)
    pub title: String,
    /// Whether the tab is currently loading
    pub loading: bool,
}

impl Tab {
    pub fn new(id: String, target_id: String, ws_url: String, url: String) -> Self {
        let title = url.clone();
        Self {
            id,
            target_id,
            ws_url,
            url,
            title,
            loading: true,
        }
    }
}

/// Serializable tab info sent to the frontend (excludes internal fields like ws_url).
#[derive(Debug, Clone, Serialize)]
pub struct TabInfo {
    pub id: String,
    pub target_id: String,
    pub url: String,
    pub title: String,
    pub loading: bool,
}

impl From<&Tab> for TabInfo {
    fn from(t: &Tab) -> Self {
        Self {
            id: t.id.clone(),
            target_id: t.target_id.clone(),
            url: t.url.clone(),
            title: t.title.clone(),
            loading: t.loading,
        }
    }
}
