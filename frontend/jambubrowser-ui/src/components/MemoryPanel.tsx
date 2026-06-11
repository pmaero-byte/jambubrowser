import React, { useEffect, useState, useCallback } from "react";
import { motion } from "framer-motion";
import { User, Tag, Brain, History, Trash2, Plus, Save, X } from "lucide-react";
import {
  getProfile, updateProfile, listSessions, recallMemory, storeMemory, forgetMemory, getMemoryStats,
} from "../utils/memory";
import type { UserProfile, SessionMemory, MemoryHit } from "../utils/types";

interface MemoryPanelProps {
  userId?: string;
  onClose?: () => void;
}

export const MemoryPanel: React.FC<MemoryPanelProps> = ({ userId = "default", onClose }) => {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [sessions, setSessions] = useState<SessionMemory[]>([]);
  const [recallHits, setRecallHits] = useState<MemoryHit[]>([]);
  const [recallQuery, setRecallQuery] = useState("");
  const [stats, setStats] = useState<any>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Partial<UserProfile>>({});
  const [newInterest, setNewInterest] = useState("");
  const [newMemory, setNewMemory] = useState("");
  const [activeTab, setActiveTab] = useState<"profile" | "recall" | "sessions">("profile");
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [p, s, st] = await Promise.all([getProfile(userId), listSessions(userId, 10), getMemoryStats(userId)]);
      setProfile(p);
      setSessions(s);
      setStats(st);
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleSaveProfile = async () => {
    if (!profile) return;
    setLoading(true);
    try {
      const updated = await updateProfile({ ...draft, user_id: userId });
      setProfile(updated);
      setEditing(false);
      setDraft({});
    } finally {
      setLoading(false);
    }
  };

  const handleAddInterest = async () => {
    if (!newInterest.trim() || !profile) return;
    const interests = Array.from(new Set([...(profile.interests || []), newInterest.trim()]));
    const updated = await updateProfile({ user_id: userId, interests });
    setProfile(updated);
    setNewInterest("");
  };

  const handleRemoveInterest = async (interest: string) => {
    if (!profile) return;
    const interests = (profile.interests || []).filter((i) => i !== interest);
    const updated = await updateProfile({ user_id: userId, interests });
    setProfile(updated);
  };

  const handleStoreMemory = async () => {
    if (!newMemory.trim()) return;
    await storeMemory(userId, newMemory.trim(), "fact", 0.6);
    setNewMemory("");
    refresh();
  };

  const handleRecall = async () => {
    if (!recallQuery.trim()) return;
    const hits = await recallMemory(userId, recallQuery, 10);
    setRecallHits(hits);
  };

  const handleForget = async (id: number) => {
    await forgetMemory(id, userId);
    setRecallHits((prev) => prev.filter((h) => h.id !== id));
    refresh();
  };

  if (!profile) {
    return (
      <div style={{ padding: 16, color: "#888" }}>Loading memory…</div>
    );
  }

  return (
    <div className="memory-panel" style={{ padding: 16, color: "#e5e5e5", height: "100%", overflowY: "auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <h2 style={{ display: "flex", alignItems: "center", gap: 8, margin: 0, fontSize: 18 }}>
          <Brain size={20} /> Memory
        </h2>
        {onClose && (
          <button onClick={onClose} style={{ background: "none", border: "none", color: "#888", cursor: "pointer" }}>
            <X size={18} />
          </button>
        )}
      </div>

      {stats && (
        <div style={{ display: "flex", gap: 12, marginBottom: 16, fontSize: 11, color: "#888" }}>
          <span><b style={{ color: "#4facfe" }}>{stats.semantic_memories}</b> memories</span>
          <span><b style={{ color: "#a78bfa" }}>{stats.sessions}</b> sessions</span>
          <span><b style={{ color: "#22c55e" }}>{stats.procedural_memories}</b> patterns</span>
        </div>
      )}

      <div style={{ display: "flex", gap: 4, marginBottom: 12, borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
        {(["profile", "recall", "sessions"] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            style={{
              padding: "8px 12px",
              background: "none",
              border: "none",
              color: activeTab === tab ? "#4facfe" : "#888",
              borderBottom: activeTab === tab ? "2px solid #4facfe" : "2px solid transparent",
              cursor: "pointer",
              fontSize: 12,
              textTransform: "capitalize",
            }}
          >
            {tab}
          </button>
        ))}
      </div>

      {activeTab === "profile" && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
          <div style={{ marginBottom: 16 }}>
            <label style={{ fontSize: 11, color: "#888", display: "block", marginBottom: 4 }}>Display name</label>
            {editing ? (
              <input
                value={draft.display_name ?? profile.display_name}
                onChange={(e) => setDraft({ ...draft, display_name: e.target.value })}
                style={inputStyle}
              />
            ) : (
              <div style={{ ...inputStyle, background: "transparent" }}>
                <User size={12} style={{ marginRight: 6, verticalAlign: "middle" }} />
                {profile.display_name || <em style={{ color: "#555" }}>(unset)</em>}
              </div>
            )}
          </div>

          <div style={{ marginBottom: 16 }}>
            <label style={{ fontSize: 11, color: "#888", display: "block", marginBottom: 4 }}>Interests</label>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 8 }}>
              {(profile.interests || []).map((i) => (
                <span key={i} style={chipStyle}>
                  {i}
                  {editing && (
                    <X size={10} style={{ marginLeft: 4, cursor: "pointer" }} onClick={() => handleRemoveInterest(i)} />
                  )}
                </span>
              ))}
            </div>
            {editing && (
              <div style={{ display: "flex", gap: 4 }}>
                <input
                  value={newInterest}
                  onChange={(e) => setNewInterest(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleAddInterest()}
                  placeholder="Add interest…"
                  style={{ ...inputStyle, flex: 1 }}
                />
                <button onClick={handleAddInterest} style={btnStyle}><Plus size={12} /></button>
              </div>
            )}
          </div>

          <div style={{ marginBottom: 16 }}>
            <label style={{ fontSize: 11, color: "#888", display: "block", marginBottom: 4 }}>Work context</label>
            {editing ? (
              <textarea
                value={draft.work_context ?? profile.work_context}
                onChange={(e) => setDraft({ ...draft, work_context: e.target.value })}
                rows={3}
                style={{ ...inputStyle, fontFamily: "inherit", resize: "vertical" }}
              />
            ) : (
              <div style={{ ...inputStyle, background: "transparent", minHeight: 40 }}>
                {profile.work_context || <em style={{ color: "#555" }}>(unset)</em>}
              </div>
            )}
          </div>

          <div style={{ display: "flex", gap: 8 }}>
            {editing ? (
              <>
                <button onClick={handleSaveProfile} style={{ ...btnStyle, background: "#22c55e" }} disabled={loading}>
                  <Save size={12} /> Save
                </button>
                <button onClick={() => { setEditing(false); setDraft({}); }} style={btnStyle}>
                  Cancel
                </button>
              </>
            ) : (
              <button onClick={() => setEditing(true)} style={btnStyle}>Edit profile</button>
            )}
          </div>

          <div style={{ marginTop: 24, paddingTop: 16, borderTop: "1px solid rgba(255,255,255,0.06)" }}>
            <label style={{ fontSize: 11, color: "#888", display: "block", marginBottom: 4 }}>Store new memory</label>
            <div style={{ display: "flex", gap: 4 }}>
              <input
                value={newMemory}
                onChange={(e) => setNewMemory(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleStoreMemory()}
                placeholder="e.g., User prefers Rust over Go"
                style={{ ...inputStyle, flex: 1 }}
              />
              <button onClick={handleStoreMemory} style={btnStyle} disabled={!newMemory.trim()}>
                <Plus size={12} />
              </button>
            </div>
          </div>
        </motion.div>
      )}

      {activeTab === "recall" && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
          <div style={{ display: "flex", gap: 4, marginBottom: 12 }}>
            <input
              value={recallQuery}
              onChange={(e) => setRecallQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleRecall()}
              placeholder="Search memories…"
              style={{ ...inputStyle, flex: 1 }}
            />
            <button onClick={handleRecall} style={btnStyle} disabled={!recallQuery.trim()}>
              <Brain size={12} /> Recall
            </button>
          </div>
          {recallHits.length === 0 && (
            <div style={{ color: "#666", fontSize: 12, padding: 8 }}>No memories recalled yet.</div>
          )}
          {recallHits.map((hit) => (
            <div
              key={hit.id}
              style={{
                background: "rgba(0,0,0,0.2)",
                border: "1px solid rgba(255,255,255,0.06)",
                borderRadius: 6,
                padding: 8,
                marginBottom: 6,
                fontSize: 12,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
                <div style={{ flex: 1 }}>
                  <div style={{ marginBottom: 4 }}>{hit.content}</div>
                  <div style={{ color: "#666", fontSize: 10 }}>
                    <Tag size={9} style={{ verticalAlign: "middle" }} /> {hit.category} · importance {hit.importance.toFixed(1)} · score {hit.score.toFixed(2)} · {hit.matched_by}
                  </div>
                </div>
                <button
                  onClick={() => handleForget(hit.id)}
                  style={{ background: "none", border: "none", color: "#ef4444", cursor: "pointer", padding: 0 }}
                  title="Forget"
                >
                  <Trash2 size={12} />
                </button>
              </div>
            </div>
          ))}
        </motion.div>
      )}

      {activeTab === "sessions" && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
          {sessions.length === 0 && (
            <div style={{ color: "#666", fontSize: 12, padding: 8 }}>No sessions yet.</div>
          )}
          {sessions.map((s) => (
            <div
              key={s.session_id}
              style={{
                background: "rgba(0,0,0,0.2)",
                border: "1px solid rgba(255,255,255,0.06)",
                borderRadius: 6,
                padding: 8,
                marginBottom: 6,
                fontSize: 12,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
                <History size={12} />
                <b>{s.topic || s.session_id}</b>
              </div>
              {s.summary && <div style={{ color: "#aaa", marginBottom: 4 }}>{s.summary}</div>}
              <div style={{ color: "#666", fontSize: 10 }}>
                {new Date(s.last_active * 1000).toLocaleString()} · {(s.active_goals || []).length} goal(s) · {(s.entities || []).length} entity(ies)
              </div>
            </div>
          ))}
        </motion.div>
      )}
    </div>
  );
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "6px 10px",
  background: "rgba(0,0,0,0.3)",
  border: "1px solid rgba(255,255,255,0.08)",
  borderRadius: 6,
  color: "#e5e5e5",
  fontSize: 12,
  outline: "none",
  boxSizing: "border-box",
};

const btnStyle: React.CSSProperties = {
  padding: "6px 10px",
  background: "rgba(79, 172, 254, 0.2)",
  border: "1px solid rgba(79, 172, 254, 0.3)",
  borderRadius: 6,
  color: "#4facfe",
  fontSize: 11,
  cursor: "pointer",
  display: "flex",
  alignItems: "center",
  gap: 4,
};

const chipStyle: React.CSSProperties = {
  padding: "3px 8px",
  background: "rgba(167, 139, 250, 0.15)",
  border: "1px solid rgba(167, 139, 250, 0.3)",
  borderRadius: 12,
  color: "#a78bfa",
  fontSize: 11,
  display: "inline-flex",
  alignItems: "center",
};
