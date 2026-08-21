/**
 * AgentRoom — pixel-art visualization of the local LLM agent.
 *
 * Shows a cute 16×16 robot in a tiny NES-style office. Robot moves between
 * zones based on the `agentState.state` and `agentState.zone` from the
 * WebSocket hook, and animates differently per state.
 *
 * Mounted inside the Agent tab in the inspector (alongside the Telemetry
 * panel) for a "what is my AI doing right now" overlay.
 *
 * Props match the spec from .omo/plans/agent-visualization.md:
 *   - agentState  : state + zone from agent.state WS event
 *   - taskActive  : whether an agent.task_start is currently active
 *   - onInterrupt : callback when user submits a redirect instruction
 */

import { useMemo } from "react";
import { motion, AnimatePresence } from "motion/react";
import "./AgentRoom.css";
import {
  renderRobotSprite,
  cellsToSvgRects,
  ROBOT_VIEWBOX,
  type SpriteState,
  type SpriteOptions,
} from "./robot.svg";
import { useRoomLayoutStore } from "../../store/roomLayoutStore";
import type { DroppedFile } from "./AgentView";

export type AgentZone = "desk" | "cabinet" | "pile" | "center" | null;

export interface MultiRobot {
  taskId: string;
  query: string;
  baseZone: Exclude<AgentZone, null>;
  offsetIndex: number;
}

export interface AgentRoomProps {
  agentState: { state: string; zone?: string; task_id?: string; timestamp: number } | null;
  taskActive: boolean;
  onInterrupt?: (instruction: string) => void;
  /** Optional multi-robot list (v2). When present and >1, render multiple. */
  multiRobots?: MultiRobot[];
  /** Dropped files shown as paper stacks near the pile (v2). */
  droppedFiles?: DroppedFile[];
  /** Currently hovered file id (for tooltip). */
  hoveredFile?: string | null;
  onHoverFile?: (id: string | null) => void;
}

// Zone coordinates inside the 320×200 viewBox (defaults — overridden by
// the layout store when the user moves things).
const DEFAULT_ZONES: Record<Exclude<AgentZone, null>, { x: number; y: number }> = {
  desk:    { x: 70,  y: 100 },
  cabinet: { x: 250, y: 120 },
  pile:    { x: 165, y: 175 },
  center:  { x: 160, y: 145 },
};

function stateToSpriteState(state: string): SpriteState {
  switch (state) {
    case "thinking":  return "thinking";
    case "searching": return "searching";
    case "reading":   return "reading";
    case "writing":   return "writing";
    case "walking":   return "walking";
    case "error":     return "error";
    default:          return "idle";
  }
}

function zoneToKey(zone?: string): Exclude<AgentZone, null> {
  if (zone === "desk" || zone === "cabinet" || zone === "pile" || zone === "center") return zone;
  return "center";
}

/** Derive a stable robot zone: prefer explicit zone, else infer from state. */
function resolveZone(state: string, zone?: string): Exclude<AgentZone, null> {
  if (zone && zone !== "reason") return zoneToKey(zone);
  // ReAct plans on the desk, scraping on the pile, reading in the cabinet.
  if (state === "thinking") return "desk";
  if (state === "searching") return "pile";
  if (state === "reading") return "cabinet";
  if (state === "writing") return "desk";
  if (state === "walking") return "center";
  if (state === "error") return "center";
  return "center";
}

