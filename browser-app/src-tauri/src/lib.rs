mod chromium;
mod commands;
mod orchestrator;

use chromium::manager::ChromiumManager;
use commands::stream::StreamRegistry;
use commands::chromium::ScreencastRegistry;
use chromium::native_view::NativeViewRegistry;
use std::sync::Arc;
use tauri::{Emitter, Manager};
use tauri::menu::{MenuBuilder, SubmenuBuilder, MenuItemBuilder};
use tokio::sync::Mutex;

/// Shared application state accessible from all Tauri commands.
pub struct AppState {
    /// The Chromium browser engine.
    /// `None` until the browser is fully initialized during setup.
    pub chromium: Arc<Mutex<Option<ChromiumManager>>>,
    /// Base URL of the Python FastAPI backend (e.g. http://127.0.0.1:8001).
    /// Configurable via the JAMBU_BACKEND_URL env var; falls back to the
    /// default that the bundled dev/start scripts use.
    pub backend_url: String,
}

impl AppState {
    pub fn new() -> Self {
        Self {
            chromium: Arc::new(Mutex::new(None)),
            backend_url: std::env::var("JAMBU_BACKEND_URL")
                .unwrap_or_else(|_| "http://127.0.0.1:8001".to_string()),
        }
    }
}

/// Claim the per-user instance lock (`~/.jambubrowser/app.lock`).
/// Returns the open lock file on success — **the caller must keep the handle
/// alive for the process lifetime**, else the lock is released. Returns
/// `None` when another Jambubrowser instance already holds it.
///
/// Implemented with a lock file rather than `tauri-plugin-single-instance`
/// (which would add a dependency + lockfile churn) and `std`-only, so the
/// unit tests below can exercise it without a Tauri runtime.
fn claim_instance_lock() -> Option<std::fs::File> {
    let lock_path = dirs_lock_path()?;
    if let Some(parent) = lock_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let file = std::fs::OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open(&lock_path)
        .ok()?;
    // Mutual exclusion via an *exclusive create* on a companion `<lock>.pid`
    // file: the first creator wins, and losers check whether the recorded pid
    // is still alive (stale locks from crashes are taken over).
    let pid_path = lock_path.with_extension("pid");
    match std::fs::OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&pid_path)
    {
        Ok(mut f) => {
            use std::io::Write as _;
            let _ = writeln!(f, "{}", std::process::id());
            Some(file)
        }
        Err(_) => {
            // A pid file exists: check whether its owner is still alive.
            // Stale lock (previous crash/kill -9): take it over.
            let stale = std::fs::read_to_string(&pid_path)
                .ok()
                .and_then(|s| s.trim().parse::<i32>().ok())
                .map(|pid| !pid_is_alive(pid))
                .unwrap_or(true);
            if stale {
                let _ = std::fs::remove_file(&pid_path);
                if let Ok(mut f) = std::fs::OpenOptions::new()
                    .create_new(true)
                    .write(true)
                    .open(&pid_path)
                {
                    use std::io::Write as _;
                    let _ = writeln!(f, "{}", std::process::id());
                    return Some(file);
                }
            }
            None
        }
    }
}

/// Directory for the instance lock: `~/.jambubrowser/`.
fn dirs_lock_path() -> Option<std::path::PathBuf> {
    std::env::var_os("HOME")
        .map(|h| std::path::PathBuf::from(h).join(".jambubrowser").join("app.lock"))
}

/// True when a process with `pid` exists (kill(pid, 0) == success or EPERM).
#[cfg(unix)]
fn pid_is_alive(pid: i32) -> bool {
    // SAFETY: kill(2) with sig 0 performs no action beyond error reporting.
    unsafe {
        extern "C" {
            fn kill(pid: i32, sig: i32) -> i32;
        }
        kill(pid, 0) == 0
    }
}

#[cfg(not(unix))]
fn pid_is_alive(_pid: i32) -> bool {
    // Conservative on non-Unix: never treat a lock as stale.
    true
}

/// Remove our `<lock>.pid` claim on exit so a clean quit never leaves a
/// stale file behind (crashes are handled by the stale-owner check above).
fn release_instance_lock() {
    if let Some(p) = dirs_lock_path() {
        let _ = std::fs::remove_file(p.with_extension("pid"));
    }
}

