import { useEffect, useMemo, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  Globe, Bot, Shield, Radar, Network, Coins, Brain, Users, Activity,
  Download, Magnet, FlaskConical, History, Video, Puzzle,
  Wallet, ScrollText, FolderKanban, Target, Cpu, Wifi, Layers, Monitor,
  Radio,
} from "lucide-react";
import { useAppStore, type CanvasTab } from "../../store/appStore";
import { localFetch } from "../../utils/api";
import { HeroMap } from "./HeroMap";

// ── Static taxonomy of everything the product can do ───────────────
// Mirrors docs/FEATURE_MAP.md pillars; each entry maps 1:1 to a panel
// (CanvasTab) so one click takes you to the live UI for that ability.

type Pillar = "browser" | "harness" | "deepnet" | "developer" | "decentralized" | "desktop";

const PILLARS: { id: Pillar; label: string; tagline: string; icon: React.ElementType; accent: string }[] = [
  { id: "browser", label: "Browser", tagline: "Eyes & hands on the live web — real DOM, real network, real downloads", icon: Globe, accent: "from-sky-500/20 to-sky-500/5 border-sky-500/30" },
  { id: "harness", label: "Harness", tagline: "Multi-agent orchestration — 8 LLM providers, ReAct loop, memory", icon: Bot, accent: "from-violet-500/20 to-violet-500/5 border-violet-500/30" },
  { id: "deepnet", label: "DeepNet", tagline: "Reach what the surface web hides — metasearch, Tor, knowledge graph", icon: Radar, accent: "from-emerald-500/20 to-emerald-500/5 border-emerald-500/30" },
  { id: "developer", label: "Developer", tagline: "Embed Jambubrowser — CLI, MCP, eval, plugins, CI gate", icon: Layers, accent: "from-amber-500/20 to-amber-500/5 border-amber-500/30" },
  { id: "decentralized", label: "Decentralized", tagline: "Mesh compute, DCM inference, MeshPay settlement, x402 paywall", icon: Network, accent: "from-cyan-500/20 to-cyan-500/5 border-cyan-500/30" },
  { id: "desktop", label: "Desktop", tagline: "Tauri + Chromium — native tabs, screencast, segmented downloads, torrents", icon: Monitor, accent: "from-rose-500/20 to-rose-500/5 border-rose-500/30" },
];

interface Ability {
  id: string;
  title: string;
  desc: string;
  tab: CanvasTab;
  pillar: Pillar;
  icon: React.ElementType;
  /** Engine endpoint probed for the "live" dot (GET, must be cheap). */
  probe?: string;
  /** One-line "how to demo" hint rendered in the detail row. */
  demo: string;
  tags: string[];
}

