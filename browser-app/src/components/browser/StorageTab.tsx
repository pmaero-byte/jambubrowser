import { useEffect, useState, useCallback, useRef } from "react";
import { Database, RefreshCw, Trash2, AlertTriangle, Cookie, HardDrive, Layers } from "lucide-react";
import { useDevtoolsStore } from "../../store/devtoolsStore";

type StorageKind = "localStorage" | "sessionStorage" | "cookies";

interface StorageState {
  kind: StorageKind;
  data: Array<{ key: string; value: string }>;
  loading: boolean;
  error: string | null;
  rev: number;
}

const SCRIPTS: Record<StorageKind, string> = {
  localStorage: `(function(){ try { return JSON.stringify(Object.entries(window.localStorage).map(([k,v])=>({key:k, value:String(v)}))); } catch(e){ return '[]'; } })()`,
  sessionStorage: `(function(){ try { return JSON.stringify(Object.entries(window.sessionStorage).map(([k,v])=>({key:k, value:String(v)}))); } catch(e){ return '[]'; } })()`,
  // Cookies come from a Tauri command, not browser_evaluate, because
  // the CDP Network domain is more reliable than document.cookie.
  cookies: "/* not used */",
};

export function StorageTab() {
  const { activeTab } = useDevtoolsStore();
  const [kind, setKind] = useState<StorageKind>("localStorage");
  const [state, setState] = useState<StorageState>({
    kind: "localStorage", data: [], loading: false, error: null, rev: 0,
  });
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    if (!activeTab) return;
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setState((s) => ({ ...s, kind, loading: true, error: null, rev: s.rev + 1 }));
    try {
      const tauri = (window as unknown as Record<string, unknown>).__TAURI__ as {
        core: { invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown> };
      };
      if (kind === "cookies") {
        const raw = await tauri.core.invoke("browser_get_cookies", { tabId: activeTab });
        if (ctrl.signal.aborted) return;
        const arr = Array.isArray(raw) ? (raw as Array<Record<string, unknown>>) : [];
        const data = arr.map((c) => ({
          key: String(c.name ?? ""),
          value: `${c.value ?? ""}${c.domain ? ` (${c.domain}${c.path ? c.path : ""})` : ""}`,
        }));
        setState((s) => ({ ...s, kind, data, loading: false, error: null }));
      } else {
        const raw = await tauri.core.invoke("browser_evaluate", {
          tabId: activeTab,
          expression: SCRIPTS[kind],
        });
        if (ctrl.signal.aborted) return;
        const text = String(raw);
        const data = text ? (JSON.parse(text) as Array<{ key: string; value: string }>) : [];
        setState((s) => ({ ...s, kind, data, loading: false, error: null }));
      }
    } catch (e) {
      if (ctrl.signal.aborted) return;
      setState((s) => ({ ...s, kind, loading: false, error: String(e) }));
    }
  }, [activeTab, kind]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const clearKind = useCallback(async () => {
    if (!activeTab) return;
    if (!confirm(`Clear all ${kind}?`)) return;
    const tauri = (window as unknown as Record<string, unknown>).__TAURI__ as {
      core: { invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown> };
    };
    if (kind === "cookies") {
      try { await tauri.core.invoke("browser_clear_cookies", { tabId: activeTab }); } catch { /* */ }
    } else {
      try {
        await tauri.core.invoke("browser_evaluate", {
          tabId: activeTab,
          expression: `try { ${kind}.clear(); } catch(e){}`,
        });
      } catch { /* */ }
    }
    refresh();
  }, [activeTab, kind, refresh]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-border/30 bg-surface/50 px-2 py-1.5">
        <Database size={11} className="text-accent/70" />
        <span className="text-[11px] text-muted-foreground/70 font-medium">Storage</span>
        <div className="flex-1" />
        <button
          onClick={clearKind} disabled={state.loading}
          title={`Clear all ${state.kind}`}
          className="rounded-md p-1 text-muted-foreground/50 transition-all duration-150 hover:bg-red-500/15 hover:text-red-400 disabled:opacity-40"
        >
          <Trash2 size={11} />
        </button>
        <button
          onClick={refresh} disabled={state.loading}
          className="rounded-md p-1 text-muted-foreground/50 transition-all duration-150 hover:bg-muted/30 hover:text-foreground disabled:opacity-40"
          title="Refresh"
        >
          <RefreshCw size={11} className={state.loading ? "animate-spin" : ""} />
        </button>
      </div>
      <div className="flex items-center gap-1 border-b border-border/30 bg-surface/30 px-2 py-1">
        <KindButton current={kind} value="localStorage" icon={<HardDrive size={10} />} onClick={setKind} />
        <KindButton current={kind} value="sessionStorage" icon={<Layers size={10} />} onClick={setKind} />
        <KindButton current={kind} value="cookies" icon={<Cookie size={10} />} onClick={setKind} />
      </div>
      <div className="flex-1 overflow-y-auto p-1 text-[11px]">
        {state.error && (
          <div className="m-2 flex items-start gap-1.5 rounded-md bg-red-500/10 border border-red-500/20 p-2 text-[10px] text-red-400/80">
            <AlertTriangle size={10} className="mt-0.5 shrink-0" />
            <span>{state.error}</span>
          </div>
        )}
        {!state.error && state.data.length === 0 && !state.loading && (
          <div className="flex flex-col items-center justify-center py-8 text-muted-foreground/40">
            <Database size={24} className="mb-2" />
            <span className="text-[10px]">No entries in {state.kind}.</span>
          </div>
        )}
        {state.data.length > 0 && (
          <table className="w-full font-mono text-[10px]">
            <thead className="sticky top-0 bg-surface/80 backdrop-blur-sm text-[10px] uppercase text-muted-foreground/60">
              <tr>
                <th className="px-2 py-1 text-left font-medium">Key</th>
                <th className="px-2 py-1 text-left font-medium">Value</th>
              </tr>
            </thead>
            <tbody>
              {state.data.map((entry, i) => (
                <tr key={`${entry.key}-${i}`} className="border-t border-border/20 transition-colors duration-100 hover:bg-muted/20">
                  <td className="max-w-[40%] truncate px-2 py-1 text-amber-300/80" title={entry.key}>{entry.key}</td>
                  <td className="max-w-[60%] truncate px-2 py-1 text-foreground/75" title={entry.value}>{entry.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function KindButton({
  current, value, icon, onClick,
}: {
  current: StorageKind;
  value: StorageKind;
  icon: React.ReactNode;
  onClick: (k: StorageKind) => void;
}) {
  const active = current === value;
  return (
    <button
      onClick={() => onClick(value)}
      className={`flex items-center gap-1 rounded-md px-2 py-0.5 text-[10px] font-medium transition-all duration-150 ${
        active ? "bg-accent/15 text-foreground" : "text-muted-foreground/50 hover:bg-muted/20 hover:text-muted-foreground"
      }`}
    >
      {icon}
      {value}
    </button>
  );
}
