/**
 * Intent classes returned by the Firewall classifier.
 * Canonical six-class taxonomy (see docs/PHASE_1_DECISIONS.md §2).
 */
export type IntentClass =
  | 'INFORM'
  | 'CLARIFY'
  | 'REFUSE'
  | 'HARM'
  | 'DECEIVE'
  | 'UNCERTAIN';

export type Verdict = 'allow' | 'block';

/** Constitutional intent classification of a checked prompt. */
export interface Intent {
  /** Six-class taxonomy. */
  class: IntentClass;
  /** 0.0 - 1.0 confidence in the classification. */
  confidence: number;
  /** Pattern signals that contributed to the verdict (e.g. ["harm:1"]). */
  signals: string[];
  /** HMAC-SHA256 of the verdict for audit replay. */
  signature: string;
}

/** Response shape from POST /v1/guard/check. */
export interface CheckResult {
  verdict: Verdict;
  intent: Intent;
}

/** Constructor options for the SDK client. */
export interface ClientOptions {
  /** Your `axfw_...` API key. */
  apiKey: string;
  /** Override for self-hosted or staging deployments. Default: https://firewall.orivael.dev */
  baseUrl?: string;
  /** Per-request timeout in milliseconds. Default: 10000 */
  timeout?: number;
  /** Optional UA suffix appended to the default SDK User-Agent. */
  userAgent?: string;
}
