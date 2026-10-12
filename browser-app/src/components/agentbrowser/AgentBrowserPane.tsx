import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Button } from "../ui/button";
import {
  Activity,
  CircleAlert,
  Globe,
  Loader2,
  MousePointerClick,
  Plug,
  RefreshCw,
  Save,
  ScanSearch,
  Send,
  Trash2,
} from "lucide-react";
import { createWebSocket, localFetch, localFetchStream } from "../../utils/api";
import {
  type AxNode,
  type SessionEvent,
  allowDomainsFor,
  formatSessionEvent,
  frameToDataUrl,
  normalizeTargetUrl,
  parseAxTree,
  pointerToPageCoords,
} from "./sessionUtils";

const MAX_EVENTS = 40;
const FRAME_STALE_MS = 3000;

interface SessionInfo {
  session_id: string;
}

interface AxTreeResponse {
  ax_tree?: string;
  title?: string;
  url?: string;
}

/**
 * Agent Browser — the engine's browser session, on screen.
 *
 * The app's other browser pane drives *the user's* embedded Chromium; this one
 * drives an engine session, which is what the Greenwich bridge work added:
 * a live screencast viewport (WS /stream), the pruned AX tree the model acts
 * on (GET /ax-tree + POST /act-by-ref), the session event bus (GET /events)
 * and one-click ingest into permanent memory (POST /sync-greenwich).
 */
