import { useState, useRef, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Button } from "../ui/button";
import {
  ArrowLeft,
  ArrowRight,
  RotateCcw,
  Home,
  Plus,
  X,
  Globe,
  Bug,
  BugOff,
} from "lucide-react";
import { useAppStore } from "../../store/appStore";
import { useDevtoolsStore } from "../../store/devtoolsStore";
import { DevToolsPanel } from "./DevToolsPanel";

const PROXY_BASE = "http://localhost:8001/proxy/";

export function BrowserPane() {
  const {
    browserTabs,
    activeBrowserTabId,
    setActiveBrowserTab,
    closeBrowserTab,
    addBrowserTab,
    updateBrowserTab,
  } = useAppStore();

  const activeTab = browserTabs.find((t) => t.id === activeBrowserTabId) || browserTabs[0];
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [inputUrl, setInputUrl] = useState(activeTab?.url || "");
  const [urlFocused, setUrlFocused] = useState(false);
  // Bump on navigate so we can re-key the iframe (fade-in on URL change).
  const [navTick, setNavTick] = useState(0);
  const [spinning, setSpinning] = useState(false);

  const devtoolsOpen = useDevtoolsStore((s) => s.devtoolsOpen);
  const setDevtoolsOpen = useDevtoolsStore((s) => s.setDevtoolsOpen);

  useEffect(() => {
    if (activeTab?.url) setInputUrl(activeTab.url);
  }, [activeTab?.url, activeBrowserTabId]);

  // ── DevTools: listen for postMessage from the proxy-injected script ──
  const handleDevtoolsMessage = useCallback((event: MessageEvent) => {
    if (!event.data || event.data.source !== "jambu-devtools") return;
    const { type, data } = event.data;
    const store = useDevtoolsStore.getState();

    switch (type) {
      case "perf:navigation":
        store.setNavigation(data);
        break;
      case "perf:lcp":
        store.setLcp(data);
        break;
      case "perf:fcp":
        store.setFcp(data);
        break;
      case "perf:cls":
        store.setCls(data);
        break;
      case "perf:longtask":
        store.addLongTask(data);
        break;
      case "perf:resource":
        store.addResource(data);
        break;
      case "console":
        store.addConsoleEntry(data);
        break;
      case "error":
        // Map error events to console error entries
        store.addConsoleEntry({
          level: "error",
          message: `[${data.source || "JS Error"}] ${data.message}${data.filename ? `\n  at ${data.filename}:${data.lineno}:${data.colno}` : ""}`,
          timestamp: data.timestamp || Date.now(),
        });
        store.addError(data);
        break;
    }
  }, []);

  useEffect(() => {
    window.addEventListener("message", handleDevtoolsMessage);
    return () => window.removeEventListener("message", handleDevtoolsMessage);
  }, [handleDevtoolsMessage]);

  // Clear devtools data when navigating to a new page
  useEffect(() => {
    useDevtoolsStore.getState().clearAll();
  }, [navTick]);

  const normalizeUrl = (raw: string) => {
    if (!raw.trim()) return "about:blank";
    if (/^(https?:|file:|about:)/i.test(raw)) return raw;
    return `https://${raw}`;
  };

  const navigate = (url: string) => {
    const next = normalizeUrl(url);
    updateBrowserTab(activeBrowserTabId, { url: next, title: next });
    setInputUrl(next);
    setNavTick((t) => t + 1);
  };

  const reload = () => {
    if (!iframeRef.current) return;
    // Re-trigger fade-in: assign the same src back to itself.
    setSpinning(true);
    setTimeout(() => setSpinning(false), 700);
    setNavTick((t) => t + 1);
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-border bg-card/50 px-2 py-1.5">
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => { try { iframeRef.current?.contentWindow?.history.back(); } catch { /* cross-origin */ } }}>
            <ArrowLeft size={14} />
          </Button>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => { try { iframeRef.current?.contentWindow?.history.forward(); } catch { /* cross-origin */ } }}>
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
            variant="ghost"
            size="icon"
            className={`h-7 w-7 ${devtoolsOpen ? "text-accent" : ""}`}
            onClick={() => setDevtoolsOpen(!devtoolsOpen)}
            title={devtoolsOpen ? "Close DevTools" : "Open DevTools"}
          >
            {devtoolsOpen ? <BugOff size={14} /> : <Bug size={14} />}
          </Button>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => navigate("about:blank")}>
            <Home size={14} />
          </Button>
        </div>
        <motion.form
          className="flex flex-1 items-center gap-2 rounded-md border bg-background px-2"
          animate={{
            borderColor: urlFocused ? "rgba(99,102,241,0.5)" : "rgba(255,255,255,0.1)",
            boxShadow: urlFocused ? "0 0 0 3px rgba(99,102,241,0.15)" : "0 0 0 0 rgba(0,0,0,0)",
          }}
          transition={{ duration: 0.18 }}
          onSubmit={(e) => {
            e.preventDefault();
            navigate(inputUrl);
          }}
        >
          <Globe size={12} className="text-muted-foreground" />
          <input
            type="text"
            value={inputUrl}
            onChange={(e) => setInputUrl(e.target.value)}
            onFocus={() => setUrlFocused(true)}
            onBlur={() => setUrlFocused(false)}
            className="flex-1 bg-transparent py-1 text-xs outline-none"
          />
        </motion.form>
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => addBrowserTab()}>
          <motion.span
            whileTap={{ scale: 0.85, rotate: 90 }}
            transition={{ type: "spring", stiffness: 400, damping: 18 }}
            className="block"
          >
            <Plus size={14} />
          </motion.span>
        </Button>
      </div>

      <div className="relative flex gap-1 overflow-x-auto border-b border-border bg-card/30 px-2 py-1">
        {browserTabs.map((tab) => {
          const isActive = tab.id === activeBrowserTabId;
          return (
            <motion.button
              key={tab.id}
              layout
              onClick={() => setActiveBrowserTab(tab.id)}
              whileTap={{ scale: 0.96 }}
              className={`group relative flex max-w-[160px] items-center gap-1.5 rounded-md px-2 py-1 text-xs ${
                isActive
                  ? "text-foreground"
                  : "text-muted-foreground hover:bg-muted"
              }`}
            >
              {isActive && (
                <motion.span
                  layoutId="browser-tab-indicator"
                  className="absolute inset-0 rounded-md bg-background shadow-sm"
                  transition={{ type: "spring", stiffness: 380, damping: 30 }}
                />
              )}
              <span className="relative truncate">{tab.title || tab.url || "New Tab"}</span>
              {browserTabs.length > 1 && (
                <span
                  onClick={(e) => {
                    e.stopPropagation();
                    closeBrowserTab(tab.id);
                  }}
                  className="relative rounded p-0.5 opacity-0 transition-opacity group-hover:opacity-100 hover:bg-muted-foreground/20"
                >
                  <X size={10} />
                </span>
              )}
            </motion.button>
          );
        })}
      </div>

      <div className="relative min-h-0 flex-1 bg-background">
        <AnimatePresence mode="wait">
          {activeTab?.url && activeTab.url !== "about:blank" ? (
            <motion.iframe
              key={`${activeBrowserTabId}-${navTick}`}
              ref={iframeRef}
              src={PROXY_BASE + activeTab.url}
              title="Browser"
              sandbox="allow-scripts allow-same-origin allow-popups allow-forms"
              className="h-full w-full border-0"
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
                <Globe size={32} className="mb-3 text-border" />
              </motion.div>
              <p className="text-sm font-medium">No page loaded</p>
              <p className="mt-1 text-xs">Enter a URL above or click a source in chat.</p>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* DevTools Panel */}
      <DevToolsPanel />
    </div>
  );
}
