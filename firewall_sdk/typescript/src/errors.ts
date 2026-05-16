import type { IntentClass } from './types.js';

/** Base error class for all Axiom Firewall SDK errors. */
export class AxiomFirewallError extends Error {
  readonly statusCode?: number;

  constructor(message: string, statusCode?: number) {
    super(message);
    this.name = 'AxiomFirewallError';
    this.statusCode = statusCode;
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

/** Thrown on HTTP 401 — API key is missing, malformed, or revoked. */
export class InvalidKeyError extends AxiomFirewallError {
  constructor(message: string) {
    super(message, 401);
    this.name = 'InvalidKeyError';
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

/** Thrown on HTTP 429 — tenant exceeded their tier's quota. */
export class RateLimitedError extends AxiomFirewallError {
  constructor(message: string) {
    super(message, 429);
    this.name = 'RateLimitedError';
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

/** Thrown on HTTP 5xx — the Firewall API is misbehaving. */
export class ServerError extends AxiomFirewallError {
  constructor(message: string, statusCode: number) {
    super(message, statusCode);
    this.name = 'ServerError';
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

/** Thrown when the HTTP request could not be completed (timeout, DNS, etc.). */
export class NetworkError extends AxiomFirewallError {
  readonly cause?: unknown;

  constructor(message: string, cause?: unknown) {
    super(message);
    this.name = 'NetworkError';
    this.cause = cause;
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

/**
 * Thrown by `Client.checkOrThrow` when verdict === 'block'.
 * Carries the intent class so callers can route on it.
 */
export class BlockedError extends AxiomFirewallError {
  readonly intentClass: IntentClass;
  readonly confidence: number;
  readonly signals: string[];

  constructor(intentClass: IntentClass, confidence: number, signals: string[]) {
    super(
      `Prompt blocked by Axiom Firewall (intent=${intentClass}, confidence=${confidence.toFixed(2)})`,
    );
    this.name = 'BlockedError';
    this.intentClass = intentClass;
    this.confidence = confidence;
    this.signals = signals;
    Object.setPrototypeOf(this, new.target.prototype);
  }
}