export function AgentBrowserPane() {
  const [inputUrl, setInputUrl] = useState("https://example.com");
  const [sessionId, setSessionId] = useState("");
  const [pageTitle, setPageTitle] = useState("");
  const [pageUrl, setPageUrl] = useState("");
  const [frame, setFrame] = useState("");
  const [frameMode, setFrameMode] = useState<"ws" | "poll" | "">("");
  const [frameSize, setFrameSize] = useState({ width: 0, height: 0 });
  const [axNodes, setAxNodes] = useState<AxNode[]>([]);
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [busy, setBusy] = useState<"" | "opening" | "navigating" | "acting">("");
  const [error, setError] = useState("");
  const [memNote, setMemNote] = useState("");
  const [engineUp, setEngineUp] = useState<boolean | null>(null);
  const [selectedRef, setSelectedRef] = useState<number | null>(null);
  const [typeText, setTypeText] = useState("");

  const wsRef = useRef<WebSocket | null>(null);
  const sseAbortRef = useRef<AbortController | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastFrameAtRef = useRef(0);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const wheelAtRef = useRef(0);
  // Handlers read the session id from a ref so the WS/SSE callbacks never
  // close over a stale value (they are created once per session).
  const sessionRef = useRef("");
  sessionRef.current = sessionId;

  // ── engine reachability ─────────────────────────────────────────────
  useEffect(() => {
    let alive = true;
    localFetch("/health")
      .then((r) => alive && setEngineUp(r.ok))
      .catch(() => alive && setEngineUp(false));
    return () => {
      alive = false;
    };
  }, []);

  const pushEvent = useCallback((event: string, data: Record<string, unknown>) => {
    setEvents((prev) => [...prev.slice(-(MAX_EVENTS - 1)), formatSessionEvent(event, data)]);
  }, []);

  const refreshAx = useCallback(async (id: string): Promise<void> => {
    try {
      const res = await localFetch(`/browser/sessions/${encodeURIComponent(id)}/ax-tree`);
      if (!res.ok) return;
      const body = (await res.json()) as AxTreeResponse;
      setAxNodes(parseAxTree(body.ax_tree || ""));
      if (body.title) setPageTitle(body.title);
      if (body.url) setPageUrl(body.url);
    } catch {
      /* the ticker already shows engine errors; a stale tree is acceptable */
    }
  }, []);

  // ── frames: pushed over WS, polled when the push stalls ─────────────
  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const startPolling = useCallback((id: string) => {
    if (pollTimerRef.current) return;
    pollTimerRef.current = setInterval(async () => {
      try {
        const res = await localFetch(
          `/browser/sessions/${encodeURIComponent(id)}/frame?quality=70`,
        );
        if (!res.ok) return;
        const body = (await res.json()) as { frame_base64?: string };
        if (body.frame_base64) {
          setFrame(body.frame_base64);
          setFrameMode((m) => (m === "ws" ? m : "poll"));
          lastFrameAtRef.current = Date.now();
        }
      } catch {
        /* keep the last frame; the next tick retries */
      }
    }, 700);
  }, []);

  // ── session teardown ────────────────────────────────────────────────
  const closeSession = useCallback(async () => {
    const id = sessionRef.current;
    stopPolling();
    sseAbortRef.current?.abort();
    sseAbortRef.current = null;
    wsRef.current?.close();
    wsRef.current = null;
    setSessionId("");
    sessionRef.current = "";
    setFrame("");
    setFrameMode("");
    setAxNodes([]);
    setEvents([]);
    setPageTitle("");
    setPageUrl("");
    setSelectedRef(null);
    if (id) {
      try {
        await localFetch(`/browser/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
      } catch {
        /* closing a session the engine already reaped is not an error */
      }
    }
  }, [stopPolling]);

  // ── attach transports when a session exists ─────────────────────────
  useEffect(() => {
    if (!sessionId) return;
    const id = sessionId;

    // 1. Live viewport + input passthrough.
    try {
      const ws = createWebSocket(`/browser/sessions/${encodeURIComponent(id)}/stream`);
      wsRef.current = ws;
      ws.onmessage = (msg) => {
        try {
          const data = JSON.parse(String(msg.data)) as {
            type?: string;
            frame_base64?: string;
            message?: string;
          };
          if (data.type === "frame" && data.frame_base64) {
            setFrame(data.frame_base64);
            setFrameMode("ws");
            lastFrameAtRef.current = Date.now();
          } else if (data.type === "error" && data.message) {
            setError(data.message);
          }
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = () => {
        // The WS carries both frames and input; once it is gone the poll loop
        // keeps the viewport alive and POST /input carries our gestures.
        if (sessionRef.current === id) {
          wsRef.current = null;
          startPolling(id);
        }
      };
    } catch {
      startPolling(id);
    }

    // 2. Watchdog: a stalled push stream falls back to polling.
    const watchdog = setInterval(() => {
      if (!lastFrameAtRef.current) return;
      if (Date.now() - lastFrameAtRef.current > FRAME_STALE_MS) startPolling(id);
    }, 1000);

    // 3. Event bus.
    const ac = new AbortController();
    sseAbortRef.current = ac;
    (async () => {
      try {
        const res = await localFetchStream(
          `/browser/sessions/${encodeURIComponent(id)}/events`,
          { signal: ac.signal },
        );
        if (!res.ok || !res.body) return;
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";
          let currentEvent = "";
          for (const line of lines) {
            if (line.startsWith("event: ")) {
              currentEvent = line.slice(7).trim();
            } else if (line.startsWith("data: ") && currentEvent) {
              try {
                pushEvent(currentEvent, JSON.parse(line.slice(6)) as Record<string, unknown>);
              } catch {
                /* skip malformed payloads rather than dropping the stream */
              }
              currentEvent = "";
            }
          }
        }
      } catch {
        /* aborted on close, or the engine went away — the badge shows it */
      }
    })();

    return () => {
      clearInterval(watchdog);
      stopPolling();
      ac.abort();
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [sessionId, pushEvent, startPolling, stopPolling]);

  // ── actions ─────────────────────────────────────────────────────────
  const ensureSession = useCallback(async (forUrl: string): Promise<string> => {
    if (sessionRef.current) return sessionRef.current;
    setBusy("opening");
    // file:// previews have no host, and the engine refuses an empty
    // allowlist by design ("fail closed") — localhost is the honest filler
    // for a workspace-local page, which the workspace roots gate.
    const domains = allowDomainsFor(forUrl);
    const res = await localFetch("/browser/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        allow_domains: domains.length ? domains : ["localhost"],
        require_approval: false,
        allow_private: true, // localhost dev servers + local daemons
        scrub_pii: false, // the operator is looking at the page they chose
      }),
    });
    if (!res.ok) {
      setBusy("");
      throw new Error(`session refused (${res.status})`);
    }
    const body = (await res.json()) as SessionInfo;
    setSessionId(body.session_id);
    sessionRef.current = body.session_id;
    return body.session_id;
  }, []);

  const navigate = useCallback(
    async (raw: string) => {
      const url = normalizeTargetUrl(raw);
      setError("");
      setMemNote("");
      setBusy("navigating");
      try {
        const id = await ensureSession(url);
        const res = await localFetch(`/browser/sessions/${encodeURIComponent(id)}/navigate`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url }),
        });
        if (!res.ok) {
          const detail = (await res.json().catch(() => ({}))) as { detail?: unknown };
          throw new Error(typeof detail.detail === "string" ? detail.detail : `HTTP ${res.status}`);
        }
        setInputUrl(url);
        await refreshAx(id);
        setTimeout(() => refreshAx(id), 1200); // late paint / redirect
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy("");
      }
    },
    [ensureSession, refreshAx],
  );

  const actByRef = useCallback(
    async (ref: number, text: string, pressEnter: boolean) => {
      const id = sessionRef.current;
      if (!id) return;
      setBusy("acting");
      setError("");
      try {
        const res = await localFetch(`/browser/sessions/${encodeURIComponent(id)}/act-by-ref`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            action: pressEnter ? "type" : "click",
            ref,
            text,
            approve: true,
            press_enter: pressEnter,
          }),
        });
        if (!res.ok) {
          const detail = (await res.json().catch(() => ({}))) as { detail?: unknown };
          throw new Error(typeof detail.detail === "string" ? detail.detail : `HTTP ${res.status}`);
        }
        setTimeout(() => refreshAx(id), 500);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy("");
      }
    },
    [refreshAx],
  );

  const sendInput = useCallback((payload: Record<string, unknown>) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(payload));
      return;
    }
    const id = sessionRef.current;
    if (!id) return;
    void localFetch(`/browser/sessions/${encodeURIComponent(id)}/input`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).catch(() => undefined);
  }, []);

  const saveToMemory = useCallback(async () => {
    const id = sessionRef.current;
    if (!id) return;
    setMemNote("");
    try {
      const res = await localFetch(`/browser/sessions/${encodeURIComponent(id)}/sync-greenwich`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: pageTitle || pageUrl || "Agent Browser page",
          url: pageUrl,
          clean_markdown: axNodes.map((n) => n.raw).join("\n"),
          citations: pageUrl ? [pageUrl] : [],
        }),
      });
      const body = (await res.json().catch(() => ({}))) as {
        ok?: boolean;
        endpoint?: string;
        error?: string;
      };
      setMemNote(
        body.ok
          ? `Saved to permanent memory (${body.endpoint || "bridge"})`
          : `Memory bridge refused: ${body.error || res.status}`,
      );
    } catch (e) {
      setMemNote(e instanceof Error ? e.message : "memory bridge unreachable");
    }
  }, [axNodes, pageTitle, pageUrl]);

  const live = useMemo(
    () => Boolean(frame) && Date.now() - lastFrameAtRef.current < FRAME_STALE_MS * 2,
    // frame is the tick that re-evaluates this
    [frame],
  );


  // ── viewport interactions ───────────────────────────────────────────
  const onFrameClick = useCallback(
    (e: React.MouseEvent<HTMLImageElement>) => {
      const point = pointerToPageCoords(e, e.currentTarget, {
        width: frameSize.width || e.currentTarget.naturalWidth,
        height: frameSize.height || e.currentTarget.naturalHeight,
      });
      sendInput({ type: "click", x: point.x, y: point.y });
      const id = sessionRef.current;
      if (id) setTimeout(() => refreshAx(id), 900);
    },
    [frameSize, refreshAx, sendInput],
  );

  const onFrameWheel = useCallback(
    (e: React.WheelEvent<HTMLImageElement>) => {
      e.preventDefault();
      const now = Date.now();
      if (now - wheelAtRef.current < 120) return; // one gesture per tick
      wheelAtRef.current = now;
      sendInput({ type: "scroll", delta_x: e.deltaX, delta_y: e.deltaY });
    },
    [sendInput],
  );

  const onViewportKey = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      const passthrough = ["Enter", "Tab", "Backspace", "Delete", "Escape",
        "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"];
      if (!passthrough.includes(e.key)) return;
      e.preventDefault();
      sendInput({ type: "keydown", key: e.key });
    },
    [sendInput],
  );

  const statusLabel = !sessionId
    ? engineUp === false
      ? "Engine offline"
      : "No session"
    : live
      ? "Live"
      : "Connecting…";

  return (
    <div className="flex h-full flex-col bg-background">
      {/* ── header ──────────────────────────────────────────────────── */}
      <div className="flex flex-col gap-2 border-b border-border bg-card/30 p-3">
        <div className="flex items-center gap-2">
          <span
            className={`flex items-center gap-1.5 rounded-md px-2 py-1 text-[10px] font-semibold uppercase tracking-wider ${
              live
                ? "bg-emerald-500/10 text-emerald-500"
                : engineUp === false
                  ? "bg-destructive/10 text-destructive"
                  : "bg-muted text-muted-foreground"
            }`}
          >
            <Activity size={11} />
            {statusLabel}
          </span>
          {frameMode && (
            <span className="rounded-md bg-muted px-2 py-1 text-[10px] text-muted-foreground">
              {frameMode === "ws" ? "screencast" : "polling"}
            </span>
          )}
          {frameSize.width > 0 && (
            <span className="rounded-md bg-muted px-2 py-1 text-[10px] text-muted-foreground">
              {frameSize.width}×{frameSize.height}
            </span>
          )}
          <div className="flex-1" />
          {pageTitle && (
            <span className="max-w-[280px] truncate text-xs text-muted-foreground" title={pageUrl}>
              {pageTitle}
            </span>
          )}
          {sessionId && (
            <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
              {sessionId}
            </span>
          )}
          {sessionId && (
            <Button
              variant="ghost"
              size="icon"
              onClick={closeSession}
              title="Close the engine session"
              aria-label="Close session"
            >
              <Trash2 size={14} />
            </Button>
          )}
        </div>


        <div className="flex items-center gap-2">
          <div className="flex flex-1 items-center gap-2 rounded-md border border-border bg-background px-2 py-1.5">
            <Globe size={13} className="shrink-0 text-muted-foreground" />
            <input
              value={inputUrl}
              onChange={(e) => setInputUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void navigate(inputUrl);
              }}
              placeholder="Enter a URL to browse with the engine…"
              spellCheck={false}
              className="flex-1 bg-transparent font-mono text-xs outline-none placeholder:text-muted-foreground/60"
            />
          </div>
          <Button
            size="sm"
            onClick={() => void navigate(inputUrl)}
            disabled={busy !== ""}
            aria-label="Go"
          >
            {busy === "navigating" || busy === "opening" ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <Send size={13} />
            )}
            <span className="ml-1">Go</span>
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!sessionId || busy !== ""}
            onClick={() => sessionId && void refreshAx(sessionId)}
            title="Re-read the accessibility tree"
            aria-label="Refresh accessibility tree"
          >
            <RefreshCw size={13} />
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!sessionId || axNodes.length === 0}
            onClick={() => void saveToMemory()}
            title="Ingest this page into permanent memory"
            aria-label="Save page to permanent memory"
          >
            <Save size={13} />
            <span className="ml-1">Remember</span>
          </Button>
        </div>

        <AnimatePresence>
          {(error || memNote) && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              className={`flex items-center gap-2 overflow-hidden rounded-md px-2 py-1.5 text-[11px] ${
                error ? "bg-destructive/10 text-destructive" : "bg-emerald-500/10 text-emerald-500"
              }`}
            >
              {error ? <CircleAlert size={12} /> : <Save size={12} />}
              <span className="truncate">{error || memNote}</span>
              <button
                className="ml-auto text-muted-foreground hover:text-foreground"
                onClick={() => {
                  setError("");
                  setMemNote("");
                }}
              >
                ✕
              </button>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* ── body ────────────────────────────────────────────────────── */}
      <div className="flex min-h-0 flex-1">
        {/* viewport */}
        <div
          className="relative flex min-w-0 flex-1 items-center justify-center overflow-hidden bg-black/40 p-2 outline-none"
          tabIndex={0}
          onKeyDown={onViewportKey}
          title="Click to interact — clicks and scrolls are sent to the engine session"
        >
          {frame ? (
            <img
              ref={imgRef}
              src={frameToDataUrl(frame)}
              alt="Engine session viewport"
              onLoad={(e) =>
                setFrameSize({
                  width: e.currentTarget.naturalWidth,
                  height: e.currentTarget.naturalHeight,
                })
              }
              onClick={onFrameClick}
              onWheel={onFrameWheel}
              className="max-h-full max-w-full cursor-crosshair rounded border border-border/60 object-contain shadow-sm"
              draggable={false}
            />
          ) : (
            <div className="flex flex-col items-center gap-3 text-center text-muted-foreground">
              <motion.div
                animate={{ scale: [1, 1.06, 1] }}
                transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
              >
                <ScanSearch size={34} className="text-border" />
              </motion.div>
              <p className="text-sm font-medium">
                {engineUp === false ? "Engine offline on :8001" : "No frames yet"}
              </p>
              <p className="max-w-[360px] text-xs">
                {engineUp === false
                  ? "Start the Jambubrowser engine, then reload the page and enter a URL."
                  : "Enter a URL above. The engine opens its own browser, streams the live view here, and exposes the accessibility tree the agent acts on."}
              </p>
            </div>
          )}
          {busy !== "" && (
            <div className="pointer-events-none absolute right-3 top-3 flex items-center gap-1.5 rounded-md bg-background/90 px-2 py-1 text-[10px] text-muted-foreground shadow">
              <Loader2 size={11} className="animate-spin" />
              {busy === "navigating" ? "navigating…" : busy === "acting" ? "acting…" : "opening session…"}
            </div>
          )}
        </div>


        {/* agent view */}
        <div className="flex w-[340px] shrink-0 flex-col border-l border-border">
          <div className="flex items-center gap-2 border-b border-border bg-card/30 px-3 py-2">
            <ScanSearch size={13} className="text-muted-foreground" />
            <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Agent view · AX tree
            </span>
            <span className="ml-auto text-[10px] text-muted-foreground">{axNodes.length} refs</span>
          </div>

          <div className="min-h-0 flex-1 overflow-auto">
            {axNodes.length === 0 ? (
              <p className="px-3 py-4 text-[11px] text-muted-foreground">
                No interactive elements yet. Navigate to a page (or press refresh) and the engine's
                pruned accessibility tree appears here — this is what the model sees instead of raw
                HTML.
              </p>
            ) : (
              axNodes.map((n) => {
                const isSelected = selectedRef === n.ref;
                return (
                  <div
                    key={n.ref}
                    onClick={() => setSelectedRef(n.ref)}
                    className={`group flex cursor-pointer items-center gap-2 border-b border-border/40 px-3 py-1.5 text-[11px] ${
                      isSelected ? "bg-muted/60" : "hover:bg-muted/40"
                    }`}
                  >
                    <span className="w-9 shrink-0 font-mono text-primary">@{n.ref}</span>
                    <span className="w-16 shrink-0 truncate text-muted-foreground">{n.role}</span>
                    <span className="min-w-0 flex-1 truncate" title={n.raw}>
                      {n.name || n.href || n.value || "—"}
                    </span>
                    {n.flags.map((f) => (
                      <span
                        key={f}
                        className="shrink-0 rounded bg-muted px-1 text-[9px] text-muted-foreground"
                      >
                        {f}
                      </span>
                    ))}
                    <span
                      role="button"
                      tabIndex={-1}
                      title={`Click @${n.ref} on the page`}
                      aria-label={`Click @${n.ref}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        void actByRef(n.ref, "", false);
                      }}
                      className="shrink-0 rounded p-0.5 text-muted-foreground opacity-0 transition-opacity hover:bg-muted-foreground/20 hover:text-foreground group-hover:opacity-100"
                    >
                      <MousePointerClick size={12} />
                    </span>
                  </div>
                );
              })
            )}
          </div>

          <div className="border-t border-border bg-card/20 p-2">
            <div className="flex items-center gap-1.5">
              <span className="font-mono text-[11px] text-muted-foreground">@</span>
              <input
                value={selectedRef ?? ""}
                onChange={(e) => setSelectedRef(e.target.value ? Number(e.target.value) : null)}
                className="w-12 rounded border border-border bg-background px-1.5 py-1 font-mono text-[11px] outline-none"
                placeholder="ref"
              />
              <input
                value={typeText}
                onChange={(e) => setTypeText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && selectedRef) void actByRef(selectedRef, typeText, true);
                }}
                placeholder="text to type…"
                className="min-w-0 flex-1 rounded border border-border bg-background px-1.5 py-1 text-[11px] outline-none"
              />
              <Button
                size="sm"
                variant="outline"
                disabled={!selectedRef || busy !== ""}
                onClick={() => selectedRef && void actByRef(selectedRef, typeText, true)}
                title="Type into the ref and press Enter"
                aria-label="Type and submit"
              >
                <Plug size={12} />
              </Button>
            </div>
            <p className="mt-1 text-[10px] text-muted-foreground">
              Rows select a ref; the icon clicks it. Type + ⏎ submits.
            </p>
          </div>
        </div>
      </div>

      {/* ── event ticker ────────────────────────────────────────────── */}
      <div className="h-[92px] shrink-0 overflow-auto border-t border-border bg-card/20 px-3 py-1.5">
        {events.length === 0 ? (
          <p className="py-2 text-[10px] text-muted-foreground">
            Session telemetry (nav:*, audit:*, error) streams here over SSE as the page loads.
          </p>
        ) : (
          events.map((evt, i) => (
            <div key={`${evt.at}-${i}`} className="flex gap-2 py-0.5 font-mono text-[10px]">
              <span className="shrink-0 text-muted-foreground/60">
                {new Date(evt.at).toLocaleTimeString([], { hour12: false })}
              </span>
              <span
                className={
                  evt.kind === "error"
                    ? "text-destructive"
                    : evt.kind.startsWith("nav:")
                      ? "text-primary"
                      : "text-muted-foreground"
                }
              >
                {evt.text}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

