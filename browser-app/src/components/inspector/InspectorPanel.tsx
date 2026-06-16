import { useState } from "react";
import { PanelRightClose, FileText, Brain, Activity } from "lucide-react";
import { useAppStore } from "../../store/appStore";
import { KnowledgeMini } from "../knowledge/KnowledgeMini";

export function InspectorPanel() {
  const { activeTab, toggleInspector, messages } = useAppStore();
  const [selectedNode, setSelectedNode] = useState<string | null>(null);

  const lastMessage = messages[messages.length - 1];
  const selectedSources = lastMessage?.sources || [];

  return (
    <div className="flex h-full flex-col border-l border-border bg-card">
      <div className="flex h-12 items-center justify-between border-b border-border px-3">
        <span className="text-sm font-semibold">Inspector</span>
        <button
          onClick={toggleInspector}
          className="rounded p-1 text-muted-foreground hover:bg-muted"
          title="Close inspector"
        >
          <PanelRightClose size={16} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3">
        <section className="mb-4">
          <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            <Brain size={12} /> Knowledge Graph
          </div>
          <KnowledgeMini onSelectNode={setSelectedNode} />
          {selectedNode && (
            <div className="mt-2 rounded-md bg-muted px-2 py-1 text-xs">
              Selected: {selectedNode}
            </div>
          )}
        </section>

        <section className="mb-4">
          <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            <FileText size={12} /> Sources
          </div>
          {selectedSources.length > 0 ? (
            <ul className="space-y-1">
              {selectedSources.map((url, i) => (
                <li key={i} className="break-all text-xs text-muted-foreground">
                  <a href={url} target="_blank" rel="noreferrer" className="hover:text-accent">
                    {url}
                  </a>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-muted-foreground">No sources selected.</p>
          )}
        </section>

        <section>
          <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            <Activity size={12} /> Context
          </div>
          <div className="rounded-md border border-border bg-card p-2 text-xs">
            <p className="text-muted-foreground">Active tab: {activeTab}</p>
            <p className="mt-1 text-muted-foreground">Messages: {messages.length}</p>
          </div>
        </section>
      </div>
    </div>
  );
}
