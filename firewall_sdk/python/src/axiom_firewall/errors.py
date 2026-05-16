"""Exception hierarchy for the Axiom Firewall SDK."""
from __future__ import annotations

from typing import Optional


class AxiomFirewallError(Exception):
    """Base exception for all SDK errors."""
    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class InvalidKeyError(AxiomFirewallError):
    """Raised on HTTP 401 — API key is missing, malformed, or revoked."""


class RateLimitedError(AxiomFirewallError):
    """Raised on HTTP 429 — tenant exceeded the rate limit for their tier."""


class ServerError(AxiomFirewallError):
    """Raised on HTTP 5xx — the Firewall API is misbehaving."""


class NetworkError(AxiomFirewallError):
    """Raised when the HTTP request could not be completed (timeout, DNS, etc.)."""


class BlockedError(AxiomFirewallError):
    """Raised by `Client.check_or_raise` when verdict == 'block'.

    Carries the intent class so callers can route on it.
    """
    def __init__(self, intent_class: str, confidence: float,
                 signals: tuple[str, ...] = ()) -> None:
        super().__init__(
            f"Prompt blocked by Axiom Firewall (intent={intent_class}, "
            f"confidence={confidence:.2f})"
        )
        self.intent_class = intent_class
        self.confidence = confidence
        self.signals = signals
