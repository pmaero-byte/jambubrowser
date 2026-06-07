import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Wrench, Plus, Download, Sparkles, Terminal, Globe, Bot } from "lucide-react";

export interface Tool {
  name: string;
  description: string;
  created: number;
  category?: "network" | "compute" | "agent" | "general";
}

interface ToolboxViewProps {
  tools: Tool[];
  remoteTools: { name: string; description: string; peer: string }[];
  onPullRemote: (name: string, peer: string) => void;
  onCreateSample: () => void;
}

const CATEGORY_ICON: Record<string, typeof Wrench> = {
  network: Globe,
  compute: Terminal,
  agent: Bot,
  general: Wrench,
};

const CATEGORY_COLOR: Record<string, string> = {
  network: "#41a6f6",
  compute: "#ffcd75",
  agent: "#a7f070",
  general: "#94b0c2",
};

export const ToolboxView = ({ tools, remoteTools, onPullRemote, onCreateSample }: ToolboxViewProps) => {
  const [creating, setCreating] = useState(false);
  const createTimer = useRef<number | null>(null);

  useEffect(() => () => {
    if (createTimer.current) window.clearTimeout(createTimer.current);
  }, []);

  const handleCreate = async () => {
    setCreating(true);
    onCreateSample();
    if (createTimer.current) window.clearTimeout(createTimer.current);
    createTimer.current = window.setTimeout(() => setCreating(false), 1200);
  };

  return (
    <div className="toolbox-view">
      <div className="toolbox-header">
        <div className="toolbox-title">
          <motion.div
            className="toolbox-icon"
            animate={creating ? { rotate: [0, -15, 15, -10, 10, 0] } : { rotate: 0 }}
            transition={{ duration: 0.8, ease: "easeInOut" }}
          >
            <Wrench size={20} />
          </motion.div>
          <div>
            <h2>Agent Skills</h2>
            <p>{tools.length} installed · {remoteTools.length} available on the network</p>
          </div>
        </div>
        <button className="tool-install-btn" onClick={handleCreate} disabled={creating}>
          {creating ? (
            <>
              <motion.span animate={{ rotate: 360 }} transition={{ duration: 1, repeat: Infinity, ease: "linear" }}>
                <Sparkles size={14} />
              </motion.span>
              Installing…
            </>
          ) : (
            <>
              <Plus size={14} /> Install Sample Tool
            </>
          )}
        </button>
      </div>

      <div className="tools-grid">
        <AnimatePresence mode="popLayout">
          {tools.length === 0 ? (
            <motion.div
              key="empty"
              className="tool-empty"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
            >
              <motion.div
                className="tool-empty-icon"
                animate={{ y: [0, -8, 0] }}
                transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
              >
                <Wrench size={36} />
              </motion.div>
              <h3>Your workshop is empty</h3>
              <p>Install skills that the agent can invoke at runtime — network probes, code sandboxes, vision routines, anything Python.</p>
              <button className="tool-empty-cta" onClick={handleCreate}>
                <Sparkles size={14} /> Install your first tool
              </button>
            </motion.div>
          ) : (
            tools.map((t, i) => {
              const Cat = CATEGORY_ICON[t.category || "general"];
              const color = CATEGORY_COLOR[t.category || "general"];
              return (
                <motion.div
                  layout
                  key={t.name}
                  className="tool-card glass"
                  initial={{ opacity: 0, scale: 0.9, y: 20 }}
                  animate={{ opacity: 1, scale: 1, y: 0 }}
                  exit={{ opacity: 0, scale: 0.9 }}
                  transition={{ delay: i * 0.05 }}
                  whileHover={{ y: -4, scale: 1.02 }}
                >
                  <div className="tool-card-top" style={{ "--cat": color } as React.CSSProperties}>
                    <div className="tool-card-icon">
                      <Cat size={16} />
                    </div>
                    <div className="tool-card-name">{t.name}</div>
                    <motion.div
                      className="tool-card-pulse"
                      animate={{ scale: [1, 1.4, 1], opacity: [0.5, 0, 0.5] }}
                      transition={{ duration: 2, repeat: Infinity, delay: i * 0.2 }}
                    />
                  </div>
                  <div className="tool-card-desc">{t.description}</div>
                  <div className="tool-card-foot">
                    <span className="tool-card-time">
                      {Math.round((Date.now() - t.created) / 1000)}s ago
                    </span>
                    <button className="tool-card-run">
                      <Terminal size={11} /> Run
                    </button>
                  </div>
                </motion.div>
              );
            })
          )}
        </AnimatePresence>
      </div>

      {remoteTools.length > 0 && (
        <motion.div
          className="remote-tools"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.2 }}
        >
          <h3><Globe size={14} /> Shared network skills</h3>
          <div className="tools-grid">
            {remoteTools.map((rt, i) => (
              <motion.div
                key={i}
                className="tool-card glass remote"
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.3 + i * 0.05 }}
                whileHover={{ y: -4 }}
              >
                <div className="tool-card-top" style={{ "--cat": "#41a6f6" } as React.CSSProperties}>
                  <div className="tool-card-icon">
                    <Globe size={16} />
                  </div>
                  <div className="tool-card-name">{rt.name}</div>
                </div>
                <div className="tool-card-desc">{rt.description}</div>
                <div className="tool-card-foot">
                  <span className="tool-card-peer">from {rt.peer}</span>
                  <button className="tool-card-run" onClick={() => onPullRemote(rt.name, rt.peer)}>
                    <Download size={11} /> Pull
                  </button>
                </div>
              </motion.div>
            ))}
          </div>
        </motion.div>
      )}
    </div>
  );
};
