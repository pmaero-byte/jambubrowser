//! Browser settings persistence.
//!
//! A single JSON file (`browser-settings.json`) in the app config dir holds
//! launch-time browser preferences: which unpacked extensions are disabled
//! and whether Chrome should use a persistent profile. Launch-time is the
//! key constraint — these are read before Chrome is spawned, so they must
//! live on the Rust side, not in frontend localStorage.
//!
//! The file is written atomically (write temp + rename) so a crash mid-save
//! can't leave a half-written JSON that silently resets all settings.

use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct BrowserSettings {
    /// IDs (directory names) of extensions the user has disabled.
    /// Anything not listed here is enabled — default-on matches the
    /// pre-existing behavior of loading every discovered extension.
    pub disabled_extensions: Vec<String>,
    /// When true, Chrome reuses a stable profile dir (cookies and logins
    /// survive restarts). Default false = fresh temp profile wiped on
    /// exit, the deliberate "forensic safety" privacy feature.
    pub persistent_profile: bool,
}

impl BrowserSettings {
    pub fn is_extension_enabled(&self, id: &str) -> bool {
        !self.disabled_extensions.iter().any(|d| d == id)
    }
}

/// App config directory. `JAMBU_CONFIG_DIR` overrides everything (used by
/// tests and by anyone who wants a portable install). No `dirs` crate —
/// the two platforms we ship on are covered by HOME/XDG below.
pub fn config_dir() -> PathBuf {
    if let Some(dir) = std::env::var_os("JAMBU_CONFIG_DIR") {
        return PathBuf::from(dir);
    }
    let home = std::env::var_os("HOME").map(PathBuf::from);
    #[cfg(target_os = "macos")]
    {
        if let Some(h) = home {
            return h.join("Library").join("Application Support").join("Jambubrowser");
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        if let Some(xdg) = std::env::var_os("XDG_CONFIG_HOME") {
            return PathBuf::from(xdg).join("jambubrowser");
        }
        if let Some(h) = home {
            return h.join(".config").join("jambubrowser");
        }
    }
    std::env::temp_dir().join("jambubrowser-config")
}

/// The stable Chrome profile dir used when `persistent_profile` is on.
pub fn persistent_profile_dir() -> PathBuf {
    config_dir().join("chrome-profile")
}

/// Where unpacked extensions live. Deliberately NOT inside the Chrome
/// profile dir: the default profile is a temp dir wiped on exit, which
/// would delete the user's extensions every launch.
pub fn extensions_dir() -> PathBuf {
    config_dir().join("extensions")
}

pub fn settings_path() -> PathBuf {
    config_dir().join("browser-settings.json")
}

/// Load settings. Missing or corrupt file = defaults (all extensions
/// enabled, ephemeral profile). Corrupt files are left in place — the
/// next successful save overwrites them.
pub fn load() -> BrowserSettings {
    let path = settings_path();
    let Ok(content) = std::fs::read_to_string(&path) else {
        return BrowserSettings::default();
    };
    serde_json::from_str(&content).unwrap_or_else(|e| {
        eprintln!("[jambu] ignoring corrupt settings file {}: {e}", path.display());
        BrowserSettings::default()
    })
}

/// Persist settings atomically.
pub fn save(settings: &BrowserSettings) -> Result<(), String> {
    let path = settings_path();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("create config dir failed: {e}"))?;
    }
    let json = serde_json::to_string_pretty(settings)
        .map_err(|e| format!("serialize settings failed: {e}"))?;
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, json).map_err(|e| format!("write settings failed: {e}"))?;
    std::fs::rename(&tmp, &path).map_err(|e| format!("rename settings failed: {e}"))?;
    Ok(())
}

/// Set one extension's enabled state and persist. Returns the new settings.
pub fn set_extension_enabled(id: &str, enabled: bool) -> Result<BrowserSettings, String> {
    let mut s = load();
    s.disabled_extensions.retain(|d| d != id);
    if !enabled {
        s.disabled_extensions.push(id.to_string());
    }
    save(&s)?;
    Ok(s)
}

/// Set the persistent-profile flag and persist. Returns the new settings.
pub fn set_persistent_profile(enabled: bool) -> Result<BrowserSettings, String> {
    let mut s = load();
    s.persistent_profile = enabled;
    save(&s)?;
    Ok(s)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Point the config dir at a fresh temp location. Tests share the
    /// process-wide JAMBU_CONFIG_DIR env var, so callers must hold the
    /// returned lock guard for the whole test to serialize env access.
    fn fresh_config_dir() -> (std::sync::MutexGuard<'static, ()>, PathBuf) {
        use rand::Rng;
        static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
        let guard = LOCK.lock().unwrap();
        let suffix: String = rand::thread_rng()
            .gen::<[u8; 4]>()
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        let dir = std::env::temp_dir().join(format!("jambu-settings-test-{suffix}"));
        std::env::set_var("JAMBU_CONFIG_DIR", &dir);
        (guard, dir)
    }

    #[test]
    fn load_returns_defaults_when_file_missing() {
        let _guard = fresh_config_dir().0;
        let s = load();
        assert!(!s.persistent_profile);
        assert!(s.disabled_extensions.is_empty());
        assert!(s.is_extension_enabled("anything"));
    }

    #[test]
    fn set_extension_enabled_roundtrips() {
        let _guard = fresh_config_dir().0;
        set_extension_enabled("adblock", false).unwrap();
        let s = load();
        assert!(!s.is_extension_enabled("adblock"));
        assert!(s.is_extension_enabled("other"));

        set_extension_enabled("adblock", true).unwrap();
        let s = load();
        assert!(s.is_extension_enabled("adblock"));
    }

    #[test]
    fn disabling_twice_does_not_duplicate() {
        let _guard = fresh_config_dir().0;
        set_extension_enabled("ext", false).unwrap();
        set_extension_enabled("ext", false).unwrap();
        let s = load();
        assert_eq!(s.disabled_extensions, vec!["ext".to_string()]);
    }

    #[test]
    fn set_persistent_profile_roundtrips() {
        let _guard = fresh_config_dir().0;
        set_persistent_profile(true).unwrap();
        assert!(load().persistent_profile);
        set_persistent_profile(false).unwrap();
        assert!(!load().persistent_profile);
    }

    #[test]
    fn corrupt_file_falls_back_to_defaults() {
        let (_guard, dir) = fresh_config_dir();
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(settings_path(), b"{not json").unwrap();
        let s = load();
        assert!(!s.persistent_profile);
        assert!(s.disabled_extensions.is_empty());
    }

    #[test]
    fn unknown_fields_in_file_are_tolerated() {
        let (_guard, dir) = fresh_config_dir();
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            settings_path(),
            r#"{"persistent_profile": true, "future_field": 42}"#,
        )
        .unwrap();
        assert!(load().persistent_profile);
    }
}
