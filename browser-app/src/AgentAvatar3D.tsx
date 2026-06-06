import { useRef, useMemo } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { Sphere, MeshDistortMaterial, Float, Points, PointMaterial } from '@react-three/drei';
import * as THREE from 'three';

const Swarm = ({ count = 50 }) => {
    const points = useMemo(() => {
        const p = new Float32Array(count * 3);
        for (let i = 0; i < count; i++) {
            p[i * 3] = (Math.random() - 0.5) * 4;
            p[i * 3 + 1] = (Math.random() - 0.5) * 4;
            p[i * 3 + 2] = (Math.random() - 0.5) * 4;
        }
        return p;
    }, [count]);

    const ref = useRef<THREE.Points>(null);
    useFrame(() => {
        if (ref.current) {
            ref.current.rotation.y += 0.01;
            ref.current.rotation.z += 0.005;
        }
    });

    return (
        <Points ref={ref} positions={points} stride={3}>
            <PointMaterial
                transparent
                color="#00f2fe"
                size={0.1}
                sizeAttenuation={true}
                depthWrite={false}
            />
        </Points>
    );
};

const Orb = ({ mode }: { mode: string }) => {
    const meshRef = useRef<THREE.Mesh>(null);
    useFrame((state) => {
        if (!meshRef.current) return;
        if (mode === "active") {
            meshRef.current.rotation.y += 0.05;
            meshRef.current.rotation.z += 0.02;
        } else if (mode === "thinking") {
            meshRef.current.rotation.y += 0.01;
            const s = Math.sin(state.clock.elapsedTime * 2) * 0.1 + 1;
            meshRef.current.scale.set(s, s, s);
        } else {
            meshRef.current.rotation.y += 0.005;
        }
    });

    return (
        <Float speed={2} rotationIntensity={1} floatIntensity={1}>
            <Sphere ref={meshRef} args={[1, 64, 64]}>
                <MeshDistortMaterial
                    color={mode === "active" ? "#4facfe" : mode === "thinking" ? "#ffd60a" : "#666"}
                    speed={mode === "active" ? 4 : 2}
                    distort={mode === "active" ? 0.4 : 0.2}
                    radius={1}
                />
            </Sphere>
        </Float>
    );
};

export const AgentAvatar3D = ({ status }: { status: string }) => {
    const mode = useMemo(() => {
        if (status.includes("Swarm") || status.includes("Parallel")) return "swarm";
        if (status.includes("Researching") || status.includes("Autonomous")) return "active";
        if (status.includes("Synthesizing") || status.includes("Drafting")) return "thinking";
        return "idle";
    }, [status]);

    return (
        <div style={{ width: '40px', height: '40px' }}>
            <Canvas camera={{ position: [0, 0, 3] }}>
                <ambientLight intensity={0.5} />
                <pointLight position={[10, 10, 10]} />
                {mode === "swarm" ? <Swarm /> : <Orb mode={mode} />}
            </Canvas>
        </div>
    );
};
