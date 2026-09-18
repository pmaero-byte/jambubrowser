/**
 * Human-takeover coordination with the Python engine.
 *
 * Flips the agent session's `human_takeover` flag so the flow runner and
 * session actions refuse mutations while a person drives this pane.
 * Observation (snapshots, assertions, screenshots) stays allowed.
 */
import { localFetch } from "../../utils/api";

export const TAKEOVER_SESSION_KEY = "jambu.takeoverSessionId";

export function savedTakeoverSessionId(): string {
  try {
    return localStorage.getItem(TAKEOVER_SESSION_KEY) ?? "";
  } catch {
    return "";
  }
}

export function saveTakeoverSessionId(sessionId: string): void {
  try {
    if (sessionId) localStorage.setItem(TAKEOVER_SESSION_KEY, sessionId);
    else localStorage.removeItem(TAKEOVER_SESSION_KEY);
  } catch {
    /* storage unavailable — session id is simply forgotten */
  }
}

/** Returns true on success; false when no session id is set or the call fails. */
export async function setAgentTakeover(sessionId: string, active: boolean): Promise<boolean> {
  if (!sessionId) return false;
  try {
    const res = await localFetch(
      `/browser/sessions/${encodeURIComponent(sessionId)}/takeover`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ active }),
      },
    );
    return res.ok;
  } catch {
    return false;
  }
}
