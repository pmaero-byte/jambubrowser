import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { motion, AnimatePresence, Reorder } from "motion/react";
import { Button } from "../ui/button";
import {
  ArrowLeft, ArrowRight, RotateCcw, Home, Plus, X,
  Globe, Bug, BugOff, Cpu, Star, Clock, Bookmark,
  Shield,
} from "lucide-react";
import { useAppStore, BrowserTab } from "../../store/appStore";
import { useDevtoolsStore } from "../../store/devtoolsStore";
import { DevToolsPanel } from "./DevToolsPanel";
import { DownloadBar } from "./DownloadBar";

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
    reorderBrowserTabs,
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

  // ── Tab preview (hover thumbnail) ──
  // Positioned absolutely above the hovered tab. Cache by tabId so re-hovering
  // the same tab in quick succession doesn't re-fetch.
  const [preview, setPreview] = useState<{
    tabId: string;
    title: string;
    url: string;
    screenshot: string | null;
    loading: boolean;
    x: number;
    y: number;
  } | null>(null);
  const previewTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const previewAbortRef = useRef<AbortController | null>(null);
  const previewCacheRef = useRef<Map<string, string>>(new Map());

  const devtoolsOpen = useDevtoolsStore((s) => s.devtoolsOpen);
  const setDevtoolsOpen = useDevtoolsStore((s) => s.setDevtoolsOpen);

  // ── CDP Page Audit ──
  interface AuditFinding {
    category: string;
    severity: string;
    title: string;
    detail: string;
    score: number | null;
    icon?: string;
  }
  const [auditRunning, setAuditRunning] = useState(false);
  const [auditFindings, setAuditFindings] = useState<AuditFinding[]>([]);
  const [auditOpen, setAuditOpen] = useState(false);
  const auditRef = useRef<HTMLDivElement>(null);

  const runPageAudit = useCallback(async () => {
    if (!activeTab || !isTauri || !engineReady) return;
    setAuditRunning(true);
    setAuditOpen(true);
    try {
      const report = await invoke("browser_run_audit", { tabId: activeTab.id }) as {
        findings: AuditFinding[];
        overall_score: number;
        url: string;
        perf_metrics: Record<string, number>;
      };
      setAuditFindings(report.findings || []);
    } catch (e) {
      setAuditFindings([{ category: "error", severity: "info", title: "Audit failed", detail: String(e), score: null }]);
    } finally {
      setAuditRunning(false);
    }
  }, [activeTab, engineReady]);

  // ── Privacy toggles (CDP ad blocking + fingerprint protection) ──
  const [adblockEnabled, setAdblockEnabled] = useState(true);
  const [fpEnabled, setFpEnabled] = useState(true);

  const toggleAdblock = useCallback(async () => {
    if (!isTauri || !engineReady || !activeTab) return;
    const next = !adblockEnabled;
    try {
      await invoke("browser_set_adblock", { tabId: activeTab.id, enabled: next });
      setAdblockEnabled(next);
    } catch { /* */ }
  }, [adblockEnabled, activeTab, engineReady]);

  const toggleFingerprint = useCallback(async () => {
    if (!isTauri || !engineReady || !activeTab) return;
    const next = !fpEnabled;
    try {
      await invoke("browser_set_fingerprint", { tabId: activeTab.id, enabled: next });
      setFpEnabled(next);
    } catch { /* */ }
  }, [fpEnabled, activeTab, engineReady]);

  const auditSeverityColors: Record<string, string> = {
    critical: "text-red-400 bg-red-500/10 border-red-500/30",
    warning: "text-amber-400 bg-amber-500/10 border-amber-500/30",
    info: "text-blue-400 bg-blue-500/10 border-blue-500/30",
  };

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

  // ── Screenshot + title/URL polling ──
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
        // Also sync tab title and URL from the live page (via CDP Runtime.evaluate)
        const tabInfo = await invoke("browser_sync_tab", { tabId: activeTab.id }) as { url: string; title: string };
        if (tabInfo && (tabInfo.title !== activeTab.title || tabInfo.url !== activeTab.url)) {
          updateBrowserTab(activeTab.id, { title: tabInfo.title, url: tabInfo.url });
        }
      } catch { /* tab closing or Chrome restarting */ }
    };
    poll();
    pollRef.current = setInterval(poll, SCREENSHOT_INTERVAL);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [activeTab?.id, activeTab?.url, engineReady, updateBrowserTab]);

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

  // ── Tab preview hover handlers ──
  // Debounced 300ms; aborts in-flight fetches when the user moves to a
  // different tab so we never display a stale screenshot.
  const cancelPreview = useCallback(() => {
    if (previewTimerRef.current) { clearTimeout(previewTimerRef.current); previewTimerRef.current = null; }
    if (previewAbortRef.current) { previewAbortRef.current.abort(); previewAbortRef.current = null; }
  }, []);

  const handleTabPreviewEnter = useCallback((tab: BrowserTab, el: HTMLElement) => {
    if (tab.id === activeBrowserTabId) return; // active tab already visible in viewport
    if (tab.url === "about:blank" || !tab.url) return; // nothing to show
    cancelPreview();
    const rect = el.getBoundingClientRect();
    previewTimerRef.current = setTimeout(async () => {
      const cached = previewCacheRef.current.get(tab.id);
      if (cached) {
        setPreview({ tabId: tab.id, title: tab.title, url: tab.url, screenshot: cached, loading: false,
          x: rect.left, y: rect.top });
        return;
      }
      if (!isTauri || !engineReady) return;
      const ctrl = new AbortController();
      previewAbortRef.current = ctrl;
      setPreview({ tabId: tab.id, title: tab.title, url: tab.url, screenshot: null, loading: true,
        x: rect.left, y: rect.top });
      try {
        const dataUrl = await invoke("browser_capture_screenshot", { tabId: tab.id });
        if (ctrl.signal.aborted) return;
        const url = String(dataUrl);
        previewCacheRef.current.set(tab.id, url);
        setPreview({ tabId: tab.id, title: tab.title, url: tab.url, screenshot: url, loading: false,
          x: rect.left, y: rect.top });
      } catch {
        // CDP error — silently dismiss the preview.
        if (!ctrl.signal.aborted) setPreview(null);
      }
    }, 300);
  }, [activeBrowserTabId, engineReady, cancelPreview]);

  const handleTabPreviewLeave = useCallback(() => {
    cancelPreview();
    setPreview(null);
  }, [cancelPreview]);

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

  // Keyboard shortcuts (Cmd/Ctrl + key)
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      if (!mod) return;
      if (e.key >= "1" && e.key <= "9") {
        e.preventDefault();
        const idx = parseInt(e.key) - 1;
        if (idx < browserTabs.length) setActiveBrowserTab(browserTabs[idx].id);
        return;
      }
      switch (e.key.toLowerCase()) {
        case "t": e.preventDefault(); handleNewTab(); break;
        case "w": e.preventDefault(); activeTab && handleCloseTab(activeTab.id); break;
        case "l": e.preventDefault(); inputRef.current?.focus(); inputRef.current?.select(); break;
        case "d": e.preventDefault(); activeTab?.url && toggleBookmark(activeTab.url, activeTab.title || activeTab.url); break;
        case "r": e.preventDefault(); reload(); break;
        case "[": e.preventDefault(); goBack(); break;
        case "]": e.preventDefault(); goForward(); break;
        case "b": if (e.shiftKey) { e.preventDefault(); setShowBookmarks((v) => !v); } break;
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [browserTabs, activeTab, handleNewTab, handleCloseTab, toggleBookmark, reload, goBack, goForward, setActiveBrowserTab]);

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
          <Button variant="ghost" size="icon" className={`h-7 w-7 ${auditOpen ? "text-accent" : ""}`}
            onClick={() => {
              if (!auditOpen) { runPageAudit(); }
              else { setAuditOpen(false); setAuditFindings([]); }
            }}
            title="Run page audit (CDP)">
            <Shield size={14} />
          </Button>
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
            {isTauri && engineReady && (
              <>
                <button type="button" onClick={toggleAdblock} title={adblockEnabled ? "Ad blocking on" : "Ad blocking off"}
                  className={`shrink-0 ${adblockEnabled ? "text-emerald-400" : "text-muted-foreground/40 hover:text-muted-foreground"}`}>
                  <Shield size={11} />
                </button>
                <button type="button" onClick={toggleFingerprint} title={fpEnabled ? "Fingerprint protection on" : "Fingerprint protection off"}
                  className={`shrink-0 ${fpEnabled ? "text-emerald-400" : "text-muted-foreground/40 hover:text-muted-foreground"}`}>
                  <Cpu size={10} />
                </button>
              </>
            )}
            {isTauri && (
              <span className={`shrink-0 text-[10px] ${engineReady ? "text-emerald-500" : "text-amber-500"}`}>
                {engineReady ? "Ready" : "..."}
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
      <Reorder.Group
        axis="x"
        values={browserTabs}
        onReorder={reorderBrowserTabs}
        className="relative flex gap-1 overflow-x-auto border-b border-border bg-card/30 px-2 py-1"
        as="div"
      >
        {browserTabs.map((tab) => {
          const isActive = tab.id === activeBrowserTabId;
          return (
            <Reorder.Item
              key={tab.id} value={tab} layout
              as="button"
              onClick={() => setActiveBrowserTab(tab.id)}
              onMouseDown={(e) => { if (e.button === 1) { e.preventDefault(); handleCloseTab(tab.id); } }}
              onMouseEnter={(e) => handleTabPreviewEnter(tab, e.currentTarget)}
              onMouseLeave={handleTabPreviewLeave}
              whileTap={{ scale: 0.96 }}
              whileDrag={{ scale: 1.04, zIndex: 10 }}
              className={`group relative flex max-w-[180px] items-center gap-1.5 rounded-md pl-2 pr-1 py-1 text-xs touch-none cursor-grab active:cursor-grabbing ${
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
                  onPointerDown={(e) => e.stopPropagation()}
                  className="relative rounded p-0.5 opacity-0 transition-opacity group-hover:opacity-100 hover:bg-muted-foreground/20 ml-auto">
                  <X size={10} />
                </span>
              )}
            </Reorder.Item>
          );
        })}
      </Reorder.Group>

      {/* ── Tab preview popup ── */}
      <AnimatePresence>
        {preview && (
          <motion.div
            initial={{ opacity: 0, y: 6, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 6, scale: 0.96 }}
            transition={{ duration: 0.14, ease: "easeOut" }}
            style={{
              position: "fixed",
              left: Math.max(8, Math.min(preview.x, window.innerWidth - 348)),
              top: Math.max(8, preview.y - 200),
              zIndex: 60,
            }}
            className="pointer-events-none w-[340px] rounded-lg border border-border bg-card shadow-2xl overflow-hidden"
          >
            {preview.loading ? (
              <div className="flex h-[180px] items-center justify-center bg-muted/30">
                <motion.div
                  animate={{ rotate: 360 }}
                  transition={{ duration: 1.2, repeat: Infinity, ease: "linear" }}
                  className="h-5 w-5 rounded-full border-2 border-muted-foreground/30 border-t-muted-foreground"
                />
              </div>
            ) : preview.screenshot ? (
              <img src={preview.screenshot} alt={preview.title} className="block h-[180px] w-full object-cover bg-white" />
            ) : null}
            <div className="border-t border-border bg-card/95 px-2.5 py-1.5">
              <div className="truncate text-[11px] font-medium text-foreground">{preview.title || "New Tab"}</div>
              <div className="truncate text-[10px] text-muted-foreground">{preview.url}</div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

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

      {/* ── Download Bar ── */}
      <DownloadBar />

      {/* ── CDP Audit Overlay ── */}
      <AnimatePresence>
        {auditOpen && (
          <motion.div
            ref={auditRef}
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18 }}
            className="shrink-0 overflow-hidden border-t border-border bg-card/60"
          >
            <div className="max-h-[200px] overflow-y-auto p-3 space-y-1.5">
              <div className="flex items-center gap-2 mb-2">
                <Shield size={12} className="text-muted-foreground" />
                <span className="text-[11px] font-medium text-muted-foreground">CDP Page Audit</span>
                {auditRunning && (
                  <motion.span animate={{ rotate: 360 }} transition={{ duration: 1, repeat: Infinity, ease: "linear" }} className="inline-block">
                    <RotateCcw size={10} className="text-accent" />
                  </motion.span>
                )}
                {!auditRunning && auditFindings.length > 0 && (
                  <span className="text-[10px] text-muted-foreground">{auditFindings.length} finding(s)</span>
                )}
                <div className="flex-1" />
                <button onClick={() => setAuditOpen(false)} className="text-muted-foreground hover:text-foreground">
                  <X size={12} />
                </button>
              </div>
              {auditFindings.length === 0 && !auditRunning && (
                <p className="text-[11px] text-muted-foreground">No issues found.</p>
              )}
              {auditFindings.map((f, i) => {
                const colors = auditSeverityColors[f.severity] || auditSeverityColors.info;
                return (
                  <div key={i} className={`flex items-start gap-2 rounded border px-2.5 py-1.5 ${colors}`}>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className="text-[11px] font-medium capitalize">{f.category}</span>
                        <span className={`rounded px-1 py-0.5 text-[9px] font-medium uppercase ${colors}`}>
                          {f.severity}
                        </span>
                      </div>
                      <p className="text-[11px] mt-0.5">{f.title}</p>
                      <p className="text-[10px] text-muted-foreground mt-0.5 line-clamp-2">{f.detail}</p>
                    </div>
                    {f.score != null && (
                      <span className="shrink-0 text-[10px] tabular-nums">
                        {Math.round(f.score * 100)}%
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <DevToolsPanel />
    </div>
  );
}
