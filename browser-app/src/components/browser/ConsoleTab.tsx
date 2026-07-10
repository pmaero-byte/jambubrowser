import { useState, useMemo, useRef, useEffect } from "react";
import { motion } from "motion/react";
import { useDevtoolsStore, type DevtoolsConsoleEntry } from "../../store/devtoolsStore";

const LEVEL_COLORS: Record<string, string> = {
  error: "text-red-400",
  warn: "text-yellow-400",
  info: "text-blue-400",
  log: "text-foreground",
  debug: "text-muted-foreground",
};

const LEVEL_BG: Record<string, string> = {
  error: "bg-red-500/10",
  warn: "bg-yellow-500/10",
  info: "bg-blue-500/10",
  log: "",
  debug: "",
};

function isDevtoolsLog(entry: DevtoolsConsoleEntry): boolean {
  return (
    entry.message.includes("jambu-devtools") ||
    entry.message.includes("[DevTools]") ||
    entry.message.includes("__vite__")
  );
}

export function ConsoleTab() {
  const { consoleEntries, errors } = useDevtoolsStore();
  const [levelFilter, setLevelFilter] = useState<string>("all");
  const [search, setSearch] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  // Merge console entries and errors into a single timeline
  const merged = useMemo(() => {
    const items: Array<{ type: "console"; entry: DevtoolsConsoleEntry } | { type: "error"; entry: DevtoolsConsoleEntry }> = [];

    for (const e of consoleEntries) {
      items.push({ type: "console", entry: e });
    }

    // Add JS errors as console.error entries
    for (const err of errors) {
      items.push({
        type: "console",
        entry: {
          level: "error",
          message: `[JS Error] ${err.message}${err.filename ? `\n  at ${err.filename}:${err.lineno}:${err.colno}` : ""}`,
          timestamp: err.timestamp || Date.now(),
        },
      });
    }

    // Sort by timestamp
    items.sort((a, b) => a.entry.timestamp - b.entry.timestamp);

    return items;
  }, [consoleEntries, errors]);

  const filtered = useMemo(() => {
    return merged.filter((item) => {
      if (levelFilter !== "all" && item.entry.level !== levelFilter) return false;
      if (isDevtoolsLog(item.entry)) return false;
      if (search.trim()) {
        const q = search.toLowerCase();
        return item.entry.message.toLowerCase().includes(q);
      }
      return true;
    });
  }, [merged, levelFilter, search]);

  const levelCounts = useMemo(() => {
    const counts: Record<string, number> = { all: merged.length };
    for (const item of merged) {
      if (!isDevtoolsLog(item.entry)) {
        counts[item.entry.level] = (counts[item.entry.level] || 0) + 1;
      }
    }
    return counts;
  }, [merged]);

  // Auto-scroll
  useEffect(() => {
    if (autoScroll && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [filtered.length, autoScroll]);

  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    const isAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50;
    setAutoScroll(isAtBottom);
  };

  const levels = ["all", "error", "warn", "info", "log", "debug"] as const;

  return (
    <div className="flex h-full flex-col">
      {/* Toolbar */}
      <div className="flex items-center gap-1 border-b border-border px-2 py-1">
        {levels.map((lvl) => (
          <button
            key={lvl}
            onClick={() => setLevelFilter(lvl)}
            className={`rounded px-1.5 py-0.5 text-[11px] font-medium transition-colors ${
              levelFilter === lvl
                ? "bg-accent/20 text-accent"
                : "text-muted-foreground hover:bg-muted"
            }`}
          >
            {lvl === "all" ? "All" : lvl}
            {levelCounts[lvl] > 0 && (
              <span className="ml-1 text-[10px] opacity-60">({levelCounts[lvl]})</span>
            )}
          </button>
        ))}
        <div className="flex-1" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search…"
          className="w-28 rounded border border-border bg-card px-1.5 py-0.5 text-[11px] outline-none focus:border-accent"
        />
      </div>

      {/* Log entries */}
      <div className="flex-1 overflow-y-auto font-mono text-xs" onScroll={handleScroll}>
        {filtered.length === 0 ? (
          <div className="flex h-full items-center justify-center text-muted-foreground">
            No console output yet.
          </div>
        ) : (
          filtered.map((item, i) => (
            <motion.div
              key={`${item.entry.timestamp}-${i}`}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.1 }}
              className={`border-b border-border/20 px-2 py-1 ${LEVEL_BG[item.entry.level] || ""}`}
            >
              <div className="flex items-start gap-2">
                <span className={`shrink-0 text-[10px] font-semibold uppercase ${LEVEL_COLORS[item.entry.level] || "text-foreground"}`}>
                  {item.entry.level}
                </span>
                <pre className="flex-1 whitespace-pre-wrap text-foreground" style={{ fontFamily: "inherit" }}>
                  {item.entry.message}
                </pre>
              </div>
            </motion.div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
