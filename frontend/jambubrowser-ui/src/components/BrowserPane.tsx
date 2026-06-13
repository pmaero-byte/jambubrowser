import { useState, useCallback } from "react";
import { motion } from "framer-motion";
import { Globe, ExternalLink } from "lucide-react";

interface BrowserPaneProps {
  url: string;
  onUrlChange: (url: string) => void;
}

export const BrowserPane = ({ url, onUrlChange }: BrowserPaneProps) => {
  const [frameFailed, setFrameFailed] = useState(false);
  const [history, setHistory] = useState<string[]>([]);
  const [historyIdx, setHistoryIdx] = useState(-1);

  const resolvedUrl = url === "about:blank"
    ? "about:blank"
    : url.startsWith("http")
      ? url
      : `https://duckduckgo.com/?q=${encodeURIComponent(url)}`;

  const navigate = useCallback((targetUrl: string) => {
    setFrameFailed(false);
    const newHistory = history.slice(0, historyIdx + 1);
    newHistory.push(targetUrl);
    setHistory(newHistory);
    setHistoryIdx(newHistory.length - 1);
    onUrlChange(targetUrl);
  }, [history, historyIdx, onUrlChange]);

  const goBack = () => {
    if (historyIdx > 0) {
      const prev = history[historyIdx - 1];
      setHistoryIdx(historyIdx - 1);
      onUrlChange(prev);
      setFrameFailed(false);
    }
  };

  const goForward = () => {
    if (historyIdx < history.length - 1) {
      const next = history[historyIdx + 1];
      setHistoryIdx(historyIdx + 1);
      onUrlChange(next);
      setFrameFailed(false);
    }
  };

  const refresh = () => {
    setFrameFailed(false);
    const iframe = document.querySelector(".browser-iframe") as HTMLIFrameElement;
    if (iframe) {
      iframe.src = iframe.src;
    }
  };

  const openExternally = () => {
    window.open(resolvedUrl, "_blank", "noopener,noreferrer");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      navigate((e.target as HTMLInputElement).value);
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      className="browser-pane glass"
    >
      <div className="browser-toolbar">
        <div className="nav-btns">
          <button onClick={goBack} disabled={historyIdx <= 0} title="Back">
            <span style={{ fontSize: "1.1rem", lineHeight: 1 }}>&larr;</span>
          </button>
          <button onClick={goForward} disabled={historyIdx >= history.length - 1} title="Forward">
            <span style={{ fontSize: "1.1rem", lineHeight: 1 }}>&rarr;</span>
          </button>
          <button onClick={refresh} title="Refresh">
            <span style={{ fontSize: "1.1rem", lineHeight: 1 }}>&#x21bb;</span>
          </button>
        </div>
        <div className="url-bar">
          <Globe size={14} />
          <input
            value={url}
            onChange={(e) => onUrlChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search or enter URL"
          />
        </div>
      </div>

      <div className="webview-container">
        {resolvedUrl === "about:blank" ? (
          <div className="blank-page">
            <Globe size={48} color="#333" />
            <p style={{ color: "var(--text-dim)", marginTop: 16 }}>Enter a URL to start browsing</p>
          </div>
        ) : (
          <>
            <iframe
              key={resolvedUrl}
              src={resolvedUrl}
              title="Web View"
              className="browser-iframe"
              onError={() => setFrameFailed(true)}
              style={{ display: frameFailed ? "none" : "block" }}
            />
            {frameFailed && (
              <div className="blank-page">
                <Globe size={36} color="#555" />
                <p style={{ color: "var(--text-dim)", marginTop: 12, marginBottom: 12 }}>
                  This site refused to load in an embedded frame.
                </p>
                <button className="open-external-btn" onClick={openExternally}>
                  <ExternalLink size={14} />
                  Open in Browser
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </motion.div>
  );
};
