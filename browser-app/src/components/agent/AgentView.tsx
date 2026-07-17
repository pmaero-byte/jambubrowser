/**
 * AgentView — composes AgentRoom + TelemetryPanel + InterruptionInput.
 *
 * This is what gets mounted in the inspector when the user activates the
 * "agent" tab. It reads from the same `useAgentWebSocket` hook used by
 * StatusBar, so every panel in the app stays synchronized.
 *
 * Wiring for the interruption endpoint lives here (single place that knows
 * about POST /interrupt/{task_id}). The rest of the app just consumes the
 * `useAgentWebSocket` stream.
 */

import { useCallback } from "react";
import { useAgentWebSocket } from "../../utils/useAgentWebSocket";
import { AgentRoom } from "./AgentRoom";
import { TelemetryPanel } from "./TelemetryPanel";
import { InterruptionInput } from "./InterruptionInput";
import { localFetch } from "../../utils/api";

interface AgentViewProps {
  className?: string;
}

export function AgentView({ className }: AgentViewProps) {
  const {
    agentState,
    telemetry,
    reasoning,
    currentTask,
    lastTaskEnd,
  } = useAgentWebSocket();

  // The WS handler clears currentTask when an agent.task_end arrives,
  // so a present currentTask is the source of truth for "task running".
  const taskIsActive = currentTask !== null;
  void lastTaskEnd;

  const handleInterrupt = useCallback(
    (instruction: string) => {
      if (!currentTask) return;
      const taskId = currentTask.task_id;
      // Fire-and-forget: the WS handler will broadcast task_end("interrupted")
      // and the backend will start a new task with the new instruction.
      localFetch(`/interrupt/${encodeURIComponent(taskId)}`, {
        method: "POST",
        body: JSON.stringify({
          new_instruction: instruction,
          client_id: "default",
        }),
      }).catch(() => {
        /* network errors are non-fatal here; the WS state will reveal truth */
      });
    },
    [currentTask]
  );

  return (
    <div className={"flex h-full flex-col overflow-hidden " + (className || "")}>
      <div className="flex h-12 items-center justify-between border-b border-border px-3">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold">Agent Room</span>
          <span className="rounded-full bg-accent/15 px-1.5 py-0.5 text-[10px] font-medium text-accent ring-1 ring-accent/30">
            live
          </span>
        </div>
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {agentState?.state ?? "idle"}
        </span>
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-0 lg:flex-row">
        {/* Pixel-art room — top half on small screens, left half on lg+ */}
        <div className="h-[260px] shrink-0 border-b border-border lg:h-auto lg:w-2/3 lg:border-b-0 lg:border-r">
          <AgentRoom
            agentState={agentState}
            taskActive={taskIsActive}
            onInterrupt={handleInterrupt}
          />
        </div>
        {/* Telemetry — bottom on small screens, right on lg+ */}
        <div className="min-h-0 flex-1 lg:w-1/3">
          <TelemetryPanel
            model={telemetry?.model ?? "—"}
            tokensPerSec={telemetry?.tokens_per_sec ?? lastTaskEnd?.tokens_per_sec}
            currentAction={telemetry?.action ?? "Standing by"}
            reasoningTrace={reasoning}
            fileBreadcrumb={telemetry?.file_path}
            contextSize={telemetry?.context_size}
            tokensGenerated={telemetry?.tokens_generated ?? lastTaskEnd?.tokens_generated}
            taskActive={taskIsActive}
          />
        </div>
      </div>

      <InterruptionInput
        visible={taskIsActive}
        taskId={currentTask?.task_id}
        onSubmit={handleInterrupt}
        onCancel={() => {
          /* keep visible — user may still want to redirect */
        }}
      />
    </div>
  );
}
