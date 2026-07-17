/**
 * AgentView — composes AgentRoom + TelemetryPanel + InterruptionInput.
 *
 * v2 additions (per .omo/plans/agent-visualization.md §8 "Out of scope"):
 *   - Multi-agent room: renders up to N robots, one per active task.
 *   - Sound effects: plays a chime on task_end("completed") and a ding on
 *     task_end("failed"), respecting the roomLayoutStore.soundEnabled flag.
 *   - Drag-and-drop: the room accepts file drops and ingests them via
 *     /knowledge/ingest (text only).
 *   - Hover preview: hovering a file on the file pile shows the first
 *     ~80 chars as a tooltip.
 *   - Layout prefs: read from roomLayoutStore so the user can move zones.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useAgentWebSocket } from "../../utils/useAgentWebSocket";
import { AgentRoom, type MultiRobot } from "./AgentRoom";
import { TelemetryPanel } from "./TelemetryPanel";
import { InterruptionInput } from "./InterruptionInput";
import { localFetch } from "../../utils/api";
import { useRoomLayoutStore } from "../../store/roomLayoutStore";
import {
  playTaskCompleteChime,
  playTaskFailedDing,
  playTypingTick,
} from "../../utils/soundManager";

interface AgentViewProps {
  className?: string;
}

export interface DroppedFile {
  id: string;
  name: string;
  size: number;
  preview: string;
  ingested: boolean;
  error?: string;
}

export function AgentView({ className }: AgentViewProps) {
  const {
    agentState,
    telemetry,
    reasoning,
    activeTasks,
    liveTasks,
    lastTaskEnd,
  } = useAgentWebSocket();
  const soundEnabled = useRoomLayoutStore((s) => s.soundEnabled);
  const toggleSound = useRoomLayoutStore((s) => s.toggleSound);

  const [droppedFiles, setDroppedFiles] = useState<DroppedFile[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [hoveredFile, setHoveredFile] = useState<string | null>(null);
  const lastReasoningTimestampRef = useRef<number>(0);
  const lastTaskEndRef = useRef<string | null>(null);

  useEffect(() => {
    if (!lastTaskEnd) return;
    const sig = lastTaskEnd.task_id + lastTaskEnd.timestamp;
    if (lastTaskEndRef.current === sig) return;
    lastTaskEndRef.current = sig;
    if (lastTaskEnd.status === "completed") playTaskCompleteChime(soundEnabled);
    else if (lastTaskEnd.status === "failed") playTaskFailedDing(soundEnabled);
  }, [lastTaskEnd, soundEnabled]);

  useEffect(() => {
    const last = telemetry?.timestamp ?? 0;
    if (last > lastReasoningTimestampRef.current) {
      lastReasoningTimestampRef.current = last;
      playTypingTick(soundEnabled);
    }
  }, [telemetry, soundEnabled]);

  const handleInterrupt = useCallback(
    (instruction: string) => {
      const primary = activeTasks[0];
      if (!primary) return;
      const taskId = primary.start.task_id;
      localFetch(`/interrupt/${encodeURIComponent(taskId)}`, {
        method: "POST",
        body: JSON.stringify({
          new_instruction: instruction,
          client_id: "default",
        }),
      }).catch(() => {
        /* WS stream is the source of truth */
      });
    },
    [activeTasks]
  );

  const handleFiles = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const fileArr = Array.from(files);
    const initial: DroppedFile[] = fileArr.map((file) => ({
      id: `${file.name}-${file.size}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      name: file.name,
      size: file.size,
      preview: "",
      ingested: false,
    }));
    setDroppedFiles((prev) => [...prev, ...initial]);

    for (let i = 0; i < fileArr.length; i++) {
      const f = fileArr[i];
      const meta = initial[i];
      const text = await f.text().catch(() => "");
      if (!text) {
        setDroppedFiles((prev) =>
          prev.map((d) => (d.id === meta.id ? { ...d, error: "empty file" } : d)),
        );
        continue;
      }
      setDroppedFiles((prev) =>
        prev.map((d) =>
          d.id === meta.id ? { ...d, preview: text.slice(0, 80) } : d,
        ),
      );
      try {
        await localFetch("/knowledge/ingest", {
          method: "POST",
          body: JSON.stringify({ text, source: f.name }),
        });
        setDroppedFiles((prev) =>
          prev.map((d) => (d.id === meta.id ? { ...d, ingested: true } : d)),
        );
      } catch (e) {
        setDroppedFiles((prev) =>
          prev.map((d) =>
            d.id === meta.id ? { ...d, error: (e as Error).message } : d,
          ),
        );
      }
    }
  }, []);

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  }, []);
  const onDragLeave = useCallback(() => setDragOver(false), []);
  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      void handleFiles(e.dataTransfer?.files ?? null);
    },
    [handleFiles],
  );

  const taskActive = activeTasks.length > 0;

  const multiRobots: MultiRobot[] = activeTasks.slice(0, 4).map((task, i) => {
    const hasFile = !!telemetry?.file_path && telemetry.task_id === task.start.task_id;
    const baseZone = hasFile ? "pile" : "center";
    void liveTasks; // referenced via hook only
    return {
      taskId: task.start.task_id,
      query: task.start.query,
      baseZone,
      offsetIndex: i,
    };
  });

  return (
    <div
      className={"flex h-full flex-col overflow-hidden " + (className || "")}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      <div className="flex h-12 items-center justify-between border-b border-border px-3">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold">Agent Room</span>
          <span className="rounded-full bg-accent/15 px-1.5 py-0.5 text-[10px] font-medium text-accent ring-1 ring-accent/30">
            live
          </span>
          {multiRobots.length > 1 && (
            <span className="rounded-full bg-purple-500/20 px-1.5 py-0.5 text-[10px] font-medium text-purple-300 ring-1 ring-purple-500/30">
              {multiRobots.length} agents
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 text-[10px]">
          <button
            onClick={toggleSound}
            title={soundEnabled ? "Mute sounds" : "Unmute sounds"}
            className="rounded p-1 text-muted-foreground transition-colors hover:bg-muted/40 hover:text-foreground"
          >
            {soundEnabled ? "🔊" : "🔇"}
          </button>
          <span className="uppercase tracking-wider text-muted-foreground">
            {agentState?.state ?? "idle"}
          </span>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-0 lg:flex-row">
        <div className="relative h-[260px] shrink-0 border-b border-border lg:h-auto lg:w-2/3 lg:border-b-0 lg:border-r">
          <AgentRoom
            agentState={agentState}
            taskActive={taskActive}
            onInterrupt={handleInterrupt}
            multiRobots={multiRobots}
            droppedFiles={droppedFiles}
            hoveredFile={hoveredFile}
            onHoverFile={setHoveredFile}
          />
          {dragOver && (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-accent/15 backdrop-blur-sm">
              <div className="rounded-lg border-2 border-dashed border-accent bg-card/80 px-4 py-3 text-sm font-medium text-accent">
                Drop files to feed the agent
              </div>
            </div>
          )}
        </div>
        <div className="min-h-0 flex-1 lg:w-1/3">
          <TelemetryPanel
            model={telemetry?.model ?? "—"}
            tokensPerSec={telemetry?.tokens_per_sec ?? lastTaskEnd?.tokens_per_sec}
            currentAction={telemetry?.action ?? "Standing by"}
            reasoningTrace={reasoning}
            fileBreadcrumb={telemetry?.file_path}
            contextSize={telemetry?.context_size}
            tokensGenerated={telemetry?.tokens_generated ?? lastTaskEnd?.tokens_generated}
            taskActive={taskActive}
          />
        </div>
      </div>

      <InterruptionInput
        visible={taskActive}
        taskId={activeTasks[0]?.start.task_id}
        onSubmit={handleInterrupt}
        onCancel={() => {
          /* keep visible — user may still want to redirect */
        }}
      />
    </div>
  );
}
