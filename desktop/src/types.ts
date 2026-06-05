// Mirrors the AX OS local service (aui/server.py) response shapes.
export type PanelStatus = "ready" | "pending" | "blocked";

export interface Panel {
  kind: string;
  title: string;
  items: string[];
  status: PanelStatus;
}

export interface WorkspacePlan {
  goal: string;
  allowed: boolean;
  scene: string;
  panels: Panel[];
  signature: string;
  planner?: "local" | "cloud";
}

export interface AuditEvent {
  event_type: string;
  outcome: string;
}

export interface AuditTrail {
  count: number;
  all_verified: boolean;
  events: AuditEvent[];
}

export interface Health {
  ok: boolean;
  tools: string[];
}

export interface Agent {
  agent: string;
  pair_id: string;
  authorized: boolean;
  state: string;
}

export interface Weather {
  ok: boolean;
  latitude: number;
  longitude: number;
  temperature_c?: number;
  wind_kph?: number;
  is_day?: boolean;
  code?: number;
  description?: string;
  timezone?: string;
  updated?: string;
  error?: string;
}

export interface ImmuneResult {
  detected: boolean;
  detection_method: string;
  confidence: number;
  cluster_id: string;
  attack_vector: string;
  fix_proposal: string;
}

export interface LlmSettings {
  enabled: boolean;
  base_url: string;
  model: string;
  api_key_set: boolean;
}

export interface LlmProbe {
  ok: boolean;
  models?: string[];
  model?: string;
  model_present?: boolean;
  error?: string;
  base_url?: string;
}
