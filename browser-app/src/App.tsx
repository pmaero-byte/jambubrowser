import { useState, useRef, useEffect } from "react";
import { invoke } from "@tauri-apps/api/core";
import { motion, AnimatePresence } from "framer-motion";
import { 
  History, 
  Globe, 
  Shield, 
  Box, 
  Zap, 
  Mic, 
  Paperclip, 
  ArrowUpRight, 
  BrainCircuit, 
  Clipboard, 
  Download,
  Trash2,
  Cpu,
  Activity,
  Plus,
  Trees,
  Compass,
  ArrowLeft,
  ArrowRight,
  RotateCw,
  Search,
  ExternalLink
} from "lucide-react";
import { localFetch } from "./utils/api";
import { BrainGraph3D } from "./BrainGraph3D";
import { AgentAvatar3D } from "./AgentAvatar3D";
import "./App.css";

interface Message {
  role: "user" | "assistant";
  content: string;
  thought?: string;
  logs?: string[];
  facts?: string[];
  confidence?: string;
  sources?: string[];
  memory_links?: string[];
  debate?: { optimist: string, skeptic: string };
  suggestions?: { label: string, type: string, x?: number, y?: number, selector?: string }[];
}

interface Artifact {
  id: string;
  type: "text" | "table" | "image";
  content: string;
  title: string;
}

