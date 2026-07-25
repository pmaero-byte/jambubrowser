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
use super::downloads;
use super::extensions::{self, Extension};
use super::privacy;
use super::settings;

/// Make a fresh per-launch profile dir under the OS temp dir. Called
/// from `initial_profile_dir` and `restart` when the user has NOT opted
/// into a persistent profile (the default — "forensic safety").
fn ensure_profile_dir() -> PathBuf {
    let dir = std::env::temp_dir().join(format!("jambubrowser-chrome-profile-{}", uuid_v4_like()));
    let _ = std::fs::create_dir_all(&dir);
    dir
}

/// Pick the Chrome profile dir for a launch based on persisted settings.
/// Returns `(dir, persistent)`. Default is a fresh ephemeral temp dir;
/// with `persistent_profile` on it's the stable config-dir profile that
/// keeps cookies/logins across launches and is never wiped.
pub fn initial_profile_dir() -> (PathBuf, bool) {
    if settings::load().persistent_profile {
        (settings::persistent_profile_dir(), true)
    } else {
        (ensure_profile_dir(), false)
    }
}

/// Tiny random-suffix helper so each restart gets its own profile dir
/// without pulling in the `uuid` crate. 8 hex chars = 32 bits of entropy
/// which is plenty to avoid collisions on a single user machine.
pub fn uuid_v4_like() -> String {
    use rand::Rng;
    let mut rng = rand::thread_rng();
    let bytes: [u8; 4] = rng.gen();
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
use super::tab::{Tab, TabInfo};

/// Manages a Chromium browser process and its tabs.
pub struct ChromiumManager {
    /// Path to the Chrome/Chromium binary. Kept around so restart() can
    /// re-spawn without the caller having to remember it.
    chrome_path: String,
    /// The Chrome/Chromium child process
    process: Child,
    /// CDP debug port
    debug_port: u16,
    /// CDP client for communication
    cdp: CdpClient,
    /// Open tabs, keyed by Jambu tab ID
    tabs: HashMap<String, Tab>,
    /// Chrome user data directory (temp dir that gets cleaned up on drop,
    /// unless `persistent_profile` is set)
    profile_dir: PathBuf,
    /// Whether `profile_dir` is the stable persistent profile. When true,
    /// the dir is never wiped (Drop/shutdown/restart leave it alone).
    persistent_profile: bool,
}

impl ChromiumManager {
    /// Launch a new Chromium instance with remote debugging enabled.
    ///
    /// # Arguments
    /// * `chrome_path` — path to Chrome/Chromium executable
    /// * `debug_port` — port for CDP remote debugging (0 = auto-assign)
    /// * `profile_dir` — directory for Chrome user data (isolated profile)
    /// * `persistent_profile` — true when `profile_dir` is the stable
    ///   persistent profile and must survive exit/restart. Note: only one
    ///   Chrome instance can use a given profile dir at a time; launching
    ///   a second Jambubrowser instance with persistence on will fail at
    ///   the debug-port check (the first instance owns the port anyway).
    pub async fn launch(
        chrome_path: &str,
        debug_port: u16,
        profile_dir: PathBuf,
        persistent_profile: bool,
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

        // Discover installed extensions and build --load-extension arg,
        // skipping any the user has disabled in settings.
        let ext_dir = extensions::ensure_extensions_dir();
        let mut discovered_exts = extensions::discover_extensions(&ext_dir);
        extensions::apply_enabled_state(&mut discovered_exts, &settings::load());
        let ext_arg = extensions::build_load_extension_arg(&discovered_exts);

        // Spawn Chromium with privacy-focused flags
        let mut cmd = Command::new(chrome_path);
        // Ensure the download directory exists before passing it to Chrome,
        // so the OS file browser can find it from the very first download.
        let download_dir = downloads::ensure_download_dir();
        cmd.args([
            // Remote debugging for CDP
            &format!("--remote-debugging-port={port}"),
            // Allow WebSocket connections from any origin (required by Chrome 130+)
            "--remote-allow-origins=*",
            // Isolated profile (no shared cookies/history/extensions)
            &format!("--user-data-dir={}", profile_dir.display()),
            // Route all downloads into a known directory (see chromium/downloads.rs).
            // `=downloads::default_download_dir()` doesn't work here because
            // ensure_download_dir() has the side effect of creating the dir.
            &format!("--download.default-directory={}", download_dir.display()),
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
            chrome_path: chrome_path.to_string(),
            process: child,
            debug_port: port,
            cdp,
            tabs: HashMap::with_capacity(8),
            profile_dir,
            persistent_profile,
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

    /// Dispatch a mouse event to the tab (see CdpClient::dispatch_mouse_event).
    #[allow(clippy::too_many_arguments)]
    pub async fn dispatch_mouse_event(
        &self,
        tab_id: &str,
        event_type: &str,
        x: f64,
        y: f64,
        button: &str,
        click_count: i32,
        delta_x: f64,
        delta_y: f64,
    ) -> Result<(), String> {
        let tab = self
            .tabs
            .get(tab_id)
            .ok_or_else(|| format!("Tab not found: {tab_id}"))?;
        self.cdp
            .dispatch_mouse_event(tab, event_type, x, y, button, click_count, delta_x, delta_y)
            .await
    }

    /// Dispatch a key event to the tab (see CdpClient::dispatch_key_event).
    pub async fn dispatch_key_event(
        &self,
        tab_id: &str,
        event_type: &str,
        key: &str,
        code: &str,
        text: Option<&str>,
        windows_virtual_key_code: Option<i32>,
        modifiers: i32,
    ) -> Result<(), String> {
        let tab = self
            .tabs
            .get(tab_id)
            .ok_or_else(|| format!("Tab not found: {tab_id}"))?;
        self.cdp
            .dispatch_key_event(tab, event_type, key, code, text, windows_virtual_key_code, modifiers)
            .await
    }

    /// Insert text into the tab's focused element (see CdpClient::insert_text).
    pub async fn insert_text(&self, tab_id: &str, text: &str) -> Result<(), String> {
        let tab = self
            .tabs
            .get(tab_id)
            .ok_or_else(|| format!("Tab not found: {tab_id}"))?;
        self.cdp.insert_text(tab, text).await
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

    /// Discover and list all extensions in the extensions directory,
    /// with each one's enabled state read from persisted settings.
    pub fn list_extensions(&self) -> Vec<Extension> {
        let dir = extensions::ensure_extensions_dir();
        let mut exts = extensions::discover_extensions(&dir);
        extensions::apply_enabled_state(&mut exts, &settings::load());
        exts
    }

    /// Get the path to the extensions directory.
    pub fn extensions_dir(&self) -> PathBuf {
        extensions::ensure_extensions_dir()
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

    /// Kill the existing process (if alive) and spawn a fresh Chromium
    /// instance on the same debug port. Wipes the tab map because the
    /// old CDP target IDs are no longer valid. Intended for the crash-
    /// recovery watchdog — a clean restart path is the frontend just
    /// calling shutdown() + relaunch from lib.rs.
    ///
    /// Settings (persistent profile, disabled extensions) are re-read
    /// here, so toggling them and restarting the browser applies them
    /// immediately — this backs the UI's "Restart browser now" action.
    pub async fn restart(&mut self) -> Result<(), String> {
        // Best-effort kill. We don't care if it returns an error
        // (process might already be dead).
        let _ = self.process.kill();
        let _ = self.process.wait();

        // Re-read settings so toggles made since launch take effect.
        let persistent = settings::load().persistent_profile;

        // Wipe the old profile only if it was ephemeral. The persistent
        // dir is user data (cookies, logins) and must survive.
        if !self.persistent_profile {
            let _ = std::fs::remove_dir_all(&self.profile_dir);
        }
        let profile_dir = if persistent {
            settings::persistent_profile_dir()
        } else {
            ensure_profile_dir()
        };

        // Reuse the same port the previous Chrome was on. If that port
        // is still in TIME_WAIT we just retry on a different one; the
        // frontend reads the new port from `debug_port` after restart.
        let port = self.debug_port;
        let mut next = Self::launch(&self.chrome_path, port, profile_dir, persistent).await?;

        // Move the new manager's state into self so the caller keeps
        // holding the same struct (Arc<Mutex<...>> doesn't need to know).
        std::mem::swap(&mut self.chrome_path, &mut next.chrome_path);
        std::mem::swap(&mut self.process, &mut next.process);
        std::mem::swap(&mut self.debug_port, &mut next.debug_port);
        std::mem::swap(&mut self.cdp, &mut next.cdp);
        std::mem::swap(&mut self.profile_dir, &mut next.profile_dir);
        std::mem::swap(&mut self.persistent_profile, &mut next.persistent_profile);
        // next now owns the dead process and dead profile_dir; its
        // Drop will clean them up (unless persistent).

        // Tab map is stale — CDP target IDs are gone with the old Chrome.
        self.tabs.clear();
        Ok(())
    }

    /// Returns true if the child process is still running. Cheap: just
    /// calls `try_wait` and treats both `Ok(None)` and any error as
    /// 'still running' (because we don't want the watchdog to panic on
    /// transient errors).
    pub fn is_alive(&mut self) -> bool {
        match self.process.try_wait() {
            Ok(None) => true,
            Ok(Some(_)) | Err(_) => false,
        }
    }

    /// The debug port the running Chrome is listening on. Useful for the
    /// watchdog and the frontend (after a restart, the port may differ
    /// from the original if the old port was still in TIME_WAIT).
    pub fn debug_port(&self) -> u16 {
        self.debug_port
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

        // Clean up profile directory (ephemeral only — a persistent
        // profile is user data and survives shutdown).
        if !self.persistent_profile {
            let _ = std::fs::remove_dir_all(&self.profile_dir);
        }
    }
}

use std::sync::Arc;
use tauri::Emitter;
use tokio::sync::Mutex;

/// Watchdog: periodically checks whether the Chromium child is alive
/// and, if it died unexpectedly, calls `restart()` to bring it back
/// up. Emits a `browser-restarted` Tauri event so the frontend can
/// surface a toast.
///
/// `state` is the same Arc<Mutex<Option<ChromiumManager>>> the rest of
/// the app uses; the watchdog briefly locks it to check the process
/// status and to perform the restart under the same lock the Tauri
/// commands use (so we never race with browser_new_tab et al).
pub fn spawn_watchdog(
    state: Arc<Mutex<Option<ChromiumManager>>>,
    app: tauri::AppHandle,
) {
    tauri::async_runtime::spawn(async move {
        // Re-arm on every restart so the watchdog keeps watching the
        // fresh process. The loop only exits if the manager slot goes
        // back to None (e.g. on app shutdown).
        loop {
            const POLL_INTERVAL_MS: u64 = 3000;
            tokio::time::sleep(Duration::from_millis(POLL_INTERVAL_MS)).await;

            let mut guard = state.lock().await;
            let Some(mgr) = guard.as_mut() else {
                // Manager slot is empty — app is shutting down.
                break;
            };
            if mgr.is_alive() {
                continue;
            }

            eprintln!("[jambu] Chromium crashed — restarting");
            match mgr.restart().await {
                Ok(()) => {
                    eprintln!("[jambu] Chromium restarted on port {}", mgr.debug_port());
                    let _ = app.emit("browser-restarted", mgr.debug_port());
                }
                Err(e) => {
                    eprintln!("[jambu] Chromium restart failed: {e}");
                    let _ = app.emit("browser-error", e);
                    // Don't tight-loop on a failed restart; the next
                    // poll tick (3s) will try again.
                }
            }
        }
    });
}

impl Drop for ChromiumManager {
    fn drop(&mut self) {
        let _ = self.process.kill();
        let _ = self.process.wait();
        // Best-effort cleanup — ignore errors on drop. Persistent
        // profiles are never wiped.
        if !self.persistent_profile {
            let _ = std::fs::remove_dir_all(&self.profile_dir);
        }
    }
}