export function AgentRoom({
  agentState,
  taskActive,
  multiRobots,
  droppedFiles,
  hoveredFile,
  onHoverFile,
}: AgentRoomProps) {
  const stateStr = agentState?.state ?? "idle";
  const spriteState = stateToSpriteState(stateStr);
  const zone = resolveZone(stateStr, agentState?.zone);
  const layout = useRoomLayoutStore((s) => s.layout);

  const spriteCells = useMemo(
    () => renderRobotSprite({ state: spriteState } satisfies SpriteOptions),
    [spriteState]
  );

  const spriteSvg = useMemo(() => cellsToSvgRects(spriteCells), [spriteCells]);

  return (
    <div className="px-stage relative h-full w-full">
      <svg
        viewBox="0 0 320 200"
        xmlns="http://www.w3.org/2000/svg"
        preserveAspectRatio="xMidYMid meet"
        className="h-full w-full"
      >
        {/* ───── Wall (top half) ───── */}
        <rect x="0" y="0" width="320" height="120" fill="var(--px-bg)" />
        {/* Subtle wall texture lines */}
        <g opacity="0.3">
          {Array.from({ length: 8 }).map((_, i) => (
            <rect key={i} x={i * 40 + 8} y={20} width={1} height={80} fill="var(--px-bg-2)" />
          ))}
        </g>
        {/* Window */}
        <g>
          <rect x={20} y={20} width={48} height={32} fill="var(--px-bg-2)" />
          <rect x={24} y={24} width={40} height={24} fill="var(--px-monitor)" opacity="0.6" />
          <rect x={44} y={20} width={1} height={32} fill="var(--px-wood-2)" />
          <rect x={20} y={36} width={48} height={1} fill="var(--px-wood-2)" />
          {/* Pixel clouds */}
          <rect x={28} y={28} width={6} height={2} fill="var(--px-white)" opacity="0.7" />
          <rect x={42} y={32} width={8} height={2} fill="var(--px-white)" opacity="0.7" />
          <rect x={54} y={26} width={4} height={2} fill="var(--px-white)" opacity="0.7" />
        </g>
        {/* Wall poster */}
        <g>
          <rect x={140} y={28} width={40} height={28} fill="var(--px-paper-2)" />
          <rect x={144} y={32} width={32} height={2} fill="var(--px-black)" opacity="0.5" />
          <rect x={144} y={36} width={20} height={1} fill="var(--px-black)" opacity="0.3" />
          <rect x={144} y={40} width={28} height={1} fill="var(--px-black)" opacity="0.3" />
          <rect x={144} y={44} width={16} height={1} fill="var(--px-black)" opacity="0.3" />
        </g>

        {/* ───── Floor (bottom half) ───── */}
        <rect x="0" y="120" width="320" height="80" fill="var(--px-floor)" />
        {/* Floor grid lines */}
        <g opacity="0.4">
          {Array.from({ length: 4 }).map((_, i) => (
            <rect key={i} x="0" y={130 + i * 20} width="320" height="1" fill="var(--px-floor-2)" />
          ))}
          {Array.from({ length: 8 }).map((_, i) => (
            <rect key={i} x={i * 40} y="120" width="1" height="80" fill="var(--px-floor-2)" />
          ))}
        </g>

        {/* ───── Desk (Zone A) ───── */}
        <g className={zone === "desk" ? "px-zone-active" : undefined}>
          {/* Desk top */}
          <rect x={40} y={130} width={80} height={6} fill="var(--px-wood)" />
          <rect x={40} y={136} width={80} height={2} fill="var(--px-wood-2)" />
          {/* Desk legs */}
          <rect x={44} y={138} width={4} height={30} fill="var(--px-wood-2)" />
          <rect x={112} y={138} width={4} height={30} fill="var(--px-wood-2)" />
          {/* Monitor */}
          <rect x={58} y={108} width={44} height={22} fill="var(--px-black)" />
          <rect x={62} y={112} width={36} height={14} fill="var(--px-monitor)" className="px-monitor-screen" />
          {/* Screen content — moving pixel line */}
          <rect x={64} y={114} width={32} height={1} fill="var(--px-white)" opacity="0.6" />
          <rect x={64} y={118} width={20} height={1} fill="var(--px-white)" opacity="0.4" />
          <rect x={64} y={121} width={26} height={1} fill="var(--px-white)" opacity="0.5" />
          {/* Monitor stand */}
          <rect x={76} y={130} width={8} height={2} fill="var(--px-wood-2)" />
          {/* Keyboard */}
          <rect x={62} y={132} width={36} height={3} fill="var(--px-paper-2)" />
          <rect x={64} y={133} width={3} height={1} fill="var(--px-black)" opacity="0.5" />
          <rect x={70} y={133} width={3} height={1} fill="var(--px-black)" opacity="0.5" />
          <rect x={76} y={133} width={3} height={1} fill="var(--px-black)" opacity="0.5" />
          <rect x={82} y={133} width={3} height={1} fill="var(--px-black)" opacity="0.5" />
          <rect x={88} y={133} width={3} height={1} fill="var(--px-black)" opacity="0.5" />
          {/* Coffee mug */}
          <rect x={106} y={124} width={8} height={6} fill="var(--px-paper-2)" />
          <rect x={107} y={125} width={6} height={4} fill="var(--px-wood-2)" opacity="0.4" />
          {/* Steam */}
          <rect className="px-steam-puff" x={108} y={120} width={2} height={2} style={{ animationDelay: "0s" }} />
          <rect className="px-steam-puff" x={110} y={120} width={2} height={2} style={{ animationDelay: "0.8s" }} />
          <rect className="px-steam-puff" x={109} y={119} width={2} height={2} style={{ animationDelay: "1.6s" }} />
        </g>

        {/* ───── Filing cabinet (Zone B) ───── */}
        <g className={zone === "cabinet" ? "px-zone-active" : undefined}>
          <rect x={210} y={70} width={50} height={80} fill="var(--px-robot-2)" />
          <rect x={210} y={70} width={50} height={4} fill="var(--px-robot)" />
          {/* Three drawers */}
          <rect x={216} y={80} width={38} height={20} fill="var(--px-robot)" />
          <rect x={228} y={88} width={14} height={4} fill="var(--px-accent)" opacity="0.8" />
          <rect x={216} y={104} width={38} height={20} fill="var(--px-robot)" />
          <rect x={228} y={112} width={14} height={4} fill="var(--px-accent)" opacity="0.6" />
          <rect x={216} y={128} width={38} height={20} fill="var(--px-robot)" />
          <rect x={228} y={136} width={14} height={4} fill="var(--px-accent)" opacity="0.6" />
        </g>

        {/* ───── File pile on floor (Zone C) ───── */}
        <g className={zone === "pile" ? "px-zone-active" : undefined}>
          <rect x={130} y={170} width={20} height={4} fill="var(--px-paper)" transform="rotate(-3 140 172)" />
          <rect x={148} y={172} width={18} height={4} fill="var(--px-paper-2)" transform="rotate(2 157 174)" />
          <rect x={166} y={170} width={22} height={4} fill="var(--px-paper)" transform="rotate(-1 177 172)" />
          <rect x={138} y={174} width={20} height={3} fill="var(--px-paper-2)" transform="rotate(4 148 175)" />
          <rect x={158} y={176} width={18} height={3} fill="var(--px-paper)" transform="rotate(-2 167 177)" />
          {/* User-dropped files (v2). Each is a small paper rect on the pile
              with a hover handler that triggers the preview tooltip. */}
          {(droppedFiles ?? []).slice(-6).map((f, i) => {
            const colors = ["var(--px-accent)", "var(--px-paper)", "var(--px-paper-2)", "var(--px-success)"];
            const fill = f.error ? "var(--px-error)" : f.ingested ? colors[i % colors.length] : "var(--px-paper)";
            const x = 142 + (i % 3) * 14;
            const y = 166 + Math.floor(i / 3) * 4;
            return (
              <g
                key={f.id}
                onMouseEnter={() => onHoverFile?.(f.id)}
                onMouseLeave={() => onHoverFile?.(null)}
                style={{ cursor: "help" }}
              >
                <rect
                  x={x}
                  y={y}
                  width={10}
                  height={4}
                  fill={fill}
                  opacity={hoveredFile === f.id ? 1 : 0.85}
                  transform={`rotate(${-5 + i * 3} ${x + 5} ${y + 2})`}
                />
              </g>
            );
          })}
        </g>

        {/* ───── Center stage rug ───── */}
        <g className={zone === "center" ? "px-zone-active" : undefined}>
          <rect x={130} y={130} width={60} height={20} fill="var(--px-accent-2)" opacity="0.6" />
          <rect x={134} y={134} width={52} height={12} fill="var(--px-wood)" opacity="0.5" />
        </g>

        {/* ───── Robots (one or many) ───── */}
        <AnimatePresence mode="popLayout">
          {renderRobotList(
            multiRobots,
            spriteState,
            spriteSvg,
            zone,
            layout,
          )}
        </AnimatePresence>

        {/* ───── Zone labels (subtle, top of each zone) ───── */}
        <text x={50}  y={160} className="px-zone-label" opacity={zone === "desk" ? 1 : 0.4}>DESK</text>
        <text x={222} y={162} className="px-zone-label" opacity={zone === "cabinet" ? 1 : 0.4}>CABINET</text>
        <text x={140} y={190} className="px-zone-label" opacity={zone === "pile" ? 1 : 0.4}>FILE PILE</text>
        <text x={148} y={122} className="px-zone-label" opacity={zone === "center" ? 1 : 0.4}>CENTER</text>
      </svg>

      {/* Status pill overlay */}
      <div className="absolute left-3 top-3 flex items-center gap-2">
        <span className="px-status">
          {taskActive ? "● LIVE" : "○ IDLE"} · {spriteState.toUpperCase()}
          {agentState?.zone ? ` · ${agentState.zone}` : ""}
        </span>
      </div>

      {/* Hover preview tooltip for dropped files */}
      {hoveredFile && droppedFiles && (
        <div className="pointer-events-none absolute bottom-3 left-3 max-w-[260px] rounded-md border border-border bg-card/95 p-2 font-mono text-[10px] shadow-lg backdrop-blur">
          {(() => {
            const f = droppedFiles.find((d) => d.id === hoveredFile);
            if (!f) return null;
            return (
              <>
                <div className="mb-1 font-semibold text-foreground">{f.name}</div>
                <div className="text-muted-foreground">
                  {f.ingested ? "✓ ingested" : f.error ?? "… ingesting"}
                </div>
                {f.preview && (
                  <div className="mt-1 truncate text-foreground/70" title={f.preview}>
                    {f.preview}
                  </div>
                )}
              </>
            );
          })()}
        </div>
      )}
    </div>
  );
}

