import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { motion, AnimatePresence, Reorder } from "motion/react";
import { Button } from "../ui/button";
import {
  ArrowLeft, ArrowRight, RotateCcw, Home, Plus, X,
  Globe, Bug, BugOff, Cpu, Star, Clock, Bookmark,
  Shield, FileText, Download, BookOpen, KeyRound,
  EllipsisVertical, FileDown, FileUp,
} from "lucide-react";
import { useAppStore, BrowserTab } from "../../store/appStore";
import { useBrowsingHistoryStore } from "../../store/browsingHistoryStore";
import { useDevtoolsStore } from "../../store/devtoolsStore";
import { DevToolsPanel } from "./DevToolsPanel";
import { DownloadBar } from "./DownloadBar";
import { ReaderMode } from "./ReaderMode";
import { FindBar } from "./FindBar";
import {
  buildFindExpression, buildClearFindExpression, FindDirection, FindResult,
} from "./findInPage";
import {
  BookmarkEntry, toNetscapeBookmarksHtml, parseNetscapeBookmarksHtml, mergeBookmarks,
} from "./bookmarksIO";

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
// BookmarkEntry is shared with the import/export helpers in bookmarksIO.ts.

// ── Persistence helpers ──────────────────────────────────────────

const BOOKMARKS_KEY = "jambu-browser-bookmarks";
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

// ── PDF detection helper ─────────────────────────────────────────
// Lightweight check on the URL only. Strips query/fragment so
// `?file=foo.pdf` doesn't false-positive. Real content-type sniffing
// would require a CDP Network.responseReceived listener, which is
// overkill for an indicator badge.

