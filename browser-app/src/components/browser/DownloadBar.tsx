import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Download, X, ExternalLink, FolderOpen, ChevronDown, ChevronUp } from "lucide-react";
import {
  useDownloadsStore, refreshDownloads, openDownload, removeDownload, Download as Dl,
} from "../../store/downloadsStore";

const REFRESH_INTERVAL_MS = 3000;

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function stateLabel(d: Dl): { text: string; className: string } {
  if (d.state === "in_progress") return { text: "Downloading…", className: "text-amber-400" };
  if (d.state === "empty") return { text: "Empty", className: "text-muted-foreground" };
  return { text: formatBytes(d.size_bytes), className: "text-muted-foreground" };
}

export function DownloadBar() {
  const downloads = useDownloadsStore((s) => s.downloads);
  const loading = useDownloadsStore((s) => s.loading);
  const error = useDownloadsStore((s) => s.error);
  const expanded = useDownloadsStore((s) => s.expanded);
  const setExpanded = useDownloadsStore((s) => s.setExpanded);
  const rev = useDownloadsStore((s) => s.rev);
  const [initialFetched, setInitialFetched] = useState(false);

  // Poll the Tauri list every 3s while mounted. Cheap, no-op in non-Tauri.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      if (cancelled) return;
      await refreshDownloads();
      if (!cancelled) setInitialFetched(true);
    };
    tick();
    const id = setInterval(tick, REFRESH_INTERVAL_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, [rev]);

  // Auto-expand when a new in-progress download appears, so the user sees
  // progress without having to click first.
  useEffect(() => {
    if (downloads.some((d) => d.state === "in_progress")) setExpanded(true);
  }, [downloads, setExpanded]);

  const inProgress = downloads.filter((d) => d.state === "in_progress").length;
  const total = downloads.length;

  if (total === 0 && !error && !loading) return null;

  return (
    <div className="border-t border-border bg-card/95 backdrop-blur">
      {/* Collapsed header bar */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[11px] transition-colors hover:bg-muted/40"
        data-testid="download-bar-toggle"
      >
        <Download size={12} className="shrink-0 text-muted-foreground" />
        <span className="text-foreground/90">
          {inProgress > 0
            ? `${inProgress} downloading · ${total} total`
            : `${total} download${total === 1 ? "" : "s"}`}
        </span>
        {loading && <span className="text-muted-foreground">· refreshing…</span>}
        {error && <span className="text-red-400">· error</span>}
        <div className="ml-auto flex items-center gap-2 text-muted-foreground">
          {expanded ? <ChevronDown size={12} /> : <ChevronUp size={12} />}
        </div>
      </button>

      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
            className="overflow-hidden border-t border-border"
          >
            <div className="max-h-64 overflow-y-auto p-2">
              {error && (
                <div className="mb-2 rounded bg-red-500/10 px-2 py-1 text-[10px] text-red-400">
                  {error}
                </div>
              )}
              {downloads.length === 0 && !loading && (
                <div className="px-2 py-3 text-center text-[11px] text-muted-foreground">
                  No downloads yet.
                </div>
              )}
              <ul className="flex flex-col gap-1">
                {downloads.map((d) => {
                  const label = stateLabel(d);
                  return (
                    <li
                      key={d.path}
                      className="group flex items-center gap-2 rounded-md bg-background/40 px-2 py-1.5 text-[11px]"
                    >
                      <FolderOpen size={12} className="shrink-0 text-muted-foreground" />
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-foreground/90" title={d.path}>{d.filename}</div>
                        <div className={label.className}>{label.text}</div>
                      </div>
                      <button
                        onClick={() => openDownload(d.path)}
                        title="Open with default app"
                        className="rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                      >
                        <ExternalLink size={11} />
                      </button>
                      <button
                        onClick={() => removeDownload(d.path)}
                        title="Remove from disk"
                        className="rounded p-1 text-muted-foreground transition-colors hover:bg-red-500/20 hover:text-red-400"
                      >
                        <X size={11} />
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
            {!initialFetched && (
              <div className="px-3 py-1 text-[10px] text-muted-foreground">
                Loading first scan…
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
