import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Settings, Cpu, Shield, Globe, RefreshCw, Check, AlertCircle, HardDrive } from "lucide-react";
import { Button } from "../ui/button";
import { localFetch } from "../../utils/api";
import { useAppStore } from "../../store/appStore";

const isTauri = typeof window !== "undefined" && "__TAURI__" in window;
let tauriInvoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;
if (isTauri) {
  const tauri = (window as unknown as Record<string, unknown>).__TAURI__ as {
    core: { invoke: typeof tauriInvoke };
  };
  tauriInvoke = tauri.core.invoke.bind(tauri.core);
}

interface ProviderInfo {
  default_provider: string;
  fallback_chain: string[];
  providers: string[];
  models: Record<string, string[]>;
}

interface BrowserSettings {
  persistent_profile: boolean;
  disabled_extensions: string[];
}

export function SettingsPanel() {
  const { privacyMode, setPrivacyMode } = useAppStore();
  const [providers, setProviders] = useState<ProviderInfo | null>(null);
  const [engineOnline, setEngineOnline] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(false);
  // null = not loaded yet (or not a Tauri host)
  const [persistentProfile, setPersistentProfile] = useState<boolean | null>(null);
  const [profilePendingRestart, setProfilePendingRestart] = useState(false);

  useEffect(() => {
    if (!isTauri) return;
    tauriInvoke("browser_get_settings")
      .then((s) => setPersistentProfile((s as BrowserSettings).persistent_profile))
      .catch(() => setPersistentProfile(null));
  }, []);

  const togglePersistentProfile = () => {
    const next = !persistentProfile;
    setPersistentProfile(next);
    tauriInvoke("browser_set_persistent_profile", { enabled: next })
      .then(() => setProfilePendingRestart(true))
      .catch(() => setPersistentProfile(!next));
  };

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

            {persistentProfile !== null && (
              <div className="rounded-md border border-border bg-card p-3">
                <div className="mb-2 flex items-center gap-1.5 text-xs font-medium">
                  <HardDrive size={14} />
                  <span>Browser Profile</span>
                </div>
                <button
                  onClick={togglePersistentProfile}
                  className="flex w-full items-center justify-between rounded-md px-2 py-1.5 text-xs transition-colors text-muted-foreground hover:bg-muted/50"
                  data-testid="persistent-profile-toggle"
                >
                  <div className="text-left">
                    <span className="font-medium text-foreground">Persistent profile</span>
                    <span className="ml-2 text-[10px]">
                      Keep cookies &amp; logins across restarts
                    </span>
                  </div>
                  {persistentProfile && <Check size={12} className="text-emerald-400" />}
                </button>
                <p className="mt-1 px-2 text-[10px] text-muted-foreground">
                  Off by default: every launch uses a fresh throwaway profile that is
                  wiped on exit (forensic safety).
                  {profilePendingRestart && (
                    <span className="text-amber-400"> Applies after a browser restart.</span>
                  )}
                </p>
              </div>
            )}

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
