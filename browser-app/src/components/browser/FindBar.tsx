import { useEffect, useRef } from "react";
import { motion } from "motion/react";
import { ChevronDown, ChevronUp, Search, X } from "lucide-react";
import { FindResult } from "./findInPage";

interface FindBarProps {
  query: string;
  result: FindResult | null;
  /** Bumped on every Cmd/Ctrl+F so the input refocuses even when already open. */
  focusToken: number;
  onQueryChange: (query: string) => void;
  onNext: () => void;
  onPrev: () => void;
  onClose: () => void;
}

/**
 * Find-in-page bar, overlaid on the browser viewport. Pure UI: the parent
 * (ChromiumPane) owns the query state and runs the CDP evaluate calls.
 */
export function FindBar({ query, result, focusToken, onQueryChange, onNext, onPrev, onClose }: FindBarProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, [focusToken]);

  return (
    <motion.div
      initial={{ opacity: 0, y: -6, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -6, scale: 0.98 }}
      transition={{ duration: 0.15, ease: "easeOut" }}
      className="absolute right-3 top-3 z-30 flex items-center gap-1 rounded-lg border border-border/50 bg-surface-elevated px-2 py-1 shadow-float"
      data-testid="find-bar"
    >
      <Search size={11} className="shrink-0 text-muted-foreground/60" />
      <input
        ref={inputRef}
        type="text"
        value={query}
        onChange={(e) => onQueryChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            if (e.shiftKey) onPrev(); else onNext();
          } else if (e.key === "Escape") {
            e.preventDefault();
            e.stopPropagation();
            onClose();
          }
        }}
        placeholder="Find in page"
        className="w-40 bg-transparent py-0.5 text-xs outline-none placeholder:text-muted-foreground/40"
      />
      <span className="min-w-[30px] shrink-0 text-center text-[10px] tabular-nums text-muted-foreground">
        {result ? `${result.active}/${result.total}` : ""}
      </span>
      <button
        type="button"
        onClick={onPrev}
        title="Previous match (Shift+Enter)"
        className="shrink-0 rounded p-0.5 text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
      >
        <ChevronUp size={12} />
      </button>
      <button
        type="button"
        onClick={onNext}
        title="Next match (Enter)"
        className="shrink-0 rounded p-0.5 text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
      >
        <ChevronDown size={12} />
      </button>
      <button
        type="button"
        onClick={onClose}
        title="Close (Esc)"
        className="shrink-0 rounded p-0.5 text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
      >
        <X size={12} />
      </button>
    </motion.div>
  );
}
