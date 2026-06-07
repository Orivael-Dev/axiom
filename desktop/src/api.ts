// Typed client for the AX OS local service. The desktop shell talks to the
// same FastAPI endpoints as the Streamlit app — set VITE_AX_OS_API to override.
import type {
  WorkspacePlan, AuditTrail, Health, Agent, Weather, ImmuneResult,
  LlmSettings, LlmProbe, SearchResults, CompanionReply, VoiceSettings,
  AnticipationSettings,
} from "./types";

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
  weather: (lat?: number, lon?: number) =>
    get<Weather>(`/widgets/weather${lat != null && lon != null ? `?lat=${lat}&lon=${lon}` : ""}`),
  immuneScan: (payload: string, vector?: string) =>
    post<ImmuneResult>("/immune/scan", vector ? { payload, vector } : { payload }),
  getLlm: () => get<LlmSettings>("/settings/llm"),
  setLlm: (patch: Partial<LlmSettings> & { api_key?: string }) =>
    post<LlmSettings>("/settings/llm", patch),
  testLlm: () => post<LlmProbe>("/settings/llm/test", {}),
  search: (q: string, n = 5) =>
    get<SearchResults>(`/search?q=${encodeURIComponent(q)}&n=${n}`),
  companion: (text: string, reset = false) =>
    post<CompanionReply>("/companion/say", { text, reset }),
  getVoice: () => get<VoiceSettings>("/settings/voice"),
  setVoice: (patch: Partial<VoiceSettings>) => post<VoiceSettings>("/settings/voice", patch),
  tts: (text: string) =>
    post<{ ok: boolean; audio_b64?: string; mime?: string; reason?: string }>("/tts", { text }),
  getAnticipation: () => get<AnticipationSettings>("/settings/anticipation"),
  setAnticipation: (patch: Partial<AnticipationSettings>) =>
    post<AnticipationSettings>("/settings/anticipation", patch),
};
