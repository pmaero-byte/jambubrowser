import { useEffect, useState } from "react";
import { motion, AnimatePresence, LayoutGroup } from "motion/react";
import { Brain, Plus, RefreshCw, Search } from "lucide-react";
import { Button } from "../ui/button";
import {
  getProfile,
  updateProfile,
  listSessions,
  storeMemory,
  recallMemory,
  getMemoryStats,
} from "../../utils/memory";
import type { UserProfile, SessionMemory, MemoryHit } from "../../utils/types";

const USER_ID = "default";

export function MemoryPanel() {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [sessions, setSessions] = useState<SessionMemory[]>([]);
  const [stats, setStats] = useState<{
    profiles: number;
    sessions: number;
    semantic_memories: number;
    procedural_memories: number;
  } | null>(null);
  const [activeTab, setActiveTab] = useState<"profile" | "recall" | "sessions" | "store">("profile");
  const [loading, setLoading] = useState(false);

  const loadAll = async () => {
    setLoading(true);
    try {
      const [p, s, st] = await Promise.all([
        getProfile(USER_ID),
        listSessions(USER_ID, 20),
        getMemoryStats(USER_ID),
      ]);
      setProfile(p);
      setSessions(s);
      setStats(st);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAll();
  }, []);

  const saveProfile = async (patch: Partial<UserProfile>) => {
    if (!profile) return;
    try {
      const updated = await updateProfile({ user_id: USER_ID, ...patch });
      setProfile(updated);
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border p-3">
        <div className="mb-2 flex items-center gap-2">
          <Brain size={18} className="text-accent" />
          <span className="font-semibold">Memory</span>
        </div>
        <div className="relative flex gap-1">
          {[
            { id: "profile", label: "Profile" },
            { id: "recall", label: "Recall" },
            { id: "sessions", label: "Sessions" },
            { id: "store", label: "Store" },
          ].map((t) => (
            <button
              key={t.id}
              onClick={() => setActiveTab(t.id as any)}
              className={`relative rounded-md px-2 py-1 text-xs transition-colors ${
                activeTab === t.id
                  ? "text-foreground"
                  : "text-muted-foreground hover:bg-muted/50"
              }`}
            >
              {activeTab === t.id && (
                <motion.span
                  layoutId="mem-tab-indicator"
                  className="absolute inset-0 rounded-md bg-muted"
                  transition={{ type: "spring", stiffness: 380, damping: 30 }}
                />
              )}
              <span className="relative font-medium">{t.label}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3">
        <AnimatePresence mode="wait">
          <motion.div
            key={activeTab}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.18 }}
          >
            {activeTab === "profile" && (
              <ProfileTab profile={profile} onSave={saveProfile} loading={loading} />
            )}
            {activeTab === "recall" && <RecallTab />}
            {activeTab === "sessions" && <SessionsTab sessions={sessions} />}
            {activeTab === "store" && <StoreTab onStore={loadAll} />}
          </motion.div>
        </AnimatePresence>
      </div>

      <div className="border-t border-border p-3">
        <div className="grid grid-cols-2 gap-2 text-[10px] text-muted-foreground">
          <div>Profiles: {stats?.profiles ?? "—"}</div>
          <div>Sessions: {stats?.sessions ?? "—"}</div>
          <div>Semantic: {stats?.semantic_memories ?? "—"}</div>
          <div>Procedural: {stats?.procedural_memories ?? "—"}</div>
        </div>
        <Button variant="outline" size="sm" className="mt-2 w-full gap-1" onClick={loadAll}>
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} /> Refresh
        </Button>
      </div>
    </div>
  );
}

function ProfileTab({
  profile,
  onSave,
  loading,
}: {
  profile: UserProfile | null;
  onSave: (p: Partial<UserProfile>) => void;
  loading: boolean;
}) {
  const [interests, setInterests] = useState(profile?.interests?.join(", ") || "");

  useEffect(() => {
    setInterests(profile?.interests?.join(", ") || "");
  }, [profile]);

  if (loading && !profile) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (!profile) return <p className="text-sm text-red-400">Failed to load profile.</p>;

  return (
    <div className="space-y-3">
      <div>
        <label className="block text-xs text-muted-foreground">Display Name</label>
        <input
          defaultValue={profile.display_name}
          onBlur={(e) => onSave({ display_name: e.target.value })}
          className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1 text-sm outline-none"
        />
      </div>
      <div>
        <label className="block text-xs text-muted-foreground">Work Context</label>
        <textarea
          defaultValue={profile.work_context}
          onBlur={(e) => onSave({ work_context: e.target.value })}
          rows={3}
          className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1 text-sm outline-none"
        />
      </div>
      <div>
        <label className="block text-xs text-muted-foreground">Interests (comma separated)</label>
        <input
          value={interests}
          onChange={(e) => setInterests(e.target.value)}
          onBlur={() =>
            onSave({
              interests: interests
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean),
            })
          }
          className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1 text-sm outline-none"
        />
      </div>
      <div className="rounded-md bg-muted p-2 text-xs text-muted-foreground">
        Language: {profile.language || "en"}
      </div>
    </div>
  );
}

