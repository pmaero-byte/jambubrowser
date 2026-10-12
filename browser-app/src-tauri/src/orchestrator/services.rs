use tauri_plugin_shell::ShellExt;

/// Resolve the repo checkout by walking up from the executable until a
/// directory containing `backend/engine.py` is found (max 8 levels).
/// Dev (`target/{debug,release}/browser-app`) finds the checkout; the
/// bundled `.app` (`Contents/MacOS/browser-app`) finds nothing.
fn find_checkout(exe: &std::path::Path) -> Option<std::path::PathBuf> {
    let mut cursor = exe.parent().map(|p| p.to_path_buf());
    for _ in 0..8 {
        match cursor {
            Some(dir) => {
                if dir.join("backend").join("engine.py").exists() {
                    return Some(dir);
                }
                cursor = dir.parent().map(|p| p.to_path_buf());
            }
            None => break,
        }
    }
    None
}

/// Find the frozen engine sidecar next to the executable
/// (`Contents/MacOS/jambubrowser-engine` in a bundled app, produced by
/// `scripts/build_engine_sidecar.sh` and shipped via Tauri `externalBin`).
/// This is what lets the bundle run on machines with no Python and no
/// checkout.
fn find_sidecar(exe: &std::path::Path) -> Option<std::path::PathBuf> {
    let name = if cfg!(windows) {
        "jambubrowser-engine.exe"
    } else {
        "jambubrowser-engine"
    };
    let candidate = exe.parent()?.join(name);
    candidate.is_file().then_some(candidate)
}

/// Backend health probe used to decide whether a spawn is needed.
/// Returns `Some(version)` when an engine already answers on the port.
async fn engine_on_port(port: u16) -> Option<String> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(2))
        .build()
        .ok()?;
    let body: serde_json::Value = client
        .get(format!("http://127.0.0.1:{port}/health"))
        .send()
        .await
        .ok()?
        .error_for_status()
        .ok()?
        .json()
        .await
        .ok()?;
    if body.get("status").and_then(|s| s.as_str()) == Some("online") {
        Some(
            body.get("version")
                .and_then(|v| v.as_str())
                .unwrap_or("?")
                .to_string(),
        )
    } else {
        None
    }
}
/// Directory Playwright should look in for browsers, as an override.
///
/// A frozen sidecar extracts its own driver into a throwaway `_MEI…` temp dir,
/// so Playwright's default lookup path becomes
/// `<_MEI>/playwright/driver/package/.local-browsers` — a directory that never
/// contains a browser. Every `POST /browser/sessions` then dies with
/// "Executable doesn't exist at …/.local-browsers/chromium_headless_shell-…",
/// which is exactly what the shipped .app did (verified against the release
/// bundle). Pointing the child at the machine's shared Playwright cache fixes
/// it, and `None` means "leave the environment alone" so an operator's
/// explicit `PLAYWRIGHT_BROWSERS_PATH` (including `0`, i.e. bundle-local)
/// always wins.
fn playwright_browsers_dir_from(
    explicit: Option<&str>,
    home: Option<&str>,
) -> Option<std::path::PathBuf> {
    if explicit.is_some() {
        return None;
    }
    let home = std::path::PathBuf::from(home?);
    if cfg!(target_os = "macos") {
        Some(home.join("Library/Caches/ms-playwright"))
    } else if cfg!(target_os = "windows") {
        Some(home.join("AppData/Local/ms-playwright"))
    } else {
        Some(home.join(".cache/ms-playwright"))
    }
}

fn playwright_browsers_dir() -> Option<std::path::PathBuf> {
    let explicit = std::env::var("PLAYWRIGHT_BROWSERS_PATH").ok();
    let home = std::env::var("HOME")
        .ok()
        .or_else(|| std::env::var("LOCALAPPDATA").ok());
    playwright_browsers_dir_from(explicit.as_deref(), home.as_deref())
}

