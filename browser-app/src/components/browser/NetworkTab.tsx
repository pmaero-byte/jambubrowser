import { useState, useMemo } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Download } from "lucide-react";
import { useDevtoolsStore, formatBytes, formatMs } from "../../store/devtoolsStore";
import { buildHar, buildCsv, downloadTextFile } from "./networkExport";

function initiatorIcon(type: string): string {
  if (type === "fetch" || type === "xmlhttprequest") return "🌐";
  if (type === "img") return "🖼";
  if (type === "script") return "📜";
  if (type === "css") return "🎨";
  if (type === "link") return "🔗";
  if (type === "font") return "🔤";
  if (type === "iframe") return "📄";
  return "📦";
}

function domain(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

function pathOnly(url: string): string {
  try {
    const u = new URL(url);
    return u.pathname + u.search;
  } catch {
    return url.length > 60 ? url.slice(0, 60) + "…" : url;
  }
}

export function NetworkTab() {
  const { resources, navigation } = useDevtoolsStore();
  const [sortBy, setSortBy] = useState<"startTime" | "duration" | "transferSize">("startTime");
  const [filter, setFilter] = useState("");
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);

  const exportWaterfall = (fmt: "har" | "csv") => {
    if (resources.length === 0) return;
    // Export the filtered+sorted view the user is looking at.
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    if (fmt === "har") {
      downloadTextFile(
        JSON.stringify(buildHar(sorted, navigation), null, 2),
        `jambu-network-${stamp}.har`,
        "application/json",
      );
    } else {
      downloadTextFile(buildCsv(sorted), `jambu-network-${stamp}.csv`, "text/csv");
    }
  };

  const sorted = useMemo(() => {
    let list = [...resources];
    if (filter.trim()) {
      const q = filter.toLowerCase();
      list = list.filter((r) => r.name.toLowerCase().includes(q));
    }
    list.sort((a, b) => {
      if (sortBy === "startTime") return a.startTime - b.startTime;
      if (sortBy === "duration") return b.duration - a.duration;
      return b.transferSize - a.transferSize;
    });
    return list;
  }, [resources, sortBy, filter]);

  // Compute max start time for waterfall visualization
  const maxStart = useMemo(() => {
    if (sorted.length === 0) return 1;
    return Math.max(...sorted.map((r) => r.startTime + r.duration), 1);
  }, [sorted]);

  const selected = selectedIdx !== null ? sorted[selectedIdx] : null;

  return (
    <div className="flex h-full flex-col">
      {/* Toolbar */}
      <div className="flex items-center gap-2 border-b border-border px-2 py-1 text-xs">
        <span className="font-medium text-muted-foreground">{resources.length} requests</span>
        <span className="text-muted-foreground/50">|</span>
        <span className="text-muted-foreground">
          {formatBytes(resources.reduce((s, r) => s + (r.transferSize || 0), 0))}
        </span>
        <div className="flex-1" />
        <select
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value as any)}
          className="rounded border border-border bg-card px-1 py-0.5 text-xs outline-none"
        >
          <option value="startTime">By time</option>
          <option value="duration">By duration</option>
          <option value="transferSize">By size</option>
        </select>
        <input
          type="text"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter URLs…"
          className="w-32 rounded border border-border bg-card px-1.5 py-0.5 text-xs outline-none focus:border-accent"
        />
        {resources.length > 0 && (
          <>
            <button
              onClick={() => exportWaterfall("har")}
              title="Export the visible waterfall as a HAR 1.2 file (open in Chrome DevTools or any HTTP analyzer)"
              className="flex items-center gap-1 rounded border border-border bg-card px-1.5 py-0.5 text-[10px] text-muted-foreground transition-colors hover:border-accent hover:text-foreground"
            >
              <Download size={10} /> HAR
            </button>
            <button
              onClick={() => exportWaterfall("csv")}
              title="Export the visible waterfall as CSV"
              className="flex items-center gap-1 rounded border border-border bg-card px-1.5 py-0.5 text-[10px] text-muted-foreground transition-colors hover:border-accent hover:text-foreground"
            >
              <Download size={10} /> CSV
            </button>
          </>
        )}
      </div>

      {/* List / Detail split */}
      <div className="flex min-h-0 flex-1">
        {/* List */}
        <div className={`flex flex-col overflow-y-auto ${selected ? "w-1/2 border-r border-border" : "flex-1"}`}>
          {sorted.length === 0 ? (
            <div className="flex flex-1 items-center justify-center text-xs text-muted-foreground">
              No requests captured yet. Navigate to a page.
            </div>
          ) : (
            sorted.map((req, i) => {
              const barPct = maxStart > 0 ? ((req.startTime + req.duration) / maxStart) * 100 : 0;
              const startPct = maxStart > 0 ? (req.startTime / maxStart) * 100 : 0;
              const barW = barPct - startPct;
              return (
                <motion.button
                  key={`${req.name}-${req.startTime}-${i}`}
                  initial={{ opacity: 0, x: -4 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: 0.15, delay: Math.min(i * 0.003, 0.3) }}
                  onClick={() => setSelectedIdx(selectedIdx === i ? null : i)}
                  className={`flex items-center gap-2 border-b border-border/30 px-2 py-1 text-left text-xs transition-colors hover:bg-muted/50 ${
                    selectedIdx === i ? "bg-accent/10" : ""
                  }`}
                >
                  {/* Waterfall bar */}
                  <div className="relative h-3 w-16 shrink-0 overflow-hidden rounded bg-muted">
                    <div
                      className="absolute top-0 h-full rounded bg-blue-500/40"
                      style={{ left: `${startPct}%`, width: `${Math.max(barW, 2)}%` }}
                    />
                  </div>
                  <span className="w-4 shrink-0 text-center">{initiatorIcon(req.initiatorType)}</span>
                  <span className="w-8 shrink-0 font-mono text-[10px] text-muted-foreground">
                    {req.duration > 0 ? `${req.duration.toFixed(0)}ms` : "—"}
                  </span>
                  <span className="flex-1 truncate text-muted-foreground">{pathOnly(req.name)}</span>
                  <span className="hidden w-16 truncate text-right font-mono text-[10px] text-muted-foreground sm:block">
                    {domain(req.name)}
                  </span>
                  <span className="w-12 text-right font-mono text-[10px] text-muted-foreground">
                    {formatBytes(req.transferSize)}
                  </span>
                  <span className="w-6 text-center font-mono text-[10px] text-muted-foreground">
                    {req.nextHopProtocol?.startsWith("h2") ? "h2" : req.nextHopProtocol?.startsWith("h3") ? "h3" : ""}
                  </span>
                </motion.button>
              );
            })
          )}
        </div>

        {/* Detail panel */}
        <AnimatePresence>
          {selected && (
            <motion.div
              initial={{ width: 0, opacity: 0 }}
              animate={{ width: "50%", opacity: 1 }}
              exit={{ width: 0, opacity: 0 }}
              transition={{ duration: 0.18 }}
              className="overflow-y-auto bg-card/50 p-3 text-xs"
            >
              <div className="mb-3 font-semibold text-foreground">Request Details</div>

              <Section label="URL">
                <code className="break-all text-muted-foreground">{selected.name}</code>
              </Section>

              <Section label="Initiator">{selected.initiatorType}</Section>

              <Section label="Protocol">{selected.nextHopProtocol || "—"}</Section>

              <div className="mb-3 mt-4 font-semibold text-foreground">Timing</div>

              <div className="space-y-1">
                <TimingRow label="Total duration" value={selected.duration} />
                <TimingRow label="TTFB" value={selected.ttfb} />
                <TimingRow label="DNS lookup" value={selected.dnsEnd - selected.dnsStart} />
                <TimingRow label="TCP connect" value={selected.connectEnd - selected.connectStart} />
                <TimingRow label="Response download" value={selected.responseEnd - selected.responseStart} />
              </div>

              <div className="mb-3 mt-4 font-semibold text-foreground">Size</div>

              <div className="space-y-1">
                <SizeRow label="Transferred" bytes={selected.transferSize} />
                <SizeRow label="Encoded body" bytes={selected.encodedBodySize} />
                <SizeRow label="Decoded body" bytes={selected.decodedBodySize} />
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-2">
      <div className="mb-0.5 font-medium text-muted-foreground">{label}</div>
      <div className="rounded bg-muted/50 px-2 py-1">{children}</div>
    </div>
  );
}

function TimingRow({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono text-foreground">{formatMs(value)}</span>
    </div>
  );
}

function SizeRow({ label, bytes }: { label: string; bytes: number }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono text-foreground">{formatBytes(bytes)}</span>
    </div>
  );
}