function RecallTab() {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<MemoryHit[]>([]);
  const [loading, setLoading] = useState(false);

  const run = async () => {
    if (!query.trim()) return;
    setLoading(true);
    try {
      setHits(await recallMemory(USER_ID, query, 8));
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
          placeholder="Search memory…"
          className="flex-1 rounded-md border border-border bg-background px-2 py-1 text-sm outline-none"
        />
        <Button size="icon" className="h-8 w-8" onClick={run} disabled={loading}>
          <Search size={14} />
        </Button>
      </div>

      <LayoutGroup>
        <div className="space-y-2">
          <AnimatePresence initial={false}>
            {hits.map((hit, i) => (
              <motion.div
                key={hit.id}
                layout
                initial={{ opacity: 0, y: 4, scale: 0.97 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: -4, scale: 0.97 }}
                transition={{ duration: 0.18, delay: Math.min(i * 0.04, 0.4) }}
                whileHover={{ y: -1, borderColor: "rgba(99,102,241,0.4)" }}
                className="rounded-md border border-border bg-card p-2 text-xs"
              >
                <div className="flex items-center justify-between">
                  <span className="rounded bg-muted px-1.5 py-0.5">{hit.category}</span>
                  <motion.span
                    key={hit.score}
                    initial={{ scale: 0.85 }}
                    animate={{ scale: 1 }}
                    transition={{ type: "spring", stiffness: 380, damping: 20 }}
                    className="text-muted-foreground"
                  >
                    {(hit.score * 100).toFixed(0)}%
                  </motion.span>
                </div>
                <p className="mt-1">{hit.content}</p>
                <div className="mt-1 text-[10px] text-muted-foreground">
                  matched by {hit.matched_by}
                </div>
              </motion.div>
            ))}
          </AnimatePresence>
          {!loading && hits.length === 0 && (
            <p className="text-center text-xs text-muted-foreground">No results.</p>
          )}
        </div>
      </LayoutGroup>
    </div>
  );
}

function SessionsTab({ sessions }: { sessions: SessionMemory[] }) {
  return (
    <LayoutGroup>
      <div className="space-y-2">
        <AnimatePresence initial={false}>
          {sessions.map((s, i) => (
            <motion.div
              key={s.session_id}
              layout
              initial={{ opacity: 0, y: 4, scale: 0.97 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -4, scale: 0.97 }}
              transition={{ duration: 0.18, delay: Math.min(i * 0.04, 0.4) }}
              whileHover={{ y: -1, borderColor: "rgba(99,102,241,0.4)" }}
              className="rounded-md border border-border bg-card p-2 text-xs"
            >
              <div className="font-medium">{s.topic || s.session_id.slice(0, 8)}</div>
              <p className="mt-1 text-muted-foreground">{s.summary}</p>
              <div className="mt-1 text-[10px]">
                {new Date(s.last_active * 1000).toLocaleDateString()}
              </div>
            </motion.div>
          ))}
        </AnimatePresence>
        {sessions.length === 0 && (
          <p className="text-center text-xs text-muted-foreground">No sessions yet.</p>
        )}
      </div>
    </LayoutGroup>
  );
}

function StoreTab({ onStore }: { onStore: () => void }) {
  const [content, setContent] = useState("");
  const [category, setCategory] = useState("fact");
  const [importance, setImportance] = useState(0.5);
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!content.trim()) return;
    setSaving(true);
    try {
      await storeMemory(USER_ID, content, category, importance);
      setContent("");
      onStore();
    } catch (e) {
      console.error(e);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3">
      <textarea
        value={content}
        onChange={(e) => setContent(e.target.value)}
        rows={5}
        placeholder="Enter a fact, preference, or observation to remember…"
        className="w-full rounded-md border border-border bg-background px-2 py-1 text-sm outline-none"
      />
      <div className="flex gap-2">
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className="rounded-md border border-border bg-background px-2 py-1 text-xs outline-none"
        >
          {["fact", "preference", "goal", "person", "project"].map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <input
          type="range"
          min={0}
          max={1}
          step={0.1}
          value={importance}
          onChange={(e) => setImportance(Number(e.target.value))}
          className="flex-1"
        />
        <span className="text-xs text-muted-foreground">{importance.toFixed(1)}</span>
      </div>
      <Button className="w-full gap-1" onClick={submit} disabled={saving || !content.trim()}>
        <Plus size={14} /> {saving ? "Saving…" : "Store Memory"}
      </Button>
    </div>
  );
}
