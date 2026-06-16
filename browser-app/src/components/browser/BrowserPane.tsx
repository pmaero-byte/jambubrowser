import { useState, useRef, useEffect } from "react";
import { Button } from "../ui/button";
import {
  ArrowLeft,
  ArrowRight,
  RotateCcw,
  Home,
  Plus,
  X,
  Globe,
} from "lucide-react";
import { useAppStore } from "../../store/appStore";

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

  useEffect(() => {
    if (activeTab?.url) setInputUrl(activeTab.url);
  }, [activeTab?.url, activeBrowserTabId]);

  const normalizeUrl = (raw: string) => {
    if (!raw.trim()) return "about:blank";
    if (/^(https?:|file:|about:)/i.test(raw)) return raw;
    return `https://${raw}`;
  };

  const navigate = (url: string) => {
    const next = normalizeUrl(url);
    updateBrowserTab(activeBrowserTabId, { url: next, title: next });
    setInputUrl(next);
  };

  const reload = () => {
    if (iframeRef.current) {
      const current = iframeRef.current.src;
      iframeRef.current.src = current;
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-border bg-card/50 px-2 py-1.5">
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => iframeRef.current?.contentWindow?.history.back()}>
            <ArrowLeft size={14} />
          </Button>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => iframeRef.current?.contentWindow?.history.forward()}>
            <ArrowRight size={14} />
          </Button>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={reload}>
            <RotateCcw size={14} />
          </Button>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => navigate("about:blank")}>
            <Home size={14} />
          </Button>
        </div>
        <form
          className="flex flex-1 items-center gap-2 rounded-md border border-border bg-background px-2"
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
            className="flex-1 bg-transparent py-1 text-xs outline-none"
          />
        </form>
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => addBrowserTab()}>
          <Plus size={14} />
        </Button>
      </div>

      <div className="flex gap-1 overflow-x-auto border-b border-border bg-card/30 px-2 py-1">
        {browserTabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveBrowserTab(tab.id)}
            className={`group flex max-w-[160px] items-center gap-1.5 rounded-md px-2 py-1 text-xs ${
              tab.id === activeBrowserTabId
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:bg-muted"
            }`}
          >
            <span className="truncate">{tab.title || tab.url || "New Tab"}</span>
            {browserTabs.length > 1 && (
              <span
                onClick={(e) => {
                  e.stopPropagation();
                  closeBrowserTab(tab.id);
                }}
                className="rounded p-0.5 opacity-0 group-hover:opacity-100 hover:bg-muted-foreground/20"
              >
                <X size={10} />
              </span>
            )}
          </button>
        ))}
      </div>

      <div className="relative min-h-0 flex-1 bg-background">
        {activeTab?.url && activeTab.url !== "about:blank" ? (
          <iframe
            ref={iframeRef}
            src={activeTab.url}
            title="Browser"
            sandbox="allow-scripts allow-same-origin allow-popups allow-forms"
            className="h-full w-full border-0"
          />
        ) : (
          <div className="flex h-full flex-col items-center justify-center text-muted-foreground">
            <Globe size={32} className="mb-3 text-border" />
            <p className="text-sm font-medium">No page loaded</p>
            <p className="mt-1 text-xs">Enter a URL above or click a source in chat.</p>
          </div>
        )}
      </div>
    </div>
  );
}
