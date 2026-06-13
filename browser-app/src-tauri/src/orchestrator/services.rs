use tauri_plugin_shell::ShellExt;

/// Background Service Orchestrator
///
/// Starts the Python FastAPI backend when the Tauri app launches.
pub fn start_all_services(app: &tauri::AppHandle) {
    let app_handle = app.clone();

    // Find project root (parent of browser-app/ since Tauri runs from there)
    let project_root = std::env::current_dir()
        .unwrap_or_else(|_| std::path::PathBuf::from("."))
        .parent()
        .map(|p| p.to_path_buf())
        .unwrap_or_else(|| std::path::PathBuf::from(".."));

    tauri::async_runtime::spawn(async move {
        let shell = app_handle.shell();
        let cmd = shell.command("python3")
            .args(["-m", "uvicorn", "backend.engine:app", "--host", "127.0.0.1", "--port", "8001"])
            .current_dir(&project_root);

        match cmd.spawn() {
            Ok((_rx, _child)) => {
                eprintln!("[jambu] Backend engine started on 127.0.0.1:8001");
            }
            Err(e) => {
                eprintln!("[jambu] Failed to start backend: {}", e);
            }
        }
    });
}