/// Jambubrowser — Tauri Desktop Shell → Brave Competitor
///
/// Architecture:
///   Tauri WebView (React browser chrome) → CDP → Chromium (page rendering)
///                                          ↕
///                                     Python FastAPI (audit/research sidecar)
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Single-instance guard: a second `open` of the .app must focus the
    // running window, not start a duplicate app + duplicate engine (we found
    // two app processes and two sidecars alive simultaneously). Keep the lock
    // handle alive for the whole process; release the pid claim on exit.
    let _instance_guard = match claim_instance_lock() {
        Some(g) => g,
        None => {
            eprintln!("[jambu] another Jambubrowser instance is already running; exiting");
            // Best-effort focus of the running window is left to the OS
            // (macOS `open` activates the existing process when the second
            // instance exits immediately).
            std::process::exit(0);
        }
    };
    // Release the pid claim on exit (Drop runs at process teardown). Crashes
    // that skip Drop are handled by the stale-owner check in
    // claim_instance_lock.
    struct ReleaseOnDrop;
    impl Drop for ReleaseOnDrop {
        fn drop(&mut self) {
            release_instance_lock();
        }
    }
    let _release = ReleaseOnDrop;

    let app_state = AppState::new();
    let chromium_state = app_state.chromium.clone();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(app_state)
        .manage(StreamRegistry::default())
        .manage(ScreencastRegistry::default())
        .manage(NativeViewRegistry::default())
        .manage(std::sync::Arc::new(
            chromium::download_manager::DownloadManager::new(),
        ))
        .manage(std::sync::Arc::new(chromium::torrent::TorrentManager::new()))
        .setup(move |app| {
            let handle = app.handle().clone();

            // 0. Build native macOS menu bar
            let file_menu = SubmenuBuilder::new(app, "File")
                .item(&MenuItemBuilder::with_id("new_tab", "New Tab")
                    .accelerator("CmdOrCtrl+T")
                    .build(app)?)
                .item(&MenuItemBuilder::with_id("close_tab", "Close Tab")
                    .accelerator("CmdOrCtrl+W")
                    .build(app)?)
                .separator()
                .quit()
                .build()?;

            let edit_menu = SubmenuBuilder::new(app, "Edit")
                .undo()
                .redo()
                .separator()
                .cut()
                .copy()
                .paste()
                .select_all()
                .separator()
                .item(&MenuItemBuilder::with_id("find", "Find...")
                    .accelerator("CmdOrCtrl+F")
                    .build(app)?)
                .build()?;

            let view_menu = SubmenuBuilder::new(app, "View")
                .item(&MenuItemBuilder::with_id("reload", "Reload Page")
                    .accelerator("CmdOrCtrl+R")
                    .build(app)?)
                .separator()
                .item(&MenuItemBuilder::with_id("toggle_bookmarks", "Show Bookmark Bar")
                    .accelerator("CmdOrCtrl+Shift+B")
                    .build(app)?)
                .item(&MenuItemBuilder::with_id("toggle_devtools", "Developer Tools")
                    .accelerator("CmdOrCtrl+Shift+I")
                    .build(app)?)
                .build()?;

            let history_menu = SubmenuBuilder::new(app, "History")
                .item(&MenuItemBuilder::with_id("go_back", "Back")
                    .accelerator("CmdOrCtrl+[")
                    .build(app)?)
                .item(&MenuItemBuilder::with_id("go_forward", "Forward")
                    .accelerator("CmdOrCtrl+]")
                    .build(app)?)
                .separator()
                .item(&MenuItemBuilder::with_id("show_history", "Show Full History")
                    .accelerator("CmdOrCtrl+Y")
                    .build(app)?)
                .build()?;

            let bookmarks_menu = SubmenuBuilder::new(app, "Bookmarks")
                .item(&MenuItemBuilder::with_id("bookmark_page", "Bookmark This Page...")
                    .accelerator("CmdOrCtrl+D")
                    .build(app)?)
                .item(&MenuItemBuilder::with_id("bookmark_all_tabs", "Bookmark All Tabs...")
                    .build(app)?)
                .build()?;

            let window_menu = SubmenuBuilder::new(app, "Window")
                .minimize()
                .separator()
                .item(&MenuItemBuilder::with_id("next_tab", "Show Next Tab")
                    .accelerator("Control+Tab")
                    .build(app)?)
                .item(&MenuItemBuilder::with_id("prev_tab", "Show Previous Tab")
                    .accelerator("Control+Shift+Tab")
                    .build(app)?)
                .build()?;

            let menu = MenuBuilder::new(app)
                .item(&file_menu)
                .item(&edit_menu)
                .item(&view_menu)
                .item(&history_menu)
                .item(&bookmarks_menu)
                .item(&window_menu)
                .build()?;

            app.set_menu(menu)?;

            // Forward menu events to the frontend
            let menu_handle = handle.clone();
            app.on_menu_event(move |_app, event| {
                let id = event.id().0.as_str();
                let _ = menu_handle.emit("menu-event", id);
            });

            // 1. Start Python backend (existing)
            orchestrator::services::start_all_services(app.handle());

            // 2. Launch Chromium browser engine
            let state = chromium_state.clone();
            let handle_for_watchdog = handle.clone();
            tauri::async_runtime::spawn(async move {
                let chrome_path = find_chrome();
                eprintln!("[jambu] Using Chrome at: {}", chrome_path);

                let (profile_dir, persistent) = chromium::manager::initial_profile_dir();

                match ChromiumManager::launch(&chrome_path, 9222, profile_dir, persistent).await {
                    Ok(mgr) => {
                        eprintln!("[jambu] Chromium engine started on port 9222");
                        let mut locked = state.lock().await;
                        *locked = Some(mgr);
                        let _ = handle.emit("browser-ready", ());
                    }
                    Err(e) => {
                        eprintln!("[jambu] Failed to start Chromium: {e}");
                        let _ = handle.emit("browser-error", e);
                        return;
                    }
                }

                // Spawn the crash-recovery watchdog. It re-arms itself
                // on every restart, so this single call is enough for
                // the lifetime of the app.
                chromium::manager::spawn_watchdog(state, handle_for_watchdog);
            });

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::proxy::proxy_localhost,
            commands::stream::proxy_stream,
            commands::stream::proxy_stream_cancel,
            commands::system::get_local_ip,
            commands::chromium::browser_new_tab,
            commands::chromium::browser_navigate,
            commands::chromium::browser_reload,
            commands::chromium::browser_go_back,
            commands::chromium::browser_go_forward,
            commands::chromium::browser_close_tab,
            commands::chromium::browser_capture_screenshot,
            commands::chromium::browser_start_screencast,
            commands::chromium::browser_stop_screencast,
            chromium::native_view::browser_native_view,
            chromium::native_view::browser_native_set_rect,
            chromium::native_view::browser_native_url,
            chromium::native_view::browser_native_close,
            chromium::native_view::browser_native_action,
            commands::chromium::browser_evaluate,
            commands::chromium::browser_mouse_event,
            commands::chromium::browser_key_event,
            commands::chromium::browser_type_text,
            commands::chromium::browser_list_tabs,
            commands::chromium::browser_get_tab_info,
            commands::chromium::browser_get_cookies,
            commands::chromium::browser_clear_cookies,
            commands::chromium::browser_delete_cookie,
            commands::chromium::browser_list_extensions,
            commands::chromium::browser_set_extension_enabled,
            commands::chromium::browser_get_settings,
            commands::chromium::browser_set_persistent_profile,
            commands::chromium::browser_restart,
            commands::chromium::browser_run_audit,
            commands::chromium::browser_sync_tab,
            commands::chromium::browser_set_adblock,
            commands::chromium::browser_set_fingerprint,
            commands::chromium::browser_list_downloads,
            commands::chromium::browser_open_download,
            commands::chromium::browser_remove_download,
            commands::chromium::browser_download_url,
            commands::chromium::browser_http_start,
            commands::chromium::browser_http_list,
            commands::chromium::browser_http_pause,
            commands::chromium::browser_http_resume,
            commands::chromium::browser_http_cancel,
            commands::chromium::browser_http_remove,
            commands::chromium::browser_torrent_add,
            commands::chromium::browser_torrent_add_bytes,
            commands::chromium::browser_torrent_list,
            commands::chromium::browser_torrent_pause,
            commands::chromium::browser_torrent_resume,
            commands::chromium::browser_torrent_remove,
            commands::chromium::vault_get_credential,
            commands::agent::agent_bridge_status,
            commands::agent::agent_session_open,
            commands::agent::agent_session_act,
            commands::agent::agent_session_close,
            commands::agent::agent_run_audit,
            commands::agent::agent_run_research,
        ])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                let state = window.state::<AppState>();
                let chromium = state.chromium.clone();
                tauri::async_runtime::spawn(async move {
                    let mut locked = chromium.lock().await;
                    if let Some(ref mut mgr) = *locked {
                        mgr.shutdown();
                    }
                });
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

/// Find Chrome/Chromium executable on macOS.
///
/// Priority: CHROME_PATH env → Google Chrome → Brave → Chromium
fn find_chrome() -> String {
    if let Ok(path) = std::env::var("CHROME_PATH") {
        if std::path::Path::new(&path).exists() {
            return path;
        }
    }

    let candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/usr/local/bin/chromium",
    ];

    for path in &candidates {
        if std::path::Path::new(path).exists() {
            return path.to_string();
        }
    }

    candidates[0].to_string()
}
