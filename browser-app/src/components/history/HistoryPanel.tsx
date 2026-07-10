import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { History, Activity, RefreshCw, Database, Shield, Lock, Globe, Trash2, ExternalLink, X } from "lucide-react";
import { Button } from "../ui/button";
import { localFetch } from "../../utils/api";
import { useBrowsingHistoryStore } from "../../store/browsingHistoryStore";
import { useAppStore } from "../../store/appStore";

interface HealthData {
  status: string;
  ram_used_gb: number;
  ram_total_gb: number;
  cpu_percent: number;
  checks: {
    database: string;
    audit: string;
    audit_entries: number;
    vault: string;
  };
}

interface StatsData {
  doc_count: number;
  active_missions: number;
  custom_tools: number;
  credentials: number;
  browser_sessions: number;
}

function timeAgo(ms: number): string {
  const sec = Math.floor((Date.now() - ms) / 1000);
  if (sec < 60) return "just now";
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

export function HistoryPanel() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [stats, setStats] = useState<StatsData | null>(null);
  const [loading, setLoading] = useState(false);
  const [historyFilter, setHistoryFilter] = useState("");

  const history = useBrowsingHistoryStore((s) => s.entries);
  const removeHistoryEntry = useBrowsingHistoryStore((s) => s.removeEntry);
  const clearHistory = useBrowsingHistoryStore((s) => s.clearAll);
  const setActiveTab = useAppStore((s) => s.setActiveTab);
  const addBrowserTab = useAppStore((s) => s.addBrowserTab);

  const loadAll = async () => {
    setLoading(true);
    try {
      const [hRes, sRes] = await Promise.all([
        localFetch("/health"),
        localFetch("/stats"),
      ]);
      setHealth(await hRes.json());
      setStats(await sRes.json());
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAll();
  }, []);

  const filteredHistory = historyFilter.trim()
    ? history.filter(
        (e) =>
          e.url.toLowerCase().includes(historyFilter.toLowerCase()) ||
          e.title.toLowerCase().includes(historyFilter.toLowerCase())
      )
    : history;

  const openInBrowser = (url: string, title: string) => {
    addBrowserTab(url, title || url);
    setActiveTab("browser");
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border p-3">
        <div className="mb-2 flex items-center gap-2">
          <History size={18} className="text-accent" />
          <span className="font-semibold">System &amp; History</span>
        </div>
        <p className="text-xs text-muted-foreground">
          Engine health, resource usage, and knowledge vault statistics.
        </p>
      </div>

      <div className="flex-1 overflow-y-auto p-3">
        <AnimatePresence mode="wait">
          <motion.div
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.18 }}
            className="space-y-3"
          >
            <Section title="Engine Health" icon={<Activity size={14} />}>
              <StatRow label="Status" value={health?.status ?? "—"} />
              <StatRow label="RAM" value={health ? `${health.ram_used_gb.toFixed(1)} / ${health.ram_total_gb.toFixed(1)} GB` : "—"} />
              <StatRow label="CPU" value={health ? `${health.cpu_percent.toFixed(1)}%` : "—"} />
            </Section>

            <Section title="System Checks" icon={<Shield size={14} />}>
              <CheckRow label="Database" status={health?.checks.database} />
              <CheckRow label="Audit Log" status={health?.checks.audit} />
              <CheckRow label="Vault" status={health?.checks.vault} />
              {health?.checks.audit_entries !== undefined && (
                <StatRow label="Audit Entries" value={String(health.checks.audit_entries)} />
              )}
            </Section>

            <Section title="Knowledge Vault" icon={<Database size={14} />}>
              <StatRow label="Documents" value={String(stats?.doc_count ?? "—")} />
              <StatRow label="Active Missions" value={String(stats?.active_missions ?? "—")} />
              <StatRow label="Custom Tools" value={String(stats?.custom_tools ?? "—")} />
              <StatRow label="Credentials" value={String(stats?.credentials ?? "—")} />
              <StatRow label="Browser Sessions" value={String(stats?.browser_sessions ?? "—")} />
            </Section>

            <Section title="Browser History" icon={<Globe size={14} />}>
              <div className="mb-2 flex items-center gap-1">
                <input
                  type="text"
                  placeholder="Filter by url or title…"
                  value={historyFilter}
                  onChange={(e) => setHistoryFilter(e.target.value)}
                  className="flex-1 rounded border border-border bg-background px-2 py-1 text-[11px] outline-none placeholder:text-muted-foreground/50"
                />
                {history.length > 0 && (
                  <Button
                    variant="ghost" size="icon"
                    onClick={() => { if (confirm(`Clear all ${history.length} history entries?`)) clearHistory(); }}
                    title="Clear all history"
                    className="h-6 w-6 text-muted-foreground hover:text-red-400"
                  >
                    <Trash2 size={11} />
                  </Button>
                )}
              </div>
              {history.length === 0 ? (
                <div className="rounded border border-dashed border-border p-3 text-center text-[11px] text-muted-foreground">
                  No browsing history yet. Pages you visit in the browser pane
                  will appear here.
                </div>
              ) : filteredHistory.length === 0 ? (
                <div className="rounded border border-dashed border-border p-3 text-center text-[11px] text-muted-foreground">
                  No history entries match "{historyFilter}".
                </div>
              ) : (
                <ul className="max-h-72 space-y-1 overflow-y-auto">
                  {filteredHistory.slice(0, 50).map((e) => (
                    <li
                      key={`${e.url}-${e.visitedAt}`}
                      className="group flex items-start gap-2 rounded px-2 py-1.5 text-[11px] hover:bg-muted/40"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-foreground/90" title={e.url}>
                          {e.title || e.url}
                        </div>
                        <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                          <span className="truncate">{e.url}</span>
                          <span>·</span>
                          <span className="shrink-0">{timeAgo(e.visitedAt)}</span>
                        </div>
                      </div>
                      <button
                        onClick={() => openInBrowser(e.url, e.title)}
                        title="Open in browser"
                        className="shrink-0 rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                      >
                        <ExternalLink size={10} />
                      </button>
                      <button
                        onClick={() => removeHistoryEntry(e.url, e.visitedAt)}
                        title="Remove this entry"
                        className="shrink-0 rounded p-1 text-muted-foreground transition-colors hover:bg-red-500/20 hover:text-red-400"
                      >
                        <X size={10} />
                      </button>
                    </li>
                  ))}
                  {filteredHistory.length > 50 && (
                    <li className="px-2 py-1 text-center text-[10px] text-muted-foreground">
                      Showing 50 of {filteredHistory.length} matching entries
                    </li>
                  )}
                </ul>
              )}
            </Section>
          </motion.div>
        </AnimatePresence>
      </div>

      <div className="border-t border-border p-3">
        <Button variant="outline" size="sm" className="w-full gap-1" onClick={loadAll}>
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} /> Refresh
        </Button>
      </div>
    </div>
  );
}

function Section({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="rounded-md border border-border bg-card p-3">
      <div className="mb-2 flex items-center gap-1.5 text-xs font-medium">
        {icon}
        <span>{title}</span>
      </div>
      <div className="space-y-1.5">{children}</div>
    </div>
  );
}

function StatRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{value}</span>
    </div>
  );
}

function CheckRow({ label, status }: { label: string; status: string | undefined }) {
  const ok = status === "ok" || status === "locked";
  return (
    <div className="flex items-center justify-between text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span className={`flex items-center gap-1 ${ok ? "text-emerald-400" : "text-red-400"}`}>
        {ok ? <Shield size={10} /> : <Lock size={10} />}
        {status ?? "—"}
      </span>
    </div>
  );
}
