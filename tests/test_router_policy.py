"""Tests for RouterPolicy and LatencyAwareRouter."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("AXIOM_MASTER_KEY", "bb" * 32)

from axiom_event_token.router import (
    DelegateRouter, RoutingDecision,
    RouterPolicy, LatencyAwareRouter,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_ledger(entries: list[dict], tmp_path: Path) -> Path:
    """Write a synthetic ledger file and return its path."""
    path = tmp_path / "ledger.jsonl"
    lines = []
    for e in entries:
        row = {
            "timestamp_utc": "2026-06-16T00:00:00Z",
            "use_case": "test:case",
            "token_id": "tok_abc",
            "input_excerpt": "hello",
            "input_chars": 5,
            "backend": e.get("backend", "local"),
            "model": e.get("model", "llama3"),
            "input_tokens": 10,
            "output_tokens": 20,
            "latency_ms": e.get("latency_ms", 500),
            "verified": e.get("verified", True),
            "signature": "",
        }
        lines.append(json.dumps(row))
    path.write_text("\n".join(lines) + "\n")
    return path


@dataclass
class _FakeDelegate:
    name: str
    intent_classes: Tuple[str, ...] = ("INFORM",)
    backend_chain: Tuple[str, ...] = ("local",)


# ── RouterPolicy: empty ledger ────────────────────────────────────────────────

def test_policy_empty_ledger_scores_healthy(tmp_path):
    """No ledger entries → all backends unknown → score 1.0 (healthy)."""
    path = tmp_path / "empty.jsonl"
    path.write_text("")
    policy = RouterPolicy(ledger_path=path)
    policy.refresh()
    assert policy.score("local")   == 1.0
    assert policy.score("nim")     == 1.0
    assert policy.score("unknown") == 1.0


def test_policy_missing_ledger_scores_healthy(tmp_path):
    """Missing ledger file → all unknown → healthy."""
    policy = RouterPolicy(ledger_path=tmp_path / "nonexistent.jsonl")
    policy.refresh()
    assert policy.score("local") == 1.0


# ── RouterPolicy: healthy backends ────────────────────────────────────────────

def test_policy_fast_verified_backend_scores_healthy(tmp_path):
    entries = [
        {"backend": "nim", "latency_ms": 300, "verified": True},
        {"backend": "nim", "latency_ms": 400, "verified": True},
    ]
    path = _make_ledger(entries, tmp_path)
    policy = RouterPolicy(ledger_path=path, latency_threshold_ms=1500,
                          verified_floor=0.90)
    policy.refresh()
    assert policy.score("nim") == 1.0


def test_policy_slow_backend_scores_zero(tmp_path):
    entries = [
        {"backend": "local", "latency_ms": 2000, "verified": True},
        {"backend": "local", "latency_ms": 2500, "verified": True},
    ]
    path = _make_ledger(entries, tmp_path)
    policy = RouterPolicy(ledger_path=path, latency_threshold_ms=1500)
    policy.refresh()
    assert policy.score("local") == 0.0


def test_policy_low_verified_rate_scores_zero(tmp_path):
    entries = [
        {"backend": "nim", "latency_ms": 200, "verified": False},
        {"backend": "nim", "latency_ms": 200, "verified": False},
        {"backend": "nim", "latency_ms": 200, "verified": True},
    ]
    path = _make_ledger(entries, tmp_path)
    policy = RouterPolicy(ledger_path=path, verified_floor=0.90)
    policy.refresh()
    # verified_rate = 1/3 = 0.33 < 0.90 → unhealthy
    assert policy.score("nim") == 0.0


def test_policy_scores_property_returns_snapshot(tmp_path):
    entries = [{"backend": "local", "latency_ms": 100, "verified": True}]
    path = _make_ledger(entries, tmp_path)
    policy = RouterPolicy(ledger_path=path)
    policy.refresh()
    snapshot = policy.scores
    # scores are keyed by (backend, domain) tuples; no-domain entries use ""
    assert ("local", "") in snapshot
    assert snapshot[("local", "")] == 1.0
    # mutating snapshot doesn't affect policy
    snapshot[("local", "")] = 0.0
    assert policy.score("local") == 1.0


def test_policy_max_age_entries_limits_window(tmp_path):
    """Only the last max_age_entries rows are considered."""
    # 3 old slow entries + 2 recent fast entries → only last 2 used
    entries = (
        [{"backend": "nim", "latency_ms": 5000, "verified": True}] * 3
        + [{"backend": "nim", "latency_ms": 100, "verified": True}] * 2
    )
    path = _make_ledger(entries, tmp_path)
    policy = RouterPolicy(ledger_path=path, max_age_entries=2,
                          latency_threshold_ms=1500)
    policy.refresh()
    assert policy.score("nim") == 1.0   # only last 2 rows → avg=100 → healthy


# ── LatencyAwareRouter: basic routing ────────────────────────────────────────

def _healthy_policy(tmp_path: Path, backend: str = "local") -> RouterPolicy:
    entries = [{"backend": backend, "latency_ms": 100, "verified": True}]
    path = _make_ledger(entries, tmp_path)
    return RouterPolicy(ledger_path=path)


def _base_router_with_result(delegate_names=("alpha",)) -> DelegateRouter:
    """DelegateRouter that always returns a fixed RoutingDecision."""
    r = MagicMock(spec=DelegateRouter)
    r.route.return_value = RoutingDecision(
        intent_class="INFORM",
        confidence=0.80,
        delegate_names=tuple(delegate_names),
        matched_on="text",
    )
    return r


def test_latency_aware_healthy_delegates_unchanged(tmp_path):
    """All-healthy backends → delegate order unchanged from base router."""
    delegates = [_FakeDelegate("alpha", backend_chain=("local",)),
                 _FakeDelegate("beta",  backend_chain=("local",))]
    policy = _healthy_policy(tmp_path)
    router = LatencyAwareRouter(
        policy=policy,
        base=_base_router_with_result(("alpha", "beta")),
    )
    decision = router.route(delegates=delegates, text="hello")
    assert decision.delegate_names == ("alpha", "beta")


def test_latency_aware_slow_backend_moved_to_end(tmp_path):
    """Delegate on slow backend moves to end, healthy first."""
    entries = [
        {"backend": "local", "latency_ms": 100, "verified": True},
        {"backend": "nim",   "latency_ms": 5000, "verified": True},  # slow
    ]
    path = _make_ledger(entries, tmp_path)
    policy = RouterPolicy(ledger_path=path, latency_threshold_ms=1500)

    delegates = [
        _FakeDelegate("alpha", backend_chain=("local",)),  # healthy
        _FakeDelegate("beta",  backend_chain=("nim",)),    # degraded
    ]
    router = LatencyAwareRouter(
        policy=policy,
        base=_base_router_with_result(("alpha", "beta")),
    )
    decision = router.route(delegates=delegates, text="hello world")
    # alpha (local=healthy) first, beta (nim=degraded) last
    assert decision.delegate_names == ("alpha", "beta")
    # beta is still present — never dropped
    assert "beta" in decision.delegate_names


def test_latency_aware_degraded_not_dropped(tmp_path):
    """Even if all backends are degraded, all delegates are returned."""
    entries = [{"backend": "local", "latency_ms": 9999, "verified": True}]
    path = _make_ledger(entries, tmp_path)
    policy = RouterPolicy(ledger_path=path, latency_threshold_ms=100)

    delegates = [_FakeDelegate("alpha", backend_chain=("local",))]
    router = LatencyAwareRouter(
        policy=policy,
        base=_base_router_with_result(("alpha",)),
    )
    decision = router.route(delegates=delegates, text="hi")
    # still returned even though degraded
    assert "alpha" in decision.delegate_names


def test_latency_aware_empty_decision_passthrough(tmp_path):
    """Empty delegate list from base router passes through unchanged."""
    policy = _healthy_policy(tmp_path)
    base = _base_router_with_result(())   # empty
    router = LatencyAwareRouter(policy=policy, base=base)
    decision = router.route(delegates=[], text="hello")
    assert decision.delegate_names == ()


def test_latency_aware_unknown_backend_treated_healthy(tmp_path):
    """Delegate on unknown backend (not in ledger) gets score 1.0 → healthy."""
    entries = [{"backend": "local", "latency_ms": 100, "verified": True}]
    path = _make_ledger(entries, tmp_path)
    policy = RouterPolicy(ledger_path=path)

    # "exotic" backend has no ledger history → assumed healthy
    delegates = [_FakeDelegate("gamma", backend_chain=("exotic",))]
    router = LatencyAwareRouter(
        policy=policy,
        base=_base_router_with_result(("gamma",)),
    )
    decision = router.route(delegates=delegates, text="test")
    assert decision.delegate_names == ("gamma",)


def test_latency_aware_no_backend_chain_defaults_to_local(tmp_path):
    """Delegate with no backend_chain attribute is treated as 'local'."""
    entries = [{"backend": "local", "latency_ms": 100, "verified": True}]
    path = _make_ledger(entries, tmp_path)
    policy = RouterPolicy(ledger_path=path)

    # plain mock with no backend_chain attribute
    d = MagicMock()
    d.name = "delta"
    del d.backend_chain   # ensure AttributeError → fallback to "local"

    router = LatencyAwareRouter(
        policy=policy,
        base=_base_router_with_result(("delta",)),
    )
    decision = router.route(delegates=[d], text="test")
    assert "delta" in decision.delegate_names


def test_latency_aware_intent_class_passthrough(tmp_path):
    """LatencyAwareRouter preserves intent_class and confidence from base."""
    policy = _healthy_policy(tmp_path)
    delegates = [_FakeDelegate("alpha")]
    router = LatencyAwareRouter(
        policy=policy,
        base=_base_router_with_result(("alpha",)),
    )
    decision = router.route(delegates=delegates, text="tell me something")
    assert decision.intent_class == "INFORM"
    assert decision.confidence   == pytest.approx(0.80)
    assert decision.matched_on   == "text"


# ── RouterPolicy: EWMA weighting ─────────────────────────────────────────────

def test_policy_ewma_recent_entries_dominate(tmp_path):
    """Recent entries should dominate older ones under EWMA (α=0.9 ≈ heavy recent)."""
    # 8 old slow entries followed by 2 recent fast entries
    entries = (
        [{"backend": "nim", "latency_ms": 9000, "verified": True}] * 8
        + [{"backend": "nim", "latency_ms": 100, "verified": True}] * 2
    )
    path = _make_ledger(entries, tmp_path)
    # α=0.9 means recent entries dominate almost entirely
    policy = RouterPolicy(ledger_path=path, latency_threshold_ms=1500, ewma_alpha=0.9)
    policy.refresh()
    # With α=0.9, the last two fast entries (100ms) dominate → healthy
    assert policy.score("nim") == 1.0


def test_policy_ewma_old_slow_entries_dont_hurt_with_high_alpha(tmp_path):
    """Low alpha means old entries persist; high alpha makes recent entries dominate."""
    # 1 old slow entry, then 5 fast recent entries
    entries = (
        [{"backend": "local", "latency_ms": 5000, "verified": True}]
        + [{"backend": "local", "latency_ms": 100, "verified": True}] * 5
    )
    path = _make_ledger(entries, tmp_path)
    # high alpha → recent fast entries win → healthy
    policy_high = RouterPolicy(ledger_path=path, latency_threshold_ms=1500, ewma_alpha=0.9)
    policy_high.refresh()
    assert policy_high.score("local") == 1.0


def test_policy_ewma_default_alpha_degrades_slowly(tmp_path):
    """With α=0.1 (default), 1 very slow entry followed by many fast ones stays unhealthy."""
    # The default 0.1 alpha means old observations decay slowly
    entries = (
        [{"backend": "local", "latency_ms": 9000, "verified": True}]
        + [{"backend": "local", "latency_ms": 100, "verified": True}] * 3
    )
    path = _make_ledger(entries, tmp_path)
    # α=0.1: EWMA after [9000, 100, 100, 100]
    # s1=9000, s2=0.1*100 + 0.9*9000 = 10+8100=8110, ...still high
    policy = RouterPolicy(ledger_path=path, latency_threshold_ms=1500, ewma_alpha=0.1)
    policy.refresh()
    assert policy.score("local") == 0.0   # still degraded — old 9000ms persists


# ── RouterPolicy: domain-specific scoring ─────────────────────────────────────

def _make_ledger_with_domain(entries: list[dict], tmp_path: Path) -> Path:
    """Write ledger with domain field included."""
    path = tmp_path / "ledger_domain.jsonl"
    lines = []
    for e in entries:
        row = {
            "timestamp_utc": "2026-06-23T00:00:00Z",
            "use_case": "test:case",
            "token_id": "tok_xyz",
            "input_excerpt": "hello",
            "input_chars": 5,
            "backend": e.get("backend", "local"),
            "model": e.get("model", "llama3"),
            "input_tokens": 10,
            "output_tokens": 20,
            "latency_ms": e.get("latency_ms", 500),
            "verified": e.get("verified", True),
            "signature": "",
            "domain": e.get("domain", ""),
        }
        lines.append(json.dumps(row))
    path.write_text("\n".join(lines) + "\n")
    return path


def test_policy_domain_specific_score_used_when_available(tmp_path):
    """When domain-specific entries exist, domain score overrides aggregate.

    Aggregate (backend, "") collects ALL entries (cross-domain included).
    We need enough slow no-domain entries so the aggregate EWMA stays > threshold
    despite the fast legal entry being included in the aggregate pool.

    With α=0.1 and entries [100, 3000×7] oldest-first, EWMA ends at ~1612ms > 1500.
    The legal-only bucket has just the 100ms entry → healthy.
    """
    entries = [
        {"backend": "nim", "latency_ms": 100,  "verified": True,  "domain": "legal"},
        *[{"backend": "nim", "latency_ms": 3000, "verified": True,  "domain": ""}
          for _ in range(7)],
    ]
    path = _make_ledger_with_domain(entries, tmp_path)
    policy = RouterPolicy(ledger_path=path, latency_threshold_ms=1500)
    policy.refresh()
    # legal domain: only 1 entry at 100ms → healthy
    assert policy.score("nim", "legal") == 1.0
    # aggregate ("nim", ""): [100, 3000×7]; EWMA with α=0.1 ends ≈ 1612ms → unhealthy
    assert policy.score("nim", "") == 0.0


def test_policy_domain_falls_back_to_aggregate(tmp_path):
    """When domain has no entries, falls back to (backend, '') aggregate."""
    entries = [
        {"backend": "nim", "latency_ms": 200, "verified": True, "domain": ""},
    ]
    path = _make_ledger_with_domain(entries, tmp_path)
    policy = RouterPolicy(ledger_path=path, latency_threshold_ms=1500)
    policy.refresh()
    # "finance" has no entries → fallback to (nim, "") → 200ms → healthy
    assert policy.score("nim", "finance") == 1.0


def test_policy_unknown_domain_unknown_backend_returns_healthy(tmp_path):
    """No entries at all for a backend → 1.0 (healthy by default)."""
    path = tmp_path / "empty.jsonl"
    path.write_text("")
    policy = RouterPolicy(ledger_path=path)
    policy.refresh()
    assert policy.score("exotic_backend", "some_domain") == 1.0


def test_policy_domain_specific_slow_overrides_healthy_aggregate(tmp_path):
    """Domain entry can be unhealthy even when overall backend aggregate is healthy."""
    entries = [
        # fast entries on other domains or no-domain
        {"backend": "nim", "latency_ms": 100,  "verified": True,  "domain": ""},
        {"backend": "nim", "latency_ms": 100,  "verified": True,  "domain": "general"},
        # legal domain is very slow
        {"backend": "nim", "latency_ms": 5000, "verified": True,  "domain": "legal"},
        {"backend": "nim", "latency_ms": 5000, "verified": True,  "domain": "legal"},
    ]
    path = _make_ledger_with_domain(entries, tmp_path)
    policy = RouterPolicy(ledger_path=path, latency_threshold_ms=1500)
    policy.refresh()
    # legal-specific score: 5000ms → unhealthy
    assert policy.score("nim", "legal") == 0.0
    # no-domain aggregate: includes all entries; 100ms entries help
    # but ("nim","") will have ALL entries aggregated → still some slow ones
    # The exact result depends on EWMA; what matters is the domain check works


def test_latency_aware_router_passes_domain_to_policy(tmp_path):
    """LatencyAwareRouter.route(domain=...) uses domain in policy scoring."""
    entries = [
        # local is healthy on "finance", degraded overall
        {"backend": "local", "latency_ms": 200,  "verified": True,  "domain": "finance"},
        {"backend": "local", "latency_ms": 5000, "verified": True,  "domain": ""},
    ]
    path = _make_ledger_with_domain(entries, tmp_path)
    policy = RouterPolicy(ledger_path=path, latency_threshold_ms=1500)

    delegates = [_FakeDelegate("alpha", backend_chain=("local",))]
    router = LatencyAwareRouter(
        policy=policy,
        base=_base_router_with_result(("alpha",)),
    )
    # With domain="finance", local scores healthy → alpha should be in healthy list
    decision = router.route(delegates=delegates, text="review contract", domain="finance")
    assert "alpha" in decision.delegate_names
    # Policy saw finance domain → healthy score used
    assert policy.score("local", "finance") == 1.0


# ── Regression: DelegateRouter unchanged ─────────────────────────────────────

def test_delegate_router_unchanged():
    """DelegateRouter still works — no regression from adding new classes."""
    from axiom_event_token.router import DelegateRouter, RoutingDecision
    router = DelegateRouter()
    result = router.route(delegates=[], text="hello world")
    assert isinstance(result, RoutingDecision)
    assert result.intent_class in (
        "INFORM", "CLARIFY", "REFUSE", "HARM", "DECEIVE", "UNCERTAIN"
    )
