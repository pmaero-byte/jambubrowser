import { useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { PanelRightClose, FileText, Brain, Activity } from "lucide-react";
import { useAppStore } from "../../store/appStore";
import { KnowledgeMini } from "../knowledge/KnowledgeMini";

export function InspectorPanel() {
  const { activeTab, toggleInspector, messages } = useAppStore();
  const [selectedNode, setSelectedNode] = useState<string | null>(null);

  const lastMessage = messages[messages.length - 1];
  const selectedSources = lastMessage?.sources || [];

  // Stagger the three section reveals on mount; the key is the activeTab so
  // the panel feels "fresh" when the user switches canvas.
  const sections = [
    { key: "knowledge", icon: Brain, label: "Knowledge Graph" },
    { key: "sources", icon: FileText, label: "Sources" },
    { key: "context", icon: Activity, label: "Context" },
  ] as const;

  return (
    <div className="flex h-full flex-col border-l border-border bg-card">
      <div className="flex h-12 items-center justify-between border-b border-border px-3">
        <span className="text-sm font-semibold">Inspector</span>
        <button
          onClick={toggleInspector}
          className="rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          title="Close inspector"
        >
          <PanelRightClose size={16} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3">
        <AnimatePresence initial={true}>
          {sections.map((section, idx) => (
            <motion.section
              key={`${activeTab}-${section.key}`}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.22, delay: idx * 0.06 }}
              className="mb-4"
            >
              <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                <section.icon size={12} /> {section.label}
              </div>

              {section.key === "knowledge" && (
                <>
                  <KnowledgeMini onSelectNode={setSelectedNode} />
                  <AnimatePresence>
                    {selectedNode && (
                      <motion.div
                        key="selected-pill"
                        initial={{ opacity: 0, x: -6, height: 0 }}
                        animate={{ opacity: 1, x: 0, height: "auto" }}
                        exit={{ opacity: 0, x: -6, height: 0 }}
                        transition={{ duration: 0.18 }}
                        className="overflow-hidden"
                      >
                        <div className="mt-2 rounded-md bg-muted px-2 py-1 text-xs">
                          Selected: <span className="text-accent">{selectedNode}</span>
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </>
              )}

              {section.key === "sources" && (
                selectedSources.length > 0 ? (
                  <ul className="space-y-1">
                    {selectedSources.map((url, i) => (
                      <motion.li
                        key={i}
                        initial={{ opacity: 0, x: -4 }}
                        animate={{ opacity: 1, x: 0 }}
                        transition={{ duration: 0.18, delay: i * 0.04 }}
                        className="break-all text-xs text-muted-foreground"
                      >
                        <motion.a
                          href={url}
                          target="_blank"
                          rel="noreferrer"
                          whileHover={{ x: 2, color: "var(--accent)" }}
                          className="block transition-colors"
                        >
                          {url}
                        </motion.a>
                      </motion.li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-xs text-muted-foreground">No sources selected.</p>
                )
              )}

              {section.key === "context" && (
                <div className="rounded-md border border-border bg-card p-2 text-xs">
                  <p className="text-muted-foreground">Active tab: {activeTab}</p>
                  <p className="mt-1 text-muted-foreground">Messages: {messages.length}</p>
                </div>
              )}
            </motion.section>
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}
