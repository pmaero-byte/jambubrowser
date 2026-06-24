import { useState, useEffect, useCallback } from "react";
import { motion } from "motion/react";
import {
  Users, Plus, UserPlus, CheckCircle, Clock,
  Trash2, Activity,
} from "lucide-react";
import { Button } from "../ui/button";
import { localFetch } from "../../utils/api";

interface Team {
  id: number;
  name: string;
  slug: string;
  owner: string;
  plan: string;
  member_count: number;
}

interface TeamMember {
  id: number;
  email: string;
  name: string | null;
  role: string;
  joined_at: number | null;
}

interface Assignment {
  id: number;
  audit_id: number;
  finding_id: string;
  assigned_to: string;
  assigned_by: string;
  status: string;
  notes: string | null;
  created_at: number;
  resolved_at: number | null;
}

interface Activity {
  id: number;
  actor: string;
  action: string;
  target: string;
  details: string | null;
  created_at: number;
}

export function TeamPanel() {
  const [teams, setTeams] = useState<Team[]>([]);
  const [selectedTeam, setSelectedTeam] = useState<Team | null>(null);
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [newTeamName, setNewTeamName] = useState("");
  const [newMemberEmail, setNewMemberEmail] = useState("");
  const [activeTab, setActiveTab] = useState<"members" | "assignments" | "activity">("members");

  const loadTeams = useCallback(async () => {
    try {
      const res = await localFetch("/teams/list");
      const data = await res.json();
      setTeams(data.teams || []);
    } catch (e) {
      console.error(e);
    }
  }, []);

  const loadTeamData = useCallback(async (teamId: number) => {
    try {
      const [membersRes, assignmentsRes, activityRes, statsRes] = await Promise.all([
        localFetch(`/teams/${teamId}/members`),
        localFetch(`/teams/${teamId}/assignments`),
        localFetch(`/teams/${teamId}/activity`),
        localFetch(`/teams/${teamId}/stats`),
      ]);
      const [membersData, assignmentsData, activityData, statsData] = await Promise.all([
        membersRes.json(),
        assignmentsRes.json(),
        activityRes.json(),
        statsRes.json(),
      ]);
      setMembers(membersData.members || []);
      setAssignments(assignmentsData.assignments || []);
      setActivity(activityData.activity || []);
      setStats(statsData);
    } catch (e) {
      console.error(e);
    }
  }, []);

  useEffect(() => {
    loadTeams();
  }, [loadTeams]);

  useEffect(() => {
    if (selectedTeam) {
      loadTeamData(selectedTeam.id);
    }
  }, [selectedTeam, loadTeamData]);

  const createTeam = async () => {
    if (!newTeamName.trim()) return;
    try {
      await localFetch("/teams/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newTeamName }),
      });
      setNewTeamName("");
      loadTeams();
    } catch (e) {
      console.error(e);
    }
  };

  const addMember = async () => {
    if (!newMemberEmail.trim() || !selectedTeam) return;
    try {
      await localFetch(`/teams/${selectedTeam.id}/members`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: newMemberEmail }),
      });
      setNewMemberEmail("");
      loadTeamData(selectedTeam.id);
    } catch (e) {
      console.error(e);
    }
  };

  const removeMember = async (email: string) => {
    if (!selectedTeam) return;
    try {
      await localFetch(`/teams/${selectedTeam.id}/members/${email}`, {
        method: "DELETE",
      });
      loadTeamData(selectedTeam.id);
    } catch (e) {
      console.error(e);
    }
  };

  const resolveAssignment = async (assignmentId: number) => {
    if (!selectedTeam) return;
    try {
      await localFetch(`/teams/${selectedTeam.id}/assignments/${assignmentId}/resolve`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resolved_by: "default" }),
      });
      loadTeamData(selectedTeam.id);
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div className="flex h-full overflow-hidden">
      {/* Sidebar: Team list */}
      <div className="w-64 shrink-0 border-r border-white/10 flex flex-col">
        <div className="p-3 border-b border-white/10">
          <h3 className="text-sm font-medium mb-2">Teams</h3>
          <div className="flex gap-2">
            <input
              type="text"
              placeholder="New team name..."
              value={newTeamName}
              onChange={(e) => setNewTeamName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && createTeam()}
              className="flex-1 rounded border border-white/10 bg-white/5 px-2 py-1 text-xs outline-none focus:border-blue-500/50"
            />
            <Button size="sm" onClick={createTeam} disabled={!newTeamName.trim()} className="shrink-0">
              <Plus className="h-3 w-3" />
            </Button>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto">
          {teams.map((team) => (
            <button
              key={team.id}
              onClick={() => setSelectedTeam(team)}
              className={`w-full text-left px-3 py-2 text-sm transition-colors ${
                selectedTeam?.id === team.id
                  ? "bg-white/10 text-white"
                  : "text-muted-foreground hover:bg-white/5"
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="font-medium">{team.name}</span>
                <span className="text-xs bg-white/10 rounded px-1.5 py-0.5">{team.member_count}</span>
              </div>
              <div className="text-xs text-muted-foreground mt-0.5">{team.plan}</div>
            </button>
          ))}
          {teams.length === 0 && (
            <div className="p-3 text-xs text-muted-foreground text-center">
              No teams yet. Create one above.
            </div>
          )}
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {!selectedTeam ? (
          <div className="flex-1 flex items-center justify-center text-muted-foreground">
            <div className="text-center">
              <Users className="h-12 w-12 mx-auto mb-3 opacity-30" />
              <p className="text-sm">Select a team or create a new one</p>
            </div>
          </div>
        ) : (
          <>
            {/* Team header */}
            <div className="shrink-0 border-b border-white/10 p-4">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-medium">{selectedTeam.name}</h2>
                  <p className="text-xs text-muted-foreground">{selectedTeam.plan} plan · {selectedTeam.member_count} members</p>
                </div>
                {stats && (
                  <div className="flex gap-3">
                    <div className="text-center">
                      <div className="text-lg font-bold text-orange-400">{stats.open}</div>
                      <div className="text-[10px] text-muted-foreground">Open</div>
                    </div>
                    <div className="text-center">
                      <div className="text-lg font-bold text-green-400">{stats.resolved}</div>
                      <div className="text-[10px] text-muted-foreground">Resolved</div>
                    </div>
                    <div className="text-center">
                      <div className="text-lg font-bold">{stats.total_assignments}</div>
                      <div className="text-[10px] text-muted-foreground">Total</div>
                    </div>
                  </div>
                )}
              </div>

              {/* Tabs */}
              <div className="flex gap-1 mt-3">
                {(["members", "assignments", "activity"] as const).map((tab) => (
                  <button
                    key={tab}
                    onClick={() => setActiveTab(tab)}
                    className={`px-3 py-1.5 text-xs rounded transition-colors ${
                      activeTab === tab ? "bg-white/10 text-white" : "text-muted-foreground hover:bg-white/5"
                    }`}
                  >
                    {tab.charAt(0).toUpperCase() + tab.slice(1)}
                  </button>
                ))}
              </div>
            </div>

            {/* Tab content */}
            <div className="flex-1 overflow-y-auto p-4">
              {activeTab === "members" && (
                <div className="space-y-3">
                  <div className="flex gap-2">
                    <input
                      type="email"
                      placeholder="member@example.com"
                      value={newMemberEmail}
                      onChange={(e) => setNewMemberEmail(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && addMember()}
                      className="flex-1 rounded border border-white/10 bg-white/5 px-3 py-1.5 text-sm outline-none focus:border-blue-500/50"
                    />
                    <Button size="sm" onClick={addMember} disabled={!newMemberEmail.trim()}>
                      <UserPlus className="mr-1 h-3 w-3" /> Invite
                    </Button>
                  </div>
                  {members.map((m) => (
                    <motion.div
                      key={m.id}
                      initial={{ opacity: 0, y: 5 }}
                      animate={{ opacity: 1, y: 0 }}
                      className="flex items-center justify-between rounded-lg border border-white/10 bg-white/5 p-3"
                    >
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-full bg-blue-500/20 flex items-center justify-center text-blue-400 text-xs font-medium">
                          {(m.name || m.email).charAt(0).toUpperCase()}
                        </div>
                        <div>
                          <div className="text-sm font-medium">{m.name || m.email}</div>
                          <div className="text-xs text-muted-foreground">{m.email}</div>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className={`text-xs px-2 py-0.5 rounded ${
                          m.role === "owner" ? "bg-purple-500/20 text-purple-400" : "bg-white/10 text-muted-foreground"
                        }`}>
                          {m.role}
                        </span>
                        {m.role !== "owner" && (
                          <button
                            onClick={() => removeMember(m.email)}
                            className="p-1 text-muted-foreground hover:text-red-400 transition-colors"
                          >
                            <Trash2 className="h-3 w-3" />
                          </button>
                        )}
                      </div>
                    </motion.div>
                  ))}
                  {members.length === 0 && (
                    <div className="text-center text-sm text-muted-foreground py-8">
                      No members yet. Invite someone above.
                    </div>
                  )}
                </div>
              )}

              {activeTab === "assignments" && (
                <div className="space-y-2">
                  {assignments.map((a) => (
                    <motion.div
                      key={a.id}
                      initial={{ opacity: 0, y: 5 }}
                      animate={{ opacity: 1, y: 0 }}
                      className={`rounded-lg border p-3 ${
                        a.status === "resolved"
                          ? "border-green-500/20 bg-green-500/5"
                          : "border-white/10 bg-white/5"
                      }`}
                    >
                      <div className="flex items-start justify-between">
                        <div>
                          <div className="flex items-center gap-2">
                            {a.status === "resolved" ? (
                              <CheckCircle className="h-4 w-4 text-green-400" />
                            ) : (
                              <Clock className="h-4 w-4 text-orange-400" />
                            )}
                            <span className="text-sm font-medium">{a.finding_id}</span>
                            <span className="text-xs text-muted-foreground">→ {a.assigned_to}</span>
                          </div>
                          {a.notes && <p className="text-xs text-muted-foreground mt-1">{a.notes}</p>}
                        </div>
                        {a.status === "open" && (
                          <Button size="sm" variant="ghost" onClick={() => resolveAssignment(a.id)}>
                            <CheckCircle className="mr-1 h-3 w-3" /> Resolve
                          </Button>
                        )}
                      </div>
                    </motion.div>
                  ))}
                  {assignments.length === 0 && (
                    <div className="text-center text-sm text-muted-foreground py-8">
                      No assignments yet. Assign findings from the audit dashboard.
                    </div>
                  )}
                </div>
              )}

              {activeTab === "activity" && (
                <div className="space-y-2">
                  {activity.map((a) => (
                    <motion.div
                      key={a.id}
                      initial={{ opacity: 0, y: 5 }}
                      animate={{ opacity: 1, y: 0 }}
                      className="flex items-start gap-3 rounded-lg border border-white/10 bg-white/5 p-3"
                    >
                      <Activity className="h-4 w-4 text-muted-foreground mt-0.5" />
                      <div>
                        <div className="text-sm">
                          <span className="font-medium">{a.actor}</span>
                          <span className="text-muted-foreground"> {a.action} </span>
                          <span className="font-medium">{a.target}</span>
                        </div>
                        <div className="text-xs text-muted-foreground mt-0.5">
                          {new Date(a.created_at * 1000).toLocaleString()}
                        </div>
                      </div>
                    </motion.div>
                  ))}
                  {activity.length === 0 && (
                    <div className="text-center text-sm text-muted-foreground py-8">
                      No activity yet.
                    </div>
                  )}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
