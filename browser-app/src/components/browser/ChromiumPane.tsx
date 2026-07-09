import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Button } from "../ui/button";
import {
  ArrowLeft, ArrowRight, RotateCcw, Home, Plus, X,
  Globe, Bug, BugOff, Cpu, Star, Clock, Bookmark,
} from "lucide-react";
import { useAppStore, BrowserTab } from "../../store/appStore";
import { useDevtoolsStore } from "../../store/devtoolsStore";
import { DevToolsPanel } from "./DevToolsPanel";

// ── Tauri API detection ──────────────────────────────────────────

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

// ── Types ────────────────────────────────────────────────────────

interface HistoryEntry {
  url: string;
  title: string;
  visitedAt: number;
}

interface BookmarkEntry {
  id: string;
  url: string;
  title: string;
  folder: string;
  addedAt: number;
}

// ── Persistence helpers ──────────────────────────────────────────

const HISTORY_KEY = "jambu-browser-history";
const BOOKMARKS_KEY = "jambu-browser-bookmarks";
const MAX_HISTORY = 500;
const MAX_SUGGESTIONS = 8;

function loadJson<T>(key: string, fallback: T): T {
  try { const v = localStorage.getItem(key); return v ? JSON.parse(v) as T : fallback; }
  catch { return fallback; }
}

function saveJson(key: string, value: unknown): void {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* quota exceeded */ }
}

// ── Favicon URL helper ───────────────────────────────────────────

function faviconUrl(url: string): string {
  try {
    const host = new URL(url).hostname;
    return `https://www.google.com/s2/favicons?domain=${host}&sz=32`;
  } catch { return ""; }
}

// ── Main Component ───────────────────────────────────────────────

const SCREENSHOT_INTERVAL = 1000;