/**
 * Build the list of robot motion.g nodes. When multiRobots is provided
 * with >1 items, fan them out horizontally around their base zone so they
 * don't overlap. When there's exactly one (or none), behave like the
 * single-robot layout.
 */
function renderRobotList(
  multiRobots: MultiRobot[] | undefined,
  fallbackSpriteState: SpriteState,
  fallbackSvg: string,
  fallbackZone: Exclude<AgentZone, null>,
  layout: { desk: { x: number; y: number }; cabinet: { x: number; y: number }; pile: { x: number; y: number }; center: { x: number; y: number } },
) {
  const robots: MultiRobot[] =
    multiRobots && multiRobots.length > 0
      ? multiRobots
      : [{ taskId: "primary", query: "", baseZone: fallbackZone, offsetIndex: 0 }];

  return robots.map((r, i) => {
    const baseXY = layout[r.baseZone] ?? DEFAULT_ZONES[r.baseZone] ?? DEFAULT_ZONES.center;
    // Fan out: each subsequent robot offset by 40px on x, capped to 4.
    const fanX = (i - Math.min(robots.length, 4) / 2) * 30;
    const x = baseXY.x - 32 + fanX;
    const y = baseXY.y - 36;
    return (
      <motion.g
        key={r.taskId + i}
        initial={{ x: x - 16, y: y + 4, opacity: 0 }}
        animate={{ x, y, opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.6, ease: [0.45, 0, 0.55, 1] }}
        className={`px-robot px-robot--${fallbackSpriteState}`}
      >
        <ellipse cx={32} cy={70} rx={18} ry={2} fill="var(--px-black)" opacity={0.35} className="px-robot-shadow" />
        <g className="px-robot-body">
          {fallbackSpriteState === "writing" && (
            <g>
              <rect className="px-sparkle" x={4}  y={20} width={2} height={2} style={{ animationDelay: "0s" }} />
              <rect className="px-sparkle" x={56} y={18} width={2} height={2} style={{ animationDelay: "0.2s" }} />
              <rect className="px-sparkle" x={30} y={6}  width={2} height={2} style={{ animationDelay: "0.4s" }} />
              <rect className="px-sparkle" x={48} y={28} width={2} height={2} style={{ animationDelay: "0.6s" }} />
            </g>
          )}
          <g className="px-robot-svg">
            <svg
              x={0}
              y={0}
              width={64}
              height={72}
              viewBox={`0 0 ${ROBOT_VIEWBOX.width} ${ROBOT_VIEWBOX.height}`}
              xmlns="http://www.w3.org/2000/svg"
              dangerouslySetInnerHTML={{ __html: fallbackSvg }}
            />
          </g>
          {fallbackSpriteState === "thinking" && (
            <g className="px-robot-eyes">
              <rect x={20} y={28} width={4} height={4} fill="var(--px-accent)" />
              <rect x={36} y={28} width={4} height={4} fill="var(--px-accent)" />
            </g>
          )}
        </g>
      </motion.g>
    );
  });
}
