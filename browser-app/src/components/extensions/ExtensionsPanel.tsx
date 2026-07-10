import { useState, useEffect } from "react";
import { motion } from "motion/react";
import {
  Puzzle, ToggleLeft, ToggleRight, ExternalLink,
  RefreshCw, XCircle,
} from "lucide-react";
import { Button } from "../ui/button";

const isTauri = typeof window !== "undefined" && "__TAURI__" in window;
let invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;
if (isTauri) {
  const tauri = (window as unknown as Record<string, unknown>).__TAURI__ as {
    core: { invoke: typeof invoke };
  };
  invoke = tauri.core.invoke.bind(tauri.core);
}

interface ExtensionManifest {
  id: string;
  name: string;
  version: string;
  description: string;
  enabled: boolean;
}

export function ExtensionsPanel() {
  const [extensions, setExtensions] = useState<ExtensionManifest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isTauri) {
      setError("Extensions are only available in the desktop app.");
      setLoading(false);
      return;
    }
    invoke("browser_list_extensions")
      .then((data) => {
        setExtensions(data as ExtensionManifest[]);
        setLoading(false);
      })
      .catch((e) => {
        setError(String(e));
        setLoading(false);
      });
  }, []);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="shrink-0 border-b border-white/10 p-4">
        <div className="flex items-center gap-3">
          <Puzzle className="h-5 w-5 text-muted-foreground" />
          <h2 className="text-sm font-medium">Extensions</h2>
          {!loading && (
            <span className="rounded-full bg-white/10 px-2 py-0.5 text-xs text-muted-foreground">
              {extensions.length}
            </span>
          )}
          <div className="flex-1" />
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setLoading(true);
              setError(null);
              invoke("browser_list_extensions")
                .then((data) => {
                  setExtensions(data as ExtensionManifest[]);
                  setLoading(false);
                })
                .catch((e) => {
                  setError(String(e));
                  setLoading(false);
                });
            }}
          >
            <RefreshCw className="mr-1 h-4 w-4" /> Refresh
          </Button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {loading && (
          <div className="flex items-center justify-center h-32">
            <motion.div
              animate={{ rotate: 360 }}
              transition={{ duration: 1, repeat: Infinity, ease: "linear" }}
            >
              <RefreshCw className="h-5 w-5 text-muted-foreground" />
            </motion.div>
          </div>
        )}

        {!loading && error && (
          <div className="flex flex-col items-center justify-center h-32 gap-2">
            <XCircle className="h-8 w-8 text-red-400" />
            <p className="text-sm text-red-400">{error}</p>
          </div>
        )}

        {!loading && !error && extensions.length === 0 && (
          <div className="flex flex-col items-center justify-center h-32 gap-2">
            <Puzzle className="h-8 w-8 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">No extensions installed</p>
            <p className="text-xs text-muted-foreground/60">
              Unpacked extensions in the extensions directory will appear here.
            </p>
          </div>
        )}

        {!loading && !error && extensions.length > 0 && (
          <div className="space-y-2">
            {extensions.map((ext) => (
              <motion.div
                key={ext.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                className="flex items-start gap-3 rounded-lg border border-white/10 bg-white/5 p-3"
              >
                <span className="flex h-8 w-8 items-center justify-center rounded-md bg-white/10 text-sm font-medium shrink-0">
                  {ext.name.charAt(0).toUpperCase()}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium truncate">{ext.name}</span>
                    <span className="shrink-0 rounded bg-white/10 px-1.5 py-0.5 text-[10px] text-muted-foreground">
                      v{ext.version}
                    </span>
                  </div>
                  {ext.description && (
                    <p className="mt-0.5 text-xs text-muted-foreground line-clamp-2">
                      {ext.description}
                    </p>
                  )}
                  <span className="mt-1 inline-block text-[10px] text-muted-foreground/50">
                    ID: {ext.id}
                  </span>
                </div>
                <button
                  className={`shrink-0 mt-1 ${ext.enabled ? "text-emerald-400" : "text-muted-foreground"}`}
                  title={ext.enabled ? "Enabled" : "Disabled"}
                >
                  {ext.enabled ? <ToggleRight size={18} /> : <ToggleLeft size={18} />}
                </button>
              </motion.div>
            ))}
          </div>
        )}

        {!loading && !error && !isTauri && (
          <div className="flex flex-col items-center justify-center h-32 gap-2">
            <ExternalLink className="h-8 w-8 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">Desktop app required</p>
            <p className="text-xs text-muted-foreground/60">
              Extensions can only be managed from the desktop build.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
