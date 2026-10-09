//! BitTorrent downloads via `librqbit` (rqbit engine).
//!
//! Supports `magnet:` links, `.torrent` files (bytes or on-disk path), and
//! HTTP(S) URLs pointing at a `.torrent` file. Files land in the same
//! `~/Downloads/JambuBrowser` directory as HTTP downloads so the existing
//! DownloadBar sees them once complete.
//!
//! The rqbit `Session` is a process-wide singleton (DHT + TCP listener are
//! expensive to restart). Torrent rows are exposed as [`TorrentInfo`] with
//! progress/speed/peers so the frontend can render them next to HTTP rows.

use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::sync::{Mutex, OnceCell};

use super::download_manager::{TransferKind, TransferStatus};
use super::downloads::ensure_download_dir;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TorrentInfo {
    pub id: String, // info-hash hex
    pub kind: TransferKind,
    pub name: String,
    pub info_hash: String,
    pub magnet: String,
    pub path: String,
    pub total_bytes: u64,
    pub total_known: bool,
    pub downloaded_bytes: u64,
    pub progress: f64,
    pub speed_bps: u64,
    pub eta_secs: Option<u64>,
    pub status: TransferStatus,
    pub peers: usize,
    pub error: Option<String>,
    pub modified_at: u64,
}

pub struct TorrentManager {
    session: OnceCell<Arc<librqbit::Session>>,
    init_lock: Mutex<()>,
    client: reqwest::Client,
}

impl TorrentManager {
    pub fn new() -> Self {
        Self {
            session: OnceCell::new(),
            init_lock: Mutex::new(()),
            client: reqwest::Client::builder()
                .user_agent("Jambubrowser/3.4 torrent")
                .timeout(std::time::Duration::from_secs(30))
                .build()
                .unwrap_or_else(|_| reqwest::Client::new()),
        }
    }

    async fn session(&self) -> Result<Arc<librqbit::Session>, String> {
        if let Some(s) = self.session.get() {
            return Ok(Arc::clone(s));
        }
        let _guard = self.init_lock.lock().await;
        if let Some(s) = self.session.get() {
            return Ok(Arc::clone(s));
        }
        let dir = ensure_download_dir();
        let sess = librqbit::Session::new(dir)
            .await
            .map_err(|e| format!("torrent session failed: {e}"))?;
        let _ = self.session.set(Arc::clone(&sess));
        Ok(sess)
    }

    /// Add a magnet link, `.torrent` HTTP URL, or local `.torrent` path.
    pub async fn add(
        &self,
        input: String,
        only_files: Option<Vec<usize>>,
    ) -> Result<TorrentInfo, String> {
        let input = input.trim().to_string();
        if input.is_empty() {
            return Err("empty torrent input".into());
        }
        let session = self.session().await?;
        let add: librqbit::AddTorrent<'_> = if input.starts_with("magnet:") {
            librqbit::AddTorrent::from_url(input.clone())
        } else if input.starts_with("http://") || input.starts_with("https://") {
            if input.contains("magnet:") {
                librqbit::AddTorrent::from_url(input.clone())
            } else {
                let bytes = self
                    .client
                    .get(&input)
                    .send()
                    .await
                    .map_err(|e| format!("fetch .torrent failed: {e}"))?
                    .bytes()
                    .await
                    .map_err(|e| format!("read .torrent body: {e}"))?;
                validate_torrent_bytes(&bytes)?;
                librqbit::AddTorrent::from_bytes(bytes)
            }
        } else if let Some(path) = input.strip_prefix("file://") {
            librqbit::AddTorrent::from_local_filename(path)
                .map_err(|e| format!("bad .torrent path: {e}"))?
        } else if std::path::Path::new(&input).exists() {
            librqbit::AddTorrent::from_local_filename(&input)
                .map_err(|e| format!("bad .torrent path: {e}"))?
        } else {
            return Err(
                "expected a magnet: link, an http(s) URL to a .torrent, or a local .torrent path"
                    .into(),
            );
        };

        let opts = only_files.map(|files| librqbit::AddTorrentOptions {
            only_files: Some(files),
            ..Default::default()
        });

