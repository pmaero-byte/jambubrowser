import { useState, useRef, useCallback } from "react";
import { AnimatePresence, motion } from "framer-motion";

// --- Modular UI Components ---
import { Header } from "./components/Header";
import { CommandBar } from "./components/CommandBar";
import { Welcome } from "./components/Welcome";
import { MetricsPanel } from "./components/MetricsPanel";
import { BrowserPane } from "./components/BrowserPane";
import { TabSystem } from "./components/TabSystem";
import { MessageList } from "./components/MessageList";
import { PrivacyControls } from "./components/PrivacyControls";
import { AuditLogViewer } from "./components/AuditLogViewer";
import { AgentStatusBar } from "./components/AgentStatusBar";
import { VaultUnlock } from "./components/VaultUnlock";

// --- Hooks ---
import { localFetch } from "./utils/api";
import { useKeyboardShortcuts } from "./utils/useKeyboardShortcuts";

import "./App.css";

interface Tab { id: string; url: string; title: string; }

function App() {
  // 1. Browser & Tab State
  const [tabs, setTabs] = useState<Tab[]>([{ id: '1', url: 'about:blank', title: 'New Tab' }]);
  const [activeTabId, setActiveTabId] = useState('1');

  // 2. Navigation & Theme State
  const [activeTab, setActiveTab] = useState<'chat' | 'stealth' | 'graph' | 'workspace' | 'privacy' | 'audit' | 'vault'>('chat');
  const [showHistory, setShowHistory] = useState(false);
  const [fullPower, setFullPower] = useState(false);

  // 3. Intelligence & Research State
  const [messages, setMessages] = useState<any[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  // 4. Performance Metrics State
  const [metrics, setMetrics] = useState({ nodes: 0, tokens: 0, ram: 0, duration: 0 });

  // History for browser navigation
  const [history, setHistory] = useState<{ url: string; title: string; timestamp: number }[]>([]);

  const currentTab = tabs.find(t => t.id === activeTabId) || tabs[0];

  // --- Handlers ---

  const addTab = () => {
    const newTab = { id: Date.now().toString(), url: 'https://example.com', title: 'New Tab' };
    setTabs([...tabs, newTab]);
    setActiveTabId(newTab.id);
  };

  const closeTab = (id: string) => {
    const newTabs = tabs.filter(t => t.id !== id);
    if (newTabs.length === 0) return addTab();
    setTabs(newTabs);
    if (activeTabId === id) setActiveTabId(newTabs[newTabs.length - 1].id);
  };

  const updateUrl = (url: string) => {
    setTabs(tabs.map(t => t.id === activeTabId ? { ...t, url } : t));
  };

  const visitUrl = (url: string, title: string) => {
    setHistory(prev => [{ url, title, timestamp: Date.now() }, ...prev].slice(0, 200));
    updateUrl(url);
  };

  const handleSourceClick = (url: string) => {
    visitUrl(url, url);
  };

  const fileInputRef = useRef<HTMLInputElement>(null);

  // --- Keyboard Shortcuts ---

  useKeyboardShortcuts({
    "Meta+K": useCallback(() => {
      document.querySelector<HTMLInputElement>(".input-area input")?.focus();
    }, []),
    "Meta+P": useCallback(() => setActiveTab("privacy"), []),
    "Meta+L": useCallback(() => setActiveTab("audit"), []),
    "Meta+1": useCallback(() => setActiveTab("chat"), []),
    "Meta+T": useCallback(() => addTab(), []),
    "Escape": useCallback(() => {
      if (activeTab === "privacy" || activeTab === "audit") setActiveTab("chat");
    }, [activeTab]),
  });

  // --- Handlers (The 'Brain' of the UI) ---

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    // A. Update local UI state
    setMessages(prev => [...prev, { role: "user", content: input }]);
    const query = input; setInput(""); setIsLoading(true);

    // B. Call the Backend API
    try {
      const res = await localFetch("/research", {
        method: "POST",
        body: JSON.stringify({ query, brain_only: !fullPower })
      });
      const data = await res.json();

      // C. Update metrics and show assistant response
      setMetrics(p => ({ ...p, nodes: data.doc_count || 0 }));
      setMessages(prev => [...prev, { 
        role: "assistant", 
        content: data.answer, 
        sources: data.sources 
      }]);
    } catch (err: any) { 
      console.error(err);
      const msg = err?.name === 'AbortError'
        ? "Research timed out. The server took too long to respond."
        : "Sorry, I encountered an error processing your request.";
      setMessages(prev => [...prev, { 
        role: "assistant", 
        content: msg
      }]);
    } finally { 
      setIsLoading(false);
    }
  };

  return (
    <main className="container dark">
      <Header 
        activeTab={activeTab} setActiveTab={setActiveTab} 
        fullPower={fullPower} setFullPower={setFullPower}
        showHistory={showHistory} setShowHistory={setShowHistory}
      />
      <AgentStatusBar />
      
      <div className="main-layout split-view">
        {/* Left Side: Agentic Chat Sidebar (30%) */}
        <div className="sidebar-chat glass">
          <MetricsPanel {...metrics} />
          <div className="chat-window">
            {messages.length === 0 && <Welcome />}
            <MessageList 
              messages={messages} 
              onSourceClick={handleSourceClick} 
            />
          </div>
          <CommandBar 
            input={input} setInput={setInput} isLoading={isLoading}
            handleSubmit={handleSubmit} domain="general" setDomain={() => {}}
            startListening={() => {}} fileInputRef={fileInputRef} handleImageSelect={() => {}}
          />
        </div>

        {/* Right Side: Immersive Browser Area (70%) */}
        <div className="browser-area">
          <TabSystem 
            tabs={tabs} 
            activeTabId={activeTabId} 
            onTabSelect={setActiveTabId} 
            onTabClose={closeTab} 
            onAddTab={addTab} 
          />
          <BrowserPane 
            url={currentTab.url} 
            onUrlChange={updateUrl} 
          />
          
          <AnimatePresence>
            {activeTab === 'privacy' && (
              <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="overlay-privacy glass">
                <PrivacyControls />
              </motion.div>
            )}
            {activeTab === 'audit' && (
              <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="overlay-audit glass">
                <AuditLogViewer />
              </motion.div>
            )}
            {activeTab === 'vault' && (
              <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="overlay-audit glass">
                <VaultUnlock />
              </motion.div>
            )}
            {showHistory && (
              <motion.div initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }} className="overlay-history glass">
                <div className="history-panel">
                  <h3>Browser History</h3>
                  {history.length === 0 ? (
                    <p style={{ color: "var(--text-dim)", fontSize: "0.85rem" }}>No history yet.</p>
                  ) : (
                    <div className="history-list">
                      {history.map((item, i) => (
                        <div key={i} className="history-item" onClick={() => visitUrl(item.url, item.title)}>
                          <div className="history-title">{item.title || item.url}</div>
                          <div className="history-url">{item.url}</div>
                          <div className="history-time">{new Date(item.timestamp).toLocaleString()}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </main>
  );
}

export default App;
