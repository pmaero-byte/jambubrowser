import { Trees, Zap, BrainCircuit, Box, Shield, History, KeyRound, FileText, Brain } from "lucide-react";

/**
 * Premium Navigation Sidebar
 * --------------------------
 * Handles switching between Research, 3D Graph, and Settings.
 * Uses 'Lucide' icons for a clean, professional look.
 */

interface HeaderProps {
  activeTab: string;
  setActiveTab: (tab: any) => void;
  fullPower: boolean;
  setFullPower: (val: boolean) => void;
  showHistory: boolean;
  setShowHistory: (val: boolean) => void;
}

export const Header = ({ activeTab, setActiveTab, fullPower, setFullPower, showHistory, setShowHistory }: HeaderProps) => {
  return (
    <header className="header glass">
      <div className="title-area">
        <h1>
          <Trees size={20} color="#00ff64" style={{marginRight: '8px', verticalAlign: 'middle'}}/>
          Jambubrowser
        </h1>
      </div>

      <div className="tabs">
        <button className={activeTab === 'chat' ? 'active' : ''} onClick={() => setActiveTab('chat')}><Zap size={14}/> Research</button>
        <button className={activeTab === 'graph' ? 'active' : ''} onClick={() => setActiveTab('graph')}><BrainCircuit size={14}/> Intelligence</button>
        <button className={activeTab === 'workspace' ? 'active' : ''} onClick={() => setActiveTab('workspace')}><Box size={14}/> Workspace</button>
        <button className={activeTab === 'privacy' ? 'active' : ''} onClick={() => setActiveTab('privacy')}><Shield size={14}/> Privacy</button>
        <button className={activeTab === 'audit' ? 'active' : ''} onClick={() => setActiveTab('audit')}><FileText size={14}/> Audit</button>
        <button className={activeTab === 'vault' ? 'active' : ''} onClick={() => setActiveTab('vault')}><KeyRound size={14}/> Vault</button>
        <button className={activeTab === 'memory' ? 'active' : ''} onClick={() => setActiveTab('memory')} title="User memory (Cmd+M)"><Brain size={14}/> Memory</button>
      </div>

      <div className="header-actions">
        <button onClick={() => setShowHistory(!showHistory)}><History size={16}/> {showHistory ? 'Hide' : 'History'}</button>
        {fullPower && <span className="god-mode-active">AGENT MODE</span>}
        <label className="full-power-toggle">
          <input type="checkbox" checked={fullPower} onChange={(e) => setFullPower(e.target.checked)} />
          <span>🤖 AGENT</span>
        </label>
      </div>
    </header>
  );
};
