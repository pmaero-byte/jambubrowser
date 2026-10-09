//! Segmented HTTP download manager — "torrent-style" pieces over plain HTTP.
//!
//! Large files are split into N byte-ranges (like torrent pieces) and fetched
//! in parallel with `Range` requests, then concatenated. Small files, servers
//! without `Accept-Ranges: bytes`, or unknown lengths fall back to a single
//! stream. Every task persists a `<target>.jambu.json` sidecar so pause /
//! resume survives app restarts.
//!
//! Frontend polling: `transfer_list` returns live progress/speed/ETA; the
//! DownloadBar polls it the same way it polls `browser_list_downloads`.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{
    atomic::{AtomicBool, AtomicU64, Ordering},
    Arc,
};
use std::time::{Instant, SystemTime, UNIX_EPOCH};
use tokio::sync::Mutex;

use super::downloads::{ensure_download_dir, unique_target_pub, url_basename_pub};

/// Minimum file size before we bother splitting (1 MiB).
pub const SEGMENT_THRESHOLD_BYTES: u64 = 1024 * 1024;
/// Smallest piece we'll create (256 KiB) — avoids 1000 tiny ranges on big files.
pub const MIN_CHUNK_BYTES: u64 = 256 * 1024;
/// Hard cap on parallel connections per download.
pub const MAX_CONNECTIONS: usize = 16;
/// Default connections when the caller doesn't specify.
pub const DEFAULT_CONNECTIONS: usize = 8;

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum TransferStatus {
    Queued,
    Downloading,
    Paused,
    Complete,
    Error,
    Cancelled,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum TransferKind {
    Http,
    Torrent,
    Chrome,
}

/// One row for the DownloadBar. `id` is stable for pause/resume/cancel.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TransferInfo {
    pub id: String,
    pub kind: TransferKind,
    pub name: String,
    pub url: String,
    pub path: String,
    pub total_bytes: u64,
    pub total_known: bool,
    pub downloaded_bytes: u64,
    /// 0.0–1.0 (0 when total unknown).
    pub progress: f64,
    pub speed_bps: u64,
    /// Seconds remaining, if estimable.
    pub eta_secs: Option<u64>,
    pub status: TransferStatus,
    pub connections: usize,
    pub error: Option<String>,
    pub modified_at: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct ChunkState {
    index: usize,
    start: u64,
    end: u64, // inclusive
    done: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct PersistedTask {
    id: String,
    url: String,
    target: PathBuf,
    total_bytes: u64,
    connections: usize,
    chunks: Vec<ChunkState>,
}

struct LiveTask {
    info: Mutex<TransferInfo>,
    downloaded: Arc<AtomicU64>,
    total: u64,
    total_known: bool,
    paused: Arc<AtomicBool>,
    cancelled: Arc<AtomicBool>,
    last_sample_at: Mutex<Instant>,
    last_sample_bytes: AtomicU64,
    speed_bps: AtomicU64,
    handle: Mutex<Option<tauri::async_runtime::JoinHandle<()>>>,
}

pub struct DownloadManager {
    tasks: Mutex<HashMap<String, Arc<LiveTask>>>,
    client: reqwest::Client,
}

impl DownloadManager {
    pub fn new() -> Self {
        let client = reqwest::Client::builder()
            .user_agent("Jambubrowser/3.4 segmented-downloader")
            .timeout(std::time::Duration::from_secs(60))
            .build()
            .unwrap_or_else(|_| reqwest::Client::new());
        Self {
            tasks: Mutex::new(HashMap::new()),
            client,
        }
    }

    pub async fn list(&self) -> Vec<TransferInfo> {
        let tasks = self.tasks.lock().await;
        let mut out = Vec::with_capacity(tasks.len());
        for t in tasks.values() {
            out.push(self.snapshot(t).await);
        }
        out.sort_by(|a, b| b.modified_at.cmp(&a.modified_at));
        out
    }

    async fn snapshot(&self, t: &Arc<LiveTask>) -> TransferInfo {
        let mut info = t.info.lock().await.clone();
        let dl = t.downloaded.load(Ordering::Relaxed);
        info.downloaded_bytes = dl;
        if t.total_known && t.total > 0 {
            info.progress = (dl as f64 / t.total as f64).clamp(0.0, 1.0);
            let speed = t.speed_bps.load(Ordering::Relaxed);
            info.speed_bps = speed;
            info.eta_secs = if speed > 0 && t.total > dl {
                Some((t.total - dl) / speed)
            } else {
                None
            };
        } else {
            info.progress = 0.0;
            info.speed_bps = t.speed_bps.load(Ordering::Relaxed);
            info.eta_secs = None;
        }
        info
    }

    /// Start a segmented HTTP download. Returns the first snapshot.
    pub async fn start_http(
        self: &Arc<Self>,
        url: String,
        connections: Option<usize>,
    ) -> Result<TransferInfo, String> {
        let want = connections.unwrap_or(DEFAULT_CONNECTIONS).clamp(1, MAX_CONNECTIONS);
        validate_http_url(&url)?;

        let dir = ensure_download_dir();
        let basename = url_basename_pub(&url)
            .unwrap_or_else(|| format!("download-{}.bin", now_secs()));
        let target = unique_target_pub(&dir, &basename);
        let id = uuid::Uuid::new_v4().to_string();

        // Probe size + range support with HEAD (fall back to GET range-0 if HEAD lies).
        let (total, range_ok) = self.probe(&url).await;
        let total_known = total.is_some();
        let total_val = total.unwrap_or(0);

        let use_segments =
            total.map(|t| t >= SEGMENT_THRESHOLD_BYTES).unwrap_or(false) && range_ok && want > 1;
        let n_chunks = if use_segments {
            plan_chunk_count(total_val, want)
        } else {
            1
        };
        let chunks = plan_chunks(total_val, n_chunks, total_known);

        let persisted = PersistedTask {
            id: id.clone(),
            url: url.clone(),
            target: target.clone(),
            total_bytes: total_val,
            connections: n_chunks,
            chunks,
        };

        let task = Arc::new(LiveTask {
            info: Mutex::new(TransferInfo {
                id: id.clone(),
                kind: TransferKind::Http,
                name: basename,
                url: url.clone(),
                path: target.to_string_lossy().to_string(),
                total_bytes: total_val,
                total_known,
                downloaded_bytes: 0,
                progress: 0.0,
                speed_bps: 0,
                eta_secs: None,
                status: TransferStatus::Downloading,
                connections: n_chunks,
                error: None,
                modified_at: now_secs(),
            }),
            downloaded: Arc::new(AtomicU64::new(resume_offset(&persisted))),
            total: total_val,
            total_known,
            paused: Arc::new(AtomicBool::new(false)),
            cancelled: Arc::new(AtomicBool::new(false)),
            last_sample_at: Mutex::new(Instant::now()),
            last_sample_bytes: AtomicU64::new(0),
            speed_bps: AtomicU64::new(0),
            handle: Mutex::new(None),
        });

        persist_task(&persisted);
        // Pre-count already-present part files (resume after restart).
        recount_parts(&persisted, &task.downloaded);

        {
            let mut tasks = self.tasks.lock().await;
            tasks.insert(id.clone(), task.clone());
        }

        let me = Arc::clone(self);
        let task_c = Arc::clone(&task);
        let persisted_c = persisted.clone();
        let handle = tauri::async_runtime::spawn(async move {
            me.run_task(task_c, persisted_c).await;
        });
        *task.handle.lock().await = Some(handle);

        Ok(self.snapshot(&task).await)
    }

    pub async fn pause(&self, id: &str) -> Result<TransferInfo, String> {
        let t = self.get(id).await?;
        t.paused.store(true, Ordering::Relaxed);
        {
            let mut info = t.info.lock().await;
            if info.status == TransferStatus::Downloading || info.status == TransferStatus::Queued {
                info.status = TransferStatus::Paused;
            }
        }
        Ok(self.snapshot(&t).await)
    }

    pub async fn resume(self: &Arc<Self>, id: &str) -> Result<TransferInfo, String> {
        let t = self.get(id).await?;
        {
            let info = t.info.lock().await;
            if info.status == TransferStatus::Complete {
                return Ok(info.clone());
            }
            if info.kind != TransferKind::Http {
                return Err("only HTTP transfers pause/resume here (use torrent commands)".into());
            }
        }
        // If the worker already exited (error/cancel/pause-drain), spawn a fresh run.
        let needs_spawn = {
            let h = t.handle.lock().await;
            h.is_none()
        };
        t.paused.store(false, Ordering::Relaxed);
        t.cancelled.store(false, Ordering::Relaxed);
        {
            let mut info = t.info.lock().await;
            info.status = TransferStatus::Downloading;
            info.error = None;
        }
        if needs_spawn {
            if let Some(persisted) = load_persisted(&task_sidecar(&PathBuf::from(
                t.info.lock().await.path.clone(),
            ))) {
                let me = Arc::clone(self);
                let t2 = Arc::clone(&t);
                let h = tauri::async_runtime::spawn(async move {
                    me.run_task(t2, persisted).await;
                });
                *t.handle.lock().await = Some(h);
            }
        }
        Ok(self.snapshot(&t).await)
    }

    pub async fn cancel(&self, id: &str) -> Result<TransferInfo, String> {
        let t = self.get(id).await?;
        t.cancelled.store(true, Ordering::Relaxed);
        {
            let mut info = t.info.lock().await;
            info.status = TransferStatus::Cancelled;
        }
        // Best-effort: abort the worker so a stuck Range GET doesn't linger.
        if let Some(h) = t.handle.lock().await.take() {
            h.abort();
        }
        Ok(self.snapshot(&t).await)
    }

    /// Remove the record; optionally delete the file + sidecars from disk.
    pub async fn remove(&self, id: &str, delete_file: bool) -> Result<(), String> {
        let t = self.get(id).await?;
        t.cancelled.store(true, Ordering::Relaxed);
        if let Some(h) = t.handle.lock().await.take() {
            h.abort();
        }
        let path = t.info.lock().await.path.clone();
        if delete_file {
            let _ = std::fs::remove_file(&path);
            cleanup_sidecars(Path::new(&path));
        }
        self.tasks.lock().await.remove(id);
        Ok(())
    }

    async fn get(&self, id: &str) -> Result<Arc<LiveTask>, String> {
        self.tasks
            .lock()
            .await
            .get(id)
            .cloned()
            .ok_or_else(|| format!("download not found: {id}"))
    }

    /// HEAD probe → (total_bytes, supports_ranges).
    async fn probe(&self, url: &str) -> (Option<u64>, bool) {
        // HEAD first.
        if let Ok(resp) = self.client.head(url).send().await {
            let total = resp
                .headers()
                .get(reqwest::header::CONTENT_LENGTH)
                .and_then(|v| v.to_str().ok())
                .and_then(|s| s.parse::<u64>().ok());
            let accept = resp
                .headers()
                .get(reqwest::header::ACCEPT_RANGES)
                .and_then(|v| v.to_str().ok())
                .unwrap_or("")
                .to_ascii_lowercase();
            if total.is_some() {
                return (total, accept.contains("bytes"));
            }
        }
        // Fallback: GET bytes=0-0 — honest servers answer 206 + Content-Range: bytes 0-0/<total>.
        if let Ok(resp) = self
            .client
            .get(url)
            .header(reqwest::header::RANGE, "bytes=0-0")
            .send()
            .await
        {
            if resp.status() == reqwest::StatusCode::PARTIAL_CONTENT {
                if let Some(cr) = resp
                    .headers()
                    .get(reqwest::header::CONTENT_RANGE)
                    .and_then(|v| v.to_str().ok())
                {
                    if let Some(total) = parse_content_range_total(cr) {
                        return (Some(total), true);
                    }
                }
            }
            // 200 with no range support but a length is still useful (single stream).
            let total = resp
                .headers()
                .get(reqwest::header::CONTENT_LENGTH)
                .and_then(|v| v.to_str().ok())
                .and_then(|s| s.parse::<u64>().ok());
            return (total, false);
        }
        (None, false)
    }

    async fn run_task(&self, task: Arc<LiveTask>, persisted: PersistedTask) {
        let result = self.drive(task.clone(), &persisted).await;
        // Mark handle slot free so `resume` knows to spawn a fresh worker.
        *task.handle.lock().await = None;
        let mut info = task.info.lock().await;
        match result {
            DriveOutcome::Complete => {
                info.status = TransferStatus::Complete;
                info.progress = 1.0;
                info.speed_bps = 0;
                info.eta_secs = Some(0);
                info.modified_at = now_secs();
                cleanup_sidecars(Path::new(&persisted.target));
            }
            DriveOutcome::Paused => {
                info.status = TransferStatus::Paused;
            }
            DriveOutcome::Cancelled => {
                info.status = TransferStatus::Cancelled;
            }
            DriveOutcome::Error(e) => {
                // Pause-drain vs real error: if the user hit pause, report Paused.
                if task.paused.load(Ordering::Relaxed) {
                    info.status = TransferStatus::Paused;
                } else {
                    info.status = TransferStatus::Error;
                    info.error = Some(e);
                }
            }
        }
    }

    async fn drive(&self, task: Arc<LiveTask>, persisted: &PersistedTask) -> DriveOutcome {
        use futures::future::join_all;
        // Refresh speed sampler in the background of this worker.
        let sampler = {
            let t = Arc::clone(&task);
            tauri::async_runtime::spawn(async move {
                loop {
                    tokio::time::sleep(std::time::Duration::from_millis(500)).await;
                    let info_status = t.info.lock().await.status;
                    if matches!(
                        info_status,
                        TransferStatus::Complete
                            | TransferStatus::Cancelled
                            | TransferStatus::Error
                    ) {
                        break;
                    }
                    let now = Instant::now();
                    let mut last = t.last_sample_at.lock().await;
                    let dt = now.duration_since(*last).as_secs_f64();
                    if dt >= 0.4 {
                        let cur = t.downloaded.load(Ordering::Relaxed);
                        let prev = t.last_sample_bytes.load(Ordering::Relaxed);
                        if dt > 0.0 {
                            let bps = ((cur.saturating_sub(prev)) as f64 / dt) as u64;
                            // EWMA to smooth jitter.
                            let old = t.speed_bps.load(Ordering::Relaxed);
                            let smooth = ((old as f64) * 0.6 + (bps as f64) * 0.4) as u64;
                            t.speed_bps.store(smooth, Ordering::Relaxed);
                        }
                        t.last_sample_bytes.store(cur, Ordering::Relaxed);
                        *last = now;
                    }
                    if info_status == TransferStatus::Paused {
                        // Keep sampling (speed decays to 0) but don't exit.
                        continue;
                    }
                }
            })
        };

        let mut futures_vec = Vec::new();
        for chunk in persisted.chunks.iter().filter(|c| !c.done) {
            // Skip chunks whose part file already covers the range (resume).
            let part = part_path(&persisted.target, chunk.index);
            let have = std::fs::metadata(&part).map(|m| m.len()).unwrap_or(0);
            let len = chunk.end.saturating_sub(chunk.start) + 1;
            if have >= len && len > 0 {
                task.downloaded.fetch_add(0, Ordering::Relaxed);
                mark_chunk_done(&persisted.target, chunk.index);
                continue;
            }
            let client = self.client.clone();
            let url = persisted.url.clone();
            let target = persisted.target.clone();
            let downloaded = Arc::clone(&task.downloaded);
            let paused = Arc::clone(&task.paused);
            let cancelled = Arc::clone(&task.cancelled);
            let chunk_c = chunk.clone();
            let have_c = have;
            futures_vec.push(async move {
                download_range(
                    &client,
                    &url,
                    &target,
                    &chunk_c,
                    have_c,
                    downloaded,
                    paused,
                    cancelled,
                )
                .await
            });
        }

        // Single-chunk fast path still goes through the same range machinery
        // (plain GET when total unknown).
        let results = join_all(futures_vec).await;
        sampler.abort();

        for r in results {
            match r {
                Ok(()) => {}
                Err(ChunkError::Paused) => return DriveOutcome::Paused,
                Err(ChunkError::Cancelled) => return DriveOutcome::Cancelled,
                Err(ChunkError::Failed(e)) => {
                    if task.cancelled.load(Ordering::Relaxed) {
                        return DriveOutcome::Cancelled;
                    }
                    if task.paused.load(Ordering::Relaxed) {
                        return DriveOutcome::Paused;
                    }
                    return DriveOutcome::Error(e);
                }
            }
        }
        if task.cancelled.load(Ordering::Relaxed) {
            return DriveOutcome::Cancelled;
        }
        if task.paused.load(Ordering::Relaxed) {
            // Persist remaining progress; worker exits, resume spawns a new one.
            return DriveOutcome::Paused;
        }
        // All chunks ok → concatenate.
        if let Err(e) = assemble_parts(&persisted.target, persisted.chunks.len()) {
            return DriveOutcome::Error(e);
        }
        // Verify size when known.
        if persisted.total_bytes > 0 {
            if let Ok(m) = std::fs::metadata(&persisted.target) {
                if m.len() != persisted.total_bytes {
                    return DriveOutcome::Error(format!(
                        "size mismatch: got {} bytes, expected {}",
                        m.len(),
                        persisted.total_bytes
                    ));
                }
                task.downloaded.store(m.len(), Ordering::Relaxed);
            }
        }
        DriveOutcome::Complete
    }
}

// Global handle so commands that only hold `&Arc<DownloadManager>` can still
// spawn workers after the initial task exits. Set on every start/resume.
use std::sync::OnceLock;
static GLOBAL_MGR: OnceLock<Arc<DownloadManager>> = OnceLock::new();

pub fn set_global_manager(mgr: Arc<DownloadManager>) {
    let _ = GLOBAL_MGR.set(mgr);
}
#[allow(dead_code)]
fn global_manager() -> Option<Arc<DownloadManager>> {
    GLOBAL_MGR.get().cloned()
}

enum DriveOutcome {
    Complete,
    Paused,
    Cancelled,
    Error(String),
}

enum ChunkError {
    Paused,
    Cancelled,
    Failed(String),
}

/// Download one byte-range into `<target>.part<index>`, resuming at `have` bytes.
#[allow(clippy::too_many_arguments)]
async fn download_range(
    client: &reqwest::Client,
    url: &str,
    target: &Path,
    chunk: &ChunkState,
    have: u64,
    downloaded: Arc<AtomicU64>,
    paused: Arc<AtomicBool>,
    cancelled: Arc<AtomicBool>,
) -> Result<(), ChunkError> {
    use futures_util::StreamExt;
    use tokio::io::AsyncWriteExt;

    let start = chunk.start + have;
    if start > chunk.end {
        return Ok(());
    }
    let range_hdr = format!("bytes={}-{}", start, chunk.end);
    let resp = client
        .get(url)
        .header(reqwest::header::RANGE, range_hdr)
        .send()
        .await
        .map_err(|e| ChunkError::Failed(format!("chunk {} request failed: {e}", chunk.index)))?;
    // 206 = honest range; 200 = server ignored Range (only valid for the single-chunk case).
    if resp.status() != reqwest::StatusCode::PARTIAL_CONTENT
        && resp.status() != reqwest::StatusCode::OK
    {
        return Err(ChunkError::Failed(format!(
            "chunk {} http {}",
            chunk.index,
            resp.status()
        )));
    }
    let part = part_path(target, chunk.index);
    let mut file = tokio::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&part)
        .await
        .map_err(|e| ChunkError::Failed(format!("chunk {} open part failed: {e}", chunk.index)))?;

    let mut stream = resp.bytes_stream();
    while let Some(item) = stream.next().await {
        if cancelled.load(Ordering::Relaxed) {
            return Err(ChunkError::Cancelled);
        }
        if paused.load(Ordering::Relaxed) {
            // Cooperative pause: bail out now; the part file already holds
            // the prefix and `resume` re-drives the missing tail with a
            // fresh Range request. No sleeping inside the async worker.
            return Err(ChunkError::Paused);
        }
        let bytes = item.map_err(|e| ChunkError::Failed(format!("chunk {} body: {e}", chunk.index)))?;
        file.write_all(&bytes)
            .await
            .map_err(|e| ChunkError::Failed(format!("chunk {} write: {e}", chunk.index)))?;
        downloaded.fetch_add(bytes.len() as u64, Ordering::Relaxed);
    }
    file.flush()
        .await
        .map_err(|e| ChunkError::Failed(format!("chunk {} flush: {e}", chunk.index)))?;
    mark_chunk_done(target, chunk.index);
    Ok(())
}

fn part_path(target: &Path, index: usize) -> PathBuf {
    let mut s = target.as_os_str().to_owned();
    s.push(format!(".part{index}"));
    PathBuf::from(s)
}

fn sidecar_path(target: &Path) -> PathBuf {
    let mut s = target.as_os_str().to_owned();
    s.push(".jambu.json");
    PathBuf::from(s)
}

fn task_sidecar(target: &Path) -> PathBuf {
    sidecar_path(target)
}

fn persist_task(p: &PersistedTask) {
    if let Ok(json) = serde_json::to_string(p) {
        let _ = std::fs::write(sidecar_path(&p.target), json);
    }
}

fn load_persisted(sidecar: &Path) -> Option<PersistedTask> {
    let raw = std::fs::read(sidecar).ok()?;
    serde_json::from_slice(&raw).ok()
}

fn mark_chunk_done(target: &Path, index: usize) {
    let sc = sidecar_path(target);
    if let Some(mut p) = load_persisted(&sc) {
        if let Some(c) = p.chunks.iter_mut().find(|c| c.index == index) {
            c.done = true;
            persist_task(&p);
        }
    }
}

fn cleanup_sidecars(target: &Path) {
    let _ = std::fs::remove_file(sidecar_path(target));
    // Remove stray parts (all indices).
    for i in 0..MAX_CONNECTIONS {
        let part = part_path(target, i);
        if part.exists() {
            let _ = std::fs::remove_file(&part);
        } else if i > 0 {
            // Parts are dense 0..N; first miss past the known count ends it.
            // (Harmless to keep scanning, but break early for speed.)
            continue;
        }
    }
}

fn assemble_parts(target: &Path, n: usize) -> Result<(), String> {
    let tmp = target.with_extension("jambu-complete.tmp");
    let mut out =
        std::fs::File::create(&tmp).map_err(|e| format!("assemble create failed: {e}"))?;
    for i in 0..n {
        let part = part_path(target, i);
        // Single-chunk fallback may have written nothing when the server
        // ignored Range and streamed 200 into part0 — still just concat.
        let mut f =
            std::fs::File::open(&part).map_err(|e| format!("part {i} missing: {e}"))?;
        std::io::copy(&mut f, &mut out).map_err(|e| format!("concat part {i}: {e}"))?;
    }
    out.sync_all().ok();
    drop(out);
    std::fs::rename(&tmp, target).map_err(|e| format!("rename assembled file: {e}"))?;
    // Remove parts only after the rename succeeded.
    for i in 0..n {
        let _ = std::fs::remove_file(part_path(target, i));
    }
    Ok(())
}

fn recount_parts(p: &PersistedTask, downloaded: &Arc<AtomicU64>) {
    let mut sum = 0u64;
    for c in &p.chunks {
        sum += std::fs::metadata(part_path(&p.target, c.index))
            .map(|m| m.len())
            .unwrap_or(0);
    }
    downloaded.store(sum, Ordering::Relaxed);
}

fn resume_offset(p: &PersistedTask) -> u64 {
    let mut sum = 0u64;
    for c in &p.chunks {
        sum += std::fs::metadata(part_path(&p.target, c.index))
            .map(|m| m.len())
            .unwrap_or(0);
    }
    sum
}

/// How many pieces? Clamp by size so a 2 MiB file doesn't spawn 16 conns.
pub(crate) fn plan_chunk_count(total: u64, want: usize) -> usize {
    if total == 0 {
        return 1;
    }
    let by_size = (total / MIN_CHUNK_BYTES).max(1) as usize;
    want.clamp(1, MAX_CONNECTIONS).min(by_size).max(1)
}

/// Even byte-ranges covering [0, total). Empty when total unknown → single open-ended chunk.
pub(crate) fn plan_chunks(total: u64, n: usize, known: bool) -> Vec<ChunkState> {
    if !known || total == 0 || n <= 1 {
        return vec![ChunkState {
            index: 0,
            start: 0,
            end: u64::MAX,
            done: false,
        }];
    }
    let n = n.max(1);
    let base = total / n as u64;
    let mut out = Vec::with_capacity(n);
    for i in 0..n {
        let start = i as u64 * base;
        let end = if i == n - 1 { total - 1 } else { (i as u64 + 1) * base - 1 };
        out.push(ChunkState {
            index: i,
            start,
            end,
            done: false,
        });
    }
    out
}

fn parse_content_range_total(cr: &str) -> Option<u64> {
    // "bytes 0-0/12345"
    cr.rsplit('/').next()?.trim().parse::<u64>().ok()
}

fn validate_http_url(url: &str) -> Result<(), String> {
    if url.starts_with("http://") || url.starts_with("https://") {
        if url.len() > 8192 {
            return Err("URL too long".into());
        }
        Ok(())
    } else {
        Err("only http(s) URLs are supported (torrents use the torrent commands)".into())
    }
}

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chunks_cover_total_without_gaps() {
        let chunks = plan_chunks(1000, 4, true);
        assert_eq!(chunks.len(), 4);
        assert_eq!((chunks[0].start, chunks[0].end), (0, 249));
        assert_eq!((chunks[3].start, chunks[3].end), (750, 999));
        // Contiguous.
        for w in chunks.windows(2) {
            assert_eq!(w[0].end + 1, w[1].start);
        }
    }

    #[test]
    fn chunk_count_respects_min_size() {
        // 300 KiB file → at most 1 chunk of ≥256 KiB.
        assert_eq!(plan_chunk_count(300 * 1024, 8), 1);
        assert_eq!(plan_chunk_count(100 * 1024 * 1024, 8), 8);
        assert_eq!(plan_chunk_count(100 * 1024 * 1024, 64), MAX_CONNECTIONS);
    }

    #[test]
    fn unknown_total_yields_single_open_chunk() {
        let chunks = plan_chunks(0, 8, false);
        assert_eq!(chunks.len(), 1);
        assert_eq!(chunks[0].end, u64::MAX);
    }

    #[test]
    fn parses_content_range() {
        assert_eq!(parse_content_range_total("bytes 0-0/12345"), Some(12345));
        assert_eq!(parse_content_range_total("bytes */987"), Some(987));
        assert_eq!(parse_content_range_total("garbage"), None);
    }

    #[test]
    fn rejects_non_http_urls() {
        assert!(validate_http_url("magnet:?xt=urn:btih:abc").is_err());
        assert!(validate_http_url("https://example.com/f.bin").is_ok());
    }
}