        let resp = session
            .add_torrent(add, opts)
            .await
            .map_err(|e| first_line(&e.to_string()))?;
        let handle = resp
            .into_handle()
            .ok_or_else(|| "torrent metadata only (no files)".to_string())?;
        Ok(self.describe(&handle))
    }

    /// Add raw `.torrent` bytes (frontend file picker → base64 → bytes).
    pub async fn add_bytes(&self, bytes: Vec<u8>) -> Result<TorrentInfo, String> {
        validate_torrent_bytes(&bytes)?;
        let session = self.session().await?;
        let add = librqbit::AddTorrent::from_bytes(bytes::Bytes::from(bytes));
        let resp = session
            .add_torrent(add, None)
            .await
            .map_err(|e| first_line(&e.to_string()))?;
        let handle = resp
            .into_handle()
            .ok_or_else(|| "torrent metadata only (no files)".to_string())?;
        Ok(self.describe(&handle))
    }

    pub async fn list(&self) -> Vec<TorrentInfo> {
        let Ok(session) = self.session().await else {
            return vec![];
        };
        // `with_torrents` takes `Fn` (not `FnMut`), so collect ids first,
        // then resolve each handle via `get`.
        let ids: Vec<librqbit::api::TorrentIdOrHash> = session.with_torrents(|it| {
            it.map(|(id, _)| librqbit::api::TorrentIdOrHash::Id(id))
                .collect()
        });
        let mut out = Vec::with_capacity(ids.len());
        for id in ids {
            if let Some(h) = session.get(id) {
                out.push(self.describe(&h));
            }
        }
        out.sort_by(|a, b| b.modified_at.cmp(&a.modified_at));
        out
    }

    pub async fn pause(&self, id_or_hash: &str) -> Result<TorrentInfo, String> {
        let session = self.session().await?;
        let h = self.lookup(&session, id_or_hash)?;
        session
            .pause(&h)
            .await
            .map_err(|e| format!("pause failed: {e}"))?;
        Ok(self.describe(&h))
    }

    pub async fn resume(&self, id_or_hash: &str) -> Result<TorrentInfo, String> {
        let session = self.session().await?;
        let h = self.lookup(&session, id_or_hash)?;
        session
            .unpause(&h)
            .await
            .map_err(|e| format!("resume failed: {e}"))?;
        Ok(self.describe(&h))
    }

    pub async fn remove(&self, id_or_hash: &str, delete_files: bool) -> Result<(), String> {
        let session = self.session().await?;
        let h = self.lookup(&session, id_or_hash)?;
        session
            .delete(
                librqbit::api::TorrentIdOrHash::Id(h.id()),
                delete_files,
            )
            .await
            .map_err(|e| format!("remove failed: {e}"))?;
        Ok(())
    }

    fn lookup(
        &self,
        session: &Arc<librqbit::Session>,
        id_or_hash: &str,
    ) -> Result<Arc<librqbit::ManagedTorrent>, String> {
        let want = id_or_hash.trim().to_ascii_lowercase();
        // Numeric id first.
        if let Ok(id) = want.parse::<usize>() {
            if let Some(h) = session.get(librqbit::api::TorrentIdOrHash::Id(id)) {
                return Ok(h);
            }
        }
        // Then info-hash hex: scan handles (avoids naming the private Id20 type).
        let found: std::sync::Mutex<Option<Arc<librqbit::ManagedTorrent>>> =
            std::sync::Mutex::new(None);
        session.with_torrents(|it| {
            for (_, t) in it {
                if t.info_hash().as_string().to_ascii_lowercase() == want {
                    *found.lock().unwrap() = Some(Arc::clone(t));
                    break;
                }
            }
        });
        let result = found.lock().unwrap().clone();
        result.ok_or_else(|| format!("torrent not found: {id_or_hash}"))
    }

    fn describe(&self, t: &Arc<librqbit::ManagedTorrent>) -> TorrentInfo {
        let stats = t.stats();
        let total = stats.total_bytes;
        let done = stats.progress_bytes;
        let progress = if total > 0 {
            (done as f64 / total as f64).clamp(0.0, 1.0)
        } else {
            0.0
        };
        // `LiveStats`/`Speed` live in a private module, but field access
        // through inference works without naming the types.
        let speed = stats
            .live
            .as_ref()
            .map(|l| l.download_speed.as_bytes())
            .unwrap_or(0);
        // Peer count via the live handle without naming private filter types.
        let peers = t
            .live()
            .map(|live| {
                live.per_peer_stats_snapshot(Default::default())
                    .peers
                    .len()
            })
            .unwrap_or(0);
        let finished = stats.finished;
        let status = match &stats.state {
            librqbit::TorrentStatsState::Paused => TransferStatus::Paused,
            librqbit::TorrentStatsState::Error => TransferStatus::Error,
            librqbit::TorrentStatsState::Initializing { .. } => TransferStatus::Queued,
            librqbit::TorrentStatsState::Live => {
                if finished {
                    TransferStatus::Complete
                } else {
                    TransferStatus::Downloading
                }
            }
        };
        let error = match &stats.state {
            librqbit::TorrentStatsState::Error => stats.error.clone(),
            _ => None,
        };
        let hash_hex = t.info_hash().as_string();
        let name = t.name().unwrap_or_else(|| hash_hex.clone());
        let dir = ensure_download_dir();
        TorrentInfo {
            id: hash_hex.clone(),
            kind: TransferKind::Torrent,
            name,
            info_hash: hash_hex.clone(),
            magnet: format!("magnet:?xt=urn:btih:{hash_hex}"),
            path: dir.to_string_lossy().to_string(),
            total_bytes: total,
            total_known: total > 0,
            downloaded_bytes: done,
            progress,
            speed_bps: speed,
            eta_secs: if speed > 0 && total > done {
                Some((total - done) / speed.max(1))
            } else {
                None
            },
            status,
            peers,
            error,
            modified_at: now_secs(),
        }
    }
}

