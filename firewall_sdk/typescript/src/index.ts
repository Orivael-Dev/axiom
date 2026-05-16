/**
 * @axiom/firewall — Official TypeScript client for Axiom Intent Firewall.
 *
 * Block harm, deception, and manipulation in your LLM calls with a single
 * HMAC-signed verdict.
 *
 * @example
 * ```ts
 * import { Client } from '@axiom/firewall';
 *
 * const client = new Client({ apiKey: process.env.AXIOM_KEY! });
 * const result = await client.check('What is the weather today?');
 *
 * if (result.verdict === 'block') {
 *   // refuse to forward to your LLM
 * }
 * ```
 */
export { Client } from './client.js';
export {
  AxiomFirewallError,
  BlockedError,
  InvalidKeyError,
  NetworkError,
  RateLimitedError,
  ServerError,
} from './errors.js';
export type {
  CheckResult,
  ClientOptions,
  Intent,
  IntentClass,
  Verdict,
} from './types.js';
