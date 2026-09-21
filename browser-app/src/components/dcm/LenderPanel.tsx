import { useCallback, useEffect, useState } from "react";
import {
  Coins,
  RefreshCw,
  Users,
  Play,
  AlertTriangle,
  ExternalLink,
} from "lucide-react";
import { Button } from "../ui/button";
import { localFetch, engineOrigin } from "../../utils/api";

/**
 * Lender dashboard — what THIS machine earns by lending compute to the
 * DecentraCode mesh, and who else is lending.
 *
 * Two separate ledgers, deliberately shown separately because they answer
 * different questions:
 *   - the fabric roster  (`/dcm/realtime/peers`) — who is connected right now
 *     and what each lender has been PAID for verified work
 *     (`jobsDone` / `earnedDct` are the node's own counters);
 *   - the DCT ledger      (`/dcm/earnings/:did`, `/dcm/token/balance/:did`) —
 *     what a DID holds and has banked, including anything earned outside the
 *     realtime fabric.
 *
 * Lending itself is the node's own `/peer` page: a device lends with zero
 * install by holding that page open, so the primary action here OPENS it with
 * a stable peer id + DID rather than reimplementing the runtime in the shell.
 */

interface RosterPeer {
  peerId?: string;
  did?: string;
  ua?: string;
  jobsDone?: number;
  earnedDct?: number;
  lastSeen?: number;
}

interface Earnings {
  did?: string;
  pendingDct?: number;
  totalEarnedDct?: number;
  [k: string]: unknown;
}

interface Balance {
  did?: string;
  balance?: number;
  [k: string]: unknown;
}

const HEARTBEAT_STALE_MS = 45_000;

/** A lender is "live" when its last heartbeat is inside the node's window. */
export function rosterPeerState(
  peer: RosterPeer,
  now: number = Date.now(),
): "live" | "stale" {
  if (typeof peer.lastSeen !== "number") return "live";
  return now - peer.lastSeen <= HEARTBEAT_STALE_MS ? "live" : "stale";
}

/** Total DCT the node says it has paid the connected lenders. */
export function rosterPaidTotal(peers: RosterPeer[]): number {
  return peers.reduce((sum, p) => sum + (Number(p.earnedDct) || 0), 0);
}

export function rosterJobsTotal(peers: RosterPeer[]): number {
  return peers.reduce((sum, p) => sum + (Number(p.jobsDone) || 0), 0);
}

/** The node's lender-page URL for this device, or null when unknown. */
export function lendUrl(nodeBase: string, peerId: string, did: string): string | null {
  if (!nodeBase) return null;
  const base = nodeBase.replace(/\/+$/, "");
  return `${base}/peer/peer.html?auto=1&peerId=${encodeURIComponent(peerId)}&did=${encodeURIComponent(did)}`;
}

/** Extract a peer id from anything the operator typed, tolerating blank input. */
export function normalizePeerId(raw: string): string {
  const trimmed = raw.trim().replace(/[^A-Za-z0-9._:-]/g, "-");
  return trimmed.slice(0, 64) || "jambu-lender";
}

function asNumber(v: unknown): number | undefined {
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
}

function formatDct(v: number | undefined): string {
  if (v === undefined) return "—";
  return `${v.toFixed(9)} DCT`;
}