const MarkdownText = ({ content, sources, onNavigate }: { content: string, sources: string[], onNavigate?: (url: string) => void }) => {
  const parts = content.split(/(\[\d+\])/g);
  return (
    <div className="answer">
      {parts.map((part, i) => {
        const match = part.match(/\[(\d+)\]/);
        if (match) {
          const index = parseInt(match[1]) - 1;
          const url = sources[index] || "#";
          return (
            <span key={i} className="citation-wrapper">
              <a href="#" onClick={(e) => { e.preventDefault(); if (onNavigate) onNavigate(url); }} className="citation-tag">{part}</a>
              <div className="citation-peek glass">
                <div className="source-domain">{url.replace(/^https?:\/\//, '').split('/')[0]}</div>
                <div className="source-url">{url}</div>
                <div className="peek-hint">Click to navigate browser</div>
              </div>
            </span>
          );
        }
        return part;
      })}
    </div>
  );
};

function App() {
  const [activeTab, setActiveTab] = useState<'browser' | 'chat' | 'stealth' | 'graph' | 'workspace' | 'toolbox'>('browser');
  const [theme, setTheme] = useState<'dark' | 'deep-blue'>('dark');
  const [showHistory, setShowHistory] = useState(false);
  const [historySearch, setHistorySearch] = useState("");
  const [notifications, setNotifications] = useState<{id: number, text: string}[]>([]);
  const [fullPower, setFullPower] = useState(false);
  
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [status, setStatus] = useState("Local LLM & Search Active");
  const [currentLogs, setCurrentLogs] = useState<string[]>([]);
  const [selectedImage, setSelectedImage] = useState<string | null>(null);
  
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [tools, setTools] = useState<{name: string, description: string, created: number}[]>([]);
  const [remoteTools, setRemoteTools] = useState<{name: string, description: string, peer: string}[]>([]);
  const [missions] = useState<{id: string, query: string}[]>([]);
  const [history, setHistory] = useState<string[]>([]);
  const [peers, setPeers] = useState<string[]>([]);
  const [graphData, setGraphData] = useState({ nodes: [], edges: [] });

  const [deepResearch, setDeepResearch] = useState(false);
  const [temperature, setTemperature] = useState(0.7);
  const [domain, setDomain] = useState("general");
  
  const [brainDocCount, setBrainDocCount] = useState(0);
  const [totalTokens, setTotalTokens] = useState(0);
  const [health, setHealth] = useState({ ram: 0, cpu: 0 });
  const [benchmark, setBenchmark] = useState({ duration: 0, peak_ram: 0 });
  const [privacyScore, setPrivacyScore] = useState(85);

  const [vault, setVault] = useState<{domain: string, user: string, pass: string}[]>([]);
  const [localIp, setLocalIp] = useState("127.0.0.1");
  const [isListening, setIsListening] = useState(false);
  const [stealthConfig, setStealthConfig] = useState({
    proxies: [] as string[],
    userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    intensity: "high",
    apiKey: "",
    engines: ["google", "wikipedia", "bing", "duckduckgo"],
    torRouting: false,
    incognito: false
  });

  const [llmConfig] = useState({
    provider: "local",
    baseUrl: "http://localhost:11434/v1",
    modelId: "gemma4:12b",
    apiKey: ""
  });

  const [browserUrl, setBrowserUrl] = useState("https://en.wikipedia.org/wiki/Main_Page");
  const [browserInput, setBrowserInput] = useState("");
  const [browserTitle, setBrowserTitle] = useState("");

  const navigateToUrl = (url: string) => {
    const target = url.startsWith('http') ? url : `https://${url}`;
    setBrowserUrl(target);
    setBrowserInput(target);
    setActiveTab('browser');
  };
  const handleBrowserSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    navigateToUrl(browserInput);
  };

  const fileInputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages, currentLogs]);

  useEffect(() => {
    const savedH = localStorage.getItem('research_history');
    if (savedH) setHistory(JSON.parse(savedH));
    const savedV = localStorage.getItem('agent_vault');
    if (savedV) setVault(JSON.parse(savedV));
    
    const fetchMeta = async () => {
      try {
        const ip: string = await invoke("get_local_ip");
        setLocalIp(ip);
      } catch (e) {}
    };
    fetchMeta();

    const ticker = setInterval(async () => {
      try {
        const hResp = await fetch('http://localhost:8001/health');
        const hData = await hResp.json();
        setHealth({ ram: hData.ram_used_gb, cpu: hData.cpu_percent });
        
        const bResp = await fetch('http://localhost:8001/benchmark');
        const bData = await bResp.json();
        setBenchmark({ duration: bData.last_research_duration, peak_ram: bData.memory_peak_gb });
      } catch (e) {}
    }, 10000);
    return () => clearInterval(ticker);
  }, []);

  useEffect(() => {
    if (activeTab === 'graph') {
      fetch('http://localhost:8001/graph_data').then(r => r.json()).then(d => setGraphData(d));
    }
    if (activeTab === 'toolbox') {
      fetch('http://localhost:8001/tools').then(r => r.json()).then(d => setTools(d.tools));
      const fetchRemote = async () => {
         const allRemote: any[] = [];
         for (const peer of peers) {
            try {
              const r = await fetch(`http://${peer}:8001/peers/tools`);
              const d = await r.json();
              d.tools.forEach((t: any) => allRemote.push({ ...t, peer }));
            } catch(e){}
         }
         setRemoteTools(allRemote);
      };
      fetchRemote();
    }
    if (domain === 'academic') setTheme('deep-blue');
    else setTheme('dark');
  }, [activeTab, domain, peers]);

  useEffect(() => {
    let score = 50;
    if (stealthConfig.proxies.length > 0) score += 20;
    if (stealthConfig.intensity === 'high') score += 20;
    if (stealthConfig.userAgent) score += 10;
    setPrivacyScore(score);
  }, [stealthConfig]);

  const addNotification = (text: string) => {
    const id = Date.now();
    setNotifications(prev => [...prev, { id, text }]);
    setTimeout(() => setNotifications(prev => prev.filter(n => n.id !== id)), 4000);
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    addNotification("Copied to clipboard.");
  };

  const downloadReport = (content: string) => {
    const blob = new Blob([content], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `research_report_${Date.now()}.md`;
    a.click();
  };

  const startListening = () => {
    const SR = (window as any).webkitSpeechRecognition || (window as any).Recognition;
    if (!SR) return addNotification("Speech not supported.");
    const rec = new SR();
    rec.onstart = () => setIsListening(true);
    rec.onresult = (e: any) => setInput(e.results[0][0].transcript);
    rec.onend = () => setIsListening(false);
    rec.start();
  };

  const saveToVault = (domain: string, user: string, pass: string) => {
    const newVault = [...vault, { domain, user, pass }];
    setVault(newVault);
    localStorage.setItem('agent_vault', JSON.stringify(newVault));
    addNotification(`Credentials for ${domain} saved.`);
  };

  const pullRemoteTool = async (name: string, peerIp: string) => {
    setStatus(`Pulling tool ${name} from peer...`);
    try {
      const resp = await fetch(`http://${peerIp}:8001/peers/tools/pull?name=${name}`);
      const data = await resp.json();
      if (data.code) {
        await localFetch('http://localhost:8001/tool/save', 'POST', { name: data.name, description: "Pulled from Peer Mesh", code: data.code });
        addNotification(`Skill '${name}' installed from network.`);
        fetch('http://localhost:8001/tools').then(r => r.json()).then(d => setTools(d.tools));
      }
    } catch (e) { console.error(e); }
    setStatus("Local LLM & Search Active");
  };

  const discoverPeers = async () => {
    setStatus("Scanning local network for peers...");
    try {
      const resp = await fetch('http://localhost:8001/peers/discover');
      const data = await resp.json();
      setPeers(data.peers);
      addNotification(`Found ${data.peers.length} research peers.`);
    } catch (e) { console.error(e); }
    setStatus("Local LLM & Search Active");
  };

  const toggleFullPower = (val: boolean) => {
    setFullPower(val);
    if (val) {
      setDeepResearch(true); setTemperature(1.2);
      setStealthConfig({ ...stealthConfig, torRouting: true, incognito: true, intensity: "high" });
      addNotification("🔥 GOD MODE ACTIVE.");
    }
  };

  const handleImageSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      const reader = new FileReader();
      reader.onloadend = () => setSelectedImage((reader.result as string).split(",")[1]);
      reader.readAsDataURL(file);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    if (input.startsWith('/')) {
      handleSlashCommand(input);
      setInput("");
      return;
    }

    setMessages(prev => [...prev, { role: "user", content: input }]);
    const query = input; setInput(""); setIsLoading(true);
    setStatus(deepResearch ? "Swarm Executing..." : "Researching...");
    setCurrentLogs([]);

    const cid = Math.random().toString(36).substring(7);
    const ws = new WebSocket(`ws://localhost:8001/ws/${cid}`);
    ws.onmessage = (ev) => {
      setCurrentLogs(prev => [...prev, ev.data]);
    };

    try {
      const res: any = await invoke("execute_query", { 
        query, persist: false, clientId: cid, imageData: selectedImage,
        stealthConfig, deepResearch, domain, reportMode: false, personality: "researcher", temperature, vault,
        torRouting: stealthConfig.torRouting, incognito: stealthConfig.incognito, llmConfig
      });
      if (res.brain_doc_count) setBrainDocCount(res.brain_doc_count);
      if (res.total_tokens) setTotalTokens(res.total_tokens);
      
      const assistantMsg: Message = { 
        role: "assistant", content: res.answer, sources: res.sources, 
        debate: res.debate, facts: res.facts, confidence: res.confidence, suggestions: res.suggestions
      };

      // Iteration 133: Auto-trigger Vision Grounding for complex research
      if (res.sources && res.sources.length > 0 && deepResearch) {
        const gResp = await fetch('http://localhost:8001/vision/grounding', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: res.sources[0], client_id: cid })
        });
        const gData = await gResp.json();
        assistantMsg.suggestions = gData.suggestions;
      }

      setMessages(prev => [...prev, assistantMsg]);
    } catch (err: any) {
      addNotification(`Error: ${err}`);
    } finally {
      setIsLoading(false); setStatus("Local LLM & Search Active"); ws.close();
    }
  };

  const handleSlashCommand = async (cmd: string) => {
    const [action, ...args] = cmd.slice(1).split(" ");
    if (action === 'ground') {
      const url = args[0] || history[0];
      if(!url) return addNotification("No URL to ground.");
      setStatus("Grounding...");
      const resp = await fetch('http://localhost:8001/vision/grounding', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, client_id: "default" })
      });
      const data = await resp.json();
      setMessages(prev => [...prev, { role: "assistant", content: `Visual grounding complete for ${url}.`, suggestions: data.suggestions }]);
      setStatus("Local LLM & Search Active");
    } else if (action === 'clear') {
      setMessages([]);
    } else {
      addNotification(`Unknown command: ${action}`);
    }
  };

  const executeAction = async (s: any) => {
    setStatus(`Executing: ${s.label}...`);
    try {
      await localFetch('http://localhost:8001/workflow/execute', 'POST', { 
        url: s.url || messages[messages.length-1].sources?.[0], 
        steps: [{ action: s.action, selector: s.selector, value: s.value }],
        client_id: "default"
      });
      addNotification(`Action ${s.label} completed.`);
    } catch (e) {
      addNotification(`Action failed: ${e}`);
    }
    setStatus("Local LLM & Search Active");
  };

  return (
    <main className={`container ${theme}`}>
      <div className="toast-container">
        {notifications.map(n => <div key={n.id} className="toast">{n.text}</div>)}
      </div>

      <header className="header glass">
        <div className="title-area">
          <h1><Trees size={20} color="#00ff64" style={{marginRight: '8px', verticalAlign: 'middle'}}/> JambuAI Browser <span className="rc-badge">RC1</span></h1>
        </div>

        <div className="tabs">
          <button className={activeTab === 'browser' ? 'active' : ''} onClick={() => setActiveTab('browser')}><Compass size={14}/> Browser</button>
          <button className={activeTab === 'chat' ? 'active' : ''} onClick={() => setActiveTab('chat')}><Zap size={14}/> Research</button>
          <button className={activeTab === 'graph' ? 'active' : ''} onClick={() => setActiveTab('graph')}><BrainCircuit size={14}/> Intelligence</button>
          <button className={activeTab === 'workspace' ? 'active' : ''} onClick={() => setActiveTab('workspace')}><Box size={14}/> Workspace</button>
          <button className={activeTab === 'toolbox' ? 'active' : ''} onClick={() => setActiveTab('toolbox')}>🧰 Toolbox</button>
          <button className={activeTab === 'stealth' ? 'active' : ''} onClick={() => setActiveTab('stealth')}><Shield size={14}/> Stealth</button>
        </div>

        <div className="header-actions">
          <button onClick={() => setShowHistory(!showHistory)}><History size={16}/> {showHistory ? 'Hide' : 'History'}</button>
          <label className="full-power-toggle">
            <input type="checkbox" checked={fullPower} onChange={(e) => toggleFullPower(e.target.checked)} />
            <span>🔥 FULL POWER</span>
          </label>
        </div>
      </header>
      
      <div className="main-layout">
        <AnimatePresence>
          {activeTab === 'graph' && (
            <motion.div 
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              className="graph-view"
            >
              <BrainGraph3D data={graphData} />
            </motion.div>
          )}
        </AnimatePresence>

        <AnimatePresence>
          {showHistory && (
            <motion.aside 
              initial={{ width: 0, opacity: 0 }} animate={{ width: 300, opacity: 1 }} exit={{ width: 0, opacity: 0 }}
              className="history-sidebar glass"
            >
              <div className="history-section">
                <h3>Recent Research</h3>
                <input className="history-search" placeholder="Search..." value={historySearch} onChange={(e) => setHistorySearch(e.target.value)} />
                {history.filter(h => h.toLowerCase().includes(historySearch.toLowerCase())).map((h, i) => (
                  <div key={i} className="history-item" onClick={() => setInput(h)}>{h}</div>
                ))}
              </div>
              <div className="mission-section">
                <h3>Active Missions</h3>
                {missions.map(m => (
                  <div key={m.id} className="mission-item">🚀 {m.query}</div>
                ))}
              </div>
            </motion.aside>
          )}
        </AnimatePresence>
        
        <div className="chat-area">
          <div className="top-metrics">
             <span><BrainCircuit size={12}/> {brainDocCount} nodes</span>
             <span><Zap size={12}/> {totalTokens} tokens</span>
             <span><Cpu size={12}/> {health.ram.toFixed(1)}GB RAM</span>
             {benchmark.duration > 0 && <span><Activity size={12}/> {benchmark.duration.toFixed(1)}s</span>}
          </div>

          {activeTab === 'browser' ? (
            <div className="browser-view">
              <form className="browser-urlbar glass" onSubmit={handleBrowserSubmit}>
                <button type="button" className="browser-nav-btn" onClick={() => setBrowserUrl(browserUrl)}><ArrowLeft size={16}/></button>
                <button type="button" className="browser-nav-btn" onClick={() => setBrowserUrl(browserUrl)}><ArrowRight size={16}/></button>
                <button type="button" className="browser-nav-btn" onClick={() => setBrowserUrl(browserUrl + '')}><RotateCw size={16}/></button>
                <Globe size={14} style={{opacity:0.5}}/>
                <input 
                  value={browserInput || browserUrl} 
                  onChange={e => setBrowserInput(e.target.value)}
                  onFocus={() => setBrowserInput(browserUrl)}
                  placeholder="Enter URL or search..."
                />
                <button type="submit"><Search size={16}/></button>
              </form>
              <div className="browser-frame">
                <iframe 
                  src={browserUrl}
                  title="Jambu Browser"
                  sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
                  style={{ width: '100%', height: '100%', border: 'none', borderRadius: '12px' }}
                />
              </div>
            </div>
          ) : activeTab === 'chat' ? (
            <>
              <div className="chat-window" ref={scrollRef}>
                {messages.length === 0 && (
                  <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="welcome">
                    <Trees size={48} color="#00ff64" style={{marginBottom: '16px'}}/>
                    <h2>Sovereign Intelligence Active.</h2>
                    <p>Start a secure research mission with JambuAI.</p>
                  </motion.div>
                )}
                {messages.map((msg, i) => (
                  <motion.div 
                    key={i} initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}
                    className={`message ${msg.role}`}
                  >
                    <div className="avatar">
                      {msg.role === "user" ? "U" : <AgentAvatar3D status="idle" />}
                    </div>
                    <div className="content">
                      {msg.debate && (
                        <div className="debate-box glass">
                          <div className="debate-label">⚖️ Consensus Debate</div>
                          <div className="debate-grid">
                            <div className="debate-pane optimist"><strong>Optimist</strong>{msg.debate.optimist}</div>
                            <div className="debate-pane skeptic"><strong>Skeptic</strong>{msg.debate.skeptic}</div>
                          </div>
                        </div>
                      )}
                      {msg.facts && msg.facts.length > 0 && (
                        <div className="facts-box glass">
                          <div className="facts-label">Evidence Found</div>
                          <ul>{msg.facts.map((f, fi) => <li key={fi} className="fact-item">{f} <button className="pin-btn" onClick={() => { setArtifacts(p => [...p, {id: Date.now().toString(), type: "text", content: f, title: "Fact"}]); addNotification("Pinned."); }}>📌</button></li>)}</ul>
                        </div>
                      )}
                      <MarkdownText content={msg.content} sources={msg.sources || []} onNavigate={navigateToUrl} />
                      {msg.sources && msg.sources.length > 0 && (
                        <div className="source-grid">
                          {msg.sources.map((src, si) => (
                            <a key={si} href="#" onClick={(e) => { e.preventDefault(); navigateToUrl(src); }} className="source-card glass">
                              <div className="source-domain">{src.replace(/^https?:\/\//, '').split('/')[0]} <ArrowUpRight size={10}/></div>
                              <div className="source-url">{src}</div>
                            </a>
                          ))}
                        </div>
                      )}
                      {msg.suggestions && msg.suggestions.length > 0 && (
                        <div className="action-suggestions">
                          {msg.suggestions.map((s, si) => (
                            <button key={si} className="suggestion-chip glass" onClick={() => executeAction(s)}>
                              <Zap size={10}/> {s.label}
                            </button>
                          ))}
                        </div>
                      )}
                      {msg.role === 'assistant' && (
                        <div className="assistant-actions">
                          <button onClick={() => copyToClipboard(msg.content)}><Clipboard size={14}/> Copy</button>
                          <button onClick={() => downloadReport(msg.content)}><Download size={14}/> Export</button>
                        </div>
                      )}
                    </div>
                  </motion.div>
                ))}
                {isLoading && (
                  <div className="message assistant loading-state">
                    <div className="avatar"><AgentAvatar3D status={status} /></div>
                    <div className="content">
                      <div className="progress-status">{status}</div>
                      <div className="log-list">{currentLogs.slice(-3).map((l, i) => <div key={i} className="log-item">{l}</div>)}</div>
                    </div>
                  </div>
                )}
              </div>
              <motion.div layout className="input-container">
                <div className="domain-bar">
                   <button className={domain === 'general' ? 'active' : ''} onClick={() => setDomain('general')}>General</button>
                   <button className={domain === 'academic' ? 'active' : ''} onClick={() => setDomain('academic')}>Academic</button>
                   <button className={domain === 'coding' ? 'active' : ''} onClick={() => setDomain('coding')}>Coding</button>
                </div>
                <form className="input-area glass" onSubmit={handleSubmit}>
                  <button type="button" onClick={startListening}>{isListening ? "🛑" : <Mic size={18}/>}</button>
                  <button type="button" onClick={() => fileInputRef.current?.click()}><Paperclip size={18}/></button>
                  <input ref={fileInputRef} type="file" style={{display:'none'}} onChange={handleImageSelect} />
                  <input value={input} onChange={e => setInput(e.target.value)} placeholder="Send a command or search query..." />
                  <button type="submit" disabled={isLoading} className="go-btn"><ArrowUpRight size={20}/></button>
                </form>
              </motion.div>
            </>
          ) : activeTab === 'workspace' ? (
            <div className="workspace-view">
              <div className="workspace-grid">
                {artifacts.map(art => (
                  <motion.div layout key={art.id} className="artifact-card glass">
                    <div className="artifact-header"><span>{art.title}</span><button onClick={() => setArtifacts(p => p.filter(a => a.id !== art.id))}><Trash2 size={14}/></button></div>
                    <div className="artifact-content">{art.content}</div>
                  </motion.div>
                ))}
              </div>
            </div>
          ) : activeTab === 'toolbox' ? (
            <div className="toolbox-view">
              <div className="tools-grid">
                {tools.map((t, i) => <motion.div layout key={i} className="tool-card glass"><div className="tool-name">⚙️ {t.name}</div><div className="tool-desc">{t.description}</div></motion.div>)}
              </div>
              {remoteTools.length > 0 && (
                <div className="remote-tools">
                   <h3>📡 Shared network skills</h3>
                   <div className="tools-grid">
                      {remoteTools.map((rt, i) => <div key={i} className="tool-card glass"><div className="tool-name">{rt.name}</div><button onClick={() => pullRemoteTool(rt.name, rt.peer)}>Pull</button></div>)}
                   </div>
                </div>
              )}
            </div>
          ) : (
            <div className="stealth-settings">
              <motion.div initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} className="privacy-header glass">
                <h2>Stealth & Sovereignty</h2>
                <div className="privacy-meter">Anonymity Score: {privacyScore}% | IP: {localIp}</div>
              </motion.div>
              <div className="settings-grid">
                 <button onClick={discoverPeers} className="glass-btn"><Globe size={14}/> Scan for Network Peers</button>
                 <div className="vault-add glass">
                    <input id="v-dom" placeholder="domain.com" />
                    <button onClick={() => {
                        const d = (document.getElementById('v-dom') as HTMLInputElement).value;
                        if(d) saveToVault(d, "u", "p");
                    }}><Plus size={14}/></button>
                 </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </main>
  );
}

export default App;
