import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Settings, Cpu, Shield, Globe, RefreshCw, Check, AlertCircle } from "lucide-react";
import { Button } from "../ui/button";
import { localFetch } from "../../utils/api";
import { useAppStore } from "../../store/appStore";

interface ProviderInfo {
  default_provider: string;
  fallback_chain: string[];
  providers: string[];
  models: Record<string, string[]>;
}

export function SettingsPanel() {
  const { privacyMode, setPrivacyMode } = useAppStore();
  const [providers, setProviders] = useState<ProviderInfo | null>(null);
  const [engineOnline, setEngineOnline] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(false);

  const loadAll = async () => {
    setLoading(true);
    try {
      const [pRes, hRes] = await Promise.all([
        localFetch("/v2/llm/providers"),
        localFetch("/health"),
      ]);
      setProviders(await pRes.json());
      setEngineOnline(hRes.ok);
    } catch {
      setEngineOnline(false);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAll();
  }, []);

  const modes = [
    { id: "standard", label: "Standard", desc: "Basic sanitization" },
    { id: "enhanced", label: "Enhanced", desc: "PII removal, tracking blocked" },
    { id: "maximum", label: "Maximum", desc: "Zero external calls" },
    { id: "local_only", label: "Local Only", desc: "No network access" },
  ] as const;

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border p-3">
        <div className="mb-2 flex items-center gap-2">
          <Settings size={18} className="text-accent" />
          <span className="font-semibold">Settings</span>
        </div>
        <p className="text-xs text-muted-foreground">
          LLM provider configuration and privacy defaults.
        </p>
      </div>

      <div className="flex-1 overflow-y-auto p-3">
        <AnimatePresence mode="wait">
          <motion.div
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.18 }}
            className="space-y-3"
          >
            <div className="rounded-md border border-border bg-card p-3">
              <div className="mb-2 flex items-center gap-1.5 text-xs font-medium">
                <Cpu size={14} />
                <span>Engine</span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">Status</span>
                <span className={`flex items-center gap-1 ${engineOnline ? "text-emerald-400" : "text-red-400"}`}>
                  {engineOnline ? <Check size={10} /> : <AlertCircle size={10} />}
                  {engineOnline === null ? "Checking…" : engineOnline ? "Online" : "Offline"}
                </span>
              </div>
              <div className="mt-1.5 flex items-center justify-between text-xs">
                <span className="text-muted-foreground">Default Provider</span>
                <span className="font-medium">{providers?.default_provider ?? "—"}</span>
              </div>
              {providers?.fallback_chain && (
                <div className="mt-1.5 text-xs">
                  <span className="text-muted-foreground">Fallback Chain</span>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {providers.fallback_chain.map((p) => (
                      <span key={p} className="rounded bg-muted px-1.5 py-0.5">{p}</span>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className="rounded-md border border-border bg-card p-3">
              <div className="mb-2 flex items-center gap-1.5 text-xs font-medium">
                <Globe size={14} />
                <span>Available Providers</span>
              </div>
              <div className="space-y-2">
                {providers?.providers.map((p) => (
                  <div key={p} className="text-xs">
                    <div className="font-medium">{p}</div>
                    <div className="mt-0.5 flex flex-wrap gap-1 text-muted-foreground">
                      {(providers.models[p] || []).map((m) => (
                        <span key={m} className="rounded bg-muted px-1 py-0.5 text-[10px]">{m}</span>
                      ))}
                      {(!providers.models[p] || providers.models[p].length === 0) && (
                        <span className="italic">no models listed</span>
                      )}
                    </div>
                  </div>
                ))}
                {(!providers?.providers || providers.providers.length === 0) && (
                  <p className="text-xs text-muted-foreground">No providers available.</p>
                )}
              </div>
            </div>

            <div className="rounded-md border border-border bg-card p-3">
              <div className="mb-2 flex items-center gap-1.5 text-xs font-medium">
                <Shield size={14} />
                <span>Privacy Mode</span>
              </div>
              <div className="space-y-1.5">
                {modes.map((m) => (
                  <button
                    key={m.id}
                    onClick={() => setPrivacyMode(m.id)}
                    className={`flex w-full items-center justify-between rounded-md px-2 py-1.5 text-xs transition-colors ${
                      privacyMode === m.id
                        ? "bg-muted text-foreground"
                        : "text-muted-foreground hover:bg-muted/50"
                    }`}
                  >
                    <div>
                      <span className="font-medium">{m.label}</span>
                      <span className="ml-2 text-[10px]">{m.desc}</span>
                    </div>
                    {privacyMode === m.id && <Check size={12} className="text-emerald-400" />}
                  </button>
                ))}
              </div>
            </div>
          </motion.div>
        </AnimatePresence>
      </div>

      <div className="border-t border-border p-3">
        <Button variant="outline" size="sm" className="w-full gap-1" onClick={loadAll}>
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} /> Refresh
        </Button>
      </div>
    </div>
  );
}
