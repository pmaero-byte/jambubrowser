import { useMemo } from "react";
import { motion } from "motion/react";
import { useDevtoolsStore, formatMs, formatBytes } from "../../store/devtoolsStore";

function scoreColor(value: number, type: "lcp" | "fcp" | "cls" | "ttfb"): string {
  // Web Vitals thresholds
  const thresholds: Record<string, [number, number]> = {
    lcp: [2500, 4000],    // ms: good, needs-improvement, poor
    fcp: [1800, 3000],
    ttfb: [800, 1800],
  };
  if (type === "cls") {
    if (value <= 0.1) return "text-green-400";
    if (value <= 0.25) return "text-yellow-400";
    return "text-red-400";
  }
  const [good, poor] = thresholds[type] || [2000, 4000];
  if (value <= good) return "text-green-400";
  if (value <= poor) return "text-yellow-400";
  return "text-red-400";
}

function scoreBg(value: number, type: "lcp" | "fcp" | "cls" | "ttfb"): string {
  const thresholds: Record<string, [number, number]> = {
    lcp: [2500, 4000],
    fcp: [1800, 3000],
    ttfb: [800, 1800],
  };
  if (type === "cls") {
    if (value <= 0.1) return "bg-green-500/15";
    if (value <= 0.25) return "bg-yellow-500/15";
    return "bg-red-500/15";
  }
  const [good, poor] = thresholds[type] || [2000, 4000];
  if (value <= good) return "bg-green-500/15";
  if (value <= poor) return "bg-yellow-500/15";
  return "bg-red-500/15";
}

function MetrixCard({
  label,
  value,
  unit,
  type,
}: {
  label: string;
  value: number | null;
  unit: string;
  type: "lcp" | "fcp" | "cls" | "ttfb";
}) {
  if (value === null || value === undefined) {
    return (
      <div className="rounded-lg border border-border bg-card p-3">
        <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">{label}</div>
        <div className="mt-1 text-lg font-semibold text-muted-foreground">—</div>
      </div>
    );
  }
  const display = type === "cls" ? value.toFixed(3) : formatMs(value);
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className={`rounded-lg border border-border ${scoreBg(value, type)} p-3`}
    >
      <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className={`mt-1 text-lg font-semibold ${scoreColor(value, type)}`}>
        {display}
        <span className="ml-1 text-sm font-normal text-muted-foreground">{unit}</span>
      </div>
    </motion.div>
  );
}

function TimingBar({ label, value, total }: { label: string; value: number; total: number }) {
  const pct = total > 0 ? (value / total) * 100 : 0;
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-28 shrink-0 text-right text-muted-foreground">{label}</span>
      <div className="relative h-3 flex-1 overflow-hidden rounded bg-muted">
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${Math.min(pct, 100)}%` }}
          transition={{ duration: 0.4, ease: "easeOut" }}
          className="absolute left-0 top-0 h-full rounded bg-blue-500/40"
        />
      </div>
      <span className="w-16 shrink-0 font-mono text-muted-foreground">{formatMs(value)}</span>
    </div>
  );
}

export function PerformanceTab() {
  const { perfSummary, longTasks, resources } = useDevtoolsStore();
  const { lcp, fcp, cls, navigation } = perfSummary;

  // Navigation timing waterfall
  const navEntries = useMemo(() => {
    if (!navigation) return [];
    const total = navigation.load || 1;
    return [
      { label: "DNS lookup", value: navigation.dnsTime, total },
      { label: "TCP connect", value: navigation.tcpTime, total },
      { label: "TTFB", value: navigation.ttfb, total },
      { label: "DOM Interactive", value: navigation.domInteractive, total },
      { label: "DOM Content Loaded", value: navigation.domContentLoaded, total },
      { label: "Full Load", value: navigation.load, total },
    ].filter((e) => e.value > 0);
  }, [navigation]);

  // Group resources by type
  const resourceBreakdown = useMemo(() => {
    const groups: Record<string, { count: number; size: number; time: number }> = {};
    for (const r of resources) {
      const t = r.initiatorType || "other";
      if (!groups[t]) groups[t] = { count: 0, size: 0, time: 0 };
      groups[t].count++;
      groups[t].size += r.transferSize || 0;
      groups[t].time += r.duration || 0;
    }
    return groups;
  }, [resources]);

  return (
    <div className="h-full overflow-y-auto p-4">
      {/* Core Web Vitals */}
      <div className="mb-6">
        <div className="mb-3 text-sm font-semibold">Core Web Vitals</div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <MetrixCard label="LCP" value={lcp?.renderTime ?? lcp?.loadTime ?? null} unit="ms" type="lcp" />
          <MetrixCard label="FCP" value={fcp?.startTime ?? null} unit="ms" type="fcp" />
          <MetrixCard label="CLS" value={cls?.value ?? null} unit="" type="cls" />
          <MetrixCard label="TTFB" value={navigation?.ttfb ?? null} unit="ms" type="ttfb" />
        </div>
        <div className="mt-3 text-[11px] text-muted-foreground">
          Last navigation: {navigation?.url ? new URL(navigation.url).hostname : "—"}
          {navigation?.type && navigation.type !== "navigate" ? ` (${navigation.type})` : ""}
        </div>
      </div>

      {/* Navigation Timing Waterfall */}
      {navEntries.length > 0 && (
        <div className="mb-6">
          <div className="mb-3 text-sm font-semibold">Navigation Timing</div>
          <div className="space-y-1">
            {navEntries.map((e) => (
              <TimingBar key={e.label} label={e.label} value={e.value} total={e.total} />
            ))}
          </div>
        </div>
      )}

      {/* Resource Breakdown */}
      {Object.keys(resourceBreakdown).length > 0 && (
        <div className="mb-6">
          <div className="mb-3 text-sm font-semibold">Resource Summary</div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {Object.entries(resourceBreakdown).map(([type, info]) => (
              <div key={type} className="rounded-lg border border-border bg-card/50 p-2.5 text-xs">
                <div className="mb-1 font-medium capitalize text-foreground">{type}</div>
                <div className="space-y-0.5 text-muted-foreground">
                  <div>{info.count} requests</div>
                  <div>{formatBytes(info.size)} transferred</div>
                  <div>{formatMs(info.time)} total</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Long Tasks */}
      {longTasks.length > 0 && (
        <div className="mb-6">
          <div className="mb-3 text-sm font-semibold">Long Tasks (&gt;50ms)</div>
          <div className="space-y-1">
            {longTasks.map((t, i) => (
              <div
                key={i}
                className="flex items-center justify-between rounded border border-border/50 bg-yellow-500/5 px-3 py-1.5 text-xs"
              >
                <span className="text-muted-foreground">{t.name || "Unnamed"}</span>
                <span className="font-mono text-yellow-400">{formatMs(t.duration)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Summary */}
      <div className="rounded-lg border border-border bg-card/30 p-3 text-xs text-muted-foreground">
        <div className="font-medium text-foreground">Summary</div>
        <div className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-4">
          <div>{perfSummary.totalResources} requests</div>
          <div>{formatBytes(perfSummary.totalTransferSize)} transferred</div>
          <div>{formatMs(perfSummary.totalDuration)} total time</div>
          <div>{longTasks.length} long tasks</div>
        </div>
      </div>
    </div>
  );
}