export function isPdfUrl(url: string): boolean {
  if (!url) return false;
  const noQuery = url.split(/[?#]/)[0];
  return noQuery.toLowerCase().endsWith(".pdf");
}

// ── URL-bar helpers ──────────────────────────────────────────────

// Search fallback for URL-bar input that isn't a URL. Uses the local
// SearXNG metasearch instance (docker-compose maps it to localhost:8888;
// see SEARXNG_URL in backend/modules/search.py).
const SEARCH_FALLBACK_URL = "http://localhost:8888/search";

export function normalizeUrl(raw: string): string {
  if (!raw.trim()) return "about:blank";
  if (/^(https?:|file:|about:)/i.test(raw)) return raw;
  if (raw.includes(".") && !raw.includes(" ")) return `https://${raw}`;
  return `${SEARCH_FALLBACK_URL}?q=${encodeURIComponent(raw)}`;
}

// ── Input forwarding helpers ─────────────────────────────────────

export interface PagePoint {
  x: number;
  y: number;
}

/**
 * Translate client (screen) coordinates into page viewport coordinates.
 *
 * The screenshot is drawn into the container with `object-contain`, so the
 * image content is letterboxed and centered: the content rect is the
 * natural capture size scaled by min(rect.w/natW, rect.h/natH). Returns
 * null when the point falls in the letterbox margin, so clicks on the
 * empty bars don't hit page elements at the viewport edges.
 */
export function clientToPageCoords(
  clientX: number,
  clientY: number,
  rect: { left: number; top: number; width: number; height: number },
  naturalWidth: number,
  naturalHeight: number,
): PagePoint | null {
  if (!naturalWidth || !naturalHeight || !rect.width || !rect.height) return null;
  const scale = Math.min(rect.width / naturalWidth, rect.height / naturalHeight);
  const contentW = naturalWidth * scale;
  const contentH = naturalHeight * scale;
  const offsetX = rect.left + (rect.width - contentW) / 2;
  const offsetY = rect.top + (rect.height - contentH) / 2;
  const x = (clientX - offsetX) / scale;
  const y = (clientY - offsetY) / scale;
  if (x < 0 || y < 0 || x > naturalWidth || y > naturalHeight) return null;
  return { x: Math.round(x), y: Math.round(y) };
}

// Windows virtual key codes for non-text keys, sent as CDP
// windowsVirtualKeyCode/nativeVirtualKeyCode so the page's key handlers
// see the same codes a real keyboard produces.
const SPECIAL_KEY_CODES: Record<string, number> = {
  Backspace: 8,
  Tab: 9,
  Enter: 13,
  Escape: 27,
  PageUp: 33,
  PageDown: 34,
  End: 35,
  Home: 36,
  ArrowLeft: 37,
  ArrowUp: 38,
  ArrowRight: 39,
  ArrowDown: 40,
  Delete: 46,
};

// CDP modifier bitmask: Alt=1, Ctrl=2, Meta=4, Shift=8.
function cdpModifiers(e: React.KeyboardEvent | React.MouseEvent): number {
  return (e.altKey ? 1 : 0) | (e.ctrlKey ? 2 : 0) | (e.metaKey ? 4 : 0) | (e.shiftKey ? 8 : 0);
}

// CDP button name for a DOM MouseEvent.button value.
function cdpButton(button: number): string {
  return button === 1 ? "middle" : button === 2 ? "right" : "left";
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
  // Input forwarding: focusable viewport container + the screenshot image
  // (its natural size is the CDP capture viewport used for coordinate math).
  const viewportRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const lastMouseMoveRef = useRef(0);
  const inputRefreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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

  // ── History (lives in the browsingHistoryStore so HistoryPanel can read it) ──
  const history = useBrowsingHistoryStore((s) => s.entries);
  const addToHistory = useBrowsingHistoryStore((s) => s.addEntry);

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

  // ── Bookmarks import/export (Netscape HTML, Chrome/Firefox-compatible) ──
  const [bmMenuOpen, setBmMenuOpen] = useState(false);
  const importInputRef = useRef<HTMLInputElement>(null);

  const exportBookmarks = useCallback(() => {
    setBmMenuOpen(false);
    if (bookmarks.length === 0) return;
    const html = toNetscapeBookmarksHtml(bookmarks);
    const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "jambu-bookmarks.html";
    a.click();
    URL.revokeObjectURL(url);
  }, [bookmarks]);

  const importBookmarks = useCallback(async (file: File) => {
    setBmMenuOpen(false);
    try {
      const imported = parseNetscapeBookmarksHtml(await file.text());
      setBookmarks((prev) => {
        const { merged } = mergeBookmarks(prev, imported);
        saveJson(BOOKMARKS_KEY, merged);
        return merged;
      });
    } catch { /* unreadable file — leave bookmarks untouched */ }
  }, []);

  // ── Find in page (Ctrl/Cmd+F) ──
  // The find logic itself runs inside the page: findInPageCore is
  // serialized into a browser_evaluate expression (see findInPage.ts).
  const [findOpen, setFindOpen] = useState(false);
  const [findQuery, setFindQuery] = useState("");
  const [findResult, setFindResult] = useState<FindResult | null>(null);
  const [findFocusToken, setFindFocusToken] = useState(0);

  const runFind = useCallback(async (query: string, direction: FindDirection) => {
    if (!isTauri || !engineReady || !activeTab || !query.trim()) return;
    try {
      const raw = await invoke("browser_evaluate", {
        tabId: activeTab.id,
        expression: buildFindExpression(query, direction),
      }) as string;
      setFindResult(JSON.parse(raw) as FindResult);
    } catch { /* tab closing or Chrome restarting */ }
  }, [activeTab, engineReady]);

  const closeFind = useCallback(() => {
    setFindOpen(false);
    setFindQuery("");
    setFindResult(null);
    if (isTauri && engineReady && activeTab) {
      invoke("browser_evaluate", {
        tabId: activeTab.id,
        expression: buildClearFindExpression(),
      }).catch(() => { /* tab closing */ });
    }
  }, [activeTab, engineReady]);

  const openFind = useCallback(() => {
    setFindOpen(true);
    setFindFocusToken((t) => t + 1); // refocus the input even if already open
  }, []);

  // Debounced re-scan on query change; also re-run after tab switches and
  // navigations (the highlights live in the old page and are gone).
  useEffect(() => {
    if (!findOpen) return;
    if (!findQuery.trim()) { setFindResult(null); return; }
    const id = setTimeout(() => runFind(findQuery, "init"), 200);
    return () => clearTimeout(id);
  }, [findOpen, findQuery, runFind]);

  // Switching tabs abandons the find — each tab has its own page state.
  useEffect(() => {
    closeFind();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab?.id]);

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
  // Shared by the 1s interval and the post-input refresh, so clicks and
  // keystrokes show their effect immediately instead of at the next tick.
  const poll = useCallback(async () => {
    if (!activeTab) return;
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
  }, [activeTab, updateBrowserTab]);

  useEffect(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    if (!isTauri || !engineReady || !activeTab?.url || activeTab.url === "about:blank") {
      setScreenshot(null); return;
    }
    poll();
    pollRef.current = setInterval(poll, SCREENSHOT_INTERVAL);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [activeTab?.id, activeTab?.url, engineReady, poll]);

  // Debounced screenshot refresh right after a forwarded input event.
  const scheduleInputRefresh = useCallback((delayMs = 250) => {
    if (inputRefreshTimerRef.current) clearTimeout(inputRefreshTimerRef.current);
    inputRefreshTimerRef.current = setTimeout(() => {
      inputRefreshTimerRef.current = null;
      poll();
    }, delayMs);
  }, [poll]);

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

  // PDF download — the screenshot-mode viewport can't render an interactive
  // PDF, so the user gets a "Download" button that fetches the bytes and
  // saves them to the download dir for opening in a native reader.
  const [pdfDownloading, setPdfDownloading] = useState(false);
  const downloadActivePdf = useCallback(async () => {
    if (!activeTab?.url || !isTauri || !engineReady) return;
    setPdfDownloading(true);
    try {
      await invoke("browser_download_url", { url: activeTab.url });
    } catch { /* surfaced via download bar on next refresh */ }
    finally { setPdfDownloading(false); }
  }, [activeTab?.url, engineReady]);

  // Reader mode — extract main content via a heuristic script injected
  // through browser_evaluate and show it in a clean overlay.
  const [readerOpen, setReaderOpen] = useState(false);

  // Vault autofill — click the key icon, fetch the saved credential for
  // the current domain from the Python backend (proxied through Tauri),
  // and fill the page's login form.
  const [vaultBusy, setVaultBusy] = useState(false);
  const [vaultToast, setVaultToast] = useState<string | null>(null);
  const autofillFromVault = useCallback(async () => {
    if (!activeTab?.url || !isTauri || !engineReady) return;
    setVaultBusy(true);
    setVaultToast(null);
    try {
      const cred = await invoke("vault_get_credential", { url: activeTab.url }) as
        | { domain: string; username: string; password: string } | null;
      if (!cred || !cred.username) {
        setVaultToast("No saved login for this site");
        return;
      }
      // Inject a script that finds the login form and fills it. The
      // native value-setter trick is required because React and other
      // frameworks override the input's value setter to track state
      // via synthetic events.
      const safeUser = cred.username.replace(/\\/g, "\\\\").replace(/'/g, "\\'");
      const safePass = cred.password.replace(/\\/g, "\\\\").replace(/'/g, "\\'");
      const script = `(function(){
const setNative = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
function fill(input, value) { setNative.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })); input.dispatchEvent(new Event('change', { bubbles: true })); }
function score(input) {
  const t = (input.type||'').toLowerCase();
  const n = (input.name||'').toLowerCase();
  const i = (input.id||'').toLowerCase();
  const a = (input.autocomplete||'').toLowerCase();
  if (t === 'password') return 100;
  if (a.includes('username') || a.includes('email')) return 80;
  if (n === 'email' || i === 'email' || n.includes('email') || i.includes('email')) return 70;
  if (n === 'username' || i === 'username' || n.includes('user') || i.includes('user')) return 60;
  if (t === 'email') return 50;
  return 0;
}
let bestForm = null, bestPw = null, bestUser = null, bestScore = 0;
for (const form of document.querySelectorAll('form')) {
  const inputs = Array.from(form.querySelectorAll('input'));
  const pw = inputs.find(x => (x.type||'').toLowerCase() === 'password');
  if (!pw) continue;
  let user = null, userScore = 0;
  for (const input of inputs) {
    if (input === pw) continue;
    const s = score(input);
    if (s > userScore) { user = input; userScore = s; }
  }
  const total = 100 + userScore;
  if (total > bestScore) { bestForm = form; bestPw = pw; bestUser = user; bestScore = total; }
}
if (!bestPw) return { filled: false, reason: 'no-password-field' };
if (bestUser) fill(bestUser, '${safeUser}');
fill(bestPw, '${safePass}');
return { filled: true, hasUser: !!bestUser, hasPass: true };
})()`;
      const result = await invoke("browser_evaluate", {
        tabId: activeTab.id,
        expression: script,
      }) as string;
      if (result.includes('"filled":true')) {
        setVaultToast(`Filled login for ${cred.username}`);
      } else {
        setVaultToast("No login form found on this page");
      }
    } catch (e) {
      setVaultToast(`Vault error: ${e}`);
    } finally {
      setVaultBusy(false);
    }
  }, [activeTab?.url, activeTab?.id, engineReady]);

  // Auto-clear the vault toast after a few seconds.
  useEffect(() => {
    if (!vaultToast) return;
    const id = setTimeout(() => setVaultToast(null), 3000);
    return () => clearTimeout(id);
  }, [vaultToast]);

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
        case "w": e.preventDefault(); if (activeTab) handleCloseTab(activeTab.id); break;
        case "l": e.preventDefault(); inputRef.current?.focus(); inputRef.current?.select(); break;
        case "d": e.preventDefault(); if (activeTab?.url) toggleBookmark(activeTab.url, activeTab.title || activeTab.url); break;
        case "r": e.preventDefault(); reload(); break;
        case "f": e.preventDefault(); openFind(); break;
        case "[": e.preventDefault(); goBack(); break;
        case "]": e.preventDefault(); goForward(); break;
        case "b": if (e.shiftKey) { e.preventDefault(); setShowBookmarks((v) => !v); } break;
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [browserTabs, activeTab, handleNewTab, handleCloseTab, toggleBookmark, reload, goBack, goForward, setActiveBrowserTab, openFind]);

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

  // ── Input forwarding (CDP Input domain) ──
  // The viewport is a screenshot of the page; these handlers translate
  // DOM events on the image into CDP input events on the live page.
  // Vault autofill uses Runtime.evaluate (JS injection) instead, so the
  // two paths never touch the same input channel.

  // Screenshot img natural size = the CDP capture viewport; the img
  // element fills the container and letterboxes via object-contain.
  const toPageCoords = useCallback((clientX: number, clientY: number): PagePoint | null => {
    const img = imgRef.current;
    if (!img || !img.naturalWidth || !img.naturalHeight) return null;
    return clientToPageCoords(clientX, clientY, img.getBoundingClientRect(), img.naturalWidth, img.naturalHeight);
  }, []);

  const sendMouseEvent = useCallback((
    eventType: string,
    clientX: number,
    clientY: number,
    button: string,
    clickCount: number,
    deltaX = 0,
    deltaY = 0,
  ) => {
    if (!isTauri || !engineReady || !activeTab) return;
    const pt = toPageCoords(clientX, clientY);
    if (!pt) return; // letterbox margin — not part of the page
    invoke("browser_mouse_event", {
      tabId: activeTab.id, eventType, x: pt.x, y: pt.y,
      button, clickCount, deltaX, deltaY,
    }).catch(() => { /* tab closing */ });
  }, [activeTab, engineReady, toPageCoords]);

  const handleViewportMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault(); // no image drag / text selection on the screenshot
    // Move keyboard focus to the page so typing goes to the page, not the URL bar.
    viewportRef.current?.focus();
    // e.detail is the native click count (1 = single, 2 = double, ...).
    sendMouseEvent("mousePressed", e.clientX, e.clientY, cdpButton(e.button), Math.max(1, e.detail));
  }, [sendMouseEvent]);

  const handleViewportMouseUp = useCallback((e: React.MouseEvent) => {
    sendMouseEvent("mouseReleased", e.clientX, e.clientY, cdpButton(e.button), Math.max(1, e.detail));
    scheduleInputRefresh(200);
  }, [sendMouseEvent, scheduleInputRefresh]);

  // Throttled to 10 Hz — each CDP call opens a fresh WebSocket, and hover
  // feedback isn't worth 60 connections/second.
  const handleViewportMouseMove = useCallback((e: React.MouseEvent) => {
    const now = Date.now();
    if (now - lastMouseMoveRef.current < 100) return;
    lastMouseMoveRef.current = now;
    sendMouseEvent("mouseMoved", e.clientX, e.clientY, "none", 0);
  }, [sendMouseEvent]);

  // Non-passive wheel listener: React attaches onWheel as passive, which
  // would ignore preventDefault and log a dev warning.
  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      sendMouseEvent("mouseWheel", e.clientX, e.clientY, "none", 0, e.deltaX, e.deltaY);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [sendMouseEvent]);

  const sendKeyEvent = useCallback((
    eventType: string,
    key: string,
    code: string,
    text: string | null,
    modifiers: number,
  ) => {
    if (!isTauri || !engineReady || !activeTab) return;
    invoke("browser_key_event", {
      tabId: activeTab.id, eventType, key, code, text,
      windowsVirtualKeyCode: SPECIAL_KEY_CODES[key] ?? null,
      modifiers,
    }).catch(() => { /* tab closing */ });
  }, [activeTab, engineReady]);

  const handleViewportKeyDown = useCallback((e: React.KeyboardEvent) => {
    // Cmd/Ctrl combos belong to the app chrome (Cmd+L, Cmd+T, Cmd+R, ...) —
    // let the global shortcut handler see them instead of the page.
    if (e.metaKey || e.ctrlKey) return;
    if (!isTauri || !engineReady || !activeTab) return;
    e.preventDefault();
    const modifiers = cdpModifiers(e);
    if (e.key.length === 1) {
      // Text-producing key: a `char` event carries the character directly.
      sendKeyEvent("char", e.key, e.code, e.key, modifiers);
    } else if (SPECIAL_KEY_CODES[e.key] !== undefined) {
      sendKeyEvent("rawKeyDown", e.key, e.code, null, modifiers);
      // Many pages only react to Enter when a text event follows the keydown.
      if (e.key === "Enter") sendKeyEvent("char", e.key, e.code, "\r", modifiers);
    }
    scheduleInputRefresh(300);
  }, [activeTab, engineReady, sendKeyEvent, scheduleInputRefresh]);

  const handleViewportKeyUp = useCallback((e: React.KeyboardEvent) => {
    if (e.metaKey || e.ctrlKey) return;
    sendKeyEvent("keyUp", e.key, e.code, null, cdpModifiers(e));
  }, [sendKeyEvent]);

  // ── Render ──
  return (
    <div className="flex h-full flex-col">
      {/* ── Bookmark bar ── */}
      {showBookmarks && (
        <div className="flex items-center gap-0.5 border-b border-border/30 bg-surface/40 px-3 py-1 overflow-x-auto">
          {bookmarks.length === 0 && (
            <span className="text-[10px] text-muted-foreground/50">
              No bookmarks yet — press ⌘D to bookmark this page, or import a bookmarks file.
            </span>
          )}
          {bookmarks.slice(0, 20).map((bm) => (
            <button
              key={bm.id}
              onClick={() => doNavigate(bm.url)}
              className="flex items-center gap-1 shrink-0 rounded-md px-2 py-0.5 text-[11px] text-muted-foreground hover:bg-muted/50 hover:text-foreground transition-all duration-150 max-w-[160px]"
              title={bm.url}
            >
              <img src={faviconUrl(bm.url)} alt="" className="w-3.5 h-3.5 rounded-sm" onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} />
              <span className="truncate">{bm.title || bm.url}</span>
              <X size={10} className="opacity-0 group-hover:opacity-100 hover:text-red-400 shrink-0 ml-0.5"
                onClick={(e) => { e.stopPropagation(); removeBookmark(bm.id); }} />
            </button>
          ))}
          {/* Import/export menu */}
          <div className="relative ml-auto shrink-0">
            <button
              type="button"
              onClick={() => setBmMenuOpen((v) => !v)}
              title="Bookmark options"
              className={`rounded-md p-1 transition-all duration-150 ${bmMenuOpen ? "text-accent" : "text-muted-foreground/60 hover:bg-muted/50 hover:text-foreground"}`}
            >
              <EllipsisVertical size={12} />
            </button>
            <AnimatePresence>
              {bmMenuOpen && (
                <>
                  {/* Backdrop closes the menu on any outside click. */}
                  <div className="fixed inset-0 z-40" onClick={() => setBmMenuOpen(false)} />
                  <motion.div
                    initial={{ opacity: 0, y: -4, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -4, scale: 0.98 }}
                    transition={{ duration: 0.15, ease: "easeOut" }}
                    className="absolute right-0 top-full z-50 mt-1 w-48 rounded-lg border border-border/50 bg-surface-elevated shadow-float overflow-hidden"
                  >
                    <button
                      type="button"
                      onClick={exportBookmarks}
                      disabled={bookmarks.length === 0}
                      className="flex w-full items-center gap-2 px-3 py-2 text-xs text-left transition-colors duration-100 hover:bg-muted/30 disabled:opacity-40"
                    >
                      <FileDown size={12} className="shrink-0 text-muted-foreground" />
                      Export bookmarks (HTML)
                    </button>
                    <button
                      type="button"
                      onClick={() => importInputRef.current?.click()}
                      className="flex w-full items-center gap-2 px-3 py-2 text-xs text-left transition-colors duration-100 hover:bg-muted/30"
                    >
                      <FileUp size={12} className="shrink-0 text-muted-foreground" />
                      Import bookmarks (HTML)
                    </button>
                  </motion.div>
                </>
              )}
            </AnimatePresence>
          </div>
          <input
            ref={importInputRef}
            type="file"
            accept=".html,.htm,text/html"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) importBookmarks(f);
              e.target.value = ""; // allow re-importing the same file
            }}
          />
        </div>
      )}

      {/* ── Address bar + nav ── */}
      <div className="flex items-center gap-2 border-b border-border/50 bg-surface/80 px-2 py-1.5">
        <div className="flex items-center gap-0.5">
          <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-all duration-150" onClick={goBack}><ArrowLeft size={14} /></Button>
          <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-all duration-150" onClick={goForward}><ArrowRight size={14} /></Button>
          <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-all duration-150" onClick={reload}>
            <motion.span animate={spinning ? { rotate: 360 } : { rotate: 0 }} transition={{ duration: 0.7, ease: "easeOut" }} className="block">
              <RotateCcw size={14} />
            </motion.span>
          </Button>
          <Button variant="ghost" size="icon" className={`h-7 w-7 transition-all duration-200 ${devtoolsOpen ? "text-accent glow-accent" : "text-muted-foreground hover:text-foreground hover:bg-muted/50"}`}
            onClick={() => setDevtoolsOpen(!devtoolsOpen)}>
            {devtoolsOpen ? <BugOff size={14} /> : <Bug size={14} />}
          </Button>
          <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-all duration-150" onClick={() => doNavigate("about:blank")}><Home size={14} /></Button>
          <Button variant="ghost" size="icon" className={`h-7 w-7 transition-all duration-200 ${auditOpen ? "text-accent glow-accent" : "text-muted-foreground hover:text-foreground hover:bg-muted/50"}`}
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
            className="flex items-center gap-2 rounded-lg border border-border/50 bg-background/60 px-2.5 transition-colors duration-200"
            animate={{
              borderColor: urlFocused ? "oklch(0.65 0.18 265 / 40%)" : "oklch(1 0 0 / 8%)",
              boxShadow: urlFocused ? "0 0 0 3px oklch(0.65 0.18 265 / 10%), 0 0 16px oklch(0.65 0.18 265 / 8%)" : "0 1px 2px oklch(0 0 0 / 5%)",
            }}
            transition={{ duration: 0.2 }}
            onSubmit={(e) => { e.preventDefault(); setSuggestionsVisible(false); doNavigate(inputUrl); }}
          >
            <Globe size={12} className="text-muted-foreground/60 shrink-0" />
            <input
              ref={inputRef} type="text" value={inputUrl}
              onChange={(e) => { setInputUrl(e.target.value); setSuggestionsVisible(true); setSelectedSuggestion(-1); }}
              onFocus={() => { setUrlFocused(true); if (inputUrl) setSuggestionsVisible(true); }}
              onBlur={() => { setTimeout(() => { setUrlFocused(false); setSuggestionsVisible(false); }, 150); }}
              onKeyDown={handleKeyDown}
              placeholder="Search or enter URL"
              className="flex-1 bg-transparent py-1 text-xs outline-none placeholder:text-muted-foreground/40"
            />
            {/* Bookmark star */}
            {activeTab?.url && activeTab.url !== "about:blank" && (
              <button type="button" onClick={() => toggleBookmark(activeTab.url, activeTab.title || activeTab.url)}
                className={`shrink-0 ${isBookmarked(activeTab.url) ? "text-amber-400" : "text-muted-foreground hover:text-foreground"}`}>
                <Star size={12} fill={isBookmarked(activeTab.url) ? "currentColor" : "none"} />
              </button>
            )}
            {/* Reader mode toggle */}
            {activeTab?.url && activeTab.url !== "about:blank" && !isPdfUrl(activeTab.url) && (
              <button type="button" onClick={() => setReaderOpen(true)}
                title="Reader mode (extract main content)"
                className={`shrink-0 ${readerOpen ? "text-accent" : "text-muted-foreground hover:text-foreground"}`}>
                <BookOpen size={12} />
              </button>
            )}
            {/* Vault autofill */}
            {activeTab?.url && activeTab.url !== "about:blank" && isTauri && engineReady && (
              <button type="button" onClick={autofillFromVault} disabled={vaultBusy}
                title="Autofill saved login"
                className="shrink-0 text-muted-foreground hover:text-foreground disabled:opacity-50">
                {vaultBusy
                  ? <span className="block h-3 w-3 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-muted-foreground" />
                  : <KeyRound size={11} />}
              </button>
            )}
            {/* PDF indicator + download */}
            {isPdfUrl(activeTab?.url || "") && (
              <>
                <span className="flex shrink-0 items-center gap-1 rounded bg-red-500/15 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-red-400">
                  <FileText size={9} /> PDF
                </span>
                <button type="button" onClick={downloadActivePdf} disabled={pdfDownloading}
                  title="Download PDF for native reader"
                  className="shrink-0 text-muted-foreground hover:text-foreground disabled:opacity-50">
                  {pdfDownloading
                    ? <span className="block h-3 w-3 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-muted-foreground" />
                    : <Download size={11} />}
                </button>
              </>
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
                initial={{ opacity: 0, y: -4, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -4, scale: 0.98 }}
                transition={{ duration: 0.15, ease: "easeOut" }}
                className="absolute top-full left-0 right-0 z-50 mt-1.5 rounded-lg border border-border/50 bg-surface-elevated shadow-float overflow-hidden"
              >
                {suggestions.map((s, i) => (
                  <button
                    key={`${s.url}-${i}`}
                    onMouseDown={(e) => { e.preventDefault(); selectSuggestion(s.url, s.title); }}
                    className={`flex items-center gap-2 w-full px-3 py-2 text-xs text-left transition-colors duration-100 ${
                      i === selectedSuggestion ? "bg-muted/60" : "hover:bg-muted/30"
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
        className="relative flex gap-0.5 overflow-x-auto border-b border-border/30 bg-surface/50 px-2 py-1"
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
              whileDrag={{ scale: 1.04, zIndex: 10, boxShadow: "0 8px 24px oklch(0 0 0 / 15%)" }}
              className={`group relative flex max-w-[180px] items-center gap-1.5 rounded-md pl-2 pr-1 py-1 text-xs touch-none cursor-grab active:cursor-grabbing transition-colors duration-150 ${
                isActive ? "text-foreground" : "text-muted-foreground hover:bg-muted/40 hover:text-foreground/80"
              }`}
            >
              {isActive && (
                <motion.span layoutId="chromium-tab-indicator"
                  className="absolute inset-0 rounded-md bg-background shadow-sm border border-border/30"
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
            transition={{ duration: 0.18, ease: [0.23, 1, 0.32, 1] }}
            style={{
              position: "fixed",
              left: Math.max(8, Math.min(preview.x, window.innerWidth - 348)),
              top: Math.max(8, preview.y - 200),
              zIndex: 60,
            }}
            className="pointer-events-none w-[340px] rounded-xl border border-border/50 bg-surface-elevated shadow-float overflow-hidden"
          >
            {preview.loading ? (
              <div className="flex h-[180px] items-center justify-center bg-muted/20">
                <motion.div
                  animate={{ rotate: 360 }}
                  transition={{ duration: 1.2, repeat: Infinity, ease: "linear" }}
                  className="h-5 w-5 rounded-full border-2 border-muted-foreground/20 border-t-accent"
                />
              </div>
            ) : preview.screenshot ? (
              <img src={preview.screenshot} alt={preview.title} className="block h-[180px] w-full object-cover bg-white" />
            ) : null}
            <div className="border-t border-border/30 bg-surface-elevated/95 px-2.5 py-1.5 backdrop-blur-sm">
              <div className="truncate text-[11px] font-medium text-foreground">{preview.title || "New Tab"}</div>
              <div className="truncate text-[10px] text-muted-foreground/70">{preview.url}</div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Viewport ── */}
      {/* Focusable so keyboard input forwards to the page; click anywhere
          in the pane moves focus here (see handleViewportMouseDown). */}
      <div
        ref={viewportRef}
        tabIndex={0}
        role="application"
        aria-label="Web page viewport"
        onMouseDown={handleViewportMouseDown}
        onMouseUp={handleViewportMouseUp}
        onMouseMove={handleViewportMouseMove}
        onKeyDown={handleViewportKeyDown}
        onKeyUp={handleViewportKeyUp}
        className="relative min-h-0 flex-1 bg-background outline-none transition-shadow duration-200 focus:shadow-[inset_0_0_0_1px_oklch(0.65_0.18_265/50%),inset_0_0_16px_oklch(0.65_0.18_265/10%)]"
      >
        {/* ── Find-in-page bar (Ctrl/Cmd+F) ── */}
        <AnimatePresence>
          {findOpen && (
            <FindBar
              query={findQuery}
              result={findResult}
              focusToken={findFocusToken}
              onQueryChange={setFindQuery}
              onNext={() => runFind(findQuery, "next")}
              onPrev={() => runFind(findQuery, "prev")}
              onClose={closeFind}
            />
          )}
        </AnimatePresence>
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
              ref={imgRef} draggable={false}
              className="h-full w-full object-contain bg-white select-none"
              initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.98 }} transition={{ duration: 0.25, ease: "easeOut" }} />
          ) : (
            <motion.div key="empty"
              className="flex h-full flex-col items-center justify-center text-muted-foreground"
              initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 8 }} transition={{ duration: 0.3, ease: "easeOut" }}>
              <motion.div animate={{ scale: [1, 1.04, 1] }} transition={{ duration: 4, repeat: Infinity, ease: "easeInOut" }}>
                {engineReady ? (
                  <div className="relative">
                    <Globe size={40} className="mb-4 text-border/60" />
                    <div className="absolute inset-0 rounded-full bg-accent/5 blur-xl" />
                  </div>
                ) : (
                  <div className="relative">
                    <Cpu size={40} className="mb-4 text-amber-500/40" />
                    <div className="absolute inset-0 rounded-full bg-amber-500/5 blur-xl" />
                  </div>
                )}
              </motion.div>
              <p className="text-sm font-medium">{isTauri && !engineReady ? "Starting Chromium engine..." : "No page loaded"}</p>
              <p className="mt-1.5 text-xs text-muted-foreground/60">{isTauri && !engineReady ? "The browser engine is initializing." : "Enter a URL above or open a bookmark."}</p>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* ── Download Bar ── */}
      <DownloadBar />

      {/* ── Reader Mode Overlay ── */}
      <ReaderMode
        tabId={engineReady ? (activeTab?.id ?? null) : null}
        open={readerOpen}
        onClose={() => setReaderOpen(false)}
      />

      {/* ── Vault toast ── */}
      <AnimatePresence>
        {vaultToast && (
          <motion.div
            initial={{ opacity: 0, y: 16, scale: 0.9 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 16, scale: 0.9 }}
            transition={{ type: "spring", stiffness: 400, damping: 25 }}
            className="pointer-events-none fixed bottom-12 left-1/2 z-40 -translate-x-1/2 rounded-full border border-border/50 bg-surface-elevated px-4 py-2 text-xs text-foreground shadow-float backdrop-blur-sm"
            data-testid="vault-toast"
          >
            <span className="flex items-center gap-2">
              <KeyRound size={12} className="text-accent shrink-0" />
              {vaultToast}
            </span>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── CDP Audit Overlay ── */}
      <AnimatePresence>
        {auditOpen && (
          <motion.div
            ref={auditRef}
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className="shrink-0 overflow-hidden border-t border-border/50 bg-surface/60 backdrop-blur-sm"
          >
            <div className="max-h-[200px] overflow-y-auto p-3 space-y-1.5">
              <div className="flex items-center gap-2 mb-2">
                <Shield size={12} className="text-muted-foreground/60" />
                <span className="text-[11px] font-medium text-muted-foreground/80">CDP Page Audit</span>
                {auditRunning && (
                  <motion.span animate={{ rotate: 360 }} transition={{ duration: 1, repeat: Infinity, ease: "linear" }} className="inline-block">
                    <RotateCcw size={10} className="text-accent" />
                  </motion.span>
                )}
                {!auditRunning && auditFindings.length > 0 && (
                  <span className="text-[10px] text-muted-foreground/60">{auditFindings.length} finding(s)</span>
                )}
                <div className="flex-1" />
                <button onClick={() => setAuditOpen(false)} className="text-muted-foreground/60 hover:text-foreground transition-colors">
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