const ABILITIES: Ability[] = [
  // Browser pillar
  { id: "browser-live", title: "Live Chromium pane", desc: "Real tabs driven over CDP with 30–60 FPS screencast", tab: "browser", pillar: "browser", icon: Globe, probe: "/health", demo: "Open a URL, watch frames stream, toggle DevTools", tags: ["cdp", "tabs"] },
  { id: "browser-qa", title: "Browser testing", desc: "One-call declarative flows: click/type/assert + telemetry", tab: "qa", pillar: "browser", icon: FlaskConical, demo: "Run a flow, inspect receipts/HAR/video", tags: ["playwright", "qa"] },
  { id: "browser-audit", title: "6 AI auditors", desc: "Security/Perf/UX/SEO/A11y/Code-Quality over the live page", tab: "audit", pillar: "browser", icon: Shield, demo: "Run audit, export SARIF/HTML, share link", tags: ["audit", "sarif"] },
  { id: "browser-monitors", title: "Regression monitors", desc: "Recurring audits + visual-diff alerts", tab: "monitors", pillar: "browser", icon: Radar, probe: "/audit/monitors", demo: "Add monitor, run now, see deltas", tags: ["visual-diff"] },
  { id: "browser-downloads", title: "Accelerated downloads", desc: "Segmented HTTP (parallel ranges) + BitTorrent P2P, pause/resume", tab: "browser", pillar: "desktop", icon: Download, demo: "Add a big file URL or magnet, watch per-chunk progress", tags: ["http", "torrent"] },
  { id: "browser-torrents", title: "Torrents", desc: "Magnet / .torrent via rqbit DHT into Downloads/JambuBrowser", tab: "browser", pillar: "desktop", icon: Magnet, demo: "Paste a magnet, watch peer count + speed", tags: ["p2p", "dht"] },
  { id: "browser-devtools", title: "DevTools panel", desc: "Console, Network (HAR/CSV), Performance, Elements, Storage", tab: "browser", pillar: "browser", icon: Activity, demo: "Open DevTools on the browser pane", tags: ["har", "csv"] },
  { id: "browser-recordings", title: "Record / replay", desc: "Scripted runs with per-step results", tab: "recordings", pillar: "browser", icon: Video, demo: "Record a flow, replay with verdicts", tags: ["replay"] },
  { id: "browser-history", title: "Visit history", desc: "Tracked URLs with timestamps", tab: "history", pillar: "browser", icon: History, demo: "Browse, then open the History panel", tags: ["history"] },

  // Harness pillar
  { id: "harness-chat", title: "Research chat", desc: "Streaming answers with sources + agent run summary", tab: "chat", pillar: "harness", icon: Bot, probe: "/v2/llm/chat", demo: "Ask a research question, watch SSE", tags: ["sse", "llm"] },
  { id: "harness-agents", title: "Agent view", desc: "ReAct steps, telemetry, cost, plan library", tab: "agent", pillar: "harness", icon: Cpu, probe: "/agent/plan-library", demo: "Watch plan → execute → verify", tags: ["react"] },
  { id: "harness-memory", title: "4-store memory", desc: "Profile/session/semantic-vec/procedural with hybrid recall", tab: "memory", pillar: "harness", icon: Brain, probe: "/v2/memory/stats", demo: "Store + recall with ranking", tags: ["vec", "rag"] },
  { id: "harness-missions", title: "Missions", desc: "Cron background research with persisted results", tab: "missions", pillar: "harness", icon: FolderKanban, probe: "/mission/list", demo: "Schedule a mission, open results", tags: ["cron"] },
  { id: "harness-goals", title: "Goals", desc: "Sovereign goal lifecycle with fallback plans", tab: "goals", pillar: "harness", icon: Target, probe: "/goal/list", demo: "Create a goal, watch it approach", tags: ["goals"] },
  { id: "harness-models", title: "8 LLM providers", desc: "Anthropic/OpenAI/Ollama/MLX/MiniMax/DCM/MoA/Mock routing", tab: "settings", pillar: "harness", icon: Layers, probe: "/v2/llm/providers", demo: "Pick a provider, see cost note", tags: ["llm"] },

  // DeepNet pillar
  { id: "deepnet-privacy", title: "Privacy controls", desc: "4 modes, PII scrub, tracker block, fingerprint rotate", tab: "privacy", pillar: "deepnet", icon: Shield, probe: "/privacy/report", demo: "Switch modes, see report", tags: ["privacy"] },
  { id: "deepnet-vault", title: "Credential vault", desc: "AES-256-GCM + PBKDF2, per-domain creds", tab: "vault", pillar: "deepnet", icon: Wallet, probe: "/vault/status", demo: "Unlock, list domains", tags: ["crypto"] },
  { id: "deepnet-vpn", title: "Dynamic VPN", desc: "Tunnel base + rotating proxy pool, fail-closed", tab: "privacy", pillar: "deepnet", icon: Wifi, probe: "/vpn/status", demo: "Probe pool health, see rotation", tags: ["vpn"] },

  // Developer pillar
  { id: "dev-extensions", title: "Extensions", desc: "Load/unload browser extensions", tab: "extensions", pillar: "developer", icon: Puzzle, probe: "/stats", demo: "Toggle an extension, restart browser", tags: ["ext"] },
  { id: "dev-logs", title: "Audit log", desc: "Tamper-evident SHA-256 chain of every action", tab: "logs", pillar: "developer", icon: ScrollText, demo: "Open logs, verify chain", tags: ["audit-log"] },
  { id: "dev-team", title: "Teams", desc: "Finding assignments, activity feed, stats", tab: "team", pillar: "developer", icon: Users, demo: "Assign a finding, comment", tags: ["teams"] },

  // Decentralized pillar
  { id: "dec-dcm", title: "DCM node", desc: "Local mesh inference provider + receipt ledger", tab: "dcm-node", pillar: "decentralized", icon: Radio, probe: "/dcm/status", demo: "Check node, run an inference", tags: ["mesh"] },
  { id: "dec-meshpay", title: "MeshPay", desc: "Epoch receipts, Merkle roots, payout approval", tab: "meshpay", pillar: "decentralized", icon: Coins, probe: "/meshpay/audit", demo: "View epochs + payout plan", tags: ["usdc"] },
  { id: "dec-sim", title: "Simulation compute", desc: "Verified replicated jobs, median-settled", tab: "agent", pillar: "decentralized", icon: Network, demo: "Quote + submit a job (queued=1)", tags: ["compute"] },
];

