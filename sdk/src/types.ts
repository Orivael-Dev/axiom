// ── Core enumerations ────────────────────────────────────────────────────────

export type Verdict   = 'BLOCKED' | 'VERIFIED' | 'SUSPICIOUS';
export type Direction = 'INPUT' | 'OUTPUT';
export type GuardMode = 'INPUT_FILTER' | 'OUTPUT_FILTER' | 'BIDIRECTIONAL';

// ── Manifest ─────────────────────────────────────────────────────────────────

export interface Manifest {
  manifest_id:                string;
  manifest_version:           string;
  engine:                     string;
  timestamp:                  string;
  request_id:                 string;
  direction:                  Direction;
  model:                      string | null;
  latency_ms:                 number | null;
  text_length:                number;
  text_preview:               string;
  verdict:                    Verdict;
  constitutional_block:       string | null;
  confidence:                 number;
  cannot_override:            boolean;
  ftc_reportable:             boolean;
  pattern_matched:            string | null;
  agent:                      string;
  refer_physician:            boolean;
  warning:                    string | null;
  constitutional_block_active: boolean;
  safe_to_proceed:            boolean;
  signature:                  string;
  ftc_report_generated?:      boolean;
  ftc_report_id?:             string;
}

// ── Request options ───────────────────────────────────────────────────────────

export interface CheckOptions {
  agents?:    string[];
  direction?: Direction;
  metadata?:  Record<string, unknown>;
}

export interface ProxyOptions {
  model?:    string;
  system?:   string;
  agents?:   string[];
  metadata?: Record<string, unknown>;
}

export interface ListManifestsOptions {
  limit?:   number;
  verdict?: Verdict;
}

export interface ConfigureOptions {
  mode?:               GuardMode;
  active_agents?:      string[];
  block_on_suspicious?: boolean;
  anthropic_model?:    string;
}

// ── Response shapes ───────────────────────────────────────────────────────────

export interface CheckResult {
  request_id:  string;
  verdict:     Verdict;
  blocked:     boolean;
  manifest:    Manifest;
  latency_ms:  number;
}

export interface FilterResult extends CheckResult {
  proceed:            boolean;
  guidance:           string;
  corrected_text?:    string;
  original_suppressed?: boolean;
}

export interface ProxyResult {
  request_id:        string;
  model:             string;
  input_manifest:    Manifest | null;
  output_manifest:   Manifest | null;
  response:          string | null;
  blocked_at:        'INPUT' | 'OUTPUT' | null;
  blocked_reason:    string | null;
  corrected_text?:   string;
  total_latency_ms:  number;
  llm_latency_ms?:   number;
  output_verdict?:   Verdict;
  constitutional:    boolean;
  ftc_report_id?:    string;
}

export interface StatusResult {
  status:           string;
  version:          string;
  engine:           string;
  mode:             GuardMode;
  active_agents:    string[];
  manifests_stored: number;
  anthropic_ready:  boolean;
  timestamp:        string;
  patent:           string;
  install:          string;
}

export interface AgentInfo {
  description: string;
  certified:   string;
  blocks:      string[];
}

export interface AgentsResult {
  available_agents: Record<string, AgentInfo>;
  active_agents:    string[];
  mode:             GuardMode;
}

export interface ManifestList {
  total:     number;
  returned:  number;
  manifests: Manifest[];
}

export interface ConfigResult {
  status: string;
  config: {
    mode:                 GuardMode;
    active_agents:        string[];
    block_on_suspicious:  boolean;
    log_all:              boolean;
    anthropic_model:      string;
  };
}
