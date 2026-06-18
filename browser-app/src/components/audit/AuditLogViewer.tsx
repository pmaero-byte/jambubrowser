import { useEffect, useState } from "react";
import { motion, AnimatePresence, LayoutGroup } from "motion/react";
import { ScrollText, RefreshCw } from "lucide-react";
import { Button } from "../ui/button";
import { localFetch, createWebSocket } from "../../utils/api";

interface AuditEntry {
  id: string;
  timestamp: number;
  category: string;
  action: string;
  details: any;
  actor?: string;
  session_id?: string;
  hash?: string;
}

interface AuditStats {
  total_entries: number;
  by_category: Record<string, number>;
  oldest_entry?: number;
  newest_entry?: number;
  retention_days: number;
}

const categories = ["all", "security", "privacy", "vault", "agent", "system", "research"];

export function AuditLogViewer() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [stats, setStats] = useState<AuditStats | null>(null);
  const [category, setCategory] = useState("all");
  const [limit, setLimit] = useState(50);
  const [loading, setLoading] = useState(true);
  // Tracks entry ids that just arrived (for the "live tail" highlight).
  const [recentIds, setRecentIds] = useState<Set<string>>(new Set());

  const fetchStats = async () => {
    try {
      const r = await localFetch("/audit/stats");
      setStats(await r.json());
    } catch (e) {
      console.error(e);
    }
  };

  const fetchLog = async () => {
    try {
      setLoading(true);
      const qs = new URLSearchParams();
      qs.set("limit", String(limit));
      if (category !== "all") qs.set("category", category);
      const r = await localFetch(`/audit/log?${qs.toString()}`);
      const data = await r.json();
      setEntries(data.entries || []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchStats();
    fetchLog();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [category, limit]);

  useEffect(() => {
    const ws = createWebSocket("/ws/audit");
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === "stats") {
          setStats(data.data);
        } else if (data.type === "entry" && data.data) {
          // Prepend the new entry, cap at the current limit, and mark it
          // as "recent" so the list highlights it for ~2s.
          const incoming = data.data as AuditEntry;
          setEntries((prev) => [incoming, ...prev].slice(0, limit));
          setRecentIds((prev) => {
            const next = new Set(prev);
            next.add(incoming.id);
            return next;
          });
          window.setTimeout(() => {
            setRecentIds((prev) => {
              const next = new Set(prev);
              next.delete(incoming.id);
              return next;
            });
          }, 2200);
        }
      } catch {
        // ignore
      }
    };
    return () => ws.close();
  }, [limit]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-border p-3">
        <div className="flex items-center gap-2">
          <motion.span
            animate={
              recentIds.size > 0
                ? { rotate: [0, -8, 6, 0], color: ["rgb(244,114,182)", "inherit"] }
                : { rotate: 0 }
            }
            transition={{ duration: 0.45, ease: "easeOut" }}
            className="inline-flex"
          >
            <ScrollText size={16} />
          </motion.span>
          <span className="font-semibold">Audit Log</span>
          {recentIds.size > 0 && (
            <motion.span
              key="live"
              initial={{ opacity: 0, x: -4 }}
              animate={{ opacity: 1, x: 0 }}
              className="ml-1 flex items-center gap-1 rounded-full bg-emerald-400/15 px-2 py-0.5 text-[10px] font-medium text-emerald-400"
            >
              <span className="relative flex h-1.5 w-1.5">
                <motion.span
                  className="absolute inline-flex h-full w-full rounded-full bg-emerald-400"
                  animate={{ scale: [1, 2.4], opacity: [0.6, 0] }}
                  transition={{ duration: 1.2, repeat: Infinity, ease: "easeOut" }}
                />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-400" />
              </span>
              live
            </motion.span>
          )}
        </div>
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={fetchLog}>
          <motion.span
            animate={loading ? { rotate: 360 } : { rotate: 0 }}
            transition={loading ? { duration: 1, repeat: Infinity, ease: "linear" } : { duration: 0.2 }}
            className="inline-flex"
          >
            <RefreshCw size={14} />
          </motion.span>
        </Button>
      </div>

      <motion.div
        className="flex items-center gap-2 border-b border-border px-3 py-2"
        initial={{ opacity: 0, y: -2 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.2 }}
      >
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className="rounded-md border border-border bg-background px-2 py-1 text-xs outline-none transition-colors focus:ring-2 focus:ring-primary/40"
        >
          {categories.map((c) => (
            <option key={c} value={c}>
              {c.charAt(0).toUpperCase() + c.slice(1)}
            </option>
          ))}
        </select>
        <select
          value={limit}
          onChange={(e) => setLimit(Number(e.target.value))}
          className="rounded-md border border-border bg-background px-2 py-1 text-xs outline-none transition-colors focus:ring-2 focus:ring-primary/40"
        >
          {[25, 50, 100, 250].map((n) => (
            <option key={n} value={n}>{n} entries</option>
          ))}
        </select>
        {stats && (
          <motion.span
            key={stats.total_entries}
            initial={{ scale: 0.85, opacity: 0.5 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ type: "spring", stiffness: 380, damping: 22 }}
            className="ml-auto text-xs text-muted-foreground"
          >
            {stats.total_entries} total
          </motion.span>
        )}
      </motion.div>

      <div className="flex-1 overflow-y-auto p-3">
        <LayoutGroup>
          <div className="space-y-2">
            <AnimatePresence initial={false}>
              {entries.map((entry, i) => {
                const isRecent = recentIds.has(entry.id);
                return (
                  <motion.div
                    key={entry.id}
                    layout
                    initial={{ opacity: 0, y: -8, scale: 0.96 }}
                    animate={{
                      opacity: 1,
                      y: 0,
                      scale: 1,
                      boxShadow: isRecent
                        ? "0 0 0 2px rgba(52,211,153,0.45)"
                        : "0 0 0 0px rgba(0,0,0,0)",
                    }}
                    exit={{ opacity: 0, y: 8, scale: 0.96 }}
                    transition={{
                      duration: 0.25,
                      delay: Math.min(i * 0.018, 0.45), // stagger, capped
                      boxShadow: { duration: 0.4 },
                    }}
                    whileHover={{ y: -1, borderColor: "rgba(99,102,241,0.4)" }}
                    className="rounded-lg border border-border bg-card p-2.5 text-xs"
                  >
                    <div className="flex items-center justify-between">
                      <span className="rounded bg-muted px-1.5 py-0.5 font-medium">
                        {entry.category}
                      </span>
                      <span className="text-muted-foreground">
                        {new Date(entry.timestamp * 1000).toLocaleString()}
                      </span>
                    </div>
                    <div className="mt-1.5 font-medium">{entry.action}</div>
                    {entry.details && (
                      <pre className="mt-1 max-h-24 overflow-auto rounded bg-muted p-1.5 text-[10px]">
                        {JSON.stringify(entry.details, null, 2)}
                      </pre>
                    )}
                    {entry.hash && (
                      <div className="mt-1.5 truncate text-[10px] text-muted-foreground">
                        hash: {entry.hash}
                      </div>
                    )}
                  </motion.div>
                );
              })}
            </AnimatePresence>
            {entries.length === 0 && !loading && (
              <p className="text-center text-xs text-muted-foreground">No audit entries.</p>
            )}
          </div>
        </LayoutGroup>
      </div>
    </div>
  );
}
