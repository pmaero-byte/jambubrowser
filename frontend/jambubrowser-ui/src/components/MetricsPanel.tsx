import { motion } from "framer-motion";
import { BrainCircuit, Zap, Cpu, Activity } from "lucide-react";

/**
 * Real-time Metrics Dashboard
 * ----------------------------
 * Displays live performance data from the engine and local machine.
 * Helps users monitor the 'Brain' capacity and 'Full Power' load.
 */

interface MetricsProps {
  nodes: number;
  tokens: number;
  ram: number;
  duration: number;
}

export const MetricsPanel = ({ nodes, tokens, ram, duration }: MetricsProps) => {
  return (
    <div className="top-metrics">
      <motion.span whileHover={{ scale: 1.05 }}>
        <BrainCircuit size={12}/> {nodes} nodes
      </motion.span>
      
      <motion.span whileHover={{ scale: 1.05 }}>
        <Zap size={12}/> {tokens} tokens
      </motion.span>
      
      <motion.span whileHover={{ scale: 1.05 }}>
        <Cpu size={12}/> {ram.toFixed(1)}GB RAM
      </motion.span>
      
      {duration > 0 && (
        <motion.span 
          initial={{ opacity: 0 }} 
          animate={{ opacity: 1 }} 
          className="active-metric"
        >
          <Activity size={12}/> {duration.toFixed(1)}s
        </motion.span>
      )}
    </div>
  );
};
