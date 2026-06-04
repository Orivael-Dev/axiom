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
