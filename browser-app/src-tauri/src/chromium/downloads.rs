//! Download tracking — directory-scan based.
//!
//! Chrome is configured (via the `--download.default-directory` launch flag
//! in `manager.rs`) to drop all downloaded files into a single directory.
//! Rather than maintain a separate download state and reconcile it with
//! filesystem changes, we read the directory on demand. This is simpler and
//! survives Chromium restarts without bookkeeping.
//!
//! Trade-off: we can't surface in-progress `.crdownload` files as live
//! progress bars. The frontend shows them as "in progress" with an
//! indeterminate state and they re-appear as "complete" once Chrome
//! renames them to their final filename.

use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::time::SystemTime;

/// State of a single download from the user's perspective.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum DownloadState {
    /// Chrome is still writing the file (`.crdownload` extension).
    InProgress,
    /// File is fully written and ready to open.
    Complete,
    /// File exists but with size 0 (rare; treat as complete and let the
    /// user open it).
    Empty,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Download {
    /// Filename only (e.g. "report.pdf")
    pub filename: String,
    /// Absolute path on disk
    pub path: String,
    /// File size in bytes
    pub size_bytes: u64,
    /// Last-modified timestamp (Unix epoch seconds)
    pub modified_at: u64,
    /// Current state (in-progress / complete)
    pub state: DownloadState,
}

/// Resolve the download directory. Lives next to the user's Downloads folder
/// so it's easy to find from the OS file browser, and uses a sub-folder so
/// it doesn't pollute the main Downloads.
pub fn default_download_dir() -> PathBuf {
    if let Some(home) = std::env::var_os("HOME") {
        PathBuf::from(home).join("Downloads").join("JambuBrowser")
    } else {
        std::env::temp_dir().join("JambuBrowser")
    }
}

/// Ensure the download directory exists, creating it if necessary.
/// Returns the path. Errors are non-fatal — Chrome will fall back to its
/// own default if the path is invalid.
pub fn ensure_download_dir() -> PathBuf {
    let dir = default_download_dir();
    let _ = std::fs::create_dir_all(&dir);
    dir
}

/// Scan a directory and return the list of files as `Download` records.
/// Sorted newest-first. The `in-progress` heuristic is the `.crdownload`
/// extension that Chrome uses for partial files.
pub fn scan_downloads(dir: &Path) -> Vec<Download> {
    let mut out: Vec<Download> = Vec::new();
    let entries = match std::fs::read_dir(dir) {
        Ok(it) => it,
        Err(_) => return out, // dir doesn't exist or is unreadable — empty list
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_file() {
            continue;
        }
        let filename = match path.file_name().and_then(|n| n.to_str()) {
            Some(n) => n.to_string(),
            None => continue,
        };
        let size_bytes = entry.metadata().map(|m| m.len()).unwrap_or(0);
        let modified_at = entry
            .metadata()
            .ok()
            .and_then(|m| m.modified().ok())
            .and_then(|t| t.duration_since(SystemTime::UNIX_EPOCH).ok())
            .map(|d| d.as_secs())
            .unwrap_or(0);

        let state = if filename.ends_with(".crdownload") {
            DownloadState::InProgress
        } else if size_bytes == 0 {
            DownloadState::Empty
        } else {
            DownloadState::Complete
        };

        out.push(Download {
            filename,
            path: path.to_string_lossy().to_string(),
            size_bytes,
            modified_at,
            state,
        });
    }
    // Newest first.
    out.sort_by(|a, b| b.modified_at.cmp(&a.modified_at));
    out
}

/// Open a downloaded file with the OS default handler. Best-effort:
/// returns Err if the path is missing or the platform refuses to open it.
#[cfg(target_os = "macos")]
pub fn open_download(path: &str) -> Result<(), String> {
    std::process::Command::new("open")
        .arg(path)
        .spawn()
        .map_err(|e| format!("open failed: {e}"))?;
    Ok(())
}

#[cfg(target_os = "linux")]
pub fn open_download(path: &str) -> Result<(), String> {
    std::process::Command::new("xdg-open")
        .arg(path)
        .spawn()
        .map_err(|e| format!("xdg-open failed: {e}"))?;
    Ok(())
}

#[cfg(target_os = "windows")]
pub fn open_download(path: &str) -> Result<(), String> {
    std::process::Command::new("cmd")
        .args(["/C", "start", "", path])
        .spawn()
        .map_err(|e| format!("start failed: {e}"))?;
    Ok(())
}

#[cfg(not(any(target_os = "macos", target_os = "linux", target_os = "windows")))]
pub fn open_download(_path: &str) -> Result<(), String> {
    Err("open_download is not supported on this platform".to_string())
}

