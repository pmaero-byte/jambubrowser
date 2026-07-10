import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { X, BookOpen } from "lucide-react";

interface Article {
  title: string;
  byline: string;
  siteName: string;
  /** The body HTML, already extracted from the live page. */
  contentHtml: string;
  /** The source URL, for the "open original" link. */
  sourceUrl: string;
}

const READER_SCRIPT = `(function() {
  // Lightweight readability-style extraction. We don't pull in
  // @mozilla/readability here because (a) it's ~80KB of code that has
  // to be injected as a string anyway, and (b) the heuristic below
  // covers the common cases (article, main, role=main, the usual CMS
  // class names, then a long-form text element as a fallback). When we
  // outgrow it, swap this IIFE for a Mozilla Readability bundle — the
  // contract (returns { title, byline, siteName, contentHtml, sourceUrl }
  // or null) is what the React side consumes.
  function score(el) {
    if (!el) return 0;
    const text = (el.innerText || "").trim();
    if (text.length < 250) return 0;
    // Penalise elements that contain more links than paragraphs
    // (navigation-y blocks), and elements with low text density.
    const pCount = el.getElementsByTagName("p").length;
    const aCount = el.getElementsByTagName("a").length;
    if (pCount === 0 && aCount > 5) return 0;
    return text.length * (1 + pCount * 0.1);
  }
  const candidates = [
    document.querySelector("article"),
    document.querySelector("main"),
    document.querySelector('[role="main"]'),
    document.querySelector(".post-content"),
    document.querySelector(".article-content"),
    document.querySelector(".entry-content"),
    document.querySelector("#content"),
    document.querySelector("#main"),
  ].filter(Boolean);
  let best = null;
  let bestScore = 0;
  for (const el of candidates) {
    const s = score(el);
    if (s > bestScore) { best = el; bestScore = s; }
  }
  if (!best) {
    // Fall back: walk the body's direct children and pick the highest-
    // scoring one. This catches blogs that wrap content in a plain div.
    const children = document.body ? document.body.children : [];
    for (const el of Array.from(children)) {
      const s = score(el);
      if (s > bestScore) { best = el; bestScore = s; }
    }
  }
  if (!best) return null;
  // Strip scripts and styles from the extracted subtree.
  const clone = best.cloneNode(true);
  clone.querySelectorAll("script, style, noscript, iframe").forEach(n => n.remove());
  return {
    title: document.title || "",
    byline: (document.querySelector('meta[name="author"]') || {}).content || "",
    siteName: (document.querySelector('meta[property="og:site_name"]') || {}).content || (location.hostname || ""),
    contentHtml: clone.innerHTML,
    sourceUrl: location.href,
  };
})()`;

export function ReaderMode({ tabId, open, onClose }: {
  tabId: string | null;
  open: boolean;
  onClose: () => void;
}) {
  const [article, setArticle] = useState<Article | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !tabId) return;
    setLoading(true);
    setError(null);
    setArticle(null);
    const isTauri = typeof window !== "undefined" && "__TAURI__" in window;
    if (!isTauri) {
      setError("Reader mode is only available in the desktop app");
      setLoading(false);
      return;
    }
    const tauri = (window as unknown as Record<string, unknown>).__TAURI__ as {
      core: { invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown> };
    };
    tauri.core
      .invoke("browser_evaluate", { tabId, expression: READER_SCRIPT })
      .then((raw) => {
        const text = String(raw);
        if (!text || text === "null") {
          setError("No article content found on this page");
          return;
        }
        try {
          const parsed = JSON.parse(text) as Article;
          if (!parsed.contentHtml) {
            setError("No article content found on this page");
            return;
          }
          setArticle(parsed);
        } catch (e) {
          setError(`Failed to parse article: ${e}`);
        }
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [open, tabId]);

  // Close on Escape so the user can dismiss the overlay without hunting
  // for the X.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.18 }}
          className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-background/95 backdrop-blur"
          onClick={onClose}
          data-testid="reader-overlay"
        >
          <motion.div
            initial={{ y: 16, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: 16, opacity: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className="my-12 w-full max-w-2xl rounded-lg border border-border bg-card p-8 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-6 flex items-center gap-2 text-muted-foreground">
              <BookOpen size={14} className="text-accent" />
              <span className="text-[11px] uppercase tracking-wide">Reader mode</span>
              <div className="flex-1" />
              <button
                onClick={onClose}
                title="Close (Esc)"
                className="rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <X size={14} />
              </button>
            </div>
            {loading && (
              <div className="flex h-32 items-center justify-center text-sm text-muted-foreground">
                <motion.div
                  animate={{ rotate: 360 }}
                  transition={{ duration: 1.2, repeat: Infinity, ease: "linear" }}
                  className="mr-3 h-4 w-4 rounded-full border-2 border-muted-foreground/30 border-t-muted-foreground"
                />
                Extracting article…
              </div>
            )}
            {error && !loading && (
              <div className="rounded border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-400">
                {error}
              </div>
            )}
            {article && !loading && (
              <article className="prose prose-invert max-w-none text-[15px] leading-relaxed">
                <h1 className="mb-2 text-2xl font-semibold tracking-tight text-foreground">
                  {article.title || "Untitled"}
                </h1>
                <div className="mb-6 text-xs text-muted-foreground">
                  {article.byline && <span>By {article.byline} · </span>}
                  {article.siteName && <span>{article.siteName}</span>}
                </div>
                <div
                  className="space-y-4 [&_a]:text-accent [&_a]:underline [&_blockquote]:border-l-2 [&_blockquote]:border-accent [&_blockquote]:pl-4 [&_blockquote]:italic [&_h2]:mt-8 [&_h2]:text-xl [&_h2]:font-semibold [&_h3]:mt-6 [&_h3]:text-lg [&_h3]:font-semibold [&_img]:my-4 [&_img]:max-w-full [&_img]:rounded [&_p]:text-foreground/90 [&_pre]:overflow-x-auto [&_pre]:rounded [&_pre]:bg-muted [&_pre]:p-3 [&_pre]:text-xs [&_ul]:list-disc [&_ul]:pl-6"
                  dangerouslySetInnerHTML={{ __html: article.contentHtml }}
                />
                <div className="mt-8 border-t border-border pt-4 text-xs text-muted-foreground">
                  Original: <a href={article.sourceUrl} target="_blank" rel="noreferrer" className="text-accent hover:underline">{article.sourceUrl}</a>
                </div>
              </article>
            )}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
