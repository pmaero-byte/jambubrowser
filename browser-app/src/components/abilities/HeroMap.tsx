import { motion } from "motion/react";
import { Globe, Bot, Radar, Layers, Network, Monitor, ArrowDown } from "lucide-react";
import { useAppStore } from "../../store/appStore";

// A self-contained "visual abilities home" piece: a concrete macOS window
// outline, live pillars connected by animated lanes, and a short summary of
// what each pillar owns. Pure SVG so it reads at any size.

export function HeroMap({
  engineUp,
  counts,
}: {
  engineUp: boolean | null;
  counts: Record<string, number>;
}) {
  const { setActiveTab } = useAppStore();
  const pillars = [
    { id: "browser", label: "Browser", icon: Globe, color: "#38bdf8", tab: "browser" as const },
    { id: "harness", label: "Harness", icon: Bot, color: "#a78bfa", tab: "chat" as const },
    { id: "deepnet", label: "DeepNet", icon: Radar, color: "#34d399", tab: "privacy" as const },
    { id: "developer", label: "Developer", icon: Layers, color: "#fbbf24", tab: "logs" as const },
    { id: "decentralized", label: "Decentralized", icon: Network, color: "#22d3ee", tab: "dcm-node" as const },
    { id: "desktop", label: "Desktop", icon: Monitor, color: "#fb7185", tab: "browser" as const },
  ];

  return (
    <div className="rounded-xl border border-border/40 bg-gradient-to-b from-surface to-background p-4">
      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[1fr_auto]">
        <div>
          <h2 className="text-sm font-semibold text-foreground/90">A browser with a nervous system</h2>
          <p className="mt-1 max-w-xl text-[12px] leading-relaxed text-muted-foreground/80">
            A Chromium pane, an AI research harness, a privacy stack, and a hash-chained audit engine —
            all behind one desktop shell. Green lane = the Python engine is answering on :8001.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2 text-[10px]">
            <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 ${engineUp === true ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200" : engineUp === false ? "border-red-500/40 bg-red-500/10 text-red-200" : "border-border/50 bg-muted/20 text-muted-foreground/70"}`}>
              <span className={`h-1.5 w-1.5 rounded-full ${engineUp === true ? "bg-emerald-400" : engineUp === false ? "bg-red-400" : "bg-muted-foreground/40"}`} />
              engine {engineUp === null ? "…" : engineUp ? "up" : "down"}
            </span>
            <span className="inline-flex items-center gap-1 rounded-full border border-border/50 bg-muted/20 px-2 py-0.5 text-muted-foreground/70">
              Tauri desktop · local test
            </span>
          </div>
        </div>

        {/* Schematic: macOS window outline with pillar lanes */}
        <svg width="280" height="170" viewBox="0 0 280 170" role="img" aria-label="Jambubrowser schematic">
          {/* window chrome */}
          <rect x="8" y="8" width="264" height="154" rx="12" fill="rgba(148,163,184,0.08)" stroke="rgba(148,163,184,0.35)" />
          <rect x="8" y="8" width="264" height="26" rx="12" fill="rgba(148,163,184,0.14)" stroke="rgba(148,163,184,0.35)" />
          <circle cx="22" cy="21" r="4" fill="#f87171" />
          <circle cx="36" cy="21" r="4" fill="#facc15" />
          <circle cx="50" cy="21" r="4" fill="#34d399" />
          <text x="140" y="25" textAnchor="middle" fill="rgba(226,232,240,0.85)" fontSize="10" fontFamily="ui-sans-serif, system-ui">Jambubrowser</text>

          {/* pillar nodes */}
          {pillars.map((p, i) => {
            const col = i % 3;
            const row = Math.floor(i / 3);
            const cx = 44 + col * 96;
            const cy = 66 + row * 56;
            const Icon = p.icon;
            return (
              <g key={p.id} transform={`translate(${cx}, ${cy})`}>
                <motion.path
                  d="M -26 0 H 26"
                  stroke={p.color}
                  strokeWidth="1"
                  strokeOpacity="0.25"
                  fill="none"
                />
                <motion.circle
                  r="14"
                  fill={p.color}
                  fillOpacity="0.1"
                  stroke={p.color}
                  strokeWidth="1.2"
                  initial={{ opacity: 0, scale: 0.6 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ delay: i * 0.06 }}
                />
                <g transform="translate(-7,-7) scale(0.58)">
                  <Icon size={24} color={p.color} />
                </g>
                <text y="28" textAnchor="middle" fill="rgba(203,213,225,0.85)" fontSize="9" fontFamily="ui-sans-serif, system-ui">
                  {p.label}
                </text>
                <text y="40" textAnchor="middle" fill="rgba(148,163,184,0.7)" fontSize="8" fontFamily="ui-sans-serif, system-ui">
                  {counts[p.id] ?? 0} abilities
                </text>
              </g>
            );
          })}
        </svg>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-1.5 text-[10px] sm:grid-cols-3">
        {pillars.map((p) => (
          <button
            key={p.id}
            onClick={() => setActiveTab(p.tab)}
            className="inline-flex items-center gap-1 rounded-md border border-border/40 bg-background/40 px-2 py-1 text-left text-muted-foreground/80 transition-colors hover:bg-muted/20 hover:text-foreground"
          >
            <p.icon size={10} />
            <span className="min-w-0 flex-1 truncate">{p.label}</span>
            <ArrowDown size={9} className="opacity-50" />
          </button>
        ))}
      </div>
    </div>
  );
}