export function ChromiumPane() {
  const {
    browserTabs, activeBrowserTabId, setActiveBrowserTab,
    closeBrowserTab, addBrowserTab, updateBrowserTab,
  } = useAppStore();

  const activeTab = browserTabs.find((t) => t.id === activeBrowserTabId) || browserTabs[0];

  // ── State ──
  const [inputUrl, setInputUrl] = useState(activeTab?.url || "");
  const [urlFocused, setUrlFocused] = useState(false);
  const [suggestionsVisible, setSuggestionsVisible] = useState(false);
  const [selectedSuggestion, setSelectedSuggestion] = useState(-1);
  const [spinning, setSpinning] = useState(false);
  const [screenshot, setScreenshot] = useState<string | null>(null);
  const [engineReady, setEngineReady] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [showBookmarks, setShowBookmarks] = useState(true);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const screenshotRef = useRef<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const devtoolsOpen = useDevtoolsStore((s) => s.devtoolsOpen);
  const setDevtoolsOpen = useDevtoolsStore((s) => s.setDevtoolsOpen);

  // ── History ──
  const [history, setHistory] = useState<HistoryEntry[]>(() => loadJson(HISTORY_KEY, []));

  const addToHistory = useCallback((url: string, title: string) => {
    if (url === "about:blank" || url.startsWith("chrome-")) return;
    setHistory((prev) => {
      const filtered = prev.filter((e) => e.url !== url);
      const next = [{ url, title, visitedAt: Date.now() }, ...filtered].slice(0, MAX_HISTORY);
      saveJson(HISTORY_KEY, next);
      return next;
    });
  }, []);

  // ── Bookmarks ──
  const [bookmarks, setBookmarks] = useState<BookmarkEntry[]>(() => loadJson(BOOKMARKS_KEY, []));

  const toggleBookmark = useCallback((url: string, title: string) => {
    setBookmarks((prev) => {
      const exists = prev.find((b) => b.url === url);
      let next: BookmarkEntry[];
      if (exists) {
        next = prev.filter((b) => b.url !== url);
      } else {
        next = [{ id: crypto.randomUUID(), url, title, folder: "Other", addedAt: Date.now() }, ...prev];
      }
      saveJson(BOOKMARKS_KEY, next);
      return next;
    });
  }, []);

  const removeBookmark = useCallback((id: string) => {
    setBookmarks((prev) => {
      const next = prev.filter((b) => b.id !== id);
      saveJson(BOOKMARKS_KEY, next);
      return next;
    });
  }, []);

  const isBookmarked = useCallback((url: string) => bookmarks.some((b) => b.url === url), [bookmarks]);

  // ── Suggestions from history + bookmarks ──
  const suggestions = useMemo(() => {
    if (!inputUrl.trim() || inputUrl === activeTab?.url) return [];
    const query = inputUrl.toLowerCase().trim();
    const results: { url: string; title: string; source: "history" | "bookmark" }[] = [];

    for (const e of history) {
      if (results.length >= MAX_SUGGESTIONS) break;
      if (e.url.toLowerCase().includes(query) || e.title.toLowerCase().includes(query)) {
        if (!results.find((r) => r.url === e.url)) {
          results.push({ url: e.url, title: e.title, source: "history" });
        }
      }
    }
    for (const b of bookmarks) {
      if (results.length >= MAX_SUGGESTIONS) break;
      if (b.url.toLowerCase().includes(query) || b.title.toLowerCase().includes(query)) {
        if (!results.find((r) => r.url === b.url)) {
          results.push({ url: b.url, title: b.title, source: "bookmark" });
        }
      }
    }
    return results;
  }, [inputUrl, history, bookmarks, activeTab?.url]);

  // ── Tauri events ──
  useEffect(() => {
    if (!isTauri) return;
    const cleanups: (() => void)[] = [];
    listen("browser-ready", () => { setEngineReady(true); setErrorMsg(null); })
      .then((fn) => cleanups.push(fn));
    listen("browser-error", (e) => { setErrorMsg(String(e.payload)); })
      .then((fn) => cleanups.push(fn));
    return () => cleanups.forEach((fn) => fn());
  }, []);

  // ── Sync URL input ──
  useEffect(() => {
    if (activeTab?.url) setInputUrl(activeTab.url);
  }, [activeTab?.url, activeBrowserTabId]);

  // ── Screenshot polling ──
  useEffect(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    if (!isTauri || !engineReady || !activeTab?.url || activeTab.url === "about:blank") {
      setScreenshot(null); return;
    }
    const poll = async () => {
      try {
        const dataUrl = await invoke("browser_capture_screenshot", { tabId: activeTab.id }) as string;
        if (dataUrl && dataUrl !== screenshotRef.current) {
          screenshotRef.current = dataUrl;
          setScreenshot(dataUrl);
        }
      } catch { /* tab closing or Chrome restarting */ }
    };
    poll();
    pollRef.current = setInterval(poll, SCREENSHOT_INTERVAL);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [activeTab?.id, activeTab?.url, engineReady]);

  // ── Window title sync ──
  useEffect(() => {
    if (!activeTab?.title) return;
    const title = activeTab.title === activeTab.url
      ? activeTab.url : `${activeTab.title} — Jambubrowser`;
    document.title = title;
    if (isTauri && engineReady) {
      invoke("browser_get_tab_info", { tabId: activeTab.id })
        .then((info: unknown) => {
          const ti = info as { title?: string };
          if (ti?.title) document.title = `${ti.title} — Jambubrowser`;
        }).catch(() => {});
    }
  }, [activeTab?.title, activeTab?.url, activeTab?.id, engineReady]);

  // ── Navigation helpers ──
  const normalizeUrl = (raw: string) => {
    if (!raw.trim()) return "about:blank";
    if (/^(https?:|file:|about:)/i.test(raw)) return raw;
    if (raw.includes(".") && !raw.includes(" ")) return `https://${raw}`;
    return `https://www.google.com/search?q=${encodeURIComponent(raw)}`;
  };

  const doNavigate = useCallback(async (url: string) => {
    const next = normalizeUrl(url);
    if (!activeTab) return;
    setSpinning(true); setTimeout(() => setSpinning(false), 700);
    screenshotRef.current = null; setScreenshot(null);
    setSuggestionsVisible(false); setSelectedSuggestion(-1);

    if (isTauri && engineReady) {
      try {
        await invoke("browser_navigate", { tabId: activeTab.id, url: next });
      } catch (e) { setErrorMsg(String(e)); }
    }
    updateBrowserTab(activeTab.id, { url: next, title: next });
    setInputUrl(next);
    addToHistory(next, next);
  }, [activeTab, engineReady, updateBrowserTab, addToHistory]);

  const handleNewTab = useCallback(async () => {
    const url = "about:blank";
    if (isTauri && engineReady) {
      try {
        const info = await invoke("browser_new_tab", { url }) as BrowserTab;
        addBrowserTab(info.url, info.title || "New Tab");
      } catch { addBrowserTab(url, "New Tab"); }
    } else { addBrowserTab(url, "New Tab"); }
  }, [engineReady, addBrowserTab]);

  const handleCloseTab = useCallback(async (id: string) => {
    if (isTauri && engineReady) {
      try { await invoke("browser_close_tab", { tabId: id }); } catch { /* */ }
    }
    closeBrowserTab(id);
  }, [engineReady, closeBrowserTab]);

  const reload = useCallback(async () => {
    if (!activeTab) return;
    setSpinning(true); setTimeout(() => setSpinning(false), 700);
    screenshotRef.current = null; setScreenshot(null);
    if (isTauri && engineReady) {
      try { await invoke("browser_reload", { tabId: activeTab.id }); } catch { /* */ }
    }
  }, [activeTab, engineReady]);

  const goBack = useCallback(async () => {
    if (!activeTab) return;
    screenshotRef.current = null; setScreenshot(null);
    if (isTauri && engineReady) {
      try { await invoke("browser_go_back", { tabId: activeTab.id }); } catch { /* */ }
    }
  }, [activeTab, engineReady]);

  const goForward = useCallback(async () => {
    if (!activeTab) return;
    screenshotRef.current = null; setScreenshot(null);
    if (isTauri && engineReady) {
      try { await invoke("browser_go_forward", { tabId: activeTab.id }); } catch { /* */ }
    }
  }, [activeTab, engineReady]);

  const selectSuggestion = useCallback((url: string, title: string) => {
    setInputUrl(url);
    setSuggestionsVisible(false);
    doNavigate(url).then(() => addToHistory(url, title));
  }, [doNavigate, addToHistory]);

  // ── Keyboard ──
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (!suggestionsVisible || suggestions.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedSuggestion((p) => Math.min(p + 1, suggestions.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedSuggestion((p) => Math.max(p - 1, -1));
    } else if (e.key === "Enter" && selectedSuggestion >= 0) {
      e.preventDefault();
      const s = suggestions[selectedSuggestion];
      selectSuggestion(s.url, s.title);
    } else if (e.key === "Escape") {
      setSuggestionsVisible(false);
    }
  }, [suggestionsVisible, suggestions, selectedSuggestion, selectSuggestion]);

  // ── Render ──
  return (
    <div className="flex h-full flex-col">
      {/* ── Bookmark bar ── */}
      {showBookmarks && bookmarks.length > 0 && (
        <div className="flex items-center gap-0.5 border-b border-border bg-card/40 px-3 py-1 overflow-x-auto">
          {bookmarks.slice(0, 20).map((bm) => (
            <button
              key={bm.id}
              onClick={() => doNavigate(bm.url)}
              className="flex items-center gap-1 shrink-0 rounded px-2 py-0.5 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground transition-colors max-w-[160px]"
              title={bm.url}
            >
              <img src={faviconUrl(bm.url)} alt="" className="w-3.5 h-3.5 rounded-sm" onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} />
              <span className="truncate">{bm.title || bm.url}</span>
              <X size={10} className="opacity-0 hover:opacity-100 hover:text-red-400 shrink-0 ml-0.5"
                onClick={(e) => { e.stopPropagation(); removeBookmark(bm.id); }} />
            </button>
          ))}
        </div>
      )}

      {/* ── Address bar + nav ── */}
      <div className="flex items-center gap-2 border-b border-border bg-card/50 px-2 py-1.5">
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={goBack}><ArrowLeft size={14} /></Button>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={goForward}><ArrowRight size={14} /></Button>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={reload}>
            <motion.span animate={spinning ? { rotate: 360 } : { rotate: 0 }} transition={{ duration: 0.7, ease: "easeOut" }} className="block">
              <RotateCcw size={14} />
            </motion.span>
          </Button>
          <Button variant="ghost" size="icon" className={`h-7 w-7 ${devtoolsOpen ? "text-accent" : ""}`}
            onClick={() => setDevtoolsOpen(!devtoolsOpen)}>
            {devtoolsOpen ? <BugOff size={14} /> : <Bug size={14} />}
          </Button>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => doNavigate("about:blank")}><Home size={14} /></Button>
        </div>

        {/* URL bar with autocomplete */}
        <div className="relative flex-1">
          <motion.form
            className="flex items-center gap-2 rounded-md border bg-background px-2"
            animate={{
              borderColor: urlFocused ? "rgba(99,102,241,0.5)" : "rgba(255,255,255,0.1)",
              boxShadow: urlFocused ? "0 0 0 3px rgba(99,102,241,0.15)" : "none",
            }}
            transition={{ duration: 0.18 }}
            onSubmit={(e) => { e.preventDefault(); setSuggestionsVisible(false); doNavigate(inputUrl); }}
          >
            <Globe size={12} className="text-muted-foreground shrink-0" />
            <input
              ref={inputRef} type="text" value={inputUrl}
              onChange={(e) => { setInputUrl(e.target.value); setSuggestionsVisible(true); setSelectedSuggestion(-1); }}
              onFocus={() => { setUrlFocused(true); if (inputUrl) setSuggestionsVisible(true); }}
              onBlur={() => { setTimeout(() => { setUrlFocused(false); setSuggestionsVisible(false); }, 150); }}
              onKeyDown={handleKeyDown}
              placeholder="Search or enter URL"
              className="flex-1 bg-transparent py-1 text-xs outline-none placeholder:text-muted-foreground/50"
            />
            {/* Bookmark star */}
            {activeTab?.url && activeTab.url !== "about:blank" && (
              <button type="button" onClick={() => toggleBookmark(activeTab.url, activeTab.title || activeTab.url)}
                className={`shrink-0 ${isBookmarked(activeTab.url) ? "text-amber-400" : "text-muted-foreground hover:text-foreground"}`}>
                <Star size={12} fill={isBookmarked(activeTab.url) ? "currentColor" : "none"} />
              </button>
            )}
            {/* Bookmark bar toggle */}
            <button type="button" onClick={() => setShowBookmarks((v) => !v)}
              className={`shrink-0 ${showBookmarks ? "text-accent" : "text-muted-foreground hover:text-foreground"}`}>
              <Bookmark size={12} />
            </button>
            {isTauri && (
              <span className={`shrink-0 text-[10px] ${engineReady ? "text-emerald-500" : "text-amber-500"}`}>
                <Cpu size={10} className="inline mr-0.5" />{engineReady ? "Chromium" : "..."}
              </span>
            )}
          </motion.form>

          {/* Suggestions dropdown */}
          <AnimatePresence>
            {suggestionsVisible && suggestions.length > 0 && (
              <motion.div
                initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.12 }}
                className="absolute top-full left-0 right-0 z-50 mt-1 rounded-md border border-border bg-popover shadow-lg max-h-64 overflow-y-auto"
              >
                {suggestions.map((s, i) => (
                  <button
                    key={`${s.url}-${i}`}
                    onMouseDown={(e) => { e.preventDefault(); selectSuggestion(s.url, s.title); }}
                    className={`flex items-center gap-2 w-full px-3 py-2 text-xs text-left hover:bg-muted transition-colors ${
                      i === selectedSuggestion ? "bg-muted" : ""
                    }`}
                  >
                    <img src={faviconUrl(s.url)} alt="" className="w-4 h-4 rounded-sm shrink-0"
                      onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} />
                    <div className="flex-1 min-w-0">
                      <div className="truncate font-medium">{s.title || s.url}</div>
                      <div className="truncate text-[10px] text-muted-foreground">{s.url}</div>
                    </div>
                    <span className="shrink-0 text-[10px] text-muted-foreground flex items-center gap-0.5">
                      {s.source === "bookmark" ? <Star size={10} className="text-amber-400" /> : <Clock size={10} />}
                      {s.source === "bookmark" ? "bookmark" : "history"}
                    </span>
                  </button>
                ))}
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={handleNewTab}>
          <motion.span whileTap={{ scale: 0.85, rotate: 90 }} transition={{ type: "spring", stiffness: 400, damping: 18 }} className="block">
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
              onMouseDown={(e) => { if (e.button === 1) { e.preventDefault(); handleCloseTab(tab.id); } }}
              whileTap={{ scale: 0.96 }}
              className={`group relative flex max-w-[180px] items-center gap-1.5 rounded-md pl-2 pr-1 py-1 text-xs ${
                isActive ? "text-foreground" : "text-muted-foreground hover:bg-muted"
              }`}
            >
              {isActive && (
                <motion.span layoutId="chromium-tab-indicator"
                  className="absolute inset-0 rounded-md bg-background shadow-sm"
                  transition={{ type: "spring", stiffness: 380, damping: 30 }} />
              )}
              {/* Favicon */}
              <img src={faviconUrl(tab.url)} alt="" className="relative w-3.5 h-3.5 rounded-sm shrink-0"
                onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} />
              {/* Bookmark pin for bookmarked tabs */}
              {isBookmarked(tab.url) && <Star size={8} className="relative shrink-0 text-amber-400" fill="currentColor" />}
              <span className="relative truncate">{tab.title || tab.url || "New Tab"}</span>
              {browserTabs.length > 1 && (
                <span onClick={(e) => { e.stopPropagation(); handleCloseTab(tab.id); }}
                  className="relative rounded p-0.5 opacity-0 transition-opacity group-hover:opacity-100 hover:bg-muted-foreground/20 ml-auto">
                  <X size={10} />
                </span>
              )}
            </motion.button>
          );
        })}
      </div>

      {/* ── Viewport ── */}
      <div className="relative min-h-0 flex-1 bg-background">
        <AnimatePresence mode="wait">
          {errorMsg && (
            <motion.div key="error" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              className="absolute inset-0 z-10 flex items-center justify-center bg-background/90">
              <div className="text-center max-w-md px-4">
                <p className="text-sm font-medium text-red-400">Engine Error</p>
                <p className="mt-1 text-xs text-muted-foreground">{errorMsg}</p>
                <Button variant="outline" size="sm" className="mt-3" onClick={() => setErrorMsg(null)}>Dismiss</Button>
              </div>
            </motion.div>
          )}
          {screenshot && activeTab?.url && activeTab.url !== "about:blank" ? (
            <motion.img key={`ss-${activeBrowserTabId}`} src={screenshot} alt={activeTab.title || "Page"}
              className="h-full w-full object-contain bg-white"
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.22 }} />
          ) : (
            <motion.div key="empty"
              className="flex h-full flex-col items-center justify-center text-muted-foreground"
              initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 4 }} transition={{ duration: 0.2 }}>
              <motion.div animate={{ scale: [1, 1.06, 1] }} transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}>
                {engineReady ? <Globe size={32} className="mb-3 text-border" /> : <Cpu size={32} className="mb-3 text-amber-500/50" />}
              </motion.div>
              <p className="text-sm font-medium">{isTauri && !engineReady ? "Starting Chromium engine..." : "No page loaded"}</p>
              <p className="mt-1 text-xs">{isTauri && !engineReady ? "The browser engine is initializing." : "Enter a URL above or open a bookmark."}</p>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <DevToolsPanel />
    </div>
  );
}
