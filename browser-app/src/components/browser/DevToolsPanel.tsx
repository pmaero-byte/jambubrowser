import { motion, AnimatePresence } from "motion/react";
import {
  Activity,
  Wifi,
  Terminal,
  BugOff,
  PanelBottomClose,
} from "lucide-react";
import { useDevtoolsStore } from "../../store/devtoolsStore";
import { NetworkTab } from "./NetworkTab";
import { ConsoleTab } from "./ConsoleTab";
import { PerformanceTab } from "./PerformanceTab";

const TABS = [
  { key: "network" as const, label: "Network", icon: Wifi },
  { key: "console" as const, label: "Console", icon: Terminal },
  { key: "performance" as const, label: "Performance", icon: Activity },
];

export function DevToolsPanel() {
  const {
    devtoolsOpen,
    activeTab,
    setActiveTab,
    setDevtoolsOpen,
    resources,
    consoleEntries,
  } = useDevtoolsStore();

  return (
    <AnimatePresence>
      {devtoolsOpen && (
        <motion.div
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: 240, opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={{ duration: 0.2, ease: "easeInOut" }}
          className="flex flex-col overflow-hidden border-t border-border bg-card"
        >
          {/* Tab bar */}
          <div className="flex items-center border-b border-border bg-card/50">
            <div className="flex items-center gap-0.5 px-1">
              {TABS.map((tab) => (
                <button
                  key={tab.key}
                  onClick={() => setActiveTab(tab.key)}
                  className={`flex items-center gap-1.5 rounded-t px-3 py-1.5 text-[11px] font-medium transition-colors ${
                    activeTab === tab.key
                      ? "border-b-2 border-accent bg-card text-foreground"
                      : "text-muted-foreground hover:bg-muted/50"
                  }`}
                >
                  <tab.icon size={12} />
                  {tab.label}
                  {tab.key === "network" && resources.length > 0 && (
                    <span className="rounded bg-muted px-1 text-[10px] text-muted-foreground">
                      {resources.length}
                    </span>
                  )}
                  {tab.key === "console" && consoleEntries.length > 0 && (
                    <span className="rounded bg-muted px-1 text-[10px] text-muted-foreground">
                      {consoleEntries.length}
                    </span>
                  )}
                </button>
              ))}
            </div>
            <div className="flex-1" />
            <div className="flex items-center gap-1 pr-2">
              <button
                onClick={() => {
                  useDevtoolsStore.getState().clearAll();
                }}
                className="rounded px-1.5 py-0.5 text-[11px] text-muted-foreground transition-colors hover:bg-muted"
                title="Clear all"
              >
                <BugOff size={12} />
              </button>
              <button
                onClick={() => setDevtoolsOpen(false)}
                className="rounded p-0.5 text-muted-foreground transition-colors hover:bg-muted"
                title="Close DevTools"
              >
                <PanelBottomClose size={14} />
              </button>
            </div>
          </div>

          {/* Active tab content */}
          <div className="flex-1 overflow-hidden">
            <AnimatePresence mode="wait">
              <motion.div
                key={activeTab}
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.15 }}
                className="h-full"
              >
                {activeTab === "network" && <NetworkTab />}
                {activeTab === "console" && <ConsoleTab />}
                {activeTab === "performance" && <PerformanceTab />}
              </motion.div>
            </AnimatePresence>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
