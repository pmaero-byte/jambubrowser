import type { CSSProperties } from "react";

export type RobotState =
  | "idle"
  | "thinking"
  | "searching"
  | "reading"
  | "writing"
  | "walking"
  | "error";

const C = {
  bg: "transparent",
  body: "#94b0c2",
  shadow: "#566c86",
  accent: "#ffcd75",
  accentDim: "#b8964a",
  screen: "#41a6f6",
  screenDim: "#2a5a8a",
  white: "#f4f4f4",
  black: "#0a0a14",
  error: "#b13e53",
} as const;

type Pixel = { x: number; y: number; color: string };
const P = 4;

const body: Pixel[] = [
  { x: 5, y: 3, color: C.body }, { x: 6, y: 3, color: C.body },
  { x: 7, y: 3, color: C.body }, { x: 8, y: 3, color: C.body },
  { x: 9, y: 3, color: C.body }, { x: 10, y: 3, color: C.body },
  { x: 4, y: 4, color: C.body }, { x: 5, y: 4, color: C.body },
  { x: 6, y: 4, color: C.body }, { x: 7, y: 4, color: C.body },
  { x: 8, y: 4, color: C.body }, { x: 9, y: 4, color: C.body },
  { x: 10, y: 4, color: C.body }, { x: 11, y: 4, color: C.body },
  { x: 3, y: 5, color: C.body }, { x: 4, y: 5, color: C.body },
  { x: 5, y: 5, color: C.body }, { x: 6, y: 5, color: C.body },
  { x: 7, y: 5, color: C.body }, { x: 8, y: 5, color: C.body },
  { x: 9, y: 5, color: C.body }, { x: 10, y: 5, color: C.body },
  { x: 11, y: 5, color: C.body }, { x: 12, y: 5, color: C.body },
  { x: 3, y: 6, color: C.body }, { x: 4, y: 6, color: C.body },
  { x: 5, y: 6, color: C.body }, { x: 6, y: 6, color: C.body },
  { x: 7, y: 6, color: C.body }, { x: 8, y: 6, color: C.body },
  { x: 9, y: 6, color: C.body }, { x: 10, y: 6, color: C.body },
  { x: 11, y: 6, color: C.body }, { x: 12, y: 6, color: C.body },
  { x: 4, y: 7, color: C.body }, { x: 5, y: 7, color: C.body },
  { x: 6, y: 7, color: C.body }, { x: 7, y: 7, color: C.body },
  { x: 8, y: 7, color: C.body }, { x: 9, y: 7, color: C.body },
  { x: 10, y: 7, color: C.body }, { x: 11, y: 7, color: C.body },
  { x: 4, y: 8, color: C.shadow }, { x: 5, y: 8, color: C.body },
  { x: 6, y: 8, color: C.body }, { x: 7, y: 8, color: C.body },
  { x: 8, y: 8, color: C.body }, { x: 9, y: 8, color: C.body },
  { x: 10, y: 8, color: C.body }, { x: 11, y: 8, color: C.body },
  { x: 5, y: 9, color: C.shadow }, { x: 6, y: 9, color: C.shadow },
  { x: 7, y: 9, color: C.shadow }, { x: 8, y: 9, color: C.shadow },
  { x: 9, y: 9, color: C.shadow }, { x: 10, y: 9, color: C.shadow },
];

const eyes: Pixel[] = [
  { x: 5, y: 5, color: C.accent }, { x: 6, y: 5, color: C.accent },
  { x: 9, y: 5, color: C.accent }, { x: 10, y: 5, color: C.accent },
];

const screen: Pixel[] = [
  { x: 5, y: 7, color: C.screen }, { x: 6, y: 7, color: C.screen },
  { x: 9, y: 7, color: C.screen }, { x: 10, y: 7, color: C.screen },
];

const antenna: Pixel[] = [
  { x: 7, y: 1, color: C.shadow },
  { x: 8, y: 1, color: C.shadow },
  { x: 7, y: 2, color: C.accent },
  { x: 8, y: 2, color: C.accent },
];

const arms: Pixel[] = [
  { x: 2, y: 7, color: C.shadow }, { x: 2, y: 8, color: C.shadow },
  { x: 13, y: 7, color: C.shadow }, { x: 13, y: 8, color: C.shadow },
];

const wheels: Pixel[] = [
  { x: 4, y: 10, color: C.shadow }, { x: 5, y: 10, color: C.shadow },
  { x: 6, y: 10, color: C.shadow },
  { x: 9, y: 10, color: C.shadow }, { x: 10, y: 10, color: C.shadow },
  { x: 11, y: 10, color: C.shadow },
  { x: 4, y: 11, color: C.black }, { x: 5, y: 11, color: C.shadow },
  { x: 6, y: 11, color: C.black },
  { x: 9, y: 11, color: C.black }, { x: 10, y: 11, color: C.shadow },
  { x: 11, y: 11, color: C.black },
];

const pixels: Pixel[] = [
  ...body, ...eyes, ...screen, ...antenna, ...arms, ...wheels,
];

interface RobotProps {
  state: RobotState;
  size?: number;
  className?: string;
  style?: CSSProperties;
}

export const Robot = ({ state, size = 96, className = "", style }: RobotProps) => {
  const isError = state === "error";
  const isThinking = state === "thinking";
  const isWriting = state === "writing";
  const isReading = state === "reading";
  const isSearching = state === "searching";

  return (
    <svg
      viewBox="0 0 80 64"
      width={size}
      height={size * 0.8}
      shapeRendering="crispEdges"
      className={`robot-svg robot-${state} ${className}`}
      style={style}
      aria-label={`Robot in ${state} state`}
    >
      <rect width="80" height="64" fill={C.bg} />

      {isError && (
        <rect x="0" y="0" width="80" height="64" fill={C.error} opacity="0.15" className="robot-flash" />
      )}

      {isThinking && (
        <>
          <circle cx="65" cy="10" r="2" fill={C.accent} className="robot-think-spark" />
          <circle cx="70" cy="15" r="1.5" fill={C.accent} className="robot-think-spark robot-think-spark-2" />
          <circle cx="73" cy="8" r="1" fill={C.accent} className="robot-think-spark robot-think-spark-3" />
        </>
      )}

      {isWriting && (
        <>
          <rect x="68" y="20" width="2" height="2" fill={C.accent} className="robot-spark" />
          <rect x="72" y="24" width="2" height="2" fill={C.accent} className="robot-spark robot-spark-2" />
          <rect x="65" y="28" width="2" height="2" fill={C.accent} className="robot-spark robot-spark-3" />
        </>
      )}

      {isReading && (
        <g className="robot-paper">
          <rect x="62" y="32" width="14" height="18" fill={C.white} stroke={C.shadow} strokeWidth="1" />
          <rect x="64" y="36" width="10" height="1" fill={C.shadow} />
          <rect x="64" y="39" width="8" height="1" fill={C.shadow} />
          <rect x="64" y="42" width="9" height="1" fill={C.shadow} />
          <rect x="64" y="45" width="7" height="1" fill={C.shadow} />
        </g>
      )}

      {isSearching && (
        <>
          <rect x="0" y="50" width="80" height="2" fill={C.accent} className="robot-scan" opacity="0.6" />
        </>
      )}

      <g className="robot-body">
        {pixels.map((p, i) => (
          <rect
            key={i}
            x={p.x * P * 0.8}
            y={p.y * P}
            width={P * 0.8}
            height={P}
            fill={p.color}
          />
        ))}
      </g>
    </svg>
  );
};
