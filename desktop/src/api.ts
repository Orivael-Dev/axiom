// Typed client for the AX OS local service. The desktop shell talks to the
// same FastAPI endpoints as the Streamlit app — set VITE_AX_OS_API to override.
import type { WorkspacePlan, AuditTrail, Health, Agent } from "./types";

export const BASE: string =
  (import.meta.env.VITE_AX_OS_API as string | undefined) ?? "http://127.0.0.1:8800";

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(BASE + path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`);
  return (await r.json()) as T;
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(BASE + path);
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`);
  return (await r.json()) as T;
}

export const api = {
  health: () => get<Health>("/health"),
  assemble: (goal: string, domain?: string) =>
    post<WorkspacePlan>("/assemble", domain ? { goal, domain } : { goal }),
  audit: (limit = 10) => get<AuditTrail>(`/audit?limit=${limit}`),
  agents: () => get<{ agents: Agent[] }>("/marketplace/agents"),
  approve: (pair_id: string) => post<unknown>("/marketplace/approve", { pair_id, actor: "human" }),
  revoke: (pair_id: string) => post<unknown>("/marketplace/revoke", { pair_id, actor: "human" }),
};
