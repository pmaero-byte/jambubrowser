import { useCallback, useEffect, useState } from "react";
import { Radio, RefreshCw, Play, Cpu, Link2, AlertTriangle } from "lucide-react";
import { Button } from "../ui/button";
import { localFetch } from "../../utils/api";

// ── Types ────────────────────────────────────────────────────────────

interface DcmModel {
  id: string;
  name?: string;
  status?: string;
  runtime?: string;
  available?: boolean;
}

interface DcmStatus {
  base_url: string;
  reachable: boolean;
  inference_status?: {
    runtime?: string;
    ready?: boolean;
    engine_ready?: boolean;
    error?: string;
    moe?: { runtime?: string; ready?: boolean; available?: boolean };
  };
  models?: DcmModel[];
  mesh_status?: { nodeId?: string; peers?: unknown };
}

interface JoinInfo {
  lanHosts?: string[];
  ports?: { peerPage?: number; ws?: number; http?: number };
  peerPages?: Record<string, string>;
}

// ── Helpers ──────────────────────────────────────────────────────────

export function dcmReadiness(status?: DcmStatus): {
  ready: boolean;
  label: string;
  detail?: string;
} {
  const inf = status?.inference_status;
  if (!inf) return { ready: false, label: "unknown" };
  if (inf.ready || inf.engine_ready) {
    return { ready: true, label: `${inf.runtime || "runtime"} ready` };
  }
  const moe = inf.moe;
  if (moe?.ready || moe?.available) {
    return {
      ready: true,
      label: `${moe.runtime || "MoE"} ready`,
      detail: inf.error ? `${inf.runtime || "default"}: ${inf.error}` : undefined,
    };
  }
  return { ready: false, label: "not ready", detail: inf.error };
}

export function availableModels(models?: DcmModel[]): DcmModel[] {
  return (models || []).filter(
    (m) => m.available || m.status === "available" || m.status === "ready",
  );
}

export function peerCount(mesh?: { peers?: unknown }): number {
  const peers = mesh?.peers;
  if (Array.isArray(peers)) return peers.length;
  if (peers && typeof peers === "object") return Object.keys(peers).length;
  if (typeof peers === "number") return peers;
  return 0;
}

// ── Component ────────────────────────────────────────────────────────

export function DcmNodePanel() {
  const [status, setStatus] = useState<DcmStatus | null>(null);
  const [join, setJoin] = useState<JoinInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [output, setOutput] = useState<string | null>(null);
  const [inferring, setInferring] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [statusRes, joinRes] = await Promise.all([
        localFetch("/dcm/status"),
        localFetch("/dcm/join-info").catch(() => null),
      ]);
      if (!statusRes.ok) {
        const body = await statusRes.json().catch(() => ({}));
        throw new Error(body.detail || `status failed (${statusRes.status})`);
      }
      setStatus(await statusRes.json());
      if (joinRes && joinRes.ok) setJoin(await joinRes.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const infer = async () => {
    if (!prompt.trim()) return;
    setInferring(true);
    setOutput(null);
    try {
      const res = await localFetch("/dcm/infer", {
        method: "POST",
        body: JSON.stringify({ prompt: prompt.trim(), max_tokens: 64 }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || `inference failed (${res.status})`);
      setOutput(body.content || "(empty response)");
    } catch (e) {
      setOutput(`⚠ ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setInferring(false);
    }
  };

  const readiness = dcmReadiness(status ?? undefined);
  const models = availableModels(status?.models);
  const peers = peerCount(status?.mesh_status);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border p-3">
        <div className="flex items-center gap-2">
          <Radio size={18} className="text-cyan-400" />
          <span className="font-semibold">DCM Node</span>
          <span className="text-[11px] text-muted-foreground">
            DecentraCode Mesh — local node
          </span>
        </div>
        <Button variant="outline" size="sm" onClick={load} disabled={loading} className="gap-1">
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} /> Refresh
        </Button>
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto p-3">
        {error && (
          <div className="flex items-start gap-2 rounded border border-red-500/30 bg-red-500/10 p-2 text-xs text-red-300">
            <AlertTriangle size={13} className="mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {status && !status.reachable && (
          <div className="rounded border border-amber-500/30 bg-amber-500/10 p-2 text-xs text-amber-200">
            Node unreachable at {status.base_url}. Start it with{" "}
            <code className="font-mono">cd decentracode/backend && npm start</code>
          </div>
        )}

        {status?.reachable && (
          <div className="rounded border border-border/60 p-3">
            <div className="mb-1 flex items-center gap-2 text-sm font-medium">
              <Cpu size={14} className="text-cyan-400" /> Node status
            </div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
              <span>
                Inference:{" "}
                <span className={readiness.ready ? "text-emerald-400" : "text-red-400"}>
                  {readiness.label}
                </span>
              </span>
              <span>
                Mesh peers: <span className="text-foreground">{peers}</span>
              </span>
              <span>
                Models available:{" "}
                <span className="text-foreground">
                  {models.length}/{status.models?.length || 0}
                </span>
              </span>
              <span>
                Node:{" "}
                <span className="font-mono">
                  {(status.mesh_status?.nodeId || "?").slice(0, 16)}
                </span>
              </span>
            </div>
            {readiness.detail && (
              <p className="mt-1 text-[10px] text-muted-foreground">{readiness.detail}</p>
            )}
          </div>
        )}

        {models.length > 0 && (
          <div className="rounded border border-border/60 p-3">
            <div className="mb-2 text-sm font-medium">Models</div>
            <ul className="space-y-1">
              {models.slice(0, 8).map((m) => (
                <li key={m.id} className="flex items-center gap-2 text-[11px]">
                  <span className="text-emerald-400">●</span>
                  <span className="font-mono">{m.id}</span>
                  <span className="text-muted-foreground">{m.runtime || ""}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {join?.lanHosts?.length ? (
          <div className="rounded border border-border/60 p-3">
            <div className="mb-1 flex items-center gap-2 text-sm font-medium">
              <Link2 size={14} className="text-violet-400" /> Join from another device
            </div>
            <p className="text-[11px] text-muted-foreground">
              On the same Wi-Fi, open:
            </p>
            <ul className="mt-1 space-y-0.5 font-mono text-[11px]">
              {join.lanHosts.slice(0, 3).map((host) => (
                <li key={host} className="text-cyan-300">
                  http://{host}:{join.ports?.peerPage ?? 3002}/node.html
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        <div className="rounded border border-border/60 p-3">
          <div className="mb-2 flex items-center gap-2 text-sm font-medium">
            <Play size={14} className="text-emerald-400" /> Run a prompt
          </div>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={2}
            placeholder="Ask the mesh…"
            className="w-full resize-none rounded border border-border/60 bg-transparent p-2 text-xs outline-none focus:border-accent"
          />
          <div className="mt-2 flex items-center justify-between">
            <span className="text-[10px] text-muted-foreground">
              Runs on the mesh's OpenAI-compatible endpoint; usage is DCT-metered
              mesh-side.
            </span>
            <Button
              size="sm"
              className="gap-1"
              disabled={inferring || !prompt.trim()}
              onClick={infer}
            >
              <Play size={11} /> {inferring ? "running…" : "Run"}
            </Button>
          </div>
          {output && (
            <pre className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap rounded bg-muted/40 p-2 text-[11px]">
              {output}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}
