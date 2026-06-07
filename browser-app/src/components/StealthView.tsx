import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { Shield, Globe, Lock, Eye, Server, Plus, Zap } from "lucide-react";

interface StealthViewProps {
  privacyScore: number;
  localIp: string;
  onScanPeers: () => void;
  onSaveCredential: (domain: string, user: string, pass: string) => void;
}

const FAKED_IPS = [
  "185.220.101.4",
  "199.249.230.114",
  "23.129.64.220",
  "162.247.74.7",
  "171.25.193.20",
];

export const StealthView = ({ privacyScore, localIp, onScanPeers, onSaveCredential }: StealthViewProps) => {
  const [scanning, setScanning] = useState(false);
  const [displayIp, setDisplayIp] = useState(localIp);
  const [glitching, setGlitching] = useState(false);
  const scanInterval = useRef<number | null>(null);
  const glitchTimer = useRef<number | null>(null);

  useEffect(() => () => {
    if (scanInterval.current) window.clearInterval(scanInterval.current);
    if (glitchTimer.current) window.clearTimeout(glitchTimer.current);
  }, []);

  const handleScan = () => {
    if (scanInterval.current) window.clearInterval(scanInterval.current);
    if (glitchTimer.current) window.clearTimeout(glitchTimer.current);
    setScanning(true);
    setGlitching(true);
    let i = 0;
    scanInterval.current = window.setInterval(() => {
      setDisplayIp(FAKED_IPS[i % FAKED_IPS.length]);
      i++;
      if (i > 8) {
        if (scanInterval.current) window.clearInterval(scanInterval.current);
        scanInterval.current = null;
        setDisplayIp(FAKED_IPS[Math.floor(Math.random() * FAKED_IPS.length)]);
        setScanning(false);
        glitchTimer.current = window.setTimeout(() => setGlitching(false), 400);
      }
    }, 120);
    onScanPeers();
  };

  useEffect(() => {
    if (!scanning) setDisplayIp(localIp);
  }, [localIp, scanning]);

  const gaugeRadius = 70;
  const gaugeCircumference = 2 * Math.PI * gaugeRadius;
  const gaugeOffset = gaugeCircumference * (1 - privacyScore / 100);

  return (
    <div className="stealth-view">
      <div className="stealth-grid">
        <motion.div
          className="gauge-card glass"
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.5 }}
        >
          <div className="gauge-title">
            <Shield size={14} /> Anonymity Score
          </div>
          <div className="gauge-wrap">
            <svg viewBox="0 0 180 180" className="gauge-svg">
              <defs>
                <linearGradient id="gaugeGrad" x1="0" y1="0" x2="1" y2="1">
                  <stop offset="0%" stopColor="#41a6f6" />
                  <stop offset="50%" stopColor="#a7f070" />
                  <stop offset="100%" stopColor="#ffcd75" />
                </linearGradient>
                <filter id="gaugeGlow">
                  <feGaussianBlur stdDeviation="3" result="blur" />
                  <feMerge>
                    <feMergeNode in="blur" />
                    <feMergeNode in="SourceGraphic" />
                  </feMerge>
                </filter>
              </defs>
              <circle
                cx="90"
                cy="90"
                r={gaugeRadius}
                fill="none"
                stroke="rgba(255,255,255,0.05)"
                strokeWidth="10"
              />
              <motion.circle
                cx="90"
                cy="90"
                r={gaugeRadius}
                fill="none"
                stroke="url(#gaugeGrad)"
                strokeWidth="10"
                strokeLinecap="round"
                strokeDasharray={gaugeCircumference}
                transform="rotate(-90 90 90)"
                initial={{ strokeDashoffset: gaugeCircumference }}
                animate={{ strokeDashoffset: gaugeOffset }}
                transition={{ duration: 1.5, ease: "easeOut" }}
                filter="url(#gaugeGlow)"
              />
              {Array.from({ length: 12 }).map((_, i) => {
                const angle = (i * 30 - 90) * (Math.PI / 180);
                const x1 = 90 + (gaugeRadius + 12) * Math.cos(angle);
                const y1 = 90 + (gaugeRadius + 12) * Math.sin(angle);
                const x2 = 90 + (gaugeRadius + 6) * Math.cos(angle);
                const y2 = 90 + (gaugeRadius + 6) * Math.sin(angle);
                return (
                  <line
                    key={i}
                    x1={x1}
                    y1={y1}
                    x2={x2}
                    y2={y2}
                    stroke="rgba(255,255,255,0.15)"
                    strokeWidth="1.5"
                  />
                );
              })}
            </svg>
            <motion.div
              className="gauge-value"
              key={privacyScore}
              initial={{ scale: 1.2, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ duration: 0.3 }}
            >
              {privacyScore}<span className="gauge-unit">%</span>
            </motion.div>
            <div className="gauge-label">{privacyScore >= 70 ? "STEALTH" : privacyScore >= 40 ? "EXPOSED" : "BURNING"}</div>
          </div>
          <div className="gauge-bars">
            {Array.from({ length: 20 }).map((_, i) => (
              <motion.div
                key={i}
                className="gauge-bar"
                initial={{ scaleY: 0 }}
                animate={{ scaleY: i < Math.floor((privacyScore / 100) * 20) ? 1 : 0.15 }}
                transition={{ delay: 0.05 * i, duration: 0.3 }}
                style={{
                  background: i < 7 ? "#b13e53" : i < 14 ? "#ffcd75" : "#a7f070",
                }}
              />
            ))}
          </div>
        </motion.div>

        <motion.div
          className={`ip-card glass ${glitching ? "glitching" : ""}`}
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
        >
          <div className="ip-title">
            <Eye size={14} /> Public IP
            {scanning && (
              <motion.span
                className="ip-scanning-badge"
                animate={{ opacity: [0.4, 1, 0.4] }}
                transition={{ duration: 0.8, repeat: Infinity }}
              >
                <Zap size={10} /> ROTATING
              </motion.span>
            )}
          </div>
          <motion.div
            className="ip-display"
            key={displayIp}
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
          >
            {displayIp}
          </motion.div>
          <div className="ip-foot">
            <span>Tor exit node: <strong>randomized</strong></span>
            <button className="ip-rotate-btn" onClick={handleScan} disabled={scanning}>
              {scanning ? "Rotating…" : "Rotate now"}
            </button>
          </div>
        </motion.div>

        <motion.div
          className="routing-card glass"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
        >
          <div className="routing-title">
            <Server size={14} /> Routing Path
          </div>
          <div className="routing-path">
            {["You", "Guard", "Middle", "Exit", "Target"].map((node, i, arr) => (
              <div key={node} className="routing-node-wrap">
                <motion.div
                  className="routing-node"
                  initial={{ scale: 0 }}
                  animate={{ scale: 1 }}
                  transition={{ delay: 0.5 + i * 0.15, type: "spring" }}
                >
                  <div className="routing-dot" />
                  <div className="routing-label">{node}</div>
                </motion.div>
                {i < arr.length - 1 && (
                  <div className="routing-link">
                    <motion.div
                      className="routing-packet"
                      animate={{ x: ["-100%", "100%"] }}
                      transition={{ duration: 1.5, repeat: Infinity, delay: 0.8 + i * 0.2, ease: "linear" }}
                    />
                  </div>
                )}
              </div>
            ))}
          </div>
        </motion.div>

        <motion.div
          className="vault-card glass"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
        >
          <div className="vault-title">
            <Lock size={14} /> Credential Vault
          </div>
          <div className="vault-add-row">
            <input id="v-dom" placeholder="domain.com" className="vault-input" />
            <input id="v-user" placeholder="username" className="vault-input" />
            <input id="v-pass" type="password" placeholder="password" className="vault-input" />
            <button
              className="vault-save"
              onClick={() => {
                const d = (document.getElementById('v-dom') as HTMLInputElement).value;
                const u = (document.getElementById('v-user') as HTMLInputElement).value;
                const p = (document.getElementById('v-pass') as HTMLInputElement).value;
                if (d && u) onSaveCredential(d, u, p);
              }}
            >
              <Plus size={12} /> Save
            </button>
          </div>
        </motion.div>
      </div>

      <motion.button
        className="stealth-scan-btn"
        onClick={handleScan}
        disabled={scanning}
        whileHover={{ scale: 1.02 }}
        whileTap={{ scale: 0.98 }}
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.5 }}
      >
        <Globe size={14} /> Scan for Network Peers
        {scanning && (
          <motion.span
            className="stealth-scan-pulse"
            animate={{ scale: [1, 1.5], opacity: [0.5, 0] }}
            transition={{ duration: 1, repeat: Infinity }}
          />
        )}
      </motion.button>
    </div>
  );
};
