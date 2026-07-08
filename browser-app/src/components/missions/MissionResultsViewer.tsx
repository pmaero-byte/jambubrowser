import { useEffect, useState } from "react";
import { Loader2, AlertCircle, Clock, CheckCircle2, XCircle } from "lucide-react";
import { fetchMissionResults, type MissionResult } from "../../utils/api";

interface MissionResultsViewerProps {
  missionId: string;
  limit?: number;
}

function formatTimestamp(ts: number): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString();
}

export function MissionResultsViewer({ missionId, limit = 50 }: MissionResultsViewerProps) {
  const [results, setResults] = useState<MissionResult[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!missionId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchMissionResults(missionId, limit)
      .then((resp) => {
        if (cancelled) return;
        setResults(resp.results);
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setError(err.message);
        setResults(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [missionId, limit]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 p-3 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading results…
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center gap-2 p-3 text-sm text-rose-400">
        <AlertCircle className="h-4 w-4 shrink-0" />
        <span className="truncate" title={error}>{error}</span>
      </div>
    );
  }

  if (!results || results.length === 0) {
    return (
      <p className="p-3 text-sm text-muted-foreground">No results yet. The mission hasn't run.</p>
    );
  }

  return (
    <ul className="divide-y divide-border/60" aria-label="Mission results">
      {results.map((r) => (
        <li key={r.id} className="p-3">
          <div className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
            {r.success ? (
              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" aria-label="success" />
            ) : (
              <XCircle className="h-3.5 w-3.5 text-rose-400" aria-label="error" />
            )}
            <Clock className="h-3 w-3" />
            <span>{formatTimestamp(r.run_at)}</span>
            <span className="ml-auto font-mono text-[10px] opacity-60">#{r.id}</span>
          </div>
          <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap break-words rounded bg-muted/40 p-2 text-xs text-foreground">
            {r.result_text}
          </pre>
        </li>
      ))}
    </ul>
  );
}
