import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  Download, X, ExternalLink, FolderOpen, ChevronDown, ChevronUp,
  Pause, Play, Trash2, Plus, Magnet, Link2, FileUp,
} from "lucide-react";
import {
  useDownloadsStore, refreshDownloads, refreshTransfers, openDownload, removeDownload,
  startHttpDownload, pauseHttpDownload, resumeHttpDownload, cancelHttpDownload, removeHttpDownload,
  addTorrent, addTorrentFile, pauseTorrent, resumeTorrent, removeTorrent,
  Download as Dl, HttpTransfer, TorrentTransfer,
} from "../../store/downloadsStore";

const REFRESH_INTERVAL_MS = 2000;

export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function formatSpeed(bps: number): string {
  if (!bps || bps <= 0) return "stalled";
  return `${formatBytes(bps)}/s`;
}

export function formatEta(total: number, done: number, bps: number): string {
  if (!total || total <= done) return "";
  if (!bps || bps <= 0) return "· ETA —";
  const s = Math.round((total - done) / bps);
  if (s < 60) return `· ${s}s left`;
  if (s < 3600) return `· ${Math.floor(s / 60)}m ${s % 60}s left`;
  return `· ${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m left`;
}

/** Display name for a download: strips the `.crdownload` partial-file
 *  suffix so the user sees the final filename while it's still coming in. */
export function displayName(d: Dl): string {
  if (d.state === "in_progress" && d.filename.endsWith(".crdownload")) {
    return d.filename.slice(0, -".crdownload".length);
  }
  return d.filename;
}

/** Status line under the filename. In-progress downloads show the bytes
 *  received so far — the 3s poll makes this a coarse but honest progress
 *  indicator (no total size is available without CDP download events). */
export function stateLabel(d: Dl): { text: string; className: string } {
  if (d.state === "in_progress") {
    return { text: `Downloading… ${formatBytes(d.size_bytes)}`, className: "text-amber-400" };
  }
  if (d.state === "empty") return { text: "Empty", className: "text-muted-foreground" };
  return { text: formatBytes(d.size_bytes), className: "text-muted-foreground" };
}

export function transferLabel(t: HttpTransfer | TorrentTransfer): string {
  const pct = Math.round((t.progress ?? 0) * 100);
  switch (t.status) {
    case "downloading":
      if (t.total_known && t.total_bytes > 0) {
        return `${pct}% · ${formatBytes(t.downloaded_bytes)} / ${formatBytes(t.total_bytes)} · ${formatSpeed(t.speed_bps)} ${formatEta(t.total_bytes, t.downloaded_bytes, t.speed_bps)}`;
      }
      return `${formatBytes(t.downloaded_bytes)} · ${formatSpeed(t.speed_bps)}`;
    case "paused":
      return `Paused · ${formatBytes(t.downloaded_bytes)}${t.total_known ? ` / ${formatBytes(t.total_bytes)}` : ""}`;
    case "complete":
      return `${formatBytes(t.total_bytes || t.downloaded_bytes)} · Complete`;
    case "error":
      return `Error: ${t.error ?? "failed"}`;
    case "cancelled":
      return "Cancelled";
    case "queued":
      return "Queued…";
    default:
      return "";
  }
}

