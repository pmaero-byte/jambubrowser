// Memory API helpers — wraps the /v2/memory/* endpoints
import { localFetch } from "./api";
import type { UserProfile, SessionMemory, MemoryHit } from "./types";

export async function getProfile(userId = "default"): Promise<UserProfile> {
  const r = await localFetch(`/v2/memory/profile?user_id=${encodeURIComponent(userId)}`);
  return r.json();
}

export async function updateProfile(profile: Partial<UserProfile> & { user_id: string }): Promise<UserProfile> {
  const r = await localFetch("/v2/memory/profile", {
    method: "PUT",
    body: JSON.stringify(profile),
  });
  return r.json();
}

export async function addInterest(userId: string, interest: string): Promise<UserProfile> {
  const p = await getProfile(userId);
  const interests = Array.from(new Set([...(p.interests || []), interest]));
  return updateProfile({ user_id: userId, interests });
}

export async function listSessions(userId = "default", limit = 20): Promise<SessionMemory[]> {
  const r = await localFetch(`/v2/memory/sessions?user_id=${encodeURIComponent(userId)}&limit=${limit}`);
  const d = await r.json();
  return d.sessions || [];
}

export async function storeMemory(
  userId: string,
  content: string,
  category = "fact",
  importance = 0.5,
): Promise<{ id: number; stored: boolean }> {
  const r = await localFetch("/v2/memory/store", {
    method: "POST",
    body: JSON.stringify({ user_id: userId, content, category, importance }),
  });
  return r.json();
}

export async function recallMemory(userId: string, query: string, k = 5): Promise<MemoryHit[]> {
  const r = await localFetch("/v2/memory/recall", {
    method: "POST",
    body: JSON.stringify({ user_id: userId, query, k }),
  });
  const d = await r.json();
  return d.hits || [];
}

export async function forgetMemory(id: number, userId?: string): Promise<{ deleted: boolean }> {
  const qs = userId ? `?user_id=${encodeURIComponent(userId)}` : "";
  const r = await localFetch(`/v2/memory/${id}${qs}`, { method: "DELETE" });
  return r.json();
}

export async function getMemoryStats(userId = "default"): Promise<{
  profiles: number;
  sessions: number;
  semantic_memories: number;
  procedural_memories: number;
}> {
  const r = await localFetch(`/v2/memory/stats?user_id=${encodeURIComponent(userId)}`);
  return r.json();
}
