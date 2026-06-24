import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { History, Activity, RefreshCw, Database, Shield, Lock } from "lucide-react";
import { Button } from "../ui/button";
import { localFetch } from "../../utils/api";

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

export function HistoryPanel() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [stats, setStats] = useState<StatsData | null>(null);
  const [loading, setLoading] = useState(false);

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
