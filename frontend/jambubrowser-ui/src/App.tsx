import { useState, useRef, useEffect } from "react";
import { invoke } from "@tauri-apps/api/core";
import { AnimatePresence, motion } from "framer-motion";

// --- Modular UI Components ---
import { Header } from "./components/Header";
import { CommandBar } from "./components/CommandBar";
import { Welcome } from "./components/Welcome";
import { MetricsPanel } from "./components/MetricsPanel";
import { BrowserPane } from "./components/BrowserPane";
import { TabSystem } from "./components/TabSystem";
import { MessageList } from "./components/MessageList";
import { BrainGraph3D } from "./BrainGraph3D";
import { AgentAvatar3D } from "./AgentAvatar3D";
import { PrivacyControls } from "./components/PrivacyControls";
import { AuditLogViewer } from "./components/AuditLogViewer";

// --- Hook for API interaction (Modular Logic) ---
import { localFetch } from "./utils/api";

import "./App.css";

interface Tab { id: string; url: string; title: string; }

function App() {
  // 1. Browser & Tab State
  const [tabs, setTabs] = useState<Tab[]>([{ id: '1', url: 'https://www.google.com', title: 'Google' }]);
  const [activeTabId, setActiveTabId] = useState('1');

  // 2. Navigation & Theme State
  const [activeTab, setActiveTab] = useState<'chat' | 'stealth' | 'graph' | 'workspace' | 'privacy' | 'audit'>('chat');
  const [showHistory, setShowHistory] = useState(false);
  const [fullPower, setFullPower] = useState(false);

  // 3. Intelligence & Research State
  const [messages, setMessages] = useState<any[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [status, setStatus] = useState("Local LLM Active");

  // 4. Performance Metrics State
  const [metrics, setMetrics] = useState({ nodes: 0, tokens: 0, ram: 0, duration: 0 });

  const activeTab = tabs.find(t => t.id === activeTabId) || tabs[0];

  // --- Handlers ---

  const addTab = () => {
    const newTab = { id: Date.now().toString(), url: 'https://www.google.com', title: 'New Tab' };
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

  const handleSourceClick = (url: string) => {
    updateUrl(url);
    addNotification("📍 Navigating to source evidence...");
  };

  const fileInputRef = useRef<HTMLInputElement>(null);

  // --- Handlers (The 'Brain' of the UI) ---

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    // A. Update local UI state
    setMessages(prev => [...prev, { role: "user", content: input }]);
    const query = input; setInput(""); setIsLoading(true);
    setStatus("Jambu Swarm Spawning...");

    // B. Call the Rust Orchestrator
    try {
      const res: any = await invoke("execute_query", { 
        query, persist: false, clientId: "ui", deepResearch: fullPower,
        domain: "general", llmConfig: { provider: "local" } 
      });

      // C. Update metrics and show assistant response
      setMetrics(p => ({ ...p, nodes: res.brain_doc_count, tokens: res.total_tokens }));
      setMessages(prev => [...prev, { role: "assistant", content: res.answer, sources: res.sources }]);
    } catch (err) { console.error(err); } 
    finally { setIsLoading(false); setStatus("Local LLM Active"); }
  };

  return (
    <main className="container dark">
      <Header 
        activeTab={activeTab} setActiveTab={setActiveTab} 
        fullPower={fullPower} setFullPower={setFullPower}
        showHistory={showHistory} setShowHistory={setShowHistory}
      />
      
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
            url={activeTab.url} 
            onUrlChange={updateUrl} 
          />
          
          <AnimatePresence>
            {activeTab === 'graph' && (
              <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="overlay-graph glass">
                <BrainGraph3D data={{}} />
              </motion.div>
            )}
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
          </AnimatePresence>
        </div>
      </div>
    </main>
  );
}

export default App;
