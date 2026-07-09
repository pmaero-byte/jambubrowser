//! Extension management system.
//!
//! Loads, discovers, and manages Chrome extensions. Extensions are loaded
//! from the Jambubrowser extensions directory and passed to Chrome via
//! the `--load-extension` launch flag.

use serde::Serialize;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize)]
pub struct Extension {
    pub id: String,
    pub name: String,
    pub version: String,
    pub description: String,
    pub enabled: bool,
    pub path: PathBuf,
}

/// Discover extensions in the given directory.
/// Each subdirectory containing a `manifest.json` is treated as an unpacked extension.
pub fn discover_extensions(ext_dir: &Path) -> Vec<Extension> {
    let mut exts = Vec::new();
    if !ext_dir.exists() {
        return exts;
    }
    if let Ok(entries) = fs::read_dir(ext_dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if !path.is_dir() {
                continue;
            }
            let manifest = path.join("manifest.json");
            if !manifest.exists() {
                continue;
            }
            if let Ok(ext) = parse_extension(&path) {
                exts.push(ext);
            }
        }
    }
    exts.sort_by(|a, b| a.name.cmp(&b.name));
    exts
}

fn parse_extension(dir: &Path) -> Result<Extension, String> {
    let manifest_path = dir.join("manifest.json");
    let content = fs::read_to_string(&manifest_path)
        .map_err(|e| format!("Cannot read manifest: {e}"))?;
    let manifest: serde_json::Value = serde_json::from_str(&content)
        .map_err(|e| format!("Invalid manifest JSON: {e}"))?;

    let name = manifest["name"].as_str().unwrap_or("Unknown").to_string();
    let version = manifest["version"].as_str().unwrap_or("0.0").to_string();
    let description = manifest["description"].as_str().unwrap_or("").to_string();

    let id = dir
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("unknown")
        .to_string();

    Ok(Extension {
        id,
        name,
        version,
        description,
        enabled: true,
        path: dir.to_path_buf(),
    })
}

/// Build the `--load-extension` argument value for Chrome launch flags.
pub fn build_load_extension_arg(extensions: &[Extension]) -> Option<String> {
    let enabled_paths: Vec<String> = extensions
        .iter()
        .filter(|e| e.enabled)
        .map(|e| e.path.to_string_lossy().to_string())
        .collect();

    if enabled_paths.is_empty() {
        None
    } else {
        Some(enabled_paths.join(","))
    }
}

/// Ensure the extensions directory exists and has a default structure.
pub fn ensure_extensions_dir(base: &Path) -> PathBuf {
    let dir = base.join("extensions");
    fs::create_dir_all(&dir).ok();
    dir
}
