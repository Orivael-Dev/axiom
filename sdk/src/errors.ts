import type { Manifest } from './types.js';

export class AxiomGuardError extends Error {
  constructor(
    message: string,
    public readonly statusCode?: number,
  ) {
    super(message);
    this.name = 'AxiomGuardError';
  }
}

/**
 * Thrown by `guard.input()` and `guard.output()` when `proceed === false`.
 * Allows callers to use try/catch instead of checking `.proceed`.
 *
 * @example
 * try {
 *   await guard.input('IRS agent demands gift card payment');
 * } catch (e) {
 *   if (e instanceof BlockedError) {
 *     console.log(e.constitutional_block); // "IRS_PAYMENT_DEMAND"
 *     console.log(e.manifest.signature);   // "hmac-sha256:..."
 *   }
 * }
 */
export class BlockedError extends AxiomGuardError {
  constructor(
    public readonly manifest: Manifest,
    public readonly constitutional_block: string,
  ) {
    super(
      `Blocked by AXIOM Guard — ${constitutional_block} (manifest: ${manifest.manifest_id})`,
    );
    this.name = 'BlockedError';
  }
}

export class NetworkError extends AxiomGuardError {
  constructor(message: string, public readonly cause?: unknown) {
    super(message);
    this.name = 'NetworkError';
  }
}
