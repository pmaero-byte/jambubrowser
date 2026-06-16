import { useEffect, useState } from "react";
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
        if (data.type === "stats") setStats(data.data);
      } catch {
        // ignore
      }
    };
    return () => ws.close();
  }, []);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-border p-3">
        <div className="flex items-center gap-2">
          <ScrollText size={16} />
          <span className="font-semibold">Audit Log</span>
        </div>
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={fetchLog}>
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
        </Button>
      </div>

      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className="rounded-md border border-border bg-background px-2 py-1 text-xs outline-none"
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
          className="rounded-md border border-border bg-background px-2 py-1 text-xs outline-none"
        >
          {[25, 50, 100, 250].map((n) => (
            <option key={n} value={n}>{n} entries</option>
          ))}
        </select>
        {stats && (
          <span className="ml-auto text-xs text-muted-foreground">
            {stats.total_entries} total
          </span>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-3">
        <div className="space-y-2">
          {entries.map((entry) => (
            <div
              key={entry.id}
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
            </div>
          ))}
          {entries.length === 0 && !loading && (
            <p className="text-center text-xs text-muted-foreground">No audit entries.</p>
          )}
        </div>
      </div>
    </div>
  );
}