/// Background Service Orchestrator
///
/// Starts the Python FastAPI backend when the Tauri app launches.
///
/// Resolution order:
///  0. **Reuse**: if a healthy engine already answers on the port (e.g. from
///     an earlier launch still running, or `bin/jambubrowser-engine`), log
///     its version and spawn nothing — previously every relaunch started a
///     second blind engine on a port whose owner it could not see.
///  1. **Checkout** (dev): spawn the checkout's `.venv` python running
///     `uvicorn backend.engine:app` with `PYTHONPATH` set — the bundled
///     `.app` runs with cwd `/`, so without both the spawn "succeeds" but
///     uvicorn exits with `ModuleNotFoundError` while the UI reports
///     "engine unreachable".
///  2. **Frozen sidecar** (bundled app): run the self-contained
///     `jambubrowser-engine` binary shipped in `Contents/MacOS/`.
///  3. Neither: log an actionable message instead of a ghost success.
pub fn start_all_services(app: &tauri::AppHandle) {
    let app_handle = app.clone();

    let exe = std::env::current_exe().unwrap_or_else(|_| std::path::PathBuf::from("."));
    let project_root = find_checkout(&exe);
    let sidecar = project_root.is_none().then(|| find_sidecar(&exe)).flatten();

    tauri::async_runtime::spawn(async move {
        // Step 0 — reuse whatever healthy engine is already on the port.
        if let Some(version) = engine_on_port(8001).await {
            eprintln!("[jambu] engine already on 127.0.0.1:8001 (v{version}); reusing, no spawn");
            return;
        }

        let shell = app_handle.shell();

        if let Some(project_root) = project_root {
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
            let mut cmd = shell.command(&interpreter)
                .args(["-m", "uvicorn", "backend.engine:app", "--host", "127.0.0.1", "--port", "8001"])
                .current_dir(&project_root)
                .env("PYTHONPATH", project_root.to_string_lossy().into_owned());
            if let Some(dir) = playwright_browsers_dir() {
                cmd = cmd.env("PLAYWRIGHT_BROWSERS_PATH", dir.to_string_lossy().into_owned());
            }

            match cmd.spawn() {
                Ok((_rx, _child)) => {
                    eprintln!("[jambu] Backend engine started on 127.0.0.1:8001 (checkout)");
                }
                Err(e) => {
                    eprintln!("[jambu] Failed to start backend: {}", e);
                }
            }
        } else if let Some(sidecar) = sidecar {
            // Bundled mode: the frozen engine is self-contained (backend +
            // deps embedded), so no PYTHONPATH, no venv, no checkout needed.
            let mut cmd = shell.command(&sidecar)
                .args(["--host", "127.0.0.1", "--port", "8001"]);
            // See playwright_browsers_dir(): without this the frozen engine
            // looks for a browser inside its own `_MEI…` extraction dir and
            // every browser session fails.
            if let Some(dir) = playwright_browsers_dir() {
                cmd = cmd.env("PLAYWRIGHT_BROWSERS_PATH", dir.to_string_lossy().into_owned());
            }

            match cmd.spawn() {
                Ok((_rx, _child)) => {
                    eprintln!("[jambu] Backend engine starting on 127.0.0.1:8001 (frozen sidecar)");
                }
                Err(e) => {
                    eprintln!("[jambu] Failed to start frozen engine sidecar: {}", e);
                }
            }
        } else {
            eprintln!(
                "[jambu] no backend checkout or engine sidecar next to the executable; \
                 engine not started. Run `bin/jambubrowser-engine --background` manually, \
                 or rebuild the app with `scripts/build_engine_sidecar.sh` first."
            );
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn scratch(tag: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("jambu_services_test_{tag}"));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn finds_checkout_when_engine_py_is_ancestor() {
        let root = scratch("checkout");
        fs::create_dir_all(root.join("backend")).unwrap();
        fs::write(root.join("backend").join("engine.py"), "").unwrap();
        let deep = root.join("a").join("b");
        fs::create_dir_all(&deep).unwrap();
        assert_eq!(find_checkout(&deep.join("exe")), Some(root));
    }

    #[test]
    fn no_checkout_without_engine_py() {
        let root = scratch("noc");
        assert_eq!(find_checkout(&root.join("exe")), None);
    }

    #[test]
    fn finds_sidecar_beside_executable() {
        let dir = scratch("sc");
        let exe = dir.join(if cfg!(windows) { "app.exe" } else { "app" });
        let name = if cfg!(windows) {
            "jambubrowser-engine.exe"
        } else {
            "jambubrowser-engine"
        };
        fs::write(dir.join(name), "").unwrap();
        assert_eq!(find_sidecar(&exe), Some(dir.join(name)));
    }

    #[test]
    fn playwright_override_wins_over_default_cache() {
        // An explicit PLAYWRIGHT_BROWSERS_PATH (including "0") must be left
        // untouched: it is how an operator points the engine at a bundle-local
        // browser install.
        assert_eq!(
            playwright_browsers_dir_from(Some("0"), Some("/Users/x")),
            None
        );
    }

    #[test]
    fn playwright_defaults_to_shared_cache() {
        let dir = playwright_browsers_dir_from(None, Some("/Users/x")).unwrap();
        let expected = if cfg!(target_os = "macos") {
            "/Users/x/Library/Caches/ms-playwright"
        } else if cfg!(target_os = "windows") {
            "/Users/x/AppData/Local/ms-playwright"
        } else {
            "/Users/x/.cache/ms-playwright"
        };
        assert_eq!(dir, std::path::PathBuf::from(expected));
    }

    #[test]
    fn playwright_dir_needs_a_home() {
        assert_eq!(playwright_browsers_dir_from(None, None), None);
    }

    #[test]
    fn sidecar_absent_when_not_shipped() {
        let dir = scratch("nosc");
        assert_eq!(find_sidecar(&dir.join("app")), None);
    }

    /// `engine_on_port` must return the version for a healthy engine and
    /// `None` for anything else — this is what stops the orchestrator from
    /// spawning a duplicate engine on every relaunch. Tested against stub
    /// TCP servers so no real engine is required.
    #[tokio::test]
    async fn engine_probe_reuses_healthy_engine() {
        use tokio::io::AsyncWriteExt as _;

        async fn stub(status: u16, body: &str) -> u16 {
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
                .await
                .unwrap();
            let port = listener.local_addr().unwrap().port();
            let body = body.to_string();
            tokio::spawn(async move {
                if let Ok((mut sock, _)) = listener.accept().await {
                    use tokio::io::AsyncReadExt as _;
                    let mut buf = vec![0u8; 4096];
                    let _ = sock.read(&mut buf).await;
                    let resp = format!(
                        "HTTP/1.1 {status} _\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                        body.len()
                    );
                    let _ = sock.write_all(resp.as_bytes()).await;
                }
            });
            port
        }

        let healthy = stub(
            200,
            r#"{"status":"online","version":"3.4.0"}"#,
        )
        .await;
        assert_eq!(
            super::engine_on_port(healthy).await.as_deref(),
            Some("3.4.0")
        );

        let starting = stub(
            200,
            r#"{"status":"starting","version":"3.4.0"}"#,
        )
        .await;
        assert_eq!(super::engine_on_port(starting).await, None);

        // Nothing listening on this port (freshly bound then dropped).
        let free = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .unwrap()
            .local_addr()
            .unwrap()
            .port();
        assert_eq!(super::engine_on_port(free).await, None);
    }
}

