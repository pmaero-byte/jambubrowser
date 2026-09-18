import { useCallback, useEffect, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { FlaskConical, Plus, RefreshCw, Play, Trash2, Power } from "lucide-react";
import { Button } from "../ui/button";
import { localFetch } from "../../utils/api";

// ── Types ────────────────────────────────────────────────────────────

interface FlowMonitor {
  id: number;
  name: string;
  url: string;
  steps: Array<Record<string, unknown>>;
  local: boolean;
  approve: boolean;
  network: Record<string, unknown> | null;
  interval_minutes: number;
  webhook_url: string | null;
  enabled: boolean;
  created_at: number;
  last_run_at: number | null;
  last_status: string | null;
}

interface FlowRun {
  id: number;
  monitor_id: number;
  run_at: number;
  status: string;
  ok: boolean;
  passed: number;
  failed: number;
  total: number;
  duration_ms: number;
  failed_steps: Array<{ i?: number; action?: string; reason?: string; error?: string }>;
  console_errors: string[];
  error: string | null;
}

const INTERVAL_OPTIONS = [
  { value: 15, label: "15 min" },
  { value: 60, label: "hourly" },
  { value: 360, label: "6 hours" },
  { value: 1440, label: "daily" },
];

const DEFAULT_STEPS = JSON.stringify(
  [
    { action: "assert_console_clean" },
    { action: "assert_no_failed_requests" },
  ],
  null,
  1,
);

export function relativeTime(epochSeconds: number | null): string {
  if (!epochSeconds) return "never";
  const deltaSec = Math.max(0, Date.now() / 1000 - epochSeconds);
  if (deltaSec < 60) return "just now";
  if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)}m ago`;
  if (deltaSec < 86400) return `${Math.floor(deltaSec / 3600)}h ago`;
  return `${Math.floor(deltaSec / 86400)}d ago`;
}

// ── Panel ────────────────────────────────────────────────────────────

export function FlowMonitorsPanel() {
  const [monitors, setMonitors] = useState<FlowMonitor[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [stepsText, setStepsText] = useState(DEFAULT_STEPS);
  const [stepsError, setStepsError] = useState<string | null>(null);
  const [interval, setIntervalMinutes] = useState(1440);
  const [webhook, setWebhook] = useState("");
  const [local, setLocal] = useState(true);

  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [runsByMonitor, setRunsByMonitor] = useState<Record<number, FlowRun[]>>({});
  const [busy, setBusy] = useState<Set<number>>(new Set());

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await localFetch("/browser/monitors");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = await res.json();
      setMonitors(body.monitors ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const parseSteps = (): Array<Record<string, unknown>> | null => {
    try {
      const parsed: unknown = JSON.parse(stepsText);
      const steps = Array.isArray(parsed) ? parsed : (parsed as { steps?: unknown }).steps;
      if (!Array.isArray(steps) || steps.length === 0) {
        setStepsError("Steps must be a non-empty JSON array.");
        return null;
      }
      setStepsError(null);
      return steps as Array<Record<string, unknown>>;
    } catch {
      setStepsError("Steps must be valid JSON.");
      return null;
    }
  };

  const create = useCallback(async () => {
    const steps = parseSteps();
    if (!steps || !name.trim() || !url.trim()) return;
    setBusy((b) => new Set(b).add(-1));
    try {
      const res = await localFetch("/browser/monitors", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(), url: url.trim(), steps, local,
          interval_minutes: interval, webhook_url: webhook.trim() || null,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setName(""); setUrl(""); setWebhook(""); setStepsText(DEFAULT_STEPS);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy((b) => { const n = new Set(b); n.delete(-1); return n; });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name, url, stepsText, local, interval, webhook, refresh]);

  const toggle = useCallback(async (monitor: FlowMonitor) => {
    await localFetch(`/browser/monitors/${monitor.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !monitor.enabled }),
    });
    await refresh();
  }, [refresh]);

  const remove = useCallback(async (id: number) => {
    await localFetch(`/browser/monitors/${id}`, { method: "DELETE" });
    await refresh();
  }, [refresh]);

  const runNow = useCallback(async (id: number) => {
    setBusy((b) => new Set(b).add(id));
    try {
      await localFetch(`/browser/monitors/${id}/run`, { method: "POST" });
    } finally {
      setBusy((b) => { const n = new Set(b); n.delete(-1); n.delete(id); return n; });
    }
    await refresh();
    // Reload the run history if the row is expanded.
    if (expanded.has(id)) {
      const res = await localFetch(`/browser/monitors/${id}/runs?limit=10`);
      if (res.ok) {
        const body = await res.json();
        setRunsByMonitor((m) => ({ ...m, [id]: body.runs ?? [] }));
      }
    }
  }, [refresh, expanded]);

  const toggleExpanded = useCallback(async (id: number) => {
    const next = new Set(expanded);
    if (next.has(id)) {
      next.delete(id);
    } else {
      next.add(id);
      if (!runsByMonitor[id]) {
        const res = await localFetch(`/browser/monitors/${id}/runs?limit=10`);
        if (res.ok) {
          const body = await res.json();
          setRunsByMonitor((m) => ({ ...m, [id]: body.runs ?? [] }));
        }
      }
    }
    setExpanded(next);
  }, [expanded, runsByMonitor]);

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-4">
      <div>
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <FlaskConical size={14} /> Flow tests
        </h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Recurring agent test flows. Each run executes the stored steps in a
          real browser and alerts on failure.
        </p>
      </div>

      {error && <p className="text-xs text-red-400">{error}</p>}

      <div className="rounded-lg border border-border/50 p-3">
        <div className="grid grid-cols-2 gap-2">
          <input value={name} onChange={(e) => setName(e.target.value)}
            placeholder="Name (e.g. login smoke)" className="rounded bg-background/60 px-2 py-1 text-xs outline-none" />
          <input value={url} onChange={(e) => setUrl(e.target.value)}
            placeholder="https://… or http://localhost:3000" className="rounded bg-background/60 px-2 py-1 text-xs outline-none" />
        </div>
        <textarea value={stepsText} onChange={(e) => setStepsText(e.target.value)} rows={4}
          spellCheck={false}
          className="mt-2 w-full rounded bg-background/60 px-2 py-1 font-mono text-[11px] outline-none" />
        {stepsError && <p className="mt-1 text-[11px] text-red-400">{stepsError}</p>}
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <select value={interval} onChange={(e) => setIntervalMinutes(Number(e.target.value))}
            className="rounded bg-background/60 px-2 py-1 text-xs">
            {INTERVAL_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <label className="flex items-center gap-1 text-xs text-muted-foreground">
            <input type="checkbox" checked={local} onChange={(e) => setLocal(e.target.checked)} />
            localhost
          </label>
          <input value={webhook} onChange={(e) => setWebhook(e.target.value)}
            placeholder="webhook URL (optional)" className="min-w-0 flex-1 rounded bg-background/60 px-2 py-1 text-xs outline-none" />
          <Button size="sm" onClick={create} disabled={busy.has(-1) || !name.trim() || !url.trim()}>
            <Plus size={12} /> Add
          </Button>
        </div>
      </div>

      {loading && <p className="text-xs text-muted-foreground">Loading…</p>}
      <div className="flex flex-col gap-2">
        {monitors.map((m) => (
          <div key={m.id} className="rounded-lg border border-border/50 p-2.5">
            <div className="flex items-center gap-2">
              <button type="button" onClick={() => toggleExpanded(m.id)}
                className="text-xs font-medium hover:underline" aria-label={m.enabled ? "expanded" : "collapsed"}>
                {m.name}
              </button>
              <span className={`rounded px-1.5 py-0.5 text-[10px] ${m.last_status === "passed" ? "bg-emerald-500/10 text-emerald-300" : m.last_status ? "bg-red-500/10 text-red-300" : "bg-muted text-muted-foreground"}`}>
                {m.last_status ?? "never run"}
              </span>
              {!m.enabled && (
                <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">disabled</span>
              )}
              <span className="ml-auto text-[11px] text-muted-foreground">{relativeTime(m.last_run_at)}</span>
              <Button variant="ghost" size="icon" className="h-6 w-6" title="Run now"
                onClick={() => runNow(m.id)} disabled={busy.has(m.id)}>
                <Play size={12} className={busy.has(m.id) ? "animate-spin" : ""} />
              </Button>
              <Button variant="ghost" size="icon" className="h-6 w-6" title={m.enabled ? "Disable" : "Enable"}
                onClick={() => toggle(m)}>
                <Power size={12} />
              </Button>
              <Button variant="ghost" size="icon" className="h-6 w-6" title="Delete"
                onClick={() => remove(m.id)}>
                <Trash2 size={12} />
              </Button>
            </div>
            <p className="mt-0.5 truncate text-[11px] text-muted-foreground">{m.url} · every {m.interval_minutes}m · {m.steps.length} steps</p>
            <AnimatePresence>
              {expanded.has(m.id) && (
                <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                  className="mt-2 border-t border-border/30 pt-2">
                  {(runsByMonitor[m.id] ?? []).length === 0 && (
                    <p className="text-[11px] text-muted-foreground">No runs yet.</p>
                  )}
                  {(runsByMonitor[m.id] ?? []).map((r) => (
                    <div key={r.id} className="flex items-center gap-2 py-0.5 text-[11px]">
                      <span className={r.ok ? "text-emerald-300" : "text-red-300"}>
                        {r.ok ? "PASS" : "FAIL"}
                      </span>
                      <span className="text-muted-foreground">{r.passed}/{r.total} steps · {r.duration_ms}ms · {relativeTime(r.run_at)}</span>
                      {!r.ok && r.failed_steps.length > 0 && (
                        <span className="truncate text-muted-foreground">
                          #{r.failed_steps[0].i} {r.failed_steps[0].action}: {r.failed_steps[0].reason}
                        </span>
                      )}
                      {r.error && <span className="truncate text-red-400">{r.error}</span>}
                    </div>
                  ))}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        ))}
        {monitors.length === 0 && !loading && (
          <p className="text-xs text-muted-foreground">No flow monitors yet — add one above.</p>
        )}
      </div>

      <Button variant="ghost" size="sm" className="self-start" onClick={() => { void refresh(); }}>
        <RefreshCw size={12} /> Refresh
      </Button>
    </div>
  );
}
