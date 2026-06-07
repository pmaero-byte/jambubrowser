import { Robot, type RobotState } from "./robot-svg";
import "./AgentRoom.css";

export type Zone = "desk" | "cabinet" | "pile" | "center";

interface AgentRoomProps {
  agentState: RobotState;
  targetZone?: Zone;
  taskActive: boolean;
}

const ZONE_POS: Record<Zone, { x: number; y: number }> = {
  desk: { x: 50, y: 240 },
  cabinet: { x: 380, y: 130 },
  pile: { x: 220, y: 270 },
  center: { x: 230, y: 200 },
};

export const AgentRoom = ({ agentState, targetZone = "center", taskActive }: AgentRoomProps) => {
  const pos = ZONE_POS[targetZone];

  return (
    <div className="agent-room">
      <div className="room-status-bar">
        <span className={`room-status-dot ${taskActive ? "live" : "idle"}`} />
        <span className="room-status-text">
          {taskActive ? "AGENT ACTIVE" : "AGENT IDLE"}
        </span>
        <span className="room-status-zone">Zone: {targetZone.toUpperCase()}</span>
        <span className="room-status-state">State: {agentState.toUpperCase()}</span>
      </div>

      <svg
        viewBox="0 0 500 360"
        preserveAspectRatio="xMidYMid meet"
        className="room-svg"
        shapeRendering="crispEdges"
      >
        <defs>
          <pattern id="floorGrid" x="0" y="0" width="20" height="20" patternUnits="userSpaceOnUse">
            <rect width="20" height="20" fill="#3b5dc9" />
            <rect x="0" y="0" width="20" height="1" fill="#2a4694" />
            <rect x="0" y="0" width="1" height="20" fill="#2a4694" />
          </pattern>
          <pattern id="screenNoise" x="0" y="0" width="6" height="6" patternUnits="userSpaceOnUse">
            <rect width="6" height="6" fill="#41a6f6" />
            <rect x="0" y="0" width="6" height="1" fill="#5fb8ff" opacity="0.6" />
            <rect x="0" y="3" width="6" height="1" fill="#2a5a8a" opacity="0.4" />
          </pattern>
        </defs>

        <rect x="0" y="0" width="500" height="220" fill="#1a1c2c" />
        <rect x="0" y="0" width="500" height="6" fill="#29366f" />
        <rect x="0" y="220" width="500" height="140" fill="url(#floorGrid)" />
        <rect x="0" y="220" width="500" height="2" fill="#2a4694" />

        <g className="window">
          <rect x="380" y="40" width="80" height="70" fill="#0a0a14" stroke="#566c86" strokeWidth="2" />
          <rect x="386" y="46" width="68" height="58" fill="#29366f" />
          <rect x="416" y="46" width="2" height="58" fill="#566c86" />
          <rect x="386" y="74" width="68" height="2" fill="#566c86" />
          <circle cx="395" cy="55" r="1" fill="#fff" opacity="0.8" className="star" />
          <circle cx="445" cy="65" r="1" fill="#fff" opacity="0.6" className="star star-2" />
          <circle cx="405" cy="90" r="1" fill="#fff" opacity="0.7" className="star star-3" />
          <circle cx="450" cy="100" r="1" fill="#fff" opacity="0.5" className="star" />
        </g>

        <g className="desk">
          <rect x="20" y="220" width="120" height="10" fill="#a04a1c" />
          <rect x="20" y="220" width="120" height="4" fill="#c25a28" />
          <rect x="20" y="230" width="6" height="60" fill="#6b3010" />
          <rect x="134" y="230" width="6" height="60" fill="#6b3010" />
          <rect x="40" y="240" width="80" height="50" fill="#29366f" stroke="#1a1c2c" strokeWidth="2" />
          <rect x="44" y="244" width="72" height="42" fill="url(#screenNoise)" className="screen-glow" />
          <rect x="50" y="252" width="40" height="2" fill="#0a0a14" opacity="0.6" />
          <rect x="50" y="260" width="55" height="2" fill="#0a0a14" opacity="0.6" />
          <rect x="50" y="268" width="35" height="2" fill="#0a0a14" opacity="0.6" />
          <rect x="50" y="276" width="48" height="2" fill="#0a0a14" opacity="0.6" />
          <rect x="78" y="290" width="4" height="6" fill="#566c86" />
        </g>

        <g className="filing-cabinet">
          <rect x="350" y="100" width="80" height="120" fill="#566c86" />
          <rect x="350" y="100" width="80" height="4" fill="#94b0c2" />
          <rect x="350" y="138" width="80" height="4" fill="#3d4a5c" />
          <rect x="350" y="176" width="80" height="4" fill="#3d4a5c" />
          <rect x="350" y="214" width="80" height="6" fill="#3d4a5c" />
          <rect x="385" y="118" width="10" height="3" fill="#1a1c2c" />
          <rect x="385" y="156" width="10" height="3" fill="#1a1c2c" />
          <rect x="385" y="194" width="10" height="3" fill="#1a1c2c" />
        </g>

        <g className="file-pile">
          <rect x="190" y="270" width="40" height="14" fill="#f4f4f4" transform="rotate(-8 210 277)" />
          <rect x="195" y="268" width="40" height="14" fill="#e0e0e0" transform="rotate(5 215 275)" />
          <rect x="200" y="272" width="40" height="14" fill="#f4f4f4" transform="rotate(-3 220 279)" />
          <rect x="220" y="275" width="38" height="12" fill="#d4d4d4" transform="rotate(10 239 281)" />
          <rect x="180" y="278" width="38" height="12" fill="#f4f4f4" transform="rotate(12 199 284)" />
        </g>

        <g className="coffee-mug">
          <rect x="148" y="200" width="16" height="20" fill="#b13e53" />
          <rect x="148" y="200" width="16" height="3" fill="#d4627a" />
          <rect x="164" y="205" width="4" height="8" fill="#b13e53" />
          <rect x="150" y="198" width="12" height="3" fill="#6b2a3a" />
          <g className="steam">
            <rect x="152" y="192" width="2" height="4" fill="#94b0c2" opacity="0.5" />
            <rect x="156" y="190" width="2" height="4" fill="#94b0c2" opacity="0.4" />
            <rect x="160" y="193" width="2" height="4" fill="#94b0c2" opacity="0.3" />
          </g>
        </g>

        <g
          className={`robot-positioner robot-${agentState}`}
          style={{
            transform: `translate(${pos.x}px, ${pos.y}px)`,
            transition: "transform 600ms cubic-bezier(0.4, 0, 0.2, 1)",
          }}
        >
          <Robot state={agentState} size={64} />
        </g>
      </svg>

      <div className="room-legend">
        <div className="legend-item"><span className="legend-swatch" style={{background:"#41a6f6"}}/> Desk · thinking/writing</div>
        <div className="legend-item"><span className="legend-swatch" style={{background:"#566c86"}}/> Cabinet · reading</div>
        <div className="legend-item"><span className="legend-swatch" style={{background:"#f4f4f4"}}/> File pile · searching</div>
      </div>
    </div>
  );
};
