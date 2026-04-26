import { AxiomGuardError, BlockedError, NetworkError } from './errors.js';
import type {
  CheckOptions,
  CheckResult,
  ConfigResult,
  ConfigureOptions,
  FilterResult,
  AgentsResult,
  ListManifestsOptions,
  Manifest,
  ManifestList,
  ProxyOptions,
  ProxyResult,
  StatusResult,
} from './types.js';

export * from './types.js';
export { AxiomGuardError, BlockedError, NetworkError };

// ── Client options ────────────────────────────────────────────────────────────

export interface AxiomGuardOptions {
  /** Base URL of the running Guard API, e.g. 'http://localhost:8001' */
  baseUrl: string;
  /** Request timeout in milliseconds. Default: 10000 */
  timeout?: number;
  /** Additional headers to send with every request */
  headers?: Record<string, string>;
}

// ── AxiomGuard client ─────────────────────────────────────────────────────────

export class AxiomGuard {
  private readonly baseUrl: string;
  private readonly timeout: number;
  private readonly headers: Record<string, string>;

  constructor(options: AxiomGuardOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, '');
    this.timeout = options.timeout ?? 10_000;
    this.headers = {
      'Content-Type': 'application/json',
      ...options.headers,
    };
  }

  // ── Private helpers ─────────────────────────────────────────────────────────

  private async request<T>(
    method: 'GET' | 'POST',
    path: string,
    body?: unknown,
  ): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeout);

    try {
      const res = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers: this.headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });

      if (!res.ok) {
        const text = await res.text().catch(() => '');
        throw new AxiomGuardError(
          `Guard API returned ${res.status}: ${text}`,
          res.status,
        );
      }

      return (await res.json()) as T;
    } catch (err) {
      if (err instanceof AxiomGuardError) throw err;
      if (err instanceof Error && err.name === 'AbortError') {
        throw new NetworkError(`Request timed out after ${this.timeout}ms`);
      }
      throw new NetworkError(
        `Failed to reach Guard API at ${this.baseUrl}`,
        err,
      );
    } finally {
      clearTimeout(timer);
    }
  }

  // ── Public API ──────────────────────────────────────────────────────────────

  /**
   * Evaluate any text against constitutional rules.
   * No LLM call — pure pattern matching, ~2ms latency.
   */
  async check(text: string, options: CheckOptions = {}): Promise<CheckResult> {
    return this.request<CheckResult>('POST', '/guard/check', {
      text,
      direction: options.direction ?? 'INPUT',
      agents: options.agents,
      metadata: options.metadata,
    });
  }

  /**
   * Input filter — screen a prompt before sending to your LLM.
   * Throws `BlockedError` if the prompt should not proceed.
   */
  async input(text: string, options: CheckOptions = {}): Promise<FilterResult> {
    const result = await this.request<FilterResult>('POST', '/guard/input', {
      text,
      direction: 'INPUT',
      agents: options.agents,
      metadata: options.metadata,
    });

    if (!result.proceed) {
      throw new BlockedError(
        result.manifest,
        result.manifest.constitutional_block ?? 'UNKNOWN_BLOCK',
      );
    }

    return result;
  }

  /**
   * Output filter — screen a model response before returning to your user.
   * Throws `BlockedError` if the output should be suppressed.
   */
  async output(text: string, options: CheckOptions = {}): Promise<FilterResult> {
    const result = await this.request<FilterResult>('POST', '/guard/output', {
      text,
      direction: 'OUTPUT',
      agents: options.agents,
      metadata: options.metadata,
    });

    if (!result.proceed) {
      throw new BlockedError(
        result.manifest,
        result.manifest.constitutional_block ?? 'UNKNOWN_BLOCK',
      );
    }

    return result;
  }

  /**
   * Full proxy — AXIOM sits between your user and an LLM.
   * Runs input check → LLM call → output check.
   * Requires ANTHROPIC_API_KEY set on the Guard API server.
   */
  async proxy(prompt: string, options: ProxyOptions = {}): Promise<ProxyResult> {
    return this.request<ProxyResult>('POST', '/guard/proxy', {
      prompt,
      model: options.model,
      system: options.system,
      agents: options.agents,
      metadata: options.metadata,
    });
  }

  /**
   * Health check and configuration summary.
   */
  async status(): Promise<StatusResult> {
    return this.request<StatusResult>('GET', '/guard/status');
  }

  /**
   * Retrieve a signed manifest by ID.
   * Manifests are the permanent audit trail for every Guard decision.
   */
  async getManifest(manifestId: string): Promise<Manifest> {
    return this.request<Manifest>('GET', `/guard/manifest/${encodeURIComponent(manifestId)}`);
  }

  /**
   * List recent manifests, optionally filtered by verdict.
   */
  async listManifests(options: ListManifestsOptions = {}): Promise<ManifestList> {
    const params = new URLSearchParams();
    if (options.limit !== undefined) params.set('limit', String(options.limit));
    if (options.verdict)            params.set('verdict', options.verdict);
    const qs = params.toString();
    return this.request<ManifestList>('GET', `/guard/manifests${qs ? `?${qs}` : ''}`);
  }

  /**
   * Update Guard configuration — mode, active agents, model.
   */
  async configure(config: ConfigureOptions): Promise<ConfigResult> {
    return this.request<ConfigResult>('POST', '/guard/configure', config);
  }

  /**
   * List available constitutional agents and their rule sets.
   */
  async agents(): Promise<AgentsResult> {
    return this.request<AgentsResult>('GET', '/guard/agents');
  }
}
