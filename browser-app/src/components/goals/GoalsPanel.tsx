import { useCallback, useEffect, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  Target,
  Plus,
  CheckCircle2,
  Ban,
  RefreshCw,
  ChevronDown,
  ChevronRight,
} from "lucide-react";
import { Button } from "../ui/button";
import { localFetch } from "../../utils/api";

interface Goal {
  id: string;
  title: string;
  status: string;
  priority: number;
  approaches_tried: number;
}

interface ActiveGoal extends Goal {
  description: string;
  approaches_succeeded: number;
  success_criteria: string[];
  constraints: string[];
}

interface Approach {
  id: string;
  iteration: number;
  strategy: string;
  hypothesis?: string;
  result?: string;
  learning?: string;
}

/**
 * Goals — surface for the sovereign goal orchestrator (`/goal/*`).
 *
 * The orchestrator had ~600 LOC and a full REST surface but no UI at all, so
 * goal-setting was reachable only over raw HTTP. This panel covers the
 * day-to-day loop: see the active goal, create one, inspect the approaches
 * tried against it, and close it out as achieved or blocked.
 */
export function GoalsPanel() {
  const [goals, setGoals] = useState<Goal[]>([]);
  const [activeGoal, setActiveGoal] = useState<ActiveGoal | null>(null);
  const [approaches, setApproaches] = useState<Approach[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [criteria, setCriteria] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [listRes, activeRes] = await Promise.all([
        localFetch("/goal/list"),
        localFetch("/goal/active"),
      ]);
      const listData = await listRes.json();
      const activeData = await activeRes.json();
      const list: Goal[] = listData.goals || [];
      setGoals(list);
      setActiveGoal(activeData.active ? (activeData.goal as ActiveGoal) : null);
      // Keep the selection pointing at something real: follow the active goal,
      // and drop the selection if a refresh made it disappear.
      setSelectedId((prev) => {
        if (prev && list.some((g) => g.id === prev)) return prev;
        return activeData.goal?.id ?? list[0]?.id ?? null;
      });
    } catch (e) {
      console.error(e);
      setError("Could not reach the goal orchestrator.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!selectedId) {
      setApproaches([]);
      return;
    }
    let cancelled = false;
    localFetch(`/goal/approaches?goal_id=${encodeURIComponent(selectedId)}&limit=20`)
      .then((r) => r.json())
      .then((data) => {
        if (!cancelled) setApproaches(data.approaches || []);
      })
      .catch((e) => {
        console.error(e);
        if (!cancelled) setApproaches([]);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const createGoal = async () => {
    if (!title.trim()) return;
    setError(null);
    try {
      const res = await localFetch("/goal/set", {
        method: "POST",
        body: JSON.stringify({
          title: title.trim(),
          description: description.trim(),
          success_criteria: criteria
            .split("\n")
            .map((s) => s.trim())
            .filter(Boolean),
        }),
      });
      const data = await res.json();
      if (data.status === "goal_set") {
        setTitle("");
        setDescription("");
        setCriteria("");
        setShowForm(false);
        await load();
      } else {
        setError("The engine rejected that goal.");
      }
    } catch (e) {
      console.error(e);
      setError("Could not create the goal.");
    }
  };

  const closeGoal = async (id: string, outcome: "achieve" | "block") => {
    setError(null);
    const endpoint =
      outcome === "achieve"
        ? `/goal/achieve?goal_id=${encodeURIComponent(id)}`
        : `/goal/block?goal_id=${encodeURIComponent(id)}`;
    try {
      await localFetch(endpoint, { method: "POST" });
      await load();
    } catch (e) {
      console.error(e);
      setError(`Could not ${outcome === "achieve" ? "close out" : "block"} that goal.`);
    }
  };

  const otherGoals = goals.filter((g) => g.id !== activeGoal?.id);
  const activeExpanded = activeGoal !== null && selectedId === activeGoal.id;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex items-center justify-between border-b border-border px-4 py-3">
        <div className="flex items-center gap-2">
          <Target className="h-4 w-4 text-primary" />
          <h2 className="text-sm font-semibold">Goals</h2>
          {goals.length > 0 && (
            <span className="text-xs text-muted-foreground">{goals.length}</span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="sm" onClick={load} title="Refresh">
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setShowForm((v) => !v)} title="New goal">
            <Plus className="h-4 w-4" />
          </Button>
        </div>
      </header>

      {error && (
        <div className="border-b border-border bg-destructive/10 px-4 py-2 text-xs text-destructive">
          {error}
        </div>
      )}

      <AnimatePresence>
        {showForm && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden border-b border-border"
          >
            <div className="space-y-2 px-4 py-3">
              <input
                aria-label="Goal title"
                data-testid="goal-title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="What should the browser be working toward?"
                className="w-full rounded border border-border bg-background px-2 py-1.5 text-sm"
              />
              <input
                aria-label="Goal description"
                data-testid="goal-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Description"
                className="w-full rounded border border-border bg-background px-2 py-1.5 text-sm"
              />
              <textarea
                aria-label="Success criteria"
                data-testid="goal-criteria"
                value={criteria}
                onChange={(e) => setCriteria(e.target.value)}
                placeholder="Success criteria, one per line"
                rows={2}
                className="w-full rounded border border-border bg-background px-2 py-1.5 text-sm"
              />
              <div className="flex justify-end gap-2">
                <Button variant="ghost" size="sm" onClick={() => setShowForm(false)}>
                  Cancel
                </Button>
                <Button size="sm" onClick={createGoal} disabled={!title.trim()}>
                  Set goal
                </Button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <div className="flex-1 overflow-y-auto">
        {goals.length === 0 && !loading && (
          <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
            <Target className="h-8 w-8 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">
              No goals yet. Set one to give the browser something to persist against.
            </p>
          </div>
        )}

        {activeGoal && (
          <div className="border-b border-border bg-primary/5">
            <div className="flex items-start gap-2 px-4 py-3">
              <button
                data-testid="goal-row-toggle"
                onClick={() => setSelectedId(activeExpanded ? null : activeGoal.id)}
                className="flex min-w-0 flex-1 items-start gap-2 text-left"
              >
                {activeExpanded ? (
                  <ChevronDown className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
                ) : (
                  <ChevronRight className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
                )}
                <span className="min-w-0 flex-1">
                  <span className="block text-[10px] font-medium uppercase tracking-wider text-primary">
                    Active
                  </span>
                  <span className="block truncate text-sm font-medium">{activeGoal.title}</span>
                </span>
              </button>
              <span className="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
                p{activeGoal.priority}
              </span>
              <div className="flex shrink-0 items-center gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  title="Mark achieved"
                  onClick={() => closeGoal(activeGoal.id, "achieve")}
                >
                  <CheckCircle2 className="h-3.5 w-3.5 text-success" />
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  title="Block"
                  onClick={() => closeGoal(activeGoal.id, "block")}
                >
                  <Ban className="h-3.5 w-3.5 text-destructive" />
                </Button>
              </div>
            </div>
            {activeGoal.description && (
              <p className="px-4 pb-2 text-xs text-muted-foreground">{activeGoal.description}</p>
            )}
            <div className="flex flex-wrap gap-x-3 gap-y-1 px-4 pb-3 text-[11px] text-muted-foreground">
              <span>
                {activeGoal.approaches_succeeded}/{activeGoal.approaches_tried} approaches worked
              </span>
              {activeGoal.success_criteria?.length > 0 && (
                <span>{activeGoal.success_criteria.length} success criteria</span>
              )}
              {activeGoal.constraints?.length > 0 && (
                <span>{activeGoal.constraints.length} constraints</span>
              )}
            </div>
            {activeExpanded && <ApproachList approaches={approaches} />}
          </div>
        )}

        {otherGoals.map((goal) => {
          const expanded = selectedId === goal.id;
          return (
            <div key={goal.id} className="border-b border-border">
              <button
                data-testid="goal-row-toggle"
                onClick={() => setSelectedId(expanded ? null : goal.id)}
                className="flex w-full items-center gap-2 px-4 py-2 text-left hover:bg-muted/50"
              >
                {expanded ? (
                  <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                )}
                <span className="truncate text-sm">{goal.title}</span>
                <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                  {goal.status}
                </span>
                <span className="ml-auto shrink-0 text-[11px] text-muted-foreground">
                  {goal.approaches_tried} tries
                </span>
              </button>
              {expanded && <ApproachList approaches={approaches} />}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Approaches tried against a goal, each annotated with its outcome + learning. */
function ApproachList({ approaches }: { approaches: Approach[] }) {
  return (
    <div className="space-y-1 bg-muted/20 px-4 py-2">
      {approaches.length === 0 ? (
        <p className="text-xs text-muted-foreground">No approaches recorded yet.</p>
      ) : (
        approaches.map((a) => (
          <div key={a.id} className="text-xs">
            <span className="text-muted-foreground">#{a.iteration}</span> <span>{a.strategy}</span>
            {a.result && (
              <span
                className={`ml-1 rounded px-1 py-0.5 text-[10px] ${
                  a.result === "success"
                    ? "bg-success/10 text-success"
                    : a.result === "partial"
                      ? "bg-warning/10 text-warning"
                      : "bg-destructive/10 text-destructive"
                }`}
              >
                {a.result}
              </span>
            )}
            {a.learning && <p className="text-[11px] text-muted-foreground">{a.learning}</p>}
          </div>
        ))
      )}
    </div>
  );
}
