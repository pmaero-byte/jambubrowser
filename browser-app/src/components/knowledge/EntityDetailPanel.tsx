import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { X, ArrowRight, ArrowLeft, ExternalLink, Loader2, AlertCircle } from "lucide-react";
import {
  fetchKnowledgeEntity,
  type KnowledgeEntityDetail,
  type KnowledgeConnection,
} from "../../utils/api";

interface EntityDetailPanelProps {
  entityId: string | null;
  onClose: () => void;
  onSelectEntity?: (id: string) => void;
}

const TYPE_COLORS: Record<string, string> = {
  person: "bg-blue-500/15 text-blue-300 border-blue-500/30",
  org: "bg-purple-500/15 text-purple-300 border-purple-500/30",
  technology: "bg-cyan-500/15 text-cyan-300 border-cyan-500/30",
  concept: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  location: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  event: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  product: "bg-fuchsia-500/15 text-fuchsia-300 border-fuchsia-500/30",
};

function TypeBadge({ type }: { type: string }) {
  const color = TYPE_COLORS[type] ?? "bg-zinc-500/15 text-zinc-300 border-zinc-500/30";
  return (
    <span className={`inline-block rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${color}`}>
      {type}
    </span>
  );
}

function ConnectionRow({
  conn,
  onSelectEntity,
}: {
  conn: KnowledgeConnection;
  onSelectEntity?: (id: string) => void;
}) {
  const isIncoming = conn.direction === "incoming";
  const Icon = isIncoming ? ArrowLeft : ArrowRight;
  return (
    <li className="group rounded-lg border border-border/60 bg-card/40 p-2.5 transition hover:border-border hover:bg-card/80">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <Icon
            className={`h-3.5 w-3.5 shrink-0 ${isIncoming ? "text-blue-400" : "text-amber-400"}`}
            aria-label={isIncoming ? "incoming" : "outgoing"}
          />
          <span className="truncate text-sm font-medium text-foreground">{conn.entity}</span>
          <TypeBadge type={conn.entity_type} />
        </div>
        {onSelectEntity && (
          <button
            type="button"
            onClick={() => onSelectEntity(conn.entity)}
            className="shrink-0 rounded p-1 text-muted-foreground opacity-0 transition hover:bg-muted hover:text-foreground group-hover:opacity-100"
            aria-label={`Open ${conn.entity}`}
          >
            <ExternalLink className="h-3 w-3" />
          </button>
        )}
      </div>
      <div className="mt-1.5 flex items-center gap-2 text-xs text-muted-foreground">
        <span className="font-mono text-foreground/70">{conn.relation}</span>
        {conn.evidence && (
          <>
            <span aria-hidden>·</span>
            <span className="line-clamp-1 italic" title={conn.evidence}>
              {conn.evidence}
            </span>
          </>
        )}
      </div>
    </li>
  );
}

export function EntityDetailPanel({ entityId, onClose, onSelectEntity }: EntityDetailPanelProps) {
  const [data, setData] = useState<KnowledgeEntityDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!entityId) {
      setData(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchKnowledgeEntity(entityId)
      .then((detail) => {
        if (cancelled) return;
        setData(detail);
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setError(err.message);
        setData(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [entityId]);

  const outgoing = data?.connections.filter((c) => c.direction === "outgoing") ?? [];
  const incoming = data?.connections.filter((c) => c.direction === "incoming") ?? [];

  return (
    <AnimatePresence>
      {entityId && (
        <motion.aside
          key={entityId}
          initial={{ opacity: 0, x: 12 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: 12 }}
          transition={{ duration: 0.18 }}
          className="flex h-full w-full flex-col overflow-hidden rounded-xl border border-border bg-card/60 backdrop-blur"
          aria-label="Entity detail"
        >
          <header className="flex items-start justify-between gap-2 border-b border-border/60 p-3">
            <div className="min-w-0 flex-1">
              {loading ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Loading entity…
                </div>
              ) : error ? (
                <div className="flex items-center gap-2 text-sm text-rose-400">
                  <AlertCircle className="h-4 w-4 shrink-0" />
                  <span className="truncate" title={error}>{error}</span>
                </div>
              ) : data ? (
                <>
                  <h3 className="truncate text-base font-semibold text-foreground" title={data.entity.id}>
                    {data.entity.name}
                  </h3>
                  <div className="mt-1 flex items-center gap-2">
                    <TypeBadge type={data.entity.type} />
                    <span className="text-xs text-muted-foreground">
                      {data.entity.occurrences} {data.entity.occurrences === 1 ? "occurrence" : "occurrences"}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      · {data.connection_count} {data.connection_count === 1 ? "connection" : "connections"}
                    </span>
                  </div>
                </>
              ) : null}
            </div>
            <button
              type="button"
              onClick={onClose}
              className="shrink-0 rounded p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground"
              aria-label="Close entity detail"
            >
              <X className="h-4 w-4" />
            </button>
          </header>

          {data && (
            <div className="min-h-0 flex-1 overflow-y-auto p-3">
              {outgoing.length > 0 && (
                <section className="mb-4">
                  <h4 className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-amber-400">
                    <ArrowRight className="h-3 w-3" />
                    Outgoing ({outgoing.length})
                  </h4>
                  <ul className="space-y-1.5">
                    {outgoing.map((c, i) => (
                      <ConnectionRow
                        key={`out-${c.entity}-${i}`}
                        conn={c}
                        onSelectEntity={onSelectEntity}
                      />
                    ))}
                  </ul>
                </section>
              )}

              {incoming.length > 0 && (
                <section className="mb-4">
                  <h4 className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-blue-400">
                    <ArrowLeft className="h-3 w-3" />
                    Incoming ({incoming.length})
                  </h4>
                  <ul className="space-y-1.5">
                    {incoming.map((c, i) => (
                      <ConnectionRow
                        key={`in-${c.entity}-${i}`}
                        conn={c}
                        onSelectEntity={onSelectEntity}
                      />
                    ))}
                  </ul>
                </section>
              )}

              {data.connections.length === 0 && (
                <p className="text-sm text-muted-foreground">No connections yet.</p>
              )}
            </div>
          )}
        </motion.aside>
      )}
    </AnimatePresence>
  );
}
