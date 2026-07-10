//! Chromium process manager.
//!
//! Spawns and manages a Chromium (Chrome) instance with remote debugging
//! enabled. Tracks open tabs and provides high-level browser operations.

use rand::Rng;
use serde_json::Value;
use std::collections::HashMap;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use tokio::time::{sleep, Duration};

use super::audit::{self, AuditReport};
use super::cdp::CdpClient;
use super::extensions::{self, Extension};
use super::privacy;
use super::tab::{Tab, TabInfo};

/// Manages a Chromium browser process and its tabs.
pub struct ChromiumManager {
    /// The Chrome/Chromium child process
    process: Child,
    /// CDP debug port
    debug_port: u16,
    /// CDP client for communication
    cdp: CdpClient,
    /// Open tabs, keyed by Jambu tab ID
    tabs: HashMap<String, Tab>,
    /// Chrome user data directory (temp dir that gets cleaned up on drop)
    profile_dir: PathBuf,
}

impl ChromiumManager {
    /// Launch a new Chromium instance with remote debugging enabled.
    ///
    /// # Arguments
    /// * `chrome_path` — path to Chrome/Chromium executable
    /// * `debug_port` — port for CDP remote debugging (0 = auto-assign)
    /// * `profile_dir` — directory for Chrome user data (isolated profile)
    pub async fn launch(
        chrome_path: &str,
        debug_port: u16,
        profile_dir: PathBuf,
    ) -> Result<Self, String> {
        // Generate a short random profile name for isolation
        let port = if debug_port == 0 {
            rand::thread_rng().gen_range(9222..9999)
        } else {
            debug_port
        };

        // Ensure profile directory exists
        std::fs::create_dir_all(&profile_dir)
            .map_err(|e| format!("Failed to create profile dir: {e}"))?;

        // Discover installed extensions and build --load-extension arg
        let ext_dir = extensions::ensure_extensions_dir(&profile_dir);
        let discovered_exts = extensions::discover_extensions(&ext_dir);
        let ext_arg = extensions::build_load_extension_arg(&discovered_exts);

        // Spawn Chromium with privacy-focused flags
        let mut cmd = Command::new(chrome_path);
        cmd.args([
            // Remote debugging for CDP
            &format!("--remote-debugging-port={port}"),
            // Allow WebSocket connections from any origin (required by Chrome 130+)
            "--remote-allow-origins=*",
            // Isolated profile (no shared cookies/history/extensions)
            &format!("--user-data-dir={}", profile_dir.display()),
            // Disable first-run wizard
            "--no-first-run",
            "--no-default-browser-check",
            // Privacy: disable cloud services
            "--disable-sync",
            "--disable-background-networking",
            "--disable-component-update",
            // Disable unwanted Chrome features
            "--disable-features=TranslateUI,PasswordManagerReauthentication",
            // Start with a blank page
            "about:blank",
        ]);
        if let Some(arg) = &ext_arg {
            cmd.arg(arg.as_str());
        }
        let child = cmd
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| format!("Failed to launch Chrome at '{}': {}", chrome_path, e))?;

        // Wait for Chrome to be ready (retry connecting up to 10 seconds)
        let cdp = Self::wait_for_chrome(port, 20).await?;

