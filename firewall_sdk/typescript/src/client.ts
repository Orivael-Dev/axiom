import {
  AxiomFirewallError,
  BlockedError,
  InvalidKeyError,
  NetworkError,
  RateLimitedError,
  ServerError,
} from './errors.js';
import type { CheckResult, ClientOptions } from './types.js';

const VERSION = '0.1.0';
const DEFAULT_BASE_URL = 'https://firewall.orivael.dev';
const DEFAULT_TIMEOUT_MS = 10_000;

/**
 * Axiom Firewall client.
 *
 * ```ts
 * const client = new Client({ apiKey: process.env.AXIOM_KEY! });
 * const result = await client.check('What is the weather today?');
 * if (result.verdict === 'block') {
 *   // refuse to forward to your LLM
 * }
 * ```
 */
export class Client {
  private readonly baseUrl: string;
  private readonly timeout: number;
  private readonly headers: Record<string, string>;

  constructor(options: ClientOptions) {
    if (!options.apiKey) {
      throw new Error('apiKey is required');
    }
    this.baseUrl = (options.baseUrl ?? DEFAULT_BASE_URL).replace(/\/$/, '');
    this.timeout = options.timeout ?? DEFAULT_TIMEOUT_MS;
    let ua = `axiom-firewall-typescript/${VERSION}`;
    if (options.userAgent) ua = `${ua} ${options.userAgent}`;
    this.headers = {
      Authorization: `Bearer ${options.apiKey}`,
      'Content-Type': 'application/json',
      'User-Agent': ua,
    };
  }

  /**
   * Classify `text` and return the verdict + intent.
   * Never throws on block — inspect `result.verdict`.
   */
  async check(text: string): Promise<CheckResult> {
    if (typeof text !== 'string') {
      throw new TypeError('text must be a string');
    }
    return this.post<CheckResult>('/v1/guard/check', { text });
  }

  /**
   * Like `check`, but throws `BlockedError` if verdict === 'block'.
   */
  async checkOrThrow(text: string): Promise<CheckResult> {
    const result = await this.check(text);
    if (result.verdict === 'block') {
      throw new BlockedError(
        result.intent.class,
        result.intent.confidence,
        result.intent.signals,
      );
    }
    return result;
  }

  // ─── HTTP transport ─────────────────────────────────────────────

  private async post<T>(path: string, body: unknown): Promise<T> {
    const url = `${this.baseUrl}${path}`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeout);

    let res: Response;
    try {
      res = await fetch(url, {
        method: 'POST',
        headers: this.headers,
        body: JSON.stringify(body),
        signal: controller.signal,
      });
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        throw new NetworkError(`Request to ${url} timed out after ${this.timeout}ms`);
      }
      throw new NetworkError(`Failed to reach ${url}`, err);
    } finally {
      clearTimeout(timer);
    }

    if (res.status === 200) {
      try {
        return (await res.json()) as T;
      } catch (err) {
        throw new ServerError(`Server returned non-JSON body: ${err}`, res.status);
      }
    }

    const detail = await this.extractDetail(res);
    if (res.status === 401) throw new InvalidKeyError(detail);
    if (res.status === 429) throw new RateLimitedError(detail);
    if (res.status >= 500) throw new ServerError(detail, res.status);
    throw new AxiomFirewallError(detail, res.status);
  }

  private async extractDetail(res: Response): Promise<string> {
    const text = await res.text().catch(() => '');
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed === 'object' && 'detail' in parsed) {
        return String((parsed as { detail: unknown }).detail);
      }
      return text;
    } catch {
      return text || `HTTP ${res.status}`;
    }
  }
}
