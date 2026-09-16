import { useCallback, useEffect, useState } from "react";
import {
  ShieldCheck, ShieldAlert, RefreshCw, Anchor, ExternalLink, Copy,
  Link2, Coins, Radio,
} from "lucide-react";
import { Button } from "../ui/button";
import { localFetch } from "../../utils/api";
import { useAppStore } from "../../store/appStore";

// ── Types ────────────────────────────────────────────────────────────

interface MeshPayConfig {
  cluster: string;
  rpc_url: string;
  has_keypair: boolean;
  dct_usd_rate: number;
  protocol_fee_pct: number;
  epoch_size: number;
  transport: string;
}

interface Verdict {
  valid: boolean;
  checked: number;
  broken_at: number | null;
  broken_reason: string | null;
  head_hash: string | null;
  window_truncated: boolean;
  kinds: Record<string, number>;
}

interface Epoch {
  index: number;
  from_index: number;
  to_index: number;
  receipts: number;
  root: string | null;
  providers: Array<{ nodeId: string; accruedDct: number; receipts: number }>;
}

interface PayoutProvider {
  nodeId: string;
  receipts: number;
  grossDct: number;
  feeDct: number;
  netDct: number;
  usdc: number;
}

interface Payout {
  epoch: { index: number; receipts: number; root: string | null };
  dct_usd_rate: number;
  protocol_fee_pct: number;
  rate_note: string;
  providers: PayoutProvider[];
  totals: { grossDct: number; feeDct: number; netDct: number; usdc: number };
}

interface Audit {
  node_total_entries: number;
  window: { requested: number; returned: number; truncated: boolean };
  verification: Verdict;
  dcm_verification: { valid?: boolean; entries?: number };
  agreement: boolean | null;
  epochs: Epoch[];
  payout: Payout | null;
  config: MeshPayConfig;
}

interface AnchorRecord {
  id: number;
  epoch: number;
  root: string;
  receipts: number;
  cluster: string;
  transport: string;
  signature: string;
  created_at: number;
  explorer_url?: string;
  status?: "verified" | "mismatch" | "unavailable";
  matches?: boolean;
  current_root?: string | null;
}

// ── Helpers ──────────────────────────────────────────────────────────

export function shortHash(hash: string | null, chars = 12): string {
  if (!hash) return "—";
  return hash.length > chars * 2 ? `${hash.slice(0, chars)}…${hash.slice(-chars)}` : hash;
}

export function anchorStatusColor(status?: string): string {
  if (status === "verified") return "text-emerald-400";
  if (status === "mismatch") return "text-red-400";
  return "text-muted-foreground";
}

function copy(text: string) {
  navigator.clipboard?.writeText(text).catch(() => {});
}

// ── Component ────────────────────────────────────────────────────────

