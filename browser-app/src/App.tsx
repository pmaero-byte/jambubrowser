import { useCallback, useState, useRef, Suspense, lazy } from "react";
import { AppShell } from "./components/layout/AppShell";
import { ChatPane } from "./components/chat/ChatPane";
import { useAppStore } from "./store/appStore";
import { useAgentWebSocket } from "./utils/useAgentWebSocket";
import { runAgentStream } from "./utils/agent";
import { localFetch } from "./utils/api";
import type { AgentEvent } from "./utils/types";

// Lazy-loaded panels: each is its own JS chunk and only fetched on first
// navigation. The eager ChatPane stays in the main bundle because it's the
// default landing tab and we want the chat ready immediately.
const BrowserPane = lazy(() =>
  import("./components/browser/BrowserPane").then((m) => ({ default: m.BrowserPane }))
);
const PrivacyControls = lazy(() =>
  import("./components/privacy/PrivacyControls").then((m) => ({ default: m.PrivacyControls }))
);
const AuditLogViewer = lazy(() =>
  import("./components/audit/AuditLogViewer").then((m) => ({ default: m.AuditLogViewer }))
);
const VaultUnlock = lazy(() =>
  import("./components/vault/VaultUnlock").then((m) => ({ default: m.VaultUnlock }))
);
const MemoryPanel = lazy(() =>
  import("./components/memory/MemoryPanel").then((m) => ({ default: m.MemoryPanel }))
);
const InspectorPanel = lazy(() =>
  import("./components/inspector/InspectorPanel").then((m) => ({ default: m.InspectorPanel }))
);
const CommandPalette = lazy(() =>
  import("./components/command/CommandPalette").then((m) => ({ default: m.CommandPalette }))
);
const OnboardingWizard = lazy(() =>
  import("./components/onboarding/OnboardingWizard").then((m) => ({ default: m.OnboardingWizard }))
);
const MissionsPanel = lazy(() =>
  import("./components/missions/MissionsPanel").then((m) => ({ default: m.MissionsPanel }))
);
const HistoryPanel = lazy(() =>
  import("./components/history/HistoryPanel").then((m) => ({ default: m.HistoryPanel }))
);
const SettingsPanel = lazy(() =>
  import("./components/settings/SettingsPanel").then((m) => ({ default: m.SettingsPanel }))
);
const AuditPanel = lazy(() =>
  import("./components/audit/AuditPanel").then((m) => ({ default: m.AuditPanel }))
);
const TeamPanel = lazy(() =>
  import("./components/team/TeamPanel").then((m) => ({ default: m.TeamPanel }))
);

const USER_ID = "default";

function PanelFallback() {
  return (
    <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
      Loading…
    </div>
  );
}

export default function App() {
  const {
    activeTab,
    addMessage,
    updateLastMessage,
    setIsLoading,
    activeModel,
    onboardingOpen,
    setOnboardingOpen,
  } = useAppStore();

  const { clearReasoning } = useAgentWebSocket();
  const [agentEvents, setAgentEvents] = useState<AgentEvent[]>([]);
  const abortRef = useRef<AbortController | null>(null);

  const handleSend = useCallback(
    async (text: string) => {
      if (!text.trim()) return;
      setIsLoading(true);
      setAgentEvents([]);
      clearReasoning();
      addMessage({
        id: crypto.randomUUID(),
        role: "user",
        content: text,
      });
      addMessage({
        id: crypto.randomUUID(),
        role: "assistant",
        content: "",
      });

      try {
        if (activeModel === "legacy") {
          const ac = new AbortController();
          abortRef.current = ac;
          const res = await localFetch("/research", {
            method: "POST",
            body: JSON.stringify({
              query: text,
              brain_only: true,
            }),
            signal: ac.signal,
          });
          const data = await res.json();
          updateLastMessage({
            content: data.answer || "",
            sources: data.sources || [],
          });
        } else {
          let answer = "";
          let sources: string[] = [];
          let lastRunCompleted: AgentEvent | null = null;

          for await (const ev of runAgentStream({
            query: text,
            user_id: USER_ID,
            max_steps: 10,
          })) {
            if (abortRef.current?.signal.aborted) break;
            setAgentEvents((prev) => [...prev, ev]);
            if (ev.type === "answer_ready") {
              answer = ev.data.answer || "";
              sources = ev.data.sources || [];
            }
            if (ev.type === "run_completed") {
              lastRunCompleted = ev;
            }
          }

          updateLastMessage({
            content: answer,
            sources,
            agentRun: lastRunCompleted?.data,
          });
        }
      } catch (err: any) {
        console.error(err);
        const msg =
          err?.name === "AbortError"
            ? "Research timed out."
            : "Sorry, I encountered an error processing your request.";
        updateLastMessage({ content: msg });
      } finally {
        setIsLoading(false);
      }
    },
    [activeModel, addMessage, updateLastMessage, setIsLoading, clearReasoning]
  );

  const handleStop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setIsLoading(false);
  }, [setIsLoading]);

  const renderCanvas = () => {
    switch (activeTab) {
      case "chat":
      case "plan":
        return <ChatPane agentEvents={agentEvents} onSend={handleSend} onStop={handleStop} />;
      case "browser":
        return <BrowserPane />;
      case "logs":
        return <AuditLogViewer />;
      case "memory":
      case "knowledge":
        return <MemoryPanel />;
      case "missions":
        return <MissionsPanel />;
      case "history":
        return <HistoryPanel />;
      case "privacy":
        return <PrivacyControls />;
      case "audit":
        return <AuditPanel />;
      case "vault":
        return <VaultUnlock />;
      case "team":
        return <TeamPanel />;
      case "settings":
        return <SettingsPanel />;
      default:
        return null;
    }
  };

  return (
    <>
      <AppShell
        inspector={
          <Suspense fallback={<PanelFallback />}>
            <InspectorPanel />
          </Suspense>
        }
      >
        <div className="flex h-full flex-col overflow-hidden">
          <Suspense fallback={<PanelFallback />}>{renderCanvas()}</Suspense>
        </div>
      </AppShell>
      <Suspense fallback={null}>
        <CommandPalette />
      </Suspense>
      <Suspense fallback={null}>
        <OnboardingWizard
          forceOpen={onboardingOpen}
          onClose={() => setOnboardingOpen(false)}
        />
      </Suspense>
    </>
  );
}
