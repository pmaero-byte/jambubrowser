use tauri_plugin_shell::ShellExt;

/// Background Service Orchestrator
///
/// Starts the Python FastAPI backend when the Tauri app launches.
///
/// The bundled `.app` runs with cwd `/` (or the user's home), *not* the
/// repo checkout — so `backend.*` is unimportable unless we (a) resolve
/// the checkout from the executable path, and (b) put it on
/// `PYTHONPATH`. Without both, the spawn "succeeds" but uvicorn exits
/// with `ModuleNotFoundError: No module named 'backend'` and the UI
/// shows "engine unreachable" on every panel.
pub fn start_all_services(app: &tauri::AppHandle) {
    let app_handle = app.clone();

    // Resolve the checkout root by walking up from the executable until a
    // directory containing `backend/engine.py` is found (max 8 levels).
    // Dev (`target/{debug,release}/browser-app`) finds the checkout; the
    // bundled `.app` (`Contents/MacOS/browser-app`) finds nothing and the
    // spawn is skipped with a clear log line instead of a ghost success.
    let exe = std::env::current_exe().unwrap_or_else(|_| std::path::PathBuf::from("."));
    let mut project_root: Option<std::path::PathBuf> = None;
    let mut cursor = exe.parent().map(|p| p.to_path_buf());
    for _ in 0..8 {
        match cursor {
            Some(dir) => {
                if dir.join("backend").join("engine.py").exists() {
                    project_root = Some(dir);
                    break;
                }
                cursor = dir.parent().map(|p| p.to_path_buf());
            }
            None => break,
        }
    }

    tauri::async_runtime::spawn(async move {
        let Some(project_root) = project_root else {
            eprintln!("[jambu] no backend checkout next to executable; skipping sidecar spawn");
            return;
        };
        let shell = app_handle.shell();
        // Prefer the project's own venv interpreter: the system `python3`
        // frequently lacks the engine's dependencies (it did here until the
        // venv was populated), and the engine silently failed to boot.
        let venv_py = project_root.join(".venv").join(if cfg!(windows) {
            "Scripts/python.exe"
        } else {
            "bin/python"
        });
        let interpreter = if venv_py.exists() {
            venv_py.to_string_lossy().into_owned()
        } else {
            "python3".to_string()
        };
        let cmd = shell.command(&interpreter)
            .args(["-m", "uvicorn", "backend.engine:app", "--host", "127.0.0.1", "--port", "8001"])
            .current_dir(&project_root)
            .env("PYTHONPATH", project_root.to_string_lossy().into_owned());

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
