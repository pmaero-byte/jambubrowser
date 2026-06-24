import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { FolderKanban, Plus, StopCircle, RefreshCw, Clock } from "lucide-react";
import { Button } from "../ui/button";
import { localFetch } from "../../utils/api";

interface Mission {
  id: string;
  query: string;
  status: string;
  last_run: number;
  next_run: number;
  schedule: string;
}

export function MissionsPanel() {
  const [missions, setMissions] = useState<Mission[]>([]);
  const [loading, setLoading] = useState(false);
  const [newQuery, setNewQuery] = useState("");

  const loadMissions = async () => {
    setLoading(true);
    try {
      const res = await localFetch("/mission/list");
      const data = await res.json();
      setMissions(data.missions || []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadMissions();
  }, []);

  const createMission = async () => {
    if (!newQuery.trim()) return;
    try {
      await localFetch("/mission", {
        method: "POST",
        body: JSON.stringify({ query: newQuery.trim() }),
      });
      setNewQuery("");
      await loadMissions();
    } catch (e) {
      console.error(e);
    }
  };

  const stopMission = async (id: string) => {
    try {
      await localFetch("/mission/stop", {
        method: "POST",
        body: JSON.stringify({ mission_id: id }),
      });
      await loadMissions();
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border p-3">
        <div className="mb-2 flex items-center gap-2">
          <FolderKanban size={18} className="text-accent" />
          <span className="font-semibold">Missions</span>
        </div>
        <p className="text-xs text-muted-foreground">
          Background research missions that run periodically.
        </p>
      </div>

      <div className="flex-1 overflow-y-auto p-3">
        <div className="mb-3 flex gap-2">
          <input
            value={newQuery}
            onChange={(e) => setNewQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && createMission()}
            placeholder="Research topic to monitor…"
            className="flex-1 rounded-md border border-border bg-background px-2 py-1 text-sm outline-none"
          />
          <Button size="icon" className="h-8 w-8" onClick={createMission} disabled={!newQuery.trim()}>
            <Plus size={14} />
          </Button>
        </div>

        <AnimatePresence initial={false}>
          {missions.map((m, i) => (
            <motion.div
              key={m.id}
              layout
              initial={{ opacity: 0, y: 4, scale: 0.97 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -4, scale: 0.97 }}
              transition={{ duration: 0.18, delay: Math.min(i * 0.04, 0.4) }}
              className="mb-2 rounded-md border border-border bg-card p-2 text-xs"
            >
              <div className="flex items-start justify-between">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{m.query}</p>
                  <div className="mt-1 flex items-center gap-2 text-muted-foreground">
                    <span className={`rounded px-1.5 py-0.5 ${
                      m.status === "active"
                        ? "bg-emerald-400/10 text-emerald-400"
                        : "bg-muted text-muted-foreground"
                    }`}>
                      {m.status}
                    </span>
                    {m.schedule && m.schedule !== "none" && (
                      <span className="flex items-center gap-1">
                        <Clock size={10} /> {m.schedule}
                      </span>
                    )}
                  </div>
                </div>
                {m.status === "active" && (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 shrink-0"
                    onClick={() => stopMission(m.id)}
                  >
                    <StopCircle size={14} className="text-red-400" />
                  </Button>
                )}
              </div>
            </motion.div>
          ))}
        </AnimatePresence>

        {!loading && missions.length === 0 && (
          <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
            <FolderKanban size={24} className="mb-2 text-border" />
            <p className="text-sm">No missions yet</p>
            <p className="text-xs">Enter a topic above to start monitoring.</p>
          </div>
        )}
      </div>

      <div className="border-t border-border p-3">
        <Button variant="outline" size="sm" className="w-full gap-1" onClick={loadMissions}>
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} /> Refresh
        </Button>
      </div>
    </div>
  );
}
