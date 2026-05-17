"""Data models for the Firewall multi-tenant dashboard."""
from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Tenant:
    """A free-tier or paying customer of Axiom Firewall."""
    tenant_id: str
    email: str
    pw_hash: str
    tier: str  # "free" | "indie" | "team" | "enterprise"
    created_at: datetime
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    recovery_hash: str | None = None

    @staticmethod
    def new(
        email: str,
        pw_hash: str,
        tier: str = "free",
        recovery_hash: str | None = None,
    ) -> "Tenant":
        return Tenant(
            tenant_id=str(uuid.uuid4()),
            email=email.strip().lower(),
            pw_hash=pw_hash,
            tier=tier,
            created_at=datetime.utcnow(),
            recovery_hash=recovery_hash,
        )


@dataclass(frozen=True)
class ApiKey:
    """An API key belonging to a tenant. Secret is shown once at creation."""
    key_id: str
    tenant_id: str
    secret: str
    name: str
    created_at: datetime
    revoked_at: datetime | None = None

    @staticmethod
    def new(tenant_id: str, name: str) -> "ApiKey":
        return ApiKey(
            key_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            secret=f"axfw_{secrets.token_urlsafe(32)}",
            name=name.strip() or "unnamed",
            created_at=datetime.utcnow(),
        )


@dataclass(frozen=True)
class UsageRecord:
    """One billable API call. Persisted per-tenant for billing + analytics."""
    record_id: str
    tenant_id: str
    api_key_id: str
    endpoint: str
    verdict: str  # "allow" | "block" | "redact"
    intent_class: str
    confidence: float
    latency_ms: float
    timestamp: datetime
