import { useCallback, useEffect, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  Radar, Plus, RefreshCw, Play, Trash2, ChevronDown, ChevronRight,
  Power, ExternalLink,
} from "lucide-react";
import { Button } from "../ui/button";
import { localFetch, engineOrigin } from "../../utils/api";

// ── Types ────────────────────────────────────────────────────────────

interface Monitor {
  id: number;
  url: string;
  mode: string;
  interval_minutes: number;
  fail_on: string;
  webhook_url: string | null;
  enabled: boolean;
  created_at: number;
  last_run_at: number | null;
  last_status: string | null;
  last_finding_count: number | null;
  last_error: string | null;
}

interface MonitorRun {
  id: number;
  monitor_id: number;
  run_at: number;
  status: string;
  baseline: boolean;
  total_findings: number;
  new_findings: number;
  resolved_findings: number;
  by_severity: Record<string, number>;
  visual_change_pct: number | null;
  has_screenshot: boolean;
  error: string | null;
}

interface RunResult {
  status: string;
  baseline?: boolean;
  total_findings?: number;
  new_findings?: number;
  resolved_findings?: number;
  alerted?: boolean;
  alert_findings?: Array<{ severity: string; title: string }>;
  visual_change_pct?: number | null;
  visual_changed?: boolean;
  error?: string;
}

const INTERVAL_OPTIONS = [
  { value: 5, label: "5 min" },
  { value: 15, label: "15 min" },
  { value: 60, label: "hourly" },
  { value: 360, label: "6 hours" },
  { value: 1440, label: "daily" },
];

const FAIL_ON_OPTIONS = ["critical", "high", "medium", "low"];

const SEVERITY_COLORS: Record<string, string> = {
  critical: "text-red-400 bg-red-500/10",
  high: "text-orange-400 bg-orange-500/10",
  medium: "text-amber-400 bg-amber-500/10",
  low: "text-blue-400 bg-blue-500/10",
};

// ── Helpers ──────────────────────────────────────────────────────────