// ── Component ─────────────────────────────────────────────────────

export function AbilitiesGrid() {
  const { setActiveTab } = useAppStore();
  const [activePillar, setActivePillar] = useState<Pillar | "all">("all");
  const [query, setQuery] = useState("");
  const [probeState, setProbeState] = useState<Record<string, "ok" | "fail" | "pending">>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  // Derived: the engine only needs one probe — /health — to agree the app is wired.
  const globalEngineUp = probeState["/health"] === "ok" ? true : probeState["/health"] === "fail" ? false : null;

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return ABILITIES.filter((a) => {
      if (activePillar !== "all" && a.pillar !== activePillar) return false;
      if (!q) return true;
      return (
        a.title.toLowerCase().includes(q) ||
        a.desc.toLowerCase().includes(q) ||
        a.tags.some((t) => t.includes(q))
      );
    });
  }, [activePillar, query]);

  // Cheap liveness probes: GET each probe path, mark the card dot.
  // Failures are recorded as "fail" (not thrown) so the grid always renders.
  useEffect(() => {
    let cancelled = false;
    const probes = ABILITIES.map((a) => a.probe).filter(Boolean) as string[];
    const unique = [...new Set(probes)];
    setProbeState((s) => {
      const next = { ...s };
      for (const p of unique) next[p] = "pending";
      return next;
    });
    for (const p of unique) {
      localFetch(p)
        .then((r) => {
          if (cancelled) return;
          setProbeState((s) => ({ ...s, [p]: r.ok ? "ok" : "fail" }));
        })
        .catch(() => {
          if (cancelled) return;
          setProbeState((s) => ({ ...s, [p]: "fail" }));
        });
    }
    return () => { cancelled = true; };
  }, []);

  const counts = useMemo(() => {
    const out: Partial<Record<Pillar | "all", number>> = { all: ABILITIES.length };
    for (const p of PILLARS) {
      out[p.id] = ABILITIES.filter((a) => a.pillar === p.id).length;
    }
    return out;
  }, []);

  return (
    <div className="flex h-full flex-col overflow-hidden bg-background">
        {/* Full-width visual schematic: macOS window + pillar lives */}
        <div className="px-5 pt-4">
          <HeroMap engineUp={globalEngineUp} counts={counts as Record<string, number>} />
        </div>

        <div className="mt-2 border-b border-border/50 bg-gradient-to-b from-surface to-background px-5 py-3">
        <div className="flex items-baseline justify-between gap-4">
          <div>
            <h1 className="text-lg font-semibold text-foreground">Jambubrowser — every ability, one map</h1>
            <p className="text-[12px] text-muted-foreground">
              {ABILITIES.length} features across {PILLARS.length} pillars. Click any card to jump into its live panel.
            </p>
          </div>
          <div className="hidden sm:flex items-center gap-1.5 text-[10px] text-muted-foreground/70">
            <span className="inline-block h-2 w-2 rounded-full bg-emerald-400" /> engine up
            <span className="ml-1 inline-block h-2 w-2 rounded-full bg-red-400" /> engine down
            <span className="ml-1 inline-block h-2 w-2 rounded-full bg-muted-foreground/40" /> no probe
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <FilterChip label={`All (${counts.all})`} active={activePillar === "all"} onClick={() => setActivePillar("all")} />
          {PILLARS.map((p) => (
            <FilterChip key={p.id} label={`${p.label} (${counts[p.id] ?? 0})`} active={activePillar === p.id} onClick={() => setActivePillar(p.id)} />
          ))}
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search abilities…"
            className="ml-auto w-56 rounded-md border border-border/50 bg-background/60 px-2 py-1 text-[11px] text-foreground placeholder:text-muted-foreground/40 focus:outline-none focus:ring-1 focus:ring-sky-500/50"
            data-testid="abilities-search"
          />
        </div>
        </div>

      {/* Grid */}
      <div className="flex-1 overflow-y-auto p-4">
        {activePillar === "all" ? (
          <div className="flex flex-col gap-5">
            {PILLARS.map((p) => {
              const items = visible.filter((a) => a.pillar === p.id);
              if (items.length === 0) return null;
              const Icon = p.icon;
              return (
                <section key={p.id} aria-label={p.label}>
                  <div className="mb-2 flex items-center gap-2">
                    <span className={`inline-flex h-6 w-6 items-center justify-center rounded-md border bg-gradient-to-br ${p.accent}`}>
                      <Icon size={12} className="text-foreground/80" />
                    </span>
                    <h2 className="text-sm font-semibold text-foreground/90">{p.label}</h2>
                    <span className="text-[11px] text-muted-foreground/70">— {p.tagline}</span>
                    <span className="ml-auto text-[10px] text-muted-foreground/50">{items.length}</span>
                  </div>

                  <Grid items={items} probeState={probeState} expanded={expanded} setExpanded={setExpanded} go={setActiveTab} />
                </section>
              );
            })}
          </div>
        ) : (
          <Grid items={visible} probeState={probeState} expanded={expanded} setExpanded={setExpanded} go={setActiveTab} />
        )}
        {visible.length === 0 && (
          <div className="flex h-40 items-center justify-center text-[12px] text-muted-foreground/50">
            No abilities match that filter.
          </div>
        )}
      </div>
    </div>
  );
}

