import { useCallback, useEffect, useState } from "react";
import { FlaskConical, RefreshCw, Play, ShieldBan, ShieldCheck } from "lucide-react";
import { Button } from "../ui/button";
import { localFetch } from "../../utils/api";
import { relativeTime } from "../monitors/FlowMonitorsPanel";

// ── Types (mirrors GET /qa/overview) ─────────────────────────────────

interface CaseStats {
  runs: number;
  pass_rate: number | null;
  heal_rate: number | null;
  flake_rate: number | null;
  quarantined: boolean;
  quarantine_reason: string | null;
  consecutive_passes: number;
  last_status: string | null;
}

interface QaCase {
  id: number;
  name: string;
  url: string;
  kind: string;
  severity: string;
  enabled: boolean;
  last_run_at: number | null;
  stats: CaseStats;
}

interface Overview {
  cases: QaCase[];
  count: number;
  summary: {
    cases: number;
    quarantined: number;
    pass_rate: number | null;
    flake_rate: number | null;
    heal_rate: number | null;
  };
}

function pct(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

function statusColor(status: string | null): string {
  if (!status) return "text-muted-foreground";
  if (status === "flaky") return "text-amber-500";
  if (status === "passed" || status === "healed") return "text-emerald-500";
  return "text-red-500";
}

// ── Panel ────────────────────────────────────────────────────────────

export function QaPanel() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Set<number>>(new Set());

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await localFetch("/qa/overview");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setOverview((await res.json()) as Overview);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const act = useCallback(
    async (caseId: number, path: string, body?: unknown) => {
      setBusy((prev) => new Set(prev).add(caseId));
      try {
        await localFetch(path, {
          method: "POST",
          body: body ? JSON.stringify(body) : "{}",
        });
        await refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy((prev) => {
          const next = new Set(prev);
          next.delete(caseId);
          return next;
        });
      }
    },
    [refresh],
  );

  const runCase = (id: number) => act(id, `/qa/cases/${id}/run`, { local: true });

  const summary = overview?.summary;

  return (
    <div className="flex h-full flex-col overflow-auto">
      <div className="flex items-center justify-between border-b border-border/50 px-4 py-3">
        <div className="flex items-center gap-2">
          <FlaskConical className="h-4 w-4" />
          <span className="text-sm font-medium">QA Cases</span>
          <span className="text-xs text-muted-foreground">
            {overview ? `${overview.count} case(s)` : ""}
          </span>
        </div>
        <Button variant="ghost" size="sm" onClick={() => void refresh()}>
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>
      <PanelBody
        summary={summary}
        cases={overview?.cases ?? []}
        error={error}
        busy={busy}
        onRun={runCase}
        onAct={act}
      />
    </div>
  );
}

// ── Body: summary cards + case list ──────────────────────────────────

function PanelBody(props: {
  summary: Overview["summary"] | undefined;
  cases: QaCase[];
  error: string | null;
  busy: Set<number>;
  onRun: (id: number) => void;
  onAct: (id: number, path: string, body?: unknown) => Promise<void>;
}) {
  const { summary, cases, error, busy, onRun, onAct } = props;
  return (
    <>
      {error && (
        <div className="px-4 py-2 text-xs text-red-500">{error}</div>
      )}

      {summary && (
        <div className="grid grid-cols-4 gap-2 px-4 py-3 text-center">
          <div className="rounded-md border border-border/50 p-2">
            <div className="text-lg font-semibold">{pct(summary.pass_rate)}</div>
            <div className="text-[11px] text-muted-foreground">pass rate</div>
          </div>
          <div className="rounded-md border border-border/50 p-2">
            <div className="text-lg font-semibold">{pct(summary.heal_rate)}</div>
            <div className="text-[11px] text-muted-foreground">heal rate</div>
          </div>
          <div className="rounded-md border border-border/50 p-2">
            <div className="text-lg font-semibold">{pct(summary.flake_rate)}</div>
            <div className="text-[11px] text-muted-foreground">flake rate</div>
          </div>
          <div className="rounded-md border border-border/50 p-2">
            <div className="text-lg font-semibold">{summary.quarantined}</div>
            <div className="text-[11px] text-muted-foreground">quarantined</div>
          </div>
        </div>
      )}

      <div className="min-h-0 flex-1 px-4 pb-4">
        {cases.map((c) => (
          <CaseCard key={c.id} caseRow={c} busy={busy} onRun={onRun} onAct={onAct} />
        ))}
        {cases.length === 0 && (
          <div className="py-8 text-center text-xs text-muted-foreground">
            No QA cases yet — create one with{" "}
            <code>jambu qa create --name ... --url ... --goal ...</code>
          </div>
        )}
      </div>
    </>
  );
}

function CaseCard(props: {
  caseRow: QaCase;
  busy: Set<number>;
  onRun: (id: number) => void;
  onAct: (id: number, path: string, body?: unknown) => Promise<void>;
}) {
  const { caseRow: c, busy, onRun, onAct } = props;
  return (
    <div className="mb-2 rounded-md border border-border/50 p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium">
              #{c.id} {c.name}
            </span>
            <span
              className={`text-xs font-medium ${statusColor(c.stats.last_status)}`}
            >
              {c.stats.last_status ?? "never run"}
            </span>
            {c.stats.quarantined && (
              <span className="rounded bg-amber-500/15 px-1.5 text-[10px] font-medium text-amber-600">
                quarantined
              </span>
            )}
          </div>
          <div className="truncate text-[11px] text-muted-foreground">
            {c.url} · {c.kind}/{c.severity} · last run{" "}
            {relativeTime(c.last_run_at)}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1.5 text-xs">
          <span title="pass rate">{pct(c.stats.pass_rate)}</span>
          <span className="text-muted-foreground">·</span>
          <span title="heal rate">{pct(c.stats.heal_rate)}</span>
          <span className="text-muted-foreground">·</span>
          <span
            className={
              c.stats.flake_rate && c.stats.flake_rate > 0 ? "text-amber-500" : ""
            }
            title="flake rate"
          >
            {pct(c.stats.flake_rate)}
          </span>
        </div>
      </div>
      {c.stats.quarantine_reason && (
        <div className="mt-1 text-[11px] text-amber-600">
          {c.stats.quarantine_reason} · green streak{" "}
          {c.stats.consecutive_passes}
        </div>
      )}
      <div className="mt-2 flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={busy.has(c.id)}
          onClick={() => onRun(c.id)}
        >
          <Play className="h-3 w-3" /> Run
        </Button>
        {c.stats.quarantined ? (
          <Button
            variant="ghost"
            size="sm"
            disabled={busy.has(c.id)}
            onClick={() => void onAct(c.id, `/qa/cases/${c.id}/unquarantine`)}
          >
            <ShieldCheck className="h-3 w-3" /> Unquarantine
          </Button>
        ) : (
          <Button
            variant="ghost"
            size="sm"
            disabled={busy.has(c.id)}
            onClick={() =>
              void onAct(c.id, `/qa/cases/${c.id}/quarantine`, {
                reason: "parked from dashboard",
              })
            }
          >
            <ShieldBan className="h-3 w-3" /> Quarantine
          </Button>
        )}
      </div>
    </div>
  );
}
