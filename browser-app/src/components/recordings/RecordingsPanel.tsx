import { useCallback, useEffect, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  Video,
  RefreshCw,
  Play,
  Trash2,
  CheckCircle2,
  XCircle,
  CircleDashed,
} from "lucide-react";
import { Button } from "../ui/button";
import { localFetch } from "../../utils/api";

interface Recording {
  id: number;
  name: string;
  start_url: string;
  step_count: number;
  duration_ms: number;
  status: string;
  error: string | null;
  created_at: number;
}

function timeAgo(julian: number): string {
  // SQLite julianday('now') → ms since epoch
  const ms = (julian - 2440587.5) * 86400000;
  const diff = Date.now() - ms;
  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

function host(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

const REPLAY_TIMEOUT_MS = 5 * 60_000; // replays can legitimately run minutes

export function RecordingsPanel() {
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [loading, setLoading] = useState(false);
  const [replayingId, setReplayingId] = useState<number | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const loadRecordings = useCallback(async () => {
    setLoading(true);
    try {
      const res = await localFetch("/sessions/recordings?limit=100");
      const data = await res.json();
      setRecordings(data.recordings || []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadRecordings();
  }, [loadRecordings]);

  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(null), 3500);
    return () => clearTimeout(id);
  }, [toast]);

  const replay = async (rec: Recording) => {
    setReplayingId(rec.id);
    try {
      const res = await localFetch(
        `/sessions/recordings/${rec.id}/replay`,
        { method: "POST" },
        { timeoutMs: REPLAY_TIMEOUT_MS },
      );
      const data = await res.json();
      if (res.ok && data.success) {
        setToast(`Replayed "${rec.name}" — ${data.replayed_steps} steps OK`);
      } else {
        setToast(`Replay of "${rec.name}" failed: ${data.error || res.status}`);
      }
    } catch (e: any) {
      const msg = e?.name === "AbortError" ? "timed out" : String(e);
      setToast(`Replay of "${rec.name}" ${msg}`);
    } finally {
      setReplayingId(null);
      loadRecordings();
    }
  };

  const remove = async (rec: Recording) => {
    try {
      await localFetch(`/sessions/recordings/${rec.id}`, { method: "DELETE" });
      setToast(`Deleted "${rec.name}"`);
      await loadRecordings();
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border p-3">
        <div className="mb-2 flex items-center gap-2">
          <Video size={18} className="text-accent" />
          <span className="font-semibold">Recordings</span>
          <div className="flex-1" />
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={loadRecordings}
            disabled={loading}
            title="Refresh recordings"
          >
            <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          Recorded browser runs. Replay one to reproduce a flow step-by-step —
          useful for debugging failed audits or agent actions.
        </p>
      </div>

      <div className="flex-1 overflow-y-auto p-3">
        {recordings.length === 0 && !loading ? (
          <div className="mt-16 flex flex-col items-center gap-2 text-center text-xs text-muted-foreground">
            <Video size={28} className="opacity-40" />
            <p className="text-sm font-medium text-foreground">No recordings yet</p>
            <p className="max-w-[280px]">
              Record a scripted browser run via the engine API
              (<code className="rounded bg-muted px-1">POST /sessions/recordings/run</code>)
              and it will appear here for one-click replay.
            </p>
          </div>
        ) : (
          <AnimatePresence initial={false}>
            {recordings.map((rec, i) => (
              <motion.div
                key={rec.id}
                layout
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.15, delay: Math.min(i * 0.03, 0.3) }}
                className="mb-2 rounded-md border border-border bg-card p-2.5 text-xs"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      {rec.status === "completed" ? (
                        <CheckCircle2 size={12} className="shrink-0 text-emerald-400" />
                      ) : rec.status === "failed" ? (
                        <XCircle size={12} className="shrink-0 text-red-400" />
                      ) : (
                        <CircleDashed size={12} className="shrink-0 animate-spin text-muted-foreground" />
                      )}
                      <span className="truncate font-medium">{rec.name}</span>
                    </div>
                    <div className="mt-1 flex items-center gap-2 text-muted-foreground">
                      <span className="max-w-[45%] truncate">{host(rec.start_url)}</span>
                      <span>·</span>
                      <span>{rec.step_count} steps</span>
                      <span>·</span>
                      <span>{(rec.duration_ms / 1000).toFixed(1)}s</span>
                      <span>·</span>
                      <span>{timeAgo(rec.created_at)}</span>
                    </div>
                    {rec.error && (
                      <p className="mt-1 truncate text-red-400/80" title={rec.error}>
                        {rec.error}
                      </p>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      disabled={rec.status === "recording" || replayingId !== null}
                      onClick={() => replay(rec)}
                      title={`Replay "${rec.name}"`}
                    >
                      {replayingId === rec.id ? (
                        <span className="block h-3 w-3 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-muted-foreground" />
                      ) : (
                        <Play size={12} />
                      )}
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      onClick={() => remove(rec)}
                      title={`Delete "${rec.name}"`}
                    >
                      <Trash2 size={12} />
                    </Button>
                  </div>
                </div>
              </motion.div>
            ))}
          </AnimatePresence>
        )}
      </div>

      <AnimatePresence>
        {toast && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 8 }}
            className="border-t border-border bg-card px-3 py-2 text-xs text-muted-foreground"
          >
            {toast}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
