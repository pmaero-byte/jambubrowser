import { motion } from "framer-motion";
import { Globe, RefreshCw, ChevronLeft, ChevronRight } from "lucide-react";

/**
 * Premium Web Browser Pane
 * ------------------------
 * This component renders the actual web view alongside the AI agent.
 * It allows the user to browse normally while the agent 'observes' and 'acts'.
 */

interface BrowserPaneProps {
  url: string;
  onUrlChange: (url: string) => void;
}

export const BrowserPane = ({ url, onUrlChange }: BrowserPaneProps) => {
  const resolvedUrl = url === 'about:blank'
    ? 'about:blank'
    : url.startsWith('http')
      ? url
      : `https://duckduckgo.com/?q=${encodeURIComponent(url)}`;

  return (
    <motion.div 
      initial={{ opacity: 0, x: 20 }} 
      animate={{ opacity: 1, x: 0 }} 
      className="browser-pane glass"
    >
      <div className="browser-toolbar">
        <div className="nav-btns">
          <ChevronLeft size={16} />
          <ChevronRight size={16} />
          <RefreshCw size={16} />
        </div>
        <div className="url-bar">
          <Globe size={14} />
          <input 
            value={url} 
            onChange={(e) => onUrlChange(e.target.value)}
            placeholder="Search or enter URL" 
          />
        </div>
      </div>
      
      <div className="webview-container">
        {resolvedUrl === 'about:blank' ? (
          <div className="blank-page">
            <Globe size={48} color="#333" />
            <p style={{ color: 'var(--text-dim)', marginTop: 16 }}>Enter a URL to start browsing</p>
          </div>
        ) : (
          <iframe 
            src={resolvedUrl} 
            title="Web View"
            className="browser-iframe"
          />
        )}
      </div>
    </motion.div>
  );
};
