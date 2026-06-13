mod orchestrator;
mod commands;

/// Jambubrowser — Tauri Desktop Shell
///
/// Thin native wrapper that spawns the Python FastAPI backend on launch
/// and hosts the React frontend in a WebView window.
///
/// Architecture:
///   Tauri WebView (React UI) → HTTP localhost:8001 → Python FastAPI backend
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            orchestrator::services::start_all_services(app.handle());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::system::get_local_ip
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
