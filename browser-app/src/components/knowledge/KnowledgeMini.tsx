import { useCallback, useMemo, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";

interface Node {
  id: string;
  label: string;
  group: string;
  val?: number;
}

interface Link {
  source: string;
  target: string;
  label?: string;
}

interface KnowledgeMiniProps {
  data?: { nodes: Node[]; links: Link[] };
  onSelectNode?: (id: string) => void;
}

const palette: Record<string, string> = {
  topic: "#5e6ad2",
  entity: "#00d4ff",
  session: "#22c55e",
  default: "#888",
};

export function KnowledgeMini({ data, onSelectNode }: KnowledgeMiniProps) {
  const [selected, setSelected] = useState<string | null>(null);

  const graphData = useMemo(
    () =>
      data || {
        nodes: [
          { id: "root", label: "Jambu", group: "topic", val: 12 },
          { id: "privacy", label: "Privacy", group: "topic", val: 8 },
          { id: "agent", label: "Agent Loop", group: "topic", val: 8 },
          { id: "memory", label: "Memory", group: "entity", val: 6 },
          { id: "vault", label: "Vault", group: "entity", val: 6 },
          { id: "search", label: "Search", group: "entity", val: 6 },
        ],
        links: [
          { source: "root", target: "privacy" },
          { source: "root", target: "agent" },
          { source: "agent", target: "memory" },
          { source: "agent", target: "search" },
          { source: "privacy", target: "vault" },
        ],
      },
    [data]
  );

  const handleClick = useCallback(
    (node: any) => {
      setSelected(node.id);
      onSelectNode?.(node.id);
    },
    [onSelectNode]
  );

  return (
    <div className="h-full w-full overflow-hidden rounded-xl border border-border bg-card p-2">
      <ForceGraph2D
        graphData={graphData}
        width={288}
        height={288}
        backgroundColor="transparent"
        nodeAutoColorBy="group"
        nodeColor={(n: any) =>
          selected === n.id ? "#f59e0b" : palette[n.group] || palette.default
        }
        nodeVal={(n: any) => n.val || 4}
        nodeLabel={(n: any) => n.label || n.id}
        linkColor={() => "rgba(255,255,255,0.15)"}
        linkWidth={1}
        warmupTicks={10}
        cooldownTicks={50}
        onNodeClick={handleClick}
        d3AlphaDecay={0.02}
        d3VelocityDecay={0.3}
      />
    </div>
  );
}