function FilterChip({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`rounded-full border px-2.5 py-1 text-[10px] transition-colors ${
        active
          ? "border-sky-500/60 bg-sky-500/15 text-sky-200"
          : "border-border/50 bg-background/40 text-muted-foreground hover:bg-muted/30"
      }`}
      data-testid={`abilities-filter-${label.split(" ")[0].toLowerCase()}`}
    >
      {label}
    </button>
  );
}

function Grid({
  items, probeState, expanded, setExpanded, go,
}: {
  items: Ability[];
  probeState: Record<string, "ok" | "fail" | "pending">;
  expanded: string | null;
  setExpanded: (id: string | null) => void;
  go: (t: CanvasTab) => void;
}) {
  return (
    <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
      {items.map((a) => {
        const Icon = a.icon;
        const state = a.probe ? probeState[a.probe] : undefined;
        const isOpen = expanded === a.id;
        return (
          <li key={a.id} className="overflow-hidden rounded-lg border border-border/40 bg-surface/50">
            <button
              onClick={() => setExpanded(isOpen ? null : a.id)}
              className="flex w-full items-start gap-2 p-3 text-left transition-colors hover:bg-muted/20"
              data-testid={`ability-${a.id}`}
            >
              <span className="mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-border/50 bg-background/60">
                <Icon size={13} className="text-foreground/80" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className="truncate text-[12px] font-medium text-foreground/90">{a.title}</span>
                  {state === "ok" && <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-400" title="engine up" />}
                  {state === "fail" && <span className="inline-block h-1.5 w-1.5 rounded-full bg-red-400" title="engine down" />}
                  {state === "pending" && <span className="inline-block h-1.5 w-1.5 rounded-full bg-muted-foreground/40" title="probing…" />}
                </div>
                <div className="mt-0.5 truncate text-[10px] text-muted-foreground/70">{a.desc}</div>
              </div>
            </button>
            <AnimatePresence initial={false}>
              {isOpen && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.18 }}
                  className="border-t border-border/30 bg-background/30"
                >
                  <div className="flex items-center gap-2 p-3 text-[10px] text-muted-foreground/80">
                    <span className="rounded bg-muted/40 px-1.5 py-0.5 text-[9px] uppercase tracking-wider">demo</span>
                    <span className="min-w-0 flex-1 truncate">{a.demo}</span>
                    <button
                      onClick={() => go(a.tab)}
                      className="shrink-0 rounded-md bg-sky-500/20 px-2 py-1 text-[10px] text-sky-200 transition-colors hover:bg-sky-500/30"
                      data-testid={`ability-open-${a.id}`}
                    >
                      Open →
                    </button>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </li>
        );
      })}
    </ul>
  );
}
