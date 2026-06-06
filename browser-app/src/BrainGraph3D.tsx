import { useRef, useMemo, useState } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { OrbitControls, Sphere, Line, Text } from '@react-three/drei';
import * as THREE from 'three';

interface NodeData {
    id: number;
    label: string;
    pos: THREE.Vector3;
}

interface EdgeData {
    start: THREE.Vector3;
    end: THREE.Vector3;
    source_id: number;
    target_id: number;
}

const Node = ({ data, isSelected, onClick }: { data: NodeData, isSelected: boolean, onClick: () => void }) => {
    return (
        <group position={data.pos} onClick={(e) => { e.stopPropagation(); onClick(); }}>
            <Sphere args={[0.2, 16, 16]}>
                <meshStandardMaterial 
                    color={isSelected ? "#ffd60a" : "#4facfe"} 
                    emissive={isSelected ? "#ffd60a" : "#4facfe"} 
                    emissiveIntensity={isSelected ? 4 : 2} 
                />
            </Sphere>
            {(isSelected || data.label.length < 20) && (
                <Text
                    position={[0, 0.4, 0]}
                    fontSize={0.2}
                    color="white"
                    anchorX="center"
                    anchorY="middle"
                >
                    {data.label}
                </Text>
            )}
        </group>
    );
};

const BrainMesh = ({ nodes, edges }: { nodes: NodeData[], edges: EdgeData[] }) => {
    const [selectedId, setSelectedId] = useState<number | null>(null);
    const groupRef = useRef<THREE.Group>(null);

    useFrame(() => {
        if (groupRef.current && !selectedId) {
            groupRef.current.rotation.y += 0.002;
        }
    });

    return (
        <group ref={groupRef}>
            {nodes.map(node => (
                <Node 
                    key={node.id} 
                    data={node} 
                    isSelected={selectedId === node.id}
                    onClick={() => setSelectedId(node.id === selectedId ? null : node.id)}
                />
            ))}
            {edges.map((edge, i) => (
                <Line
                    key={i}
                    points={[edge.start, edge.end]}
                    color={edge.source_id === selectedId || edge.target_id === selectedId ? "#ffd60a" : "#00f2fe"}
                    lineWidth={edge.source_id === selectedId || edge.target_id === selectedId ? 2 : 0.5}
                    transparent
                    opacity={edge.source_id === selectedId || edge.target_id === selectedId ? 1 : 0.2}
                />
            ))}
        </group>
    );
};

export const BrainGraph3D = ({ data }: { data: any }) => {
    const { nodes, edges } = useMemo(() => {
        if (!data || !data.nodes) return { nodes: [], edges: [] };

        const nodeMap = new Map<number, NodeData>();
        const processedNodes = data.nodes.map((n: any, i: number) => {
            const phi = Math.acos(-1 + (2 * i) / data.nodes.length);
            const theta = Math.sqrt(data.nodes.length * Math.PI) * phi;
            const pos = new THREE.Vector3().setFromSphericalCoords(5, phi, theta);
            
            const node = { id: n.id, label: n.label, pos };
            nodeMap.set(n.id, node);
            return node;
        });

        const processedEdges = data.edges.map((e: any) => ({
            start: nodeMap.get(e.source)?.pos || new THREE.Vector3(),
            end: nodeMap.get(e.target)?.pos || new THREE.Vector3(),
            source_id: e.source,
            target_id: e.target
        })).filter((e: any) => e.start.length() > 0 && e.end.length() > 0);

        return { nodes: processedNodes, edges: processedEdges };
    }, [data]);

    return (
        <div style={{ width: '100%', height: '100%', background: '#000' }}>
            <Canvas camera={{ position: [0, 0, 15], fov: 60 }}>
                <ambientLight intensity={0.5} />
                <pointLight position={[10, 10, 10]} intensity={1} />
                <BrainMesh nodes={nodes} edges={edges} />
                <OrbitControls enablePan={false} />
            </Canvas>
        </div>
    );
};