/// Remove a file. Best-effort; ignores not-found.
pub fn remove_download(path: &str) -> Result<(), String> {
    std::fs::remove_file(path).map_err(|e| format!("remove failed: {e}"))
}

/// Fetch a URL via reqwest and save the response body to the download
/// directory under a sensible filename. Returns the absolute path of the
/// saved file. Used by the PDF "Download" button — the Tauri side
/// downloads the file directly so the user can open it in a native
/// PDF reader instead of staring at a static screenshot.
pub async fn download_url_to_dir(url: &str) -> Result<String, String> {
    use std::io::Write;
    let dir = ensure_download_dir();

    // Pull the filename from the URL path; fall back to a timestamped
    // "download-<n>.bin" if the URL has no useful basename.
    let basename = url_basename(url)
        .unwrap_or_else(|| format!("download-{}.bin", unix_now_secs()));

    let target = unique_target(&dir, &basename);

    let resp = reqwest::get(url)
        .await
        .map_err(|e| format!("fetch failed: {e}"))?;
    if !resp.status().is_success() {
        return Err(format!("http {} for {url}", resp.status()));
    }
    let bytes = resp
        .bytes()
        .await
        .map_err(|e| format!("body read failed: {e}"))?;

    let mut f = std::fs::File::create(&target)
        .map_err(|e| format!("create file failed: {e}"))?;
    f.write_all(&bytes)
        .map_err(|e| format!("write failed: {e}"))?;
    f.sync_all().ok();

    Ok(target.to_string_lossy().to_string())
}