export function MeshPayPanel() {
  const [audit, setAudit] = useState<Audit | null>(null);
  const [anchors, setAnchors] = useState<AnchorRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [anchoring, setAnchoring] = useState<number | null>(null);
  const setActiveTab = useAppStore((s) => s.setActiveTab);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [auditRes, anchorRes] = await Promise.all([
        localFetch("/meshpay/audit?limit=200"),
        localFetch("/meshpay/anchors?limit=20"),
      ]);
      if (!auditRes.ok) {
        const body = await auditRes.json().catch(() => ({}));
        throw new Error(body.detail || `audit failed (${auditRes.status})`);
      }
      setAudit(await auditRes.json());
      if (anchorRes.ok) {
        setAnchors((await anchorRes.json()).anchors || []);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const anchorEpoch = async (epochIndex: number) => {
    setAnchoring(epochIndex);
    try {
      const res = await localFetch("/meshpay/anchor", {
        method: "POST",
        body: JSON.stringify({ epoch_index: epochIndex, limit: 200 }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `anchor failed (${res.status})`);
      }
      const record = await res.json();
      setAnchors((prev) => [{ ...record, status: "verified", matches: true }, ...prev]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setAnchoring(null);
    }
  };

  const verdict = audit?.verification;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border p-3">
        <div className="flex items-center gap-2">
          <Coins size={18} className="text-amber-400" />
          <span className="font-semibold">MeshPay</span>
          <span className="text-[11px] text-muted-foreground">
            USDC settlement for mesh compute
          </span>
        </div>
        <Button variant="outline" size="sm" onClick={load} disabled={loading} className="gap-1">
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} /> Refresh
        </Button>
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto p-3">
        {error && (
          <div className="rounded border border-red-500/30 bg-red-500/10 p-2 text-xs text-red-300">
            {error}
          </div>
        )}

        {audit && (
          <div className="rounded border border-border/60 p-2 text-[11px] text-muted-foreground">
            <span className="text-foreground">{audit.config.transport}</span>
            {" · "}
            rate 1 DCT = ${audit.config.dct_usd_rate} (configured, not an oracle)
            {" · "}
            fee {(audit.config.protocol_fee_pct * 100).toFixed(0)}%
          </div>
        )}

        {/* Chain verdict */}
        {verdict && (
          <div className="rounded border border-border/60 p-3">
            <div className="mb-1 flex items-center gap-2 text-sm font-medium">
              {verdict.valid ? (
                <ShieldCheck size={15} className="text-emerald-400" />
              ) : (
                <ShieldAlert size={15} className="text-red-400" />
              )}
              Receipt chain {verdict.valid ? "verified" : "BROKEN"}
              <span className="text-[10px] font-normal text-muted-foreground">
                (independent replay, {verdict.checked} receipts)
              </span>
            </div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
              <span>
                DCM's own verdict:{" "}
                <span className="text-foreground">
                  {String(audit?.dcm_verification?.valid)}
                </span>
                {audit?.agreement === false && (
                  <span className="ml-1 text-red-400">— DISAGREEMENT</span>
                )}
              </span>
              <span>
                Head: <span className="font-mono">{shortHash(verdict.head_hash)}</span>
              </span>
              <span>
                Window: {audit?.window.returned}/{audit?.node_total_entries}
                {audit?.window.truncated ? " (truncated)" : ""}
              </span>
              <span>
                Kinds:{" "}
                {Object.entries(verdict.kinds)
                  .map(([k, n]) => `${k}×${n}`)
                  .join(", ") || "—"}
              </span>
            </div>
            {verdict.broken_at != null && (
              <p className="mt-1 text-[11px] text-red-300">
                Broken at #{verdict.broken_at}: {verdict.broken_reason}
              </p>
            )}
          </div>
        )}

        {/* Epochs */}
        {audit && audit.epochs.length > 0 && (
          <div className="rounded border border-border/60 p-3">
            <div className="mb-2 flex items-center gap-2 text-sm font-medium">
              <Link2 size={14} className="text-violet-400" /> Epochs ({audit.epochs.length})
            </div>
            <ul className="space-y-1">
              {audit.epochs.map((e) => (
                <li key={e.index} className="flex items-center gap-2 text-[11px]">
                  <span className="w-14 shrink-0 text-muted-foreground">#{e.index}</span>
                  <span className="w-20 shrink-0 text-muted-foreground">
                    {e.receipts} receipts
                  </span>
                  <span className="min-w-0 flex-1 truncate font-mono text-muted-foreground">
                    {shortHash(e.root, 8)}
                  </span>
                  <span className="shrink-0 text-muted-foreground">
                    {e.providers.length} provider{e.providers.length === 1 ? "" : "s"}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-6 shrink-0 gap-1 px-2 text-[10px]"
                    disabled={anchoring !== null || !e.root}
                    onClick={() => anchorEpoch(e.index)}
                  >
                    <Anchor size={10} />
                    {anchoring === e.index ? "anchoring…" : "anchor"}
                  </Button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Payout plan */}
        {audit?.payout && audit.payout.providers.length > 0 && (
          <div className="rounded border border-border/60 p-3">
            <div className="mb-1 text-sm font-medium">
              Payout plan — latest epoch #{audit.payout.epoch.index}
            </div>
            <table className="w-full text-left text-[11px]">
              <thead className="text-muted-foreground">
                <tr>
                  <th className="py-0.5 font-normal">Provider</th>
                  <th className="py-0.5 text-right font-normal">Receipts</th>
                  <th className="py-0.5 text-right font-normal">Gross DCT</th>
                  <th className="py-0.5 text-right font-normal">Net DCT</th>
                  <th className="py-0.5 text-right font-normal">USDC</th>
                </tr>
              </thead>
              <tbody>
                {audit.payout.providers.map((p) => (
                  <tr key={p.nodeId}>
                    <td className="py-0.5 font-mono">{p.nodeId.slice(0, 14)}</td>
                    <td className="py-0.5 text-right text-muted-foreground">{p.receipts}</td>
                    <td className="py-0.5 text-right text-muted-foreground">{p.grossDct}</td>
                    <td className="py-0.5 text-right">{p.netDct}</td>
                    <td className="py-0.5 text-right text-amber-300">{p.usdc}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-1 text-[10px] text-muted-foreground">{audit.payout.rate_note}</p>
          </div>
        )}

        {/* Anchors */}
        <div className="rounded border border-border/60 p-3">
          <div className="mb-2 flex items-center gap-2 text-sm font-medium">
            <Anchor size={14} className="text-amber-400" /> Anchored roots ({anchors.length})
          </div>
          {anchors.length === 0 ? (
            <p className="text-[11px] text-muted-foreground">
              Nothing anchored yet — anchor an epoch above.
            </p>
          ) : (
            <ul className="space-y-1">
              {anchors.map((a) => (
                <li key={a.id} className="flex items-center gap-2 text-[11px]">
                  <span className="w-12 shrink-0 text-muted-foreground">#{a.epoch}</span>
                  <span className={`w-20 shrink-0 ${anchorStatusColor(a.status)}`}>
                    {a.status || "anchored"}
                  </span>
                  <span className="shrink-0 text-muted-foreground">{a.transport}</span>
                  <span className="min-w-0 flex-1 truncate font-mono text-muted-foreground">
                    {shortHash(a.root, 8)}
                  </span>
                  <button
                    className="shrink-0 text-muted-foreground hover:text-foreground"
                    title="Copy signature"
                    onClick={() => copy(a.signature)}
                  >
                    <Copy size={11} />
                  </button>
                  {a.explorer_url ? (
                    <a
                      href={a.explorer_url}
                      target="_blank"
                      rel="noreferrer"
                      className="shrink-0 text-violet-400"
                      title={a.signature}
                    >
                      <ExternalLink size={11} />
                    </a>
                  ) : (
                    <span className="shrink-0 font-mono text-[9px] text-muted-foreground" title={a.signature}>
                      {a.signature.slice(0, 14)}…
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Cross-link */}
        <button
          onClick={() => setActiveTab("dcm-node")}
          className="flex w-full items-center justify-center gap-1 rounded border border-border/60 p-2 text-[11px] text-muted-foreground hover:text-foreground"
        >
          <Radio size={12} /> Open the DCM node panel
        </button>
      </div>
    </div>
  );
}
