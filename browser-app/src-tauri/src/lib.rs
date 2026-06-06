mod orchestrator;
mod commands;

/// Jambubrowser: The Rust Orchestration Layer
/// -----------------------------------------
/// This is the entry point for the native macOS application.
/// It uses 'Tauri' to connect our fast Rust code to the React UI.
///
/// To keep the code simple for AI and non-technical contributors, 
/// the logic is split into:
/// 1. 'orchestrator/': The Brain (Swarm, Debate, Intent logic).
/// 2. 'commands/': The API (Functions that the UI calls).

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            // Iteration 134: One-Click Startup
            orchestrator::services::start_all_services(app.handle());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::research::execute_query,
            commands::system::get_local_ip
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
