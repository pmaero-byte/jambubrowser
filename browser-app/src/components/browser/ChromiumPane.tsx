import { useState, useRef, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Button } from "../ui/button";
import {
  ArrowLeft, ArrowRight, RotateCcw, Home, Plus, X,
  Globe, Bug, BugOff, Cpu,
} from "lucide-react";
import { useAppStore, BrowserTab } from "../../store/appStore";
import { useDevtoolsStore } from "../../store/devtoolsStore";
import { DevToolsPanel } from "./DevToolsPanel";

// Tauri API — available only inside the Tauri shell
const isTauri = typeof window !== "undefined" && "__TAURI__" in window;
let invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;
let listen: (event: string, cb: (e: { payload: unknown }) => void) => Promise<() => void>;
if (isTauri) {
  const tauri = (window as unknown as Record<string, unknown>).__TAURI__ as {
    core: { invoke: typeof invoke; };
    event: { listen: typeof listen; };
  };
  invoke = tauri.core.invoke.bind(tauri.core);
  listen = tauri.event.listen.bind(tauri.event);
}

const SCREENSHOT_INTERVAL = 1000; // ms between viewport refreshes

export function ChromiumPane() {
  const {
    browserTabs, activeBrowserTabId, setActiveBrowserTab,
    closeBrowserTab, addBrowserTab, updateBrowserTab,
  } = useAppStore();

  const activeTab = browserTabs.find((t) => t.id === activeBrowserTabId) || browserTabs[0];
  const [inputUrl, setInputUrl] = useState(activeTab?.url || "");
  const [urlFocused, setUrlFocused] = useState(false);
  const [spinning, setSpinning] = useState(false);
  const [screenshot, setScreenshot] = useState<string | null>(null);
  const [engineReady, setEngineReady] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const screenshotRef = useRef<string | null>(null);

  const devtoolsOpen = useDevtoolsStore((s) => s.devtoolsOpen);
  const setDevtoolsOpen = useDevtoolsStore((s) => s.setDevtoolsOpen);

  // Listen for browser-ready event from Rust
  useEffect(() => {
    if (!isTauri) return;
    const cleanups: (() => void)[] = [];

    listen("browser-ready", () => {
      setEngineReady(true);
      setErrorMsg(null);
    }).then((fn) => cleanups.push(fn));

    listen("browser-error", (e) => {
      setErrorMsg(String(e.payload));
    }).then((fn) => cleanups.push(fn));

    return () => { cleanups.forEach((fn) => fn()); };
  }, []);

  // Sync input URL when active tab changes
  useEffect(() => {
    if (activeTab?.url) setInputUrl(activeTab.url);
  }, [activeTab?.url, activeBrowserTabId]);

  // Screenshot polling for the active tab
  useEffect(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    if (!isTauri || !engineReady || !activeTab?.url || activeTab.url === "about:blank") {
      setScreenshot(null);
      return;
    }

    const poll = async () => {
      try {
        const dataUrl = await invoke("browser_capture_screenshot", {
          tabId: activeTab.id,
        }) as string;
        if (dataUrl && dataUrl !== screenshotRef.current) {
          screenshotRef.current = dataUrl;
          setScreenshot(dataUrl);
        }
      } catch {
        // Tab might be closing or Chrome might be restarting
      }
    };

    poll(); // Immediate first capture
    pollRef.current = setInterval(poll, SCREENSHOT_INTERVAL);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [activeTab?.id, activeTab?.url, engineReady]);

  const normalizeUrl = (raw: string) => {
    if (!raw.trim()) return "about:blank";
    if (/^(https?:|file:|about:)/i.test(raw)) return raw;
    return `https://${raw}`;
  };

  const navigate = useCallback(async (url: string) => {
    const next = normalizeUrl(url);
    if (!activeTab) return;

    setSpinning(true);
    setTimeout(() => setSpinning(false), 700);
    screenshotRef.current = null;
    setScreenshot(null);

    if (isTauri && engineReady) {
      try {
        await invoke("browser_navigate", { tabId: activeTab.id, url: next });
        updateBrowserTab(activeTab.id, { url: next, title: next });
        setInputUrl(next);
      } catch (e) {
        setErrorMsg(String(e));
      }
    } else {
      updateBrowserTab(activeTab.id, { url: next, title: next });
      setInputUrl(next);
    }
  }, [activeTab, engineReady, updateBrowserTab]);

  const handleNewTab = useCallback(async () => {
    const url = "https://google.com";
    if (isTauri && engineReady) {
      try {
        const info = await invoke("browser_new_tab", { url }) as BrowserTab;
        addBrowserTab(info.url, info.title || info.url);
      } catch (e) {
        setErrorMsg(String(e));
        addBrowserTab(url, url);
      }
    } else {
      addBrowserTab(url, url);
    }
  }, [engineReady, addBrowserTab]);

  const handleCloseTab = useCallback(async (id: string) => {
    if (isTauri && engineReady) {
      try { await invoke("browser_close_tab", { tabId: id }); } catch { /* ignore */ }
    }
    closeBrowserTab(id);
  }, [engineReady, closeBrowserTab]);

  const reload = useCallback(async () => {
    if (!activeTab) return;
    setSpinning(true);
    setTimeout(() => setSpinning(false), 700);
    screenshotRef.current = null;
    setScreenshot(null);
    if (isTauri && engineReady) {
      try { await invoke("browser_reload", { tabId: activeTab.id }); } catch { /* ignore */ }
    }
  }, [activeTab, engineReady]);

  const goBack = useCallback(async () => {
    if (!activeTab) return;
    screenshotRef.current = null;
    setScreenshot(null);
    if (isTauri && engineReady) {
      try { await invoke("browser_go_back", { tabId: activeTab.id }); } catch { /* ignore */ }
    }
  }, [activeTab, engineReady]);

  const goForward = useCallback(async () => {
    if (!activeTab) return;
    screenshotRef.current = null;
    setScreenshot(null);
    if (isTauri && engineReady) {
      try { await invoke("browser_go_forward", { tabId: activeTab.id }); } catch { /* ignore */ }
    }
  }, [activeTab, engineReady]);

  return (
    <div className="flex h-full flex-col">
      {/* ── Address bar + nav buttons ── */}
      <div className="flex items-center gap-2 border-b border-border bg-card/50 px-2 py-1.5">
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={goBack}>
            <ArrowLeft size={14} />
          </Button>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={goForward}>
            <ArrowRight size={14} />
          </Button>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={reload}>
            <motion.span
              animate={spinning ? { rotate: 360 } : { rotate: 0 }}
              transition={{ duration: 0.7, ease: "easeOut" }}
              className="block"
            >
              <RotateCcw size={14} />
            </motion.span>
          </Button>
          <Button
            variant="ghost" size="icon"
            className={`h-7 w-7 ${devtoolsOpen ? "text-accent" : ""}`}
            onClick={() => setDevtoolsOpen(!devtoolsOpen)}
          >
            {devtoolsOpen ? <BugOff size={14} /> : <Bug size={14} />}
          </Button>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => navigate("about:blank")}>
            <Home size={14} />
          </Button>
        </div>

        {/* URL bar */}
        <motion.form
          className="flex flex-1 items-center gap-2 rounded-md border bg-background px-2"
          animate={{
            borderColor: urlFocused ? "rgba(99,102,241,0.5)" : "rgba(255,255,255,0.1)",
            boxShadow: urlFocused ? "0 0 0 3px rgba(99,102,241,0.15)" : "none",
          }}
          transition={{ duration: 0.18 }}
          onSubmit={(e) => { e.preventDefault(); navigate(inputUrl); }}
        >
          <Globe size={12} className="text-muted-foreground shrink-0" />
          <input
            type="text" value={inputUrl}
            onChange={(e) => setInputUrl(e.target.value)}
            onFocus={() => setUrlFocused(true)}
            onBlur={() => setUrlFocused(false)}
            className="flex-1 bg-transparent py-1 text-xs outline-none"
          />
          {isTauri && (
            <span className={`shrink-0 text-[10px] ${engineReady ? "text-emerald-500" : "text-amber-500"}`}>
              <Cpu size={10} className="inline mr-0.5" />
              {engineReady ? "Chromium" : "connecting..."}
            </span>
          )}
        </motion.form>

        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={handleNewTab}>
          <motion.span
            whileTap={{ scale: 0.85, rotate: 90 }}
            transition={{ type: "spring", stiffness: 400, damping: 18 }}
            className="block"
          >
            <Plus size={14} />
          </motion.span>
        </Button>
      </div>

      {/* ── Tab strip ── */}
      <div className="relative flex gap-1 overflow-x-auto border-b border-border bg-card/30 px-2 py-1">
        {browserTabs.map((tab) => {
          const isActive = tab.id === activeBrowserTabId;
          return (
            <motion.button
              key={tab.id} layout
              onClick={() => setActiveBrowserTab(tab.id)}
              whileTap={{ scale: 0.96 }}
              className={`group relative flex max-w-[160px] items-center gap-1.5 rounded-md px-2 py-1 text-xs ${
                isActive ? "text-foreground" : "text-muted-foreground hover:bg-muted"
              }`}
            >
              {isActive && (
                <motion.span
                  layoutId="chromium-tab-indicator"
                  className="absolute inset-0 rounded-md bg-background shadow-sm"
                  transition={{ type: "spring", stiffness: 380, damping: 30 }}
                />
              )}
              <span className="relative truncate">{tab.title || tab.url || "New Tab"}</span>
              {browserTabs.length > 1 && (
                <span
                  onClick={(e) => { e.stopPropagation(); handleCloseTab(tab.id); }}
                  className="relative rounded p-0.5 opacity-0 transition-opacity group-hover:opacity-100 hover:bg-muted-foreground/20"
                >
                  <X size={10} />
                </span>
              )}
            </motion.button>
          );
        })}
      </div>

      {/* ── Viewport: screenshot from Chromium ── */}
      <div className="relative min-h-0 flex-1 bg-background">
        <AnimatePresence mode="wait">
          {errorMsg && (
            <motion.div
              key="error" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              className="absolute inset-0 z-10 flex items-center justify-center bg-background/90"
            >
              <div className="text-center max-w-md px-4">
                <p className="text-sm font-medium text-red-400">Engine Error</p>
                <p className="mt-1 text-xs text-muted-foreground">{errorMsg}</p>
                <Button variant="outline" size="sm" className="mt-3" onClick={() => setErrorMsg(null)}>
                  Dismiss
                </Button>
              </div>
            </motion.div>
          )}

          {screenshot && activeTab?.url && activeTab.url !== "about:blank" ? (
            <motion.img
              key={`ss-${activeBrowserTabId}`}
              src={screenshot}
              alt={activeTab.title || "Page"}
              className="h-full w-full object-contain bg-white"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.22 }}
            />
          ) : (
            <motion.div
              key="empty"
              className="flex h-full flex-col items-center justify-center text-muted-foreground"
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 4 }}
              transition={{ duration: 0.2 }}
            >
              <motion.div
                animate={{ scale: [1, 1.06, 1] }}
                transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
              >
                {engineReady ? (
                  <Globe size={32} className="mb-3 text-border" />
                ) : (
                  <Cpu size={32} className="mb-3 text-amber-500/50" />
                )}
              </motion.div>
              <p className="text-sm font-medium">
                {isTauri && !engineReady ? "Starting Chromium engine..." : "No page loaded"}
              </p>
              <p className="mt-1 text-xs">
                {isTauri && !engineReady
                  ? "The browser engine is initializing. This may take a moment."
                  : "Enter a URL above or click a source in chat."}
              </p>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <DevToolsPanel />
    </div>
  );
}