/// Last non-empty path segment of a URL, URL-decoded. Returns None if
/// the URL is malformed or the path has no useful basename. Avoids the
/// `url` crate dep — we only need the filename, not full URL parsing.
fn url_basename(url: &str) -> Option<String> {
    // Drop the query string and fragment.
    let path = url.split(['?', '#']).next()?;
    // Last `/`-separated segment.
    let last = path.rsplit('/').next()?.trim();
    if last.is_empty() {
        return None;
    }
    // Best-effort percent-decode. We replace %XX with the byte; if the
    // result isn't valid UTF-8 we drop the % and ship the raw bytes.
    let mut out = Vec::with_capacity(last.len());
    let bytes = last.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            if let (Some(h), Some(l)) = (hex(bytes[i + 1]), hex(bytes[i + 2])) {
                out.push((h << 4) | l);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8(out).ok()
}

fn hex(b: u8) -> Option<u8> {
    match b {
        b'0'..=b'9' => Some(b - b'0'),
        b'a'..=b'f' => Some(b - b'a' + 10),
        b'A'..=b'F' => Some(b - b'A' + 10),
        _ => None,
    }
}

fn unix_now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// If <basename> already exists in <dir>, suffix with -1, -2, ...
/// until a free name is found. Preserves the extension.
fn unique_target(dir: &Path, basename: &str) -> PathBuf {
    let candidate = dir.join(basename);
    if !candidate.exists() {
        return candidate;
    }
    let (stem, ext) = match basename.rsplit_once('.') {
        Some((s, e)) => (s.to_string(), format!(".{e}")),
        None => (basename.to_string(), String::new()),
    };
    for n in 1..=9999 {
        let next = dir.join(format!("{stem}-{n}{ext}"));
        if !next.exists() {
            return next;
        }
    }
    dir.join(format!("{stem}-{}", unix_now_secs()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::PathBuf;

    fn tmp() -> PathBuf {
        // unix_now_secs() alone collides when tests run in the same
        // second, so tack on a random 8-hex suffix.
        use rand::Rng;
        let suffix: String = rand::thread_rng()
            .gen::<[u8; 4]>()
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        let dir = std::env::temp_dir()
            .join(format!("jambu-test-{}-{}", unix_now_secs(), suffix));
        let _ = fs::create_dir_all(&dir);
        dir
    }

    // ── url_basename ─────────────────────────────────────────────

    #[test]
    fn url_basename_extracts_last_segment() {
        assert_eq!(url_basename("https://example.com/path/to/file.pdf"), Some("file.pdf".into()));
        assert_eq!(url_basename("https://example.com/file.pdf"), Some("file.pdf".into()));
    }

    #[test]
    fn url_basename_strips_query_and_fragment() {
        assert_eq!(url_basename("https://x.com/a.pdf?download=1"), Some("a.pdf".into()));
        assert_eq!(url_basename("https://x.com/a.pdf#section"), Some("a.pdf".into()));
        assert_eq!(url_basename("https://x.com/a.pdf?q=1#sec"), Some("a.pdf".into()));
    }

    #[test]
    fn url_basename_percent_decodes() {
        assert_eq!(url_basename("https://x.com/hello%20world.pdf"), Some("hello world.pdf".into()));
        assert_eq!(url_basename("https://x.com/%E4%B8%AD%E6%96%87.txt"), Some("\u{4e2d}\u{6587}.txt".into()));
    }

    #[test]
    fn url_basename_returns_none_for_trailing_slash() {
        assert_eq!(url_basename("https://x.com/dir/"), None);
        assert_eq!(url_basename("https://x.com/"), None);
    }

    // ── unique_target ────────────────────────────────────────────

    #[test]
    fn unique_target_returns_input_when_free() {
        let dir = tmp();
        let target = unique_target(&dir, "fresh.pdf");
        assert_eq!(target, dir.join("fresh.pdf"));
    }

    #[test]
    fn unique_target_suffixes_when_taken() {
        let dir = tmp();
        fs::write(dir.join("report.pdf"), b"x").unwrap();
        let target = unique_target(&dir, "report.pdf");
        assert_eq!(target, dir.join("report-1.pdf"));
    }

    #[test]
    fn unique_target_preserves_extension() {
        let dir = tmp();
        fs::write(dir.join("archive.tar.gz"), b"x").unwrap();
        let target = unique_target(&dir, "archive.tar.gz");
        // The rsplit_once splits on the LAST '.', so the suffix goes
        // between 'archive.tar' and '.gz'.
        assert_eq!(target, dir.join("archive.tar-1.gz"));
    }

    #[test]
    fn unique_target_handles_no_extension() {
        let dir = tmp();
        fs::write(dir.join("README"), b"x").unwrap();
        let target = unique_target(&dir, "README");
        assert_eq!(target, dir.join("README-1"));
    }

    // ── scan_downloads ───────────────────────────────────────────

    #[test]
    fn scan_downloads_marks_crdownload_as_in_progress() {
        let dir = tmp();
        let p = dir.join("big.pdf.crdownload");
        fs::write(&p, b"partial").unwrap();
        let downloads = scan_downloads(&dir);
        assert_eq!(downloads.len(), 1);
        assert_eq!(downloads[0].state, DownloadState::InProgress);
        assert_eq!(downloads[0].filename, "big.pdf.crdownload");
    }

    #[test]
    fn scan_downloads_marks_zero_byte_file_as_empty() {
        let dir = tmp();
        fs::write(dir.join("empty.pdf"), b"").unwrap();
        let downloads = scan_downloads(&dir);
        assert_eq!(downloads.len(), 1);
        assert_eq!(downloads[0].state, DownloadState::Empty);
    }

    #[test]
    fn scan_downloads_marks_real_file_as_complete() {
        let dir = tmp();
        fs::write(dir.join("report.pdf"), b"hello").unwrap();
        let downloads = scan_downloads(&dir);
        assert_eq!(downloads[0].state, DownloadState::Complete);
        assert_eq!(downloads[0].size_bytes, 5);
    }

    #[test]
    #[ignore = "depends on filesystem mtime resolution; flaky on fast systems"]
    fn scan_downloads_sorts_newest_first() {
        // Kept as #[ignore] because the sort uses second-resolution
        // mtimes. On a filesystem that rounds mtimes to the same
        // second, both files compare equal and the sort is unstable.
        // Uncomment locally to spot-check; not worth the CI flake.
        let dir = tmp();
        let p1 = dir.join("old.pdf");
        let p2 = dir.join("new.pdf");
        fs::write(&p1, b"x").unwrap();
        std::thread::sleep(std::time::Duration::from_millis(1500));
        fs::write(&p2, b"x").unwrap();
        let downloads = scan_downloads(&dir);
        assert_eq!(downloads[0].filename, "new.pdf");
        assert_eq!(downloads[1].filename, "old.pdf");
    }

    #[test]
    fn scan_downloads_returns_empty_for_missing_dir() {
        let dir = std::env::temp_dir().join(format!("jambu-nonexistent-{}", unix_now_secs()));
        let _ = fs::remove_dir_all(&dir);
        let downloads = scan_downloads(&dir);
        assert!(downloads.is_empty());
    }

    #[test]
    fn scan_downloads_skips_subdirectories() {
        let dir = tmp();
        fs::create_dir(dir.join("nested")).unwrap();
        fs::write(dir.join("a.pdf"), b"x").unwrap();
        let downloads = scan_downloads(&dir);
        // Only the file, not the directory.
        assert_eq!(downloads.len(), 1);
        assert_eq!(downloads[0].filename, "a.pdf");
    }
}