/// `.torrent` files are bencoded dicts starting with `d`. Reject HTML error
/// pages early with a helpful message instead of a cryptic bencode error.
fn validate_torrent_bytes(bytes: &[u8]) -> Result<(), String> {
    if bytes.is_empty() {
        return Err("empty .torrent file".into());
    }
    if bytes.len() > 16 * 1024 * 1024 {
        return Err(".torrent file too large (>16 MiB)".into());
    }
    let head_len = bytes.len().min(256);
    let head = &bytes[..head_len];
    let lower: Vec<u8> = head.iter().map(|b| b.to_ascii_lowercase()).collect();
    if lower.starts_with(b"<html") || lower.starts_with(b"<!doct") {
        return Err("URL returned an HTML page, not a .torrent file".into());
    }
    if bytes[0] != b'd' {
        return Err("not a .torrent file (expected bencoded dict)".into());
    }
    if !bytes.windows(4).any(|w| w == b"info") {
        return Err("could not parse .torrent metadata".into());
    }
    Ok(())
}

/// rqbit errors can embed tracker URLs / peer IPs — keep the first line.
fn first_line(s: &str) -> String {
    let first = s.lines().next().unwrap_or("torrent failed").trim();
    if first.len() > 300 {
        format!("{}…", &first[..300])
    } else {
        first.to_string()
    }
}

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Parse a magnet link without pulling in a new crate.
/// Returns (info-hash hex, display name?) — used by tests + validation.
#[allow(dead_code)]
pub fn parse_magnet(input: &str) -> Result<(String, Option<String>), String> {
    if !input.starts_with("magnet:") {
        return Err("not a magnet: link".into());
    }
    let mut hash: Option<String> = None;
    let mut name: Option<String> = None;
    let query = input.splitn(2, '?').nth(1).unwrap_or("");
    for pair in query.split('&') {
        let (k, v) = match pair.split_once('=') {
            Some(p) => p,
            None => continue,
        };
        if k == "xt" && v.starts_with("urn:btih:") {
            let h = v.trim_start_matches("urn:btih:");
            // 40-hex (v1) or 32-base32 (v1 compat) — normalise to lowercase.
            if h.len() == 40 && h.chars().all(|c| c.is_ascii_hexdigit()) {
                hash = Some(h.to_ascii_lowercase());
            } else if h.len() == 32 {
                hash = Some(h.to_ascii_lowercase());
            }
        } else if k == "dn" {
            name = percent_decode(v);
        }
    }
    hash
        .map(|h| (h, name))
        .ok_or_else(|| "magnet link has no urn:btih: hash".into())
}

fn percent_decode(s: &str) -> Option<String> {
    let mut out = Vec::with_capacity(s.len());
    let b = s.as_bytes();
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'%' && i + 2 < b.len() {
            let h = hex_val(b[i + 1])?;
            let l = hex_val(b[i + 2])?;
            // '+' in query strings means space; %2B is a literal plus.
            out.push((h << 4) | l);
            i += 3;
        } else if b[i] == b'+' {
            out.push(b' ');
            i += 1;
        } else {
            out.push(b[i]);
            i += 1;
        }
    }
    String::from_utf8(out).ok()
}

fn hex_val(b: u8) -> Option<u8> {
    match b {
        b'0'..=b'9' => Some(b - b'0'),
        b'a'..=b'f' => Some(b - b'a' + 10),
        b'A'..=b'F' => Some(b - b'A' + 10),
        _ => None,
    }
}

#[allow(dead_code)]
fn _torrent_output_dir() -> PathBuf {
    ensure_download_dir()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn magnet_parses_hex_hash_and_name() {
        let (h, name) = parse_magnet(
            "magnet:?xt=urn:btih:CAB507494D02EBB1178B38F2E9D7BE299C86B862&dn=ubuntu",
        )
        .unwrap();
        assert_eq!(h, "cab507494d02ebb1178b38f2e9d7be299c86b862");
        assert_eq!(name.as_deref(), Some("ubuntu"));
    }

    #[test]
    fn magnet_rejects_missing_hash() {
        assert!(parse_magnet("magnet:?dn=noname").is_err());
        assert!(parse_magnet("https://example.com/x.torrent").is_err());
    }

    #[test]
    fn torrent_bytes_reject_html() {
        assert!(validate_torrent_bytes(b"<html>nope</html>").is_err());
        assert!(validate_torrent_bytes(b"not-bencoded").is_err());
        assert!(validate_torrent_bytes(b"").is_err());
    }
}
