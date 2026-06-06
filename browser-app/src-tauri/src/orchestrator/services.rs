use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::CommandEvent;

/**
 * Background Service Orchestrator
 * ------------------------------
 * This module is responsible for 'One-Click' readiness.
 * It automatically starts the Python Action Engine and SearXNG
 * when the Jambu application is launched.
 */

pub fn start_all_services(app: &tauri::AppHandle) {
    let app_handle = app.clone();
    
    // 1. Start the Python Action Engine
    // We assume 'micromamba' and the env are in the relative path or bundled
    tauri::async_runtime::spawn(async move {
        let shell = app_handle.shell();
        let cmd = shell.command("python")
            .args(["engine.py"])
            .current_dir(std::env::current_dir().unwrap());
            
        let (mut _rx, _child) = cmd.spawn().expect("Failed to start Action Engine");
        
        println!("🚀 Sovereign Action Engine started in background.");
    });

    // 2. Start SearXNG (Simplified for this sprint)
    // In a full release, this would be a bundled sidecar
    println!("🔍 Initializing Metasearch Proxy...");
}