export function LenderPanel() {
  const [peers, setPeers] = useState<RosterPeer[] | null>(null);
  const [nodeBase, setNodeBase] = useState("");
  const [peerId, setPeerId] = useState("jambu-lender");
  const [did, setDid] = useState("did:decentracode:jambu-lender");
  const [earnings, setEarnings] = useState<Earnings | null>(null);
  const [balance, setBalance] = useState<Balance | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const statusRes = await localFetch("/dcm/status");
      const status = await statusRes.json().catch(() => ({}));
      if (status && typeof status.base_url === "string") setNodeBase(status.base_url);

      const rosterRes = await localFetch("/dcm/realtime/peers");
      const roster = await rosterRes.json().catch(() => ({}));
      if (!rosterRes.ok) {
        throw new Error(roster.detail || `roster failed (${rosterRes.status})`);
      }
      setPeers(Array.isArray(roster.peers) ? roster.peers : []);

      // Per-DID ledgers. A missing route or a cold DID is reported as such
      // rather than rendered as zero.
      const [earnRes, balRes] = await Promise.all([
        localFetch(`/dcm/earnings/${encodeURIComponent(did)}`).catch(() => null),
        localFetch(`/dcm/token/balance/${encodeURIComponent(did)}`).catch(() => null),
      ]);
      setEarnings(earnRes && earnRes.ok ? await earnRes.json().catch(() => null) : null);
      setBalance(balRes && balRes.ok ? await balRes.json().catch(() => null) : null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPeers(null);
    } finally {
      setLoading(false);
    }
  }, [did]);

  useEffect(() => {
    load();
  }, [load]);

  const roster = peers || [];
  const live = roster.filter((p) => rosterPeerState(p) === "live");
  const url = lendUrl(nodeBase || engineOrigin() || "", normalizePeerId(peerId), did);
  const balanceDct = asNumber(balance?.balance);
  const pendingDct = asNumber(earnings?.pendingDct);
  const totalEarned = asNumber(earnings?.totalEarnedDct);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border p-3">
        <div className="flex items-center gap-2">
          <Coins size={18} className="text-amber-400" />
          <span className="font-semibold">Lend compute</span>
          <span className="text-[11px] text-muted-foreground">
            earn DCT for verified solves on the DecentraCode mesh
          </span>
        </div>
        <Button size="sm" variant="ghost" onClick={load} disabled={loading}>
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          Refresh
        </Button>
      </div>

      <div className="flex-1 overflow-auto p-3 space-y-4">
        {error && (
          <div className="flex items-start gap-2 rounded border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
            <AlertTriangle size={16} className="mt-0.5 text-amber-400" />
            <div>
              <div className="font-medium">No mesh node reachable</div>
              <div className="text-muted-foreground">{error}</div>
              <div className="mt-1 text-[11px] text-muted-foreground">
                Start one with <code>cd decentracode/backend &amp;&amp; npm start</code>.
              </div>
            </div>
          </div>
        )}

        {/* Lending action — the node's own peer page IS the runtime. */}
        <section className="rounded border border-border p-3">
          <div className="mb-2 flex items-center gap-2 text-sm font-medium">
            <Play size={15} className="text-emerald-400" />
            Lend from this device
          </div>
          <p className="mb-3 text-[12px] text-muted-foreground">
            Holding the lender page open is the whole install: the node offers
            your browser a job, you solve it, the coordinator re-runs the solve
            and verifies your checkpoints, and escrow pays your DID.
          </p>
          <div className="mb-3 grid grid-cols-2 gap-2">
            <label className="text-[11px]">
              <span className="text-muted-foreground">Peer id</span>
              <input
                aria-label="Peer id"
                className="mt-1 w-full rounded border border-border bg-transparent px-2 py-1 text-sm"
                value={peerId}
                onChange={(e) => setPeerId(e.target.value)}
              />
            </label>
            <label className="text-[11px]">
              <span className="text-muted-foreground">DID (payment address)</span>
              <input
                aria-label="DID"
                className="mt-1 w-full rounded border border-border bg-transparent px-2 py-1 text-sm"
                value={did}
                onChange={(e) => setDid(e.target.value)}
              />
            </label>
          </div>
          {url ? (
            <a
              href={url}
              target="_blank"
              rel="noreferrer"
              data-testid="lender-open-peer-page"
              className="inline-flex items-center gap-1 text-sm text-cyan-400 hover:underline"
            >
              Open the lender page <ExternalLink size={13} />
            </a>
          ) : (
            <div className="text-[12px] text-muted-foreground">
              Node address unknown — refresh once a node answers.
            </div>
          )}
        </section>

        {/* Live roster */}
        <section className="rounded border border-border p-3">
          <div className="mb-2 flex items-center gap-2 text-sm font-medium">
            <Users size={15} className="text-cyan-400" />
            Lenders on this mesh
            <span className="text-[11px] text-muted-foreground">
              {peers === null ? "unknown" : `${live.length} live of ${roster.length}`}
            </span>
          </div>
          {peers === null ? (
            <div className="text-[12px] text-muted-foreground">
              Roster unavailable — no reading is invented.
            </div>
          ) : roster.length === 0 ? (
            <div className="text-[12px] text-muted-foreground">
              No device is lending right now, so a submitted job would sit queued.
            </div>
          ) : (
            <table className="w-full text-left text-[12px]">
              <thead className="text-muted-foreground">
                <tr>
                  <th className="py-1">Lender</th>
                  <th className="py-1">Jobs</th>
                  <th className="py-1">Earned</th>
                </tr>
              </thead>
              <tbody>
                {roster.map((p) => (
                  <tr key={p.peerId} className="border-t border-border/50">
                    <td className="py-1">
                      <div className="flex items-center gap-1.5">
                        <span
                          className={`inline-block h-1.5 w-1.5 rounded-full ${
                            rosterPeerState(p) === "live" ? "bg-emerald-400" : "bg-slate-500"
                          }`}
                        />
                        <span>{p.peerId || "unnamed"}</span>
                      </div>
                      <div className="text-[10px] text-muted-foreground">{p.did}</div>
                    </td>
                    <td className="py-1">{p.jobsDone ?? 0}</td>
                    <td className="py-1">{formatDct(asNumber(p.earnedDct))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {roster.length > 0 && (
            <div className="mt-2 text-[11px] text-muted-foreground">
              Paid by this node for verified work: {formatDct(rosterPaidTotal(roster))} across{" "}
              {rosterJobsTotal(roster)} job(s).
            </div>
          )}
        </section>

        {/* This DID's ledgers */}
        <section className="rounded border border-border p-3">
          <div className="mb-2 text-sm font-medium">Ledger for {did}</div>
          <dl className="grid grid-cols-3 gap-2 text-[12px]">
            <div>
              <dt className="text-muted-foreground">Ledger balance</dt>
              <dd data-testid="lender-balance">
                {balance === null ? "unavailable" : formatDct(balanceDct)}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Pending</dt>
              <dd data-testid="lender-pending">
                {earnings === null ? "unavailable" : formatDct(pendingDct)}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Total earned</dt>
              <dd data-testid="lender-total-earned">
                {earnings === null || totalEarned === undefined
                  ? "unavailable"
                  : formatDct(totalEarned)}
              </dd>
            </div>
          </dl>
          <p className="mt-3 text-[11px] text-muted-foreground">
            Cash-out doors (wDCT on Solana, MeshPay USDC withdrawal) are not
            live yet — DCT stays in this node's ledger. Nothing here quotes a
            fiat value.
          </p>
        </section>
      </div>
    </div>
  );
}
