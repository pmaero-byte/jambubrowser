mod chromium;
mod commands;
mod orchestrator;

use chromium::manager::ChromiumManager;
use std::sync::Arc;
use tauri::{Emitter, Manager};
use tokio::sync::Mutex;

/// Shared application state accessible from all Tauri commands.
pub struct AppState {
    /// The Chromium browser engine.
    /// `None` until the browser is fully initialized during setup.
    pub chromium: Arc<Mutex<Option<ChromiumManager>>>,
}

impl AppState {
    pub fn new() -> Self {
        Self {
            chromium: Arc::new(Mutex::new(None)),
        }
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
    let app_state = AppState::new();
    let chromium_state = app_state.chromium.clone();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(app_state)
        .setup(move |app| {
            let handle = app.handle().clone();

            // 1. Start Python backend (existing)
            orchestrator::services::start_all_services(app.handle());

            // 2. Launch Chromium browser engine
            let state = chromium_state.clone();
            tauri::async_runtime::spawn(async move {
                let chrome_path = find_chrome();
                eprintln!("[jambu] Using Chrome at: {}", chrome_path);

                let profile_dir = std::env::temp_dir().join("jambubrowser-chrome-profile");

                match ChromiumManager::launch(&chrome_path, 9222, profile_dir).await {
                    Ok(mgr) => {
                        eprintln!("[jambu] Chromium engine started on port 9222");
                        let mut locked = state.lock().await;
                        *locked = Some(mgr);
                        let _ = handle.emit("browser-ready", ());
                    }
                    Err(e) => {
                        eprintln!("[jambu] Failed to start Chromium: {e}");
                        let _ = handle.emit("browser-error", e);
                    }
                }
            });

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::proxy::proxy_localhost,
            commands::system::get_local_ip,
            commands::chromium::browser_new_tab,
            commands::chromium::browser_navigate,
            commands::chromium::browser_reload,
            commands::chromium::browser_go_back,
            commands::chromium::browser_go_forward,
            commands::chromium::browser_close_tab,
            commands::chromium::browser_capture_screenshot,
            commands::chromium::browser_evaluate,
            commands::chromium::browser_list_tabs,
            commands::chromium::browser_get_tab_info,
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