export function relativeTime(epochSeconds: number | null): string {
  if (!epochSeconds) return "never";
  const deltaSec = Math.max(0, Date.now() / 1000 - epochSeconds);
  if (deltaSec < 60) return "just now";
  if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)}m ago`;
  if (deltaSec < 86400) return `${Math.floor(deltaSec / 3600)}h ago`;
  return `${Math.floor(deltaSec / 86400)}d ago`;
}

export function intervalLabel(minutes: number): string {
  const known = INTERVAL_OPTIONS.find((o) => o.value === minutes);
  if (known) return known.label;
  if (minutes % 1440 === 0) return `${minutes / 1440}d`;
  if (minutes % 60 === 0) return `${minutes / 60}h`;
  return `${minutes}m`;
}

export function normalizeMonitorUrl(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return "";
  return /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
}

export function runScreenshotUrl(monitorId: number, runId: number): string {
  return `${engineOrigin()}/audit/monitors/${monitorId}/runs/${runId}/screenshot`;
}

export function runDiffUrl(monitorId: number, runId: number): string {
  return `${engineOrigin()}/audit/monitors/${monitorId}/runs/${runId}/diff`;
}

// ── Component ────────────────────────────────────────────────────────

export function MonitorsPanel() {
  const [monitors, setMonitors] = useState<Monitor[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  // Create form
  const [url, setUrl] = useState("");
  const [interval, setIntervalMinutes] = useState(1440);
  const [failOn, setFailOn] = useState("high");
  const [webhook, setWebhook] = useState("");
  const [runNow, setRunNow] = useState(true);

  // Per-monitor UI state
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [runsByMonitor, setRunsByMonitor] = useState<Record<number, MonitorRun[]>>({});
  const [busy, setBusy] = useState<Set<number>>(new Set());
  const [runResults, setRunResults] = useState<Record<number, RunResult>>({});

  const loadMonitors = useCallback(async () => {
    setLoading(true);
    try {
      const res = await localFetch("/audit/monitors");
      const data = await res.json();
      setMonitors(data.monitors || []);
      setError(null);
    } catch (e) {
      console.error(e);
      setError("Could not load monitors — is the engine running?");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadMonitors();
  }, [loadMonitors]);

  const createMonitor = async () => {
    const normalized = normalizeMonitorUrl(url);
    if (!normalized || creating) return;
    setCreating(true);
    setError(null);
    try {
      const res = await localFetch("/audit/monitors", {
        method: "POST",
        body: JSON.stringify({
          url: normalized,
          interval_minutes: interval,
          fail_on: failOn,
          run_now: runNow,
          ...(webhook.trim() ? { webhook_url: webhook.trim() } : {}),
        }),
      });
      if (!res.ok) {
        const detail = await res.text();
        throw new Error(detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      if (data.initial_run && data.monitor) {
        setRunResults((prev) => ({ ...prev, [data.monitor.id]: data.initial_run }));
      }
      setUrl("");
      setWebhook("");
      await loadMonitors();
    } catch (e) {
      setError(`Could not create monitor: ${String(e)}`);
    } finally {
      setCreating(false);
    }
  };

  const withBusy = async (id: number, fn: () => Promise<void>) => {
    setBusy((prev) => new Set(prev).add(id));
    try {
      await fn();
    } finally {
      setBusy((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  };

  const runMonitor = (id: number) =>
    withBusy(id, async () => {
      try {
        const res = await localFetch(`/audit/monitors/${id}/run`, { method: "POST" });
        const result: RunResult = await res.json();
        setRunResults((prev) => ({ ...prev, [id]: result }));
        await loadMonitors();
        if (expanded.has(id)) await loadRuns(id);
      } catch (e) {
        setError(`Run failed: ${String(e)}`);
      }
    });

  const toggleEnabled = (monitor: Monitor) =>
    withBusy(monitor.id, async () => {
      try {
        await localFetch(`/audit/monitors/${monitor.id}`, {
          method: "PATCH",
          body: JSON.stringify({ enabled: !monitor.enabled }),
        });
        await loadMonitors();
      } catch (e) {
        setError(`Update failed: ${String(e)}`);
      }
    });

  const removeMonitor = (id: number) =>
    withBusy(id, async () => {
      try {
        await localFetch(`/audit/monitors/${id}`, { method: "DELETE" });
        setExpanded((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
        await loadMonitors();
      } catch (e) {
        setError(`Delete failed: ${String(e)}`);
      }
    });

  const loadRuns = async (id: number) => {
    try {
      const res = await localFetch(`/audit/monitors/${id}/runs?limit=10`);
      const data = await res.json();
      setRunsByMonitor((prev) => ({ ...prev, [id]: data.runs || [] }));
    } catch (e) {
      console.error(e);
    }
  };

  const toggleHistory = async (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    if (!runsByMonitor[id]) await loadRuns(id);
  };

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="border-b border-border p-3">
        <div className="mb-2 flex items-center gap-2">
          <Radar size={18} className="text-accent" />
          <span className="font-semibold">Monitors</span>
          <span className="text-[10px] text-muted-foreground/60">
            recurring audits · regression alerts
          </span>
        </div>
        <p className="text-xs text-muted-foreground">
          Re-audits each site on a schedule and alerts only when new findings
          at or above the threshold appear. The first run is a baseline.
        </p>
      </div>

      <div className="flex-1 overflow-y-auto p-3">
        {/* Create form */}
        <div className="mb-4 rounded-md border border-border bg-card p-2">
          <div className="flex gap-2">
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && createMonitor()}
              placeholder="https://app.example.com"
              aria-label="URL to monitor"
              className="flex-1 rounded-md border border-border bg-background px-2 py-1 text-sm outline-none"
            />
            <Button
              size="icon"
              className="h-8 w-8"
              onClick={createMonitor}
              disabled={!url.trim() || creating}
              aria-label="Add monitor"
            >
              <Plus size={14} />
            </Button>
          </div>

          <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
            <select
              value={interval}
              onChange={(e) => setIntervalMinutes(Number(e.target.value))}
              aria-label="Run interval"
              className="rounded border border-border bg-background px-1.5 py-0.5"
            >
              {INTERVAL_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
            <select
              value={failOn}
              onChange={(e) => setFailOn(e.target.value)}
              aria-label="Alert threshold"
              className="rounded border border-border bg-background px-1.5 py-0.5"
            >
              {FAIL_ON_OPTIONS.map((s) => (
                <option key={s} value={s}>alert on {s}+</option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => setShowAdvanced((v) => !v)}
              className="ml-auto text-[11px] text-muted-foreground/70 hover:text-foreground"
            >
              {showAdvanced ? "hide" : "webhook…"}
            </button>
            <label className="flex items-center gap-1">
              <input
                type="checkbox"
                checked={runNow}
                onChange={(e) => setRunNow(e.target.checked)}
              />
              baseline now
            </label>
          </div>

          {showAdvanced && (
            <div className="mt-2">
              <input
                value={webhook}
                onChange={(e) => setWebhook(e.target.value)}
                placeholder="Webhook URL (Slack-compatible POST on regression)"
                aria-label="Webhook URL"
                className="w-full rounded-md border border-border bg-background px-2 py-1 text-xs outline-none"
              />
            </div>
          )}

          {error && (
            <p className="mt-2 text-[11px] text-red-400" role="alert">{error}</p>
          )}
        </div>

        {/* List */}
        <AnimatePresence initial={false}>
          {monitors.map((m, i) => {
            const isBusy = busy.has(m.id);
            const isExpanded = expanded.has(m.id);
            const result = runResults[m.id];
            return (
              <motion.div
                key={m.id}
                layout
                initial={{ opacity: 0, y: 4, scale: 0.97 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: -4, scale: 0.97 }}
                transition={{ duration: 0.18, delay: Math.min(i * 0.04, 0.4) }}
                className="mb-2 rounded-md border border-border bg-card p-2 text-xs"
              >
                <div className="flex items-start gap-2">
                  <span
                    className={`mt-1 h-2 w-2 shrink-0 rounded-full ${
                      m.enabled
                        ? m.last_status === "error" ? "bg-red-400" : "bg-emerald-400"
                        : "bg-muted-foreground/40"
                    }`}
                    title={m.enabled ? (m.last_status || "enabled") : "disabled"}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="truncate font-medium" title={m.url}>{m.url}</span>
                      <a
                        href={m.url}
                        target="_blank"
                        rel="noreferrer"
                        className="shrink-0 text-muted-foreground/50 hover:text-foreground"
                        aria-label={`Open ${m.url}`}
                      >
                        <ExternalLink size={10} />
                      </a>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-muted-foreground">
                      <span className="rounded bg-muted px-1.5 py-0.5">{m.mode}</span>
                      <span>every {intervalLabel(m.interval_minutes)}</span>
                      <span className={`rounded px-1.5 py-0.5 ${SEVERITY_COLORS[m.fail_on] || "bg-muted"}`}>
                        {m.fail_on}+
                      </span>
                      {m.webhook_url && <span title={m.webhook_url}>webhook</span>}
                      <span>
                        last: {relativeTime(m.last_run_at)}
                        {m.last_finding_count != null && ` · ${m.last_finding_count} findings`}
                      </span>
                    </div>
                    {m.last_error && (
                      <p className="mt-1 truncate text-[10px] text-red-400" title={m.last_error}>
                        {m.last_error}
                      </p>
                    )}
                    {result && (
                      <p className={`mt-1 text-[10px] ${result.alerted || result.visual_changed ? "text-orange-400" : "text-emerald-400"}`}>
                        run: {result.baseline ? "baseline" : `${result.new_findings ?? 0} new / ${result.resolved_findings ?? 0} resolved`}
                        {result.visual_change_pct != null && !result.baseline
                          ? ` · visual ${result.visual_change_pct.toFixed(2)}%`
                          : ""}
                        {result.alerted ? " — alerted" : ""}
                        {result.visual_changed ? " — visual change" : ""}
                      </p>
                    )}
                  </div>

                  <div className="flex shrink-0 items-center gap-0.5">
                    <Button
                      variant="ghost" size="icon" className="h-7 w-7"
                      onClick={() => runMonitor(m.id)} disabled={isBusy}
                      aria-label={`Run monitor ${m.id} now`} title="Run now"
                    >
                      <Play size={13} className={isBusy ? "animate-pulse text-accent" : ""} />
                    </Button>
                    <Button
                      variant="ghost" size="icon" className="h-7 w-7"
                      onClick={() => toggleEnabled(m)}
                      aria-label={m.enabled ? `Disable monitor ${m.id}` : `Enable monitor ${m.id}`}
                      title={m.enabled ? "Disable" : "Enable"}
                    >
                      <Power size={13} className={m.enabled ? "text-emerald-400" : "text-muted-foreground/40"} />
                    </Button>
                    <Button
                      variant="ghost" size="icon" className="h-7 w-7"
                      onClick={() => toggleHistory(m.id)}
                      aria-label={`Toggle run history for monitor ${m.id}`}
                      title="Run history"
                    >
                      {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    </Button>
                    <Button
                      variant="ghost" size="icon" className="h-7 w-7"
                      onClick={() => removeMonitor(m.id)} disabled={isBusy}
                      aria-label={`Delete monitor ${m.id}`} title="Delete"
                    >
                      <Trash2 size={13} className="text-red-400" />
                    </Button>
                  </div>
                </div>

                <AnimatePresence initial={false}>
                  {isExpanded && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: "auto", opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.15 }}
                      className="overflow-hidden"
                    >
                      <div className="mt-2 border-t border-border/50 pt-2">
                        {(runsByMonitor[m.id] || []).length === 0 ? (
                          <p className="text-[11px] text-muted-foreground">
                            No runs yet — press ▶ to run now.
                          </p>
                        ) : (
                          <ul className="space-y-1">
                            {(runsByMonitor[m.id] || []).map((r) => (
                              <li key={r.id} className="flex items-center gap-2 text-[11px]">
                                <span className="w-24 shrink-0 text-muted-foreground">
                                  {relativeTime(r.run_at)}
                                </span>
                                {r.status === "error" ? (
                                  <span className="text-red-400">error: {r.error}</span>
                                ) : (
                                  <>
                                    <span className="text-muted-foreground">
                                      {r.total_findings} findings
                                    </span>
                                    {r.new_findings > 0 && (
                                      <span className="text-orange-400">+{r.new_findings} new</span>
                                    )}
                                    {r.resolved_findings > 0 && (
                                      <span className="text-emerald-400">−{r.resolved_findings} resolved</span>
                                    )}
                                    {r.visual_change_pct != null && r.visual_change_pct > 0 && (
                                      <span className="text-violet-400">
                                        visual {r.visual_change_pct.toFixed(2)}%
                                      </span>
                                    )}
                                    {r.has_screenshot && (
                                      <a
                                        href={runScreenshotUrl(m.id, r.id)}
                                        target="_blank"
                                        rel="noreferrer"
                                        aria-label={`Open screenshot for run ${r.id}`}
                                        title="Open run screenshot"
                                        className="shrink-0 overflow-hidden rounded border border-border/50"
                                      >
                                        <img
                                          src={runScreenshotUrl(m.id, r.id)}
                                          alt={`Screenshot of run ${r.id}`}
                                          className="h-10 w-auto"
                                          loading="lazy"
                                        />
                                      </a>
                                    )}
                                    {r.visual_change_pct != null && r.has_screenshot && (
                                      <a
                                        href={runDiffUrl(m.id, r.id)}
                                        target="_blank"
                                        rel="noreferrer"
                                        aria-label={`Open visual diff for run ${r.id}`}
                                        title="Open heatmap of what changed since the previous run"
                                        className="shrink-0 text-violet-400 underline decoration-dotted underline-offset-2"
                                      >
                                        diff
                                      </a>
                                    )}
                                    {r.baseline && (
                                      <span className="rounded bg-muted px-1 py-0.5 text-muted-foreground">
                                        baseline
                                      </span>
                                    )}
                                  </>
                                )}
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </motion.div>
            );
          })}
        </AnimatePresence>

        {!loading && monitors.length === 0 && (
          <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
            <Radar size={24} className="mb-2 text-border" />
            <p className="text-sm">No monitors yet</p>
            <p className="text-xs">Enter a URL above to watch it for regressions.</p>
          </div>
        )}
      </div>

      <div className="border-t border-border p-3">
        <Button variant="outline" size="sm" className="w-full gap-1" onClick={loadMonitors}>
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} /> Refresh
        </Button>
      </div>
    </div>
  );
}