        Ok(Self {
            process: child,
            debug_port: port,
            cdp,
            tabs: HashMap::with_capacity(8),
            profile_dir,
        })
    }

    /// Wait for Chrome to be ready on the given port, retrying up to `max_retries` times.
    async fn wait_for_chrome(port: u16, max_retries: u32) -> Result<CdpClient, String> {
        for i in 0..max_retries {
            sleep(Duration::from_millis(500)).await;
            match CdpClient::connect(port).await {
                Ok(client) => {
                    eprintln!("[jambu] Chrome ready on port {port} after {}ms", (i + 1) * 500);
                    return Ok(client);
                }
                Err(_) if i < max_retries - 1 => continue,
                Err(e) => return Err(format!("Chrome failed to start on port {port}: {e}")),
            }
        }
        unreachable!()
    }

    /// Create a new tab and navigate to the given URL.
    /// Applies ad/tracker blocking and fingerprint protection automatically.
    pub async fn create_tab(&mut self, url: &str) -> Result<TabInfo, String> {
        let tab = self.cdp.new_tab(url).await?;

        // Apply privacy protection
        let blocked = privacy::get_blocked_urls();
        if let Err(e) = self.cdp.set_blocked_urls(&tab, &blocked).await {
            eprintln!("[jambu] Failed to apply ad blocking: {e}");
        }
        if let Err(e) = self
            .cdp
            .add_script_on_new_document(&tab, privacy::FINGERPRINT_PROTECTION_SCRIPT)
            .await
        {
            eprintln!("[jambu] Failed to inject fingerprint protection: {e}");
        }

        self.cdp.navigate(&tab, url).await?;
        let info = TabInfo::from(&tab);
        self.tabs.insert(tab.id.clone(), tab);
        Ok(info)
    }

    /// Navigate an existing tab to a new URL.
    pub async fn navigate(&self, tab_id: &str, url: &str) -> Result<(), String> {
        let tab = self
            .tabs
            .get(tab_id)
            .ok_or_else(|| format!("Tab not found: {tab_id}"))?;
        self.cdp.navigate(tab, url).await
    }

    /// Reload the current page in a tab.
    pub async fn reload(&self, tab_id: &str) -> Result<(), String> {
        let tab = self
            .tabs
            .get(tab_id)
            .ok_or_else(|| format!("Tab not found: {tab_id}"))?;
        self.cdp.reload(tab).await
    }

    /// Go back in history for a tab.
    pub async fn go_back(&self, tab_id: &str) -> Result<(), String> {
        let tab = self
            .tabs
            .get(tab_id)
            .ok_or_else(|| format!("Tab not found: {tab_id}"))?;
        self.cdp.go_back(tab).await
    }

    /// Go forward in history for a tab.
    pub async fn go_forward(&self, tab_id: &str) -> Result<(), String> {
        let tab = self
            .tabs
            .get(tab_id)
            .ok_or_else(|| format!("Tab not found: {tab_id}"))?;
        self.cdp.go_forward(tab).await
    }

    /// Close a tab.
    pub async fn close_tab(&mut self, tab_id: &str) -> Result<(), String> {
        let tab = self
            .tabs
            .remove(tab_id)
            .ok_or_else(|| format!("Tab not found: {tab_id}"))?;
        self.cdp.close_tab(&tab.target_id).await?;
        Ok(())
    }

    /// Capture a screenshot of the tab (returns base64 PNG).
    pub async fn capture_screenshot(&self, tab_id: &str) -> Result<String, String> {
        let tab = self
            .tabs
            .get(tab_id)
            .ok_or_else(|| format!("Tab not found: {tab_id}"))?;
        self.cdp.capture_screenshot(tab).await
    }

    /// Execute JavaScript in the tab.
    pub async fn evaluate(&self, tab_id: &str, expression: &str) -> Result<String, String> {
        let tab = self
            .tabs
            .get(tab_id)
            .ok_or_else(|| format!("Tab not found: {tab_id}"))?;
        self.cdp.evaluate(tab, expression).await
    }

    /// Get all cookies for a tab.
    pub async fn get_cookies(&self, tab_id: &str) -> Result<Value, String> {
        let tab = self
            .tabs
            .get(tab_id)
            .ok_or_else(|| format!("Tab not found: {tab_id}"))?;
        self.cdp.get_cookies(tab).await
    }

    /// Clear all cookies for a tab.
    pub async fn clear_cookies(&self, tab_id: &str) -> Result<(), String> {
        let tab = self
            .tabs
            .get(tab_id)
            .ok_or_else(|| format!("Tab not found: {tab_id}"))?;
        self.cdp.clear_cookies(tab).await
    }

    /// Delete a specific cookie.
    pub async fn delete_cookie(
        &self,
        tab_id: &str,
        name: &str,
        domain: &str,
    ) -> Result<(), String> {
        let tab = self
            .tabs
            .get(tab_id)
            .ok_or_else(|| format!("Tab not found: {tab_id}"))?;
        self.cdp.delete_cookies(tab, name, domain).await
    }

    /// Get tab info.
    pub fn get_tab(&self, tab_id: &str) -> Option<TabInfo> {
        self.tabs.get(tab_id).map(TabInfo::from)
    }

    /// Sync tab state from the actual page (title + URL).
    pub async fn sync_tab(&mut self, tab_id: &str) -> Result<TabInfo, String> {
        let title;
        let url;
        {
            let tab = self
                .tabs
                .get(tab_id)
                .ok_or_else(|| format!("Tab not found: {tab_id}"))?;
            title = self.cdp.get_page_title(tab).await.unwrap_or_else(|_| tab.title.clone());
            url = self.cdp.get_page_url(tab).await.unwrap_or_else(|_| tab.url.clone());
        }
        // Update the stored tab and capture privacy state
        let (adblock_enabled, fp_enabled) = if let Some(tab) = self.tabs.get_mut(tab_id) {
            tab.title = title.clone();
            tab.url = url.clone();
            tab.loading = false;
            (tab.adblock_enabled, tab.fp_enabled)
        } else {
            (true, true)
        };
        Ok(TabInfo {
            id: tab_id.to_string(),
            target_id: String::new(),
            url,
            title,
            loading: false,
            adblock_enabled,
            fp_enabled,
        })
    }

    /// List all open tabs.
    pub fn list_tabs(&self) -> Vec<TabInfo> {
        self.tabs.values().map(TabInfo::from).collect()
    }

    /// Discover and list all extensions in the extensions directory.
    pub fn list_extensions(&self) -> Vec<Extension> {
        let dir = extensions::ensure_extensions_dir(&self.profile_dir);
        extensions::discover_extensions(&dir)
    }

    /// Get the path to the extensions directory.
    pub fn extensions_dir(&self) -> PathBuf {
        extensions::ensure_extensions_dir(&self.profile_dir)
    }

    /// Enable or disable ad/tracker blocking on a tab.
    pub async fn set_adblock_enabled(&mut self, tab_id: &str, enabled: bool) -> Result<(), String> {
        let tab = self
            .tabs
            .get(tab_id)
            .ok_or_else(|| format!("Tab not found: {tab_id}"))?;
        let patterns = if enabled {
            privacy::get_blocked_urls()
        } else {
            Vec::new()
        };
        self.cdp.set_blocked_urls(tab, &patterns).await?;
        if let Some(tab) = self.tabs.get_mut(tab_id) {
            tab.adblock_enabled = enabled;
        }
        Ok(())
    }

    /// Enable or disable fingerprint protection on a tab.
    /// When enabling, injects into the current page and future documents.
    /// When disabling, only affects future navigations (can't un-inject from current page).
    pub async fn set_fingerprint_enabled(&mut self, tab_id: &str, enabled: bool) -> Result<(), String> {
        let tab = self
            .tabs
            .get(tab_id)
            .ok_or_else(|| format!("Tab not found: {tab_id}"))?;
        if enabled {
            // Inject into the current page via evaluate (immediate)
            self.cdp.evaluate(tab, privacy::FINGERPRINT_PROTECTION_SCRIPT).await.ok();
            // Inject for future documents
            self.cdp.add_script_on_new_document(tab, privacy::FINGERPRINT_PROTECTION_SCRIPT).await?;
        }
        if let Some(tab) = self.tabs.get_mut(tab_id) {
            tab.fp_enabled = enabled;
        }
        Ok(())
    }

    /// Run a page audit on the given tab.
    pub async fn run_audit(&self, tab_id: &str) -> Result<AuditReport, String> {
        let tab = self
            .tabs
            .get(tab_id)
            .ok_or_else(|| format!("Tab not found: {tab_id}"))?;
        audit::run_audit(&self.cdp, tab).await
    }

    /// Shut down the Chromium process gracefully.
    pub fn shutdown(&mut self) {
        // Try to close via CDP first
        let close_url = format!("http://127.0.0.1:{}/json/close/all", self.debug_port);
        let rt = tokio::runtime::Runtime::new().unwrap();
        let _ = rt.block_on(async { reqwest::get(&close_url).await });

        // Force kill if still running
        let _ = self.process.kill();
        let _ = self.process.wait();

        // Clean up profile directory
        let _ = std::fs::remove_dir_all(&self.profile_dir);
    }
}

impl Drop for ChromiumManager {
    fn drop(&mut self) {
        let _ = self.process.kill();
        let _ = self.process.wait();
        // Best-effort cleanup — ignore errors on drop
        let _ = std::fs::remove_dir_all(&self.profile_dir);
    }
}
