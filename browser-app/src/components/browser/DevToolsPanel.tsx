import { motion, AnimatePresence } from "motion/react";
import {
  Activity,
  Wifi,
  Terminal,
  BugOff,
  PanelBottomClose,
  FileCode,
  Database,
} from "lucide-react";
import { useDevtoolsStore } from "../../store/devtoolsStore";
import { NetworkTab } from "./NetworkTab";
import { ConsoleTab } from "./ConsoleTab";
import { PerformanceTab } from "./PerformanceTab";
import { ElementsTab } from "./ElementsTab";
import { StorageTab } from "./StorageTab";

const TABS = [
  { key: "elements" as const, label: "Elements", icon: FileCode },
  { key: "network" as const, label: "Network", icon: Wifi },
  { key: "console" as const, label: "Console", icon: Terminal },
  { key: "performance" as const, label: "Performance", icon: Activity },
  { key: "storage" as const, label: "Storage", icon: Database },
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
          transition={{ duration: 0.25, ease: [0.23, 1, 0.32, 1] }}
          className="flex flex-col overflow-hidden border-t border-border/50 bg-surface"
        >
          {/* Tab bar */}
          <div className="flex items-center border-b border-border/30 bg-surface/80">
            <div className="flex items-center gap-0.5 px-1">
              {TABS.map((tab) => (
                <button
                  key={tab.key}
                  onClick={() => setActiveTab(tab.key)}
                  className={`relative flex items-center gap-1.5 rounded-t px-3 py-1.5 text-[11px] font-medium transition-all duration-200 ${
                    activeTab === tab.key
                      ? "text-foreground bg-surface-elevated"
                      : "text-muted-foreground/60 hover:text-muted-foreground hover:bg-muted/30"
                  }`}
                >
                  <tab.icon size={12} className={activeTab === tab.key ? "text-accent" : ""} />
                  {tab.label}
                  {tab.key === "network" && resources.length > 0 && (
                    <span className="rounded-full bg-accent/15 px-1.5 text-[10px] text-accent font-medium">
                      {resources.length}
                    </span>
                  )}
                  {tab.key === "console" && consoleEntries.length > 0 && (
                    <span className="rounded-full bg-accent/15 px-1.5 text-[10px] text-accent font-medium">
                      {consoleEntries.length}
                    </span>
                  )}
                  {activeTab === tab.key && (
                    <motion.span
                      layoutId="devtools-tab-indicator"
                      className="absolute bottom-0 left-0 right-0 h-0.5 bg-accent rounded-full"
                      transition={{ type: "spring", stiffness: 400, damping: 30 }}
                    />
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
                className="rounded-md px-2 py-1 text-[11px] text-muted-foreground/60 transition-all duration-150 hover:bg-muted/30 hover:text-foreground"
                title="Clear all"
              >
                <BugOff size={12} />
              </button>
              <button
                onClick={() => setDevtoolsOpen(false)}
                className="rounded-md p-1 text-muted-foreground/60 transition-all duration-150 hover:bg-muted/30 hover:text-foreground"
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
                transition={{ duration: 0.18, ease: "easeOut" }}
                className="h-full"
              >
                {activeTab === "elements" && <ElementsTab />}
                {activeTab === "network" && <NetworkTab />}
                {activeTab === "console" && <ConsoleTab />}
                {activeTab === "performance" && <PerformanceTab />}
                {activeTab === "storage" && <StorageTab />}
              </motion.div>
            </AnimatePresence>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
