import { useState } from "react";
import { MonitorsPanel } from "./MonitorsPanel";
import { FlowMonitorsPanel } from "./FlowMonitorsPanel";

type Tab = "audits" | "flows";

/** Combined recurring-checks view: audit monitors and agent flow tests. */
export function MonitorsView() {
  const [tab, setTab] = useState<Tab>("audits");
  return (
    <div className="flex h-full flex-col">
      <div className="flex gap-1 border-b border-border/50 px-4 pt-2" role="tablist">
        {(["audits", "flows"] as const).map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`rounded-t px-3 py-1.5 text-xs font-medium ${
              tab === t
                ? "bg-surface text-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {t === "audits" ? "Audits" : "Flows"}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1">
        {tab === "audits" ? <MonitorsPanel /> : <FlowMonitorsPanel />}
      </div>
    </div>
  );
}