function ProgressBar({ value, tone }: { value: number; tone: string }) {
  const pct = Math.max(0, Math.min(1, value || 0)) * 100;
  return (
    <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-muted/40">
      <div className={`h-full rounded-full transition-all duration-300 ${tone}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function DownloadBar() {
  const downloads = useDownloadsStore((s) => s.downloads);
  const transfers = useDownloadsStore((s) => s.transfers);
  const torrents = useDownloadsStore((s) => s.torrents);
  const loading = useDownloadsStore((s) => s.loading);
  const error = useDownloadsStore((s) => s.error);
  const expanded = useDownloadsStore((s) => s.expanded);
  const setExpanded = useDownloadsStore((s) => s.setExpanded);
  const rev = useDownloadsStore((s) => s.rev);
  const [initialFetched, setInitialFetched] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [urlInput, setUrlInput] = useState("");
  const [connections, setConnections] = useState(8);
  const [magnetInput, setMagnetInput] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // Poll both lists while mounted. Cheap, no-op in non-Tauri.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      if (cancelled) return;
      await Promise.all([refreshDownloads(), refreshTransfers()]);
      if (!cancelled) setInitialFetched(true);
    };
    tick();
    const id = setInterval(tick, REFRESH_INTERVAL_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, [rev]);

  // Auto-expand when a new in-progress download appears, so the user sees
  // progress without having to click first.
  useEffect(() => {
    const active =
      downloads.some((d) => d.state === "in_progress") ||
      transfers.some((t) => t.status === "downloading") ||
      torrents.some((t) => t.status === "downloading");
    if (active) setExpanded(true);
  }, [downloads, transfers, torrents, setExpanded]);

  const inProgress =
    downloads.filter((d) => d.state === "in_progress").length +
    transfers.filter((t) => t.status === "downloading").length +
    torrents.filter((t) => t.status === "downloading").length;
  const total = downloads.length + transfers.length + torrents.length;

  async function handleStartHttp() {
    const url = urlInput.trim();
    if (!url) { setFormError("Paste an http(s) URL first."); return; }
    if (!/^https?:\/\//i.test(url)) { setFormError("Only http(s) URLs here — magnets go in the torrent box below."); return; }
    setBusy("http"); setFormError(null);
    try {
      await startHttpDownload(url, connections);
      setUrlInput("");
    } catch (e) {
      setFormError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function handleAddTorrent() {
    const input = magnetInput.trim();
    if (!input) { setFormError("Paste a magnet link, .torrent URL, or pick a .torrent file."); return; }
    setBusy("torrent"); setFormError(null);
    try {
      await addTorrent(input);
      setMagnetInput("");
    } catch (e) {
      setFormError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function handleTorrentFile(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    setBusy("torrent-file"); setFormError(null);
    try {
      const buf = await f.arrayBuffer();
      // btoa on large files: chunk it.
      const bytes = new Uint8Array(buf);
      let bin = "";
      for (let i = 0; i < bytes.length; i += 8192) {
        bin += String.fromCharCode(...bytes.subarray(i, i + 8192));
      }
      await addTorrentFile(btoa(bin));
    } catch (err) {
      setFormError(String(err));
    } finally {
      setBusy(null);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  if (total === 0 && !error && !loading && !showAdd) {
    // Still render a slim entry point so torrents/chunked downloads are discoverable.
    return (
      <div className="border-t border-border/50 bg-surface/80 backdrop-blur-sm">
        <button
          onClick={() => { setShowAdd(true); setExpanded(true); }}
          className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[11px] text-muted-foreground/70 transition-all duration-150 hover:bg-muted/30"
          data-testid="download-bar-add"
        >
          <Plus size={12} className="shrink-0" />
          <span>Add download · HTTP (multi-connection) · magnet / .torrent</span>
        </button>
        {showAdd && expanded && (
          <AddDownloadForm
            urlInput={urlInput} setUrlInput={setUrlInput}
            connections={connections} setConnections={setConnections}
            magnetInput={magnetInput} setMagnetInput={setMagnetInput}
            busy={busy} formError={formError}
            onStartHttp={handleStartHttp} onAddTorrent={handleAddTorrent}
            onFile={handleTorrentFile} fileRef={fileRef}
            onClose={() => setShowAdd(false)}
          />
        )}
      </div>
    );
  }

  return (
    <div className="border-t border-border/50 bg-surface/80 backdrop-blur-sm">
      {/* Collapsed header bar */}
      <div className="flex w-full items-center gap-2 px-3 py-1.5 text-[11px]">
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex flex-1 items-center gap-2 text-left transition-all duration-150 hover:bg-muted/30"
          data-testid="download-bar-toggle"
        >
          <Download size={12} className="shrink-0 text-muted-foreground/60" />
          <span className="text-foreground/80">
            {inProgress > 0
              ? `${inProgress} downloading · ${total} total`
              : `${total} download${total === 1 ? "" : "s"}`}
          </span>
          {loading && <span className="text-muted-foreground/50">· refreshing…</span>}
          {error && <span className="text-red-400/80">· error</span>}
          <span className="ml-auto flex items-center gap-2 text-muted-foreground/50">
            {expanded ? <ChevronDown size={12} /> : <ChevronUp size={12} />}
          </span>
        </button>
        <button
          onClick={() => { setShowAdd((v) => !v); setExpanded(true); }}
          title="New download (HTTP multi-connection / torrent)"
          className="rounded-md p-1 text-muted-foreground/60 transition-all hover:bg-muted/50 hover:text-foreground"
          data-testid="download-bar-add"
        >
          <Plus size={12} />
        </button>
      </div>

      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: [0.23, 1, 0.32, 1] }}
            className="overflow-hidden border-t border-border/30"
          >
            <div className="max-h-72 overflow-y-auto p-2">
              {error && (
                <div className="mb-2 rounded-md bg-red-500/10 border border-red-500/20 px-2.5 py-1.5 text-[10px] text-red-400/90">
                  {error}
                </div>
              )}
              {showAdd && (
                <AddDownloadForm
                  urlInput={urlInput} setUrlInput={setUrlInput}
                  connections={connections} setConnections={setConnections}
                  magnetInput={magnetInput} setMagnetInput={setMagnetInput}
                  busy={busy} formError={formError}
                  onStartHttp={handleStartHttp} onAddTorrent={handleAddTorrent}
                  onFile={handleTorrentFile} fileRef={fileRef}
                  onClose={() => setShowAdd(false)}
                />
              )}

              {total === 0 && !loading && (
                <div className="px-2 py-4 text-center text-[11px] text-muted-foreground/50">
                  No downloads yet.
                </div>
              )}

              {/* Segmented HTTP transfers */}
              {transfers.length > 0 && (
                <SectionTitle title={`Accelerated HTTP · ${transfers.length}`} />
              )}
              <ul className="flex flex-col gap-1">
                {transfers.map((t) => (
                  <li
                    key={t.id}
                    className="group flex items-center gap-2 rounded-md bg-background/30 px-2 py-1.5 text-[11px] transition-colors duration-150 hover:bg-muted/30"
                  >
                    <Link2 size={12} className="shrink-0 text-sky-400/70" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-foreground/80" title={`${t.name}\n${t.url}`}>
                        {t.name}
                        <span className="ml-1.5 rounded bg-sky-500/15 px-1 text-[9px] text-sky-300/90">
                          {t.connections}×
                        </span>
                      </div>
                      <div className={t.status === "error" ? "text-red-400/90" : "text-muted-foreground"}>
                        {transferLabel(t)}
                      </div>
                      {(t.status === "downloading" || t.status === "paused") && (
                        <ProgressBar value={t.progress} tone={t.status === "paused" ? "bg-amber-400/70" : "bg-sky-400/80"} />
                      )}
                    </div>
                    {t.status === "downloading" ? (
                      <IconBtn title="Pause" onClick={() => pauseHttpDownload(t.id)}><Pause size={11} /></IconBtn>
                    ) : (t.status === "paused" || t.status === "error") ? (
                      <IconBtn title="Resume" onClick={() => resumeHttpDownload(t.id)}><Play size={11} /></IconBtn>
                    ) : null}
                    {(t.status === "downloading" || t.status === "paused" || t.status === "error") && (
                      <IconBtn title="Cancel" onClick={() => cancelHttpDownload(t.id)}><X size={11} /></IconBtn>
                    )}
                    <IconBtn title="Open file" onClick={() => openDownload(t.path)}><ExternalLink size={11} /></IconBtn>
                    <IconBtn title="Remove (keep file)" onClick={() => removeHttpDownload(t.id, false)}><Trash2 size={11} /></IconBtn>
                  </li>
                ))}
              </ul>

              {/* Torrents */}
              {torrents.length > 0 && (
                <SectionTitle title={`Torrents · ${torrents.length}`} />
              )}
              <ul className="flex flex-col gap-1">
                {torrents.map((t) => (
                  <li
                    key={t.id}
                    className="group flex items-center gap-2 rounded-md bg-background/30 px-2 py-1.5 text-[11px] transition-colors duration-150 hover:bg-muted/30"
                  >
                    <Magnet size={12} className="shrink-0 text-emerald-400/70" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-foreground/80" title={`${t.name}\n${t.magnet}`}>
                        {t.name}
                        <span className="ml-1.5 rounded bg-emerald-500/15 px-1 text-[9px] text-emerald-300/90">
                          {t.peers} peers
                        </span>
                      </div>
                      <div className={t.status === "error" ? "text-red-400/90" : "text-muted-foreground"}>
                        {transferLabel(t)}
                      </div>
                      {(t.status === "downloading" || t.status === "paused") && (
                        <ProgressBar value={t.progress} tone="bg-emerald-400/80" />
                      )}
                    </div>
                    {t.status === "downloading" ? (
                      <IconBtn title="Pause torrent" onClick={() => pauseTorrent(t.id)}><Pause size={11} /></IconBtn>
                    ) : (t.status === "paused" || t.status === "error") ? (
                      <IconBtn title="Resume torrent" onClick={() => resumeTorrent(t.id)}><Play size={11} /></IconBtn>
                    ) : null}
                    <IconBtn title="Remove torrent (keep files)" onClick={() => removeTorrent(t.id, false)}><Trash2 size={11} /></IconBtn>
                  </li>
                ))}
              </ul>

              {/* Classic Chrome downloads */}
              {downloads.length > 0 && (
                <SectionTitle title={`Browser · ${downloads.length}`} />
              )}
              <ul className="flex flex-col gap-1">
                {downloads.map((d) => {
                  const label = stateLabel(d);
                  return (
                    <li
                      key={d.path}
                      className="group flex items-center gap-2 rounded-md bg-background/30 px-2 py-1.5 text-[11px] transition-colors duration-150 hover:bg-muted/30"
                    >
                      <FolderOpen size={12} className="shrink-0 text-muted-foreground/50" />
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-foreground/80" title={d.path}>{displayName(d)}</div>
                        <div className={label.className}>{label.text}</div>
                      </div>
                      <button
                        onClick={() => openDownload(d.path)}
                        title="Open with default app"
                        className="rounded-md p-1 text-muted-foreground/50 transition-all duration-150 hover:bg-muted/50 hover:text-foreground"
                      >
                        <ExternalLink size={11} />
                      </button>
                      <button
                        onClick={() => removeDownload(d.path)}
                        title="Remove from disk"
                        className="rounded-md p-1 text-muted-foreground/50 transition-all duration-150 hover:bg-red-500/15 hover:text-red-400"
                      >
                        <X size={11} />
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
            {!initialFetched && (
              <div className="px-3 py-1 text-[10px] text-muted-foreground/40">
                Loading first scan…
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function SectionTitle({ title }: { title: string }) {
  return (
    <div className="px-1 pb-0.5 pt-2 text-[9px] font-medium uppercase tracking-wider text-muted-foreground/60">
      {title}
    </div>
  );
}

function IconBtn({ title, onClick, children }: { title: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      title={title}
      className="rounded-md p-1 text-muted-foreground/50 transition-all duration-150 hover:bg-muted/50 hover:text-foreground"
    >
      {children}
    </button>
  );
}

function AddDownloadForm(props: {
  urlInput: string; setUrlInput: (v: string) => void;
  connections: number; setConnections: (n: number) => void;
  magnetInput: string; setMagnetInput: (v: string) => void;
  busy: string | null; formError: string | null;
  onStartHttp: () => void; onAddTorrent: () => void;
  onFile: (e: React.ChangeEvent<HTMLInputElement>) => void;
  fileRef: React.RefObject<HTMLInputElement | null>;
  onClose: () => void;
}) {
  const { urlInput, setUrlInput, connections, setConnections, magnetInput, setMagnetInput } = props;
  return (
    <div className="mb-2 rounded-md border border-border/40 bg-background/40 p-2">
      <div className="mb-1.5 flex items-center gap-1.5 text-[10px] font-medium text-foreground/80">
        <Link2 size={11} className="text-sky-400/80" />
        Accelerated HTTP — split into parallel pieces like a torrent
      </div>
      <div className="flex gap-1.5">
        <input
          value={urlInput}
          onChange={(e) => setUrlInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") props.onStartHttp(); }}
          placeholder="https://example.com/large-file.iso"
          className="min-w-0 flex-1 rounded-md border border-border/40 bg-background/60 px-2 py-1 text-[11px] text-foreground placeholder:text-muted-foreground/40 focus:outline-none focus:ring-1 focus:ring-sky-500/50"
          data-testid="download-url-input"
        />
        <label className="flex items-center gap-1 text-[10px] text-muted-foreground" title="Parallel connections (1–16). More = faster on good servers.">
          <span>{connections}×</span>
          <input
            type="range" min={1} max={16} value={connections}
            onChange={(e) => setConnections(Number(e.target.value))}
            className="w-16"
            data-testid="download-connections"
          />
        </label>
        <button
          onClick={props.onStartHttp}
          disabled={props.busy === "http"}
          className="rounded-md bg-sky-500/20 px-2 py-1 text-[11px] text-sky-300 transition-colors hover:bg-sky-500/30 disabled:opacity-50"
          data-testid="download-start-http"
        >
          {props.busy === "http" ? "…" : "Download"}
        </button>
      </div>

      <div className="mb-1.5 mt-2.5 flex items-center gap-1.5 text-[10px] font-medium text-foreground/80">
        <Magnet size={11} className="text-emerald-400/80" />
        Torrent — magnet link, .torrent URL, or .torrent file
      </div>
      <div className="flex gap-1.5">
        <input
          value={magnetInput}
          onChange={(e) => setMagnetInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") props.onAddTorrent(); }}
          placeholder="magnet:?xt=urn:btih:…"
          className="min-w-0 flex-1 rounded-md border border-border/40 bg-background/60 px-2 py-1 text-[11px] text-foreground placeholder:text-muted-foreground/40 focus:outline-none focus:ring-1 focus:ring-emerald-500/50"
          data-testid="torrent-input"
        />
        <button
          onClick={props.onAddTorrent}
          disabled={props.busy === "torrent"}
          className="rounded-md bg-emerald-500/20 px-2 py-1 text-[11px] text-emerald-300 transition-colors hover:bg-emerald-500/30 disabled:opacity-50"
          data-testid="torrent-add"
        >
          {props.busy === "torrent" ? "…" : "Add"}
        </button>
        <button
          onClick={() => props.fileRef.current?.click()}
          title="Pick a .torrent file"
          className="rounded-md border border-border/40 px-2 py-1 text-[11px] text-muted-foreground transition-colors hover:bg-muted/30 hover:text-foreground"
          data-testid="torrent-file-btn"
        >
          <FileUp size={11} />
        </button>
        <input ref={props.fileRef} type="file" accept=".torrent" className="hidden" onChange={props.onFile} data-testid="torrent-file-input" />
      </div>

      {props.formError && (
        <div className="mt-1.5 rounded-md bg-red-500/10 border border-red-500/20 px-2 py-1 text-[10px] text-red-400/90">
          {props.formError}
        </div>
      )}
      <div className="mt-1.5 flex justify-end">
        <button onClick={props.onClose} className="text-[10px] text-muted-foreground/60 hover:text-foreground">
          Close
        </button>
      </div>
    </div>
  );
}
