# -*- coding: utf-8 -*-
"""
Inference OS Layer-1 adaptive router — fuses exoskeleton-ledger health (EWMA of
latency + verified-rate) and the cognition metabolic economy hint into a RouteDirective
the pipeline acts on before generation. Tests cover the economy-tier token budget, the
health-degraded fallback recommendation, and safe defaults on an empty ledger.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_router"

from axiom_os_router import (AdaptiveRouter, RouteDirective, STANDARD, ECONOMY,
                             STANDARD_MAX_TOKENS, ECONOMY_MAX_TOKENS)


def _row(backend, domain, latency_ms, verified):
    return json.dumps({
        "timestamp_utc": "2026-01-01T00:00:00.000Z", "use_case": "inference_os",
        "token_id": "t", "input_excerpt": "x", "input_chars": 1,
        "backend": backend, "model": "m", "input_tokens": 1, "output_tokens": 1,
        "latency_ms": latency_ms, "verified": verified, "signature": "", "domain": domain,
    })


def _seed_ledger(tmp_path, rows):
    p = tmp_path / "exo.jsonl"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    from axiom_event_token.router import RouterPolicy
    return RouterPolicy(ledger_path=str(p), latency_threshold_ms=1500, verified_floor=0.90)


class TestEconomyTier:

    def test_reason_cheaply_shrinks_token_budget(self):
        d = AdaptiveRouter(policy=None).decide(
            "local", "general", cognition={"action": "REASON_CHEAPLY"})
        assert d.tier == ECONOMY
        assert d.max_output_tokens == ECONOMY_MAX_TOKENS
        assert d.max_output_tokens < STANDARD_MAX_TOKENS

    def test_refuse_for_health_is_economy(self):
        d = AdaptiveRouter(policy=None).decide(
            "local", "general", cognition={"action": "REFUSE_FOR_HEALTH"})
        assert d.tier == ECONOMY

    def test_proceed_is_standard_budget(self):
        d = AdaptiveRouter(policy=None).decide(
            "local", "general", cognition={"action": "PROCEED"}, base_max_tokens=512)
        assert d.tier == STANDARD
        assert d.max_output_tokens == 512

    def test_no_cognition_is_standard(self):
        d = AdaptiveRouter(policy=None).decide("local", "general")
        assert d.tier == STANDARD


class TestHealthRouting:

    def test_healthy_backend_keeps_route(self, tmp_path):
        # fast + verified recent history → healthy → no fallback
        policy = _seed_ledger(tmp_path, [_row("nim", "legal", 300, True)] * 8)
        d = AdaptiveRouter(policy=policy).decide("nim", "legal")
        assert d.backend_healthy is True
        assert d.prefer_fallback is False
        assert d.route == "nim"

    def test_degraded_backend_prefers_fallback(self, tmp_path):
        # slow + failing recent history → degraded → recommend fallback proactively
        policy = _seed_ledger(tmp_path, [_row("nim", "legal", 9000, False)] * 8)
        d = AdaptiveRouter(policy=policy).decide("nim", "legal")
        assert d.backend_healthy is False
        assert d.prefer_fallback is True
        assert d.route == "fallback"

    def test_unknown_backend_assumed_healthy(self, tmp_path):
        policy = _seed_ledger(tmp_path, [_row("nim", "legal", 300, True)])
        d = AdaptiveRouter(policy=policy).decide("brand-new-backend", "general")
        assert d.backend_healthy is True

    def test_health_and_economy_compose(self, tmp_path):
        policy = _seed_ledger(tmp_path, [_row("nim", "legal", 9000, False)] * 8)
        d = AdaptiveRouter(policy=policy).decide(
            "nim", "legal", cognition={"action": "REASON_CHEAPLY"})
        assert d.prefer_fallback is True and d.tier == ECONOMY
        assert d.max_output_tokens == ECONOMY_MAX_TOKENS


class TestRankForChains:

    def test_rank_partitions_by_health(self, tmp_path):
        # 'nim' degraded on general, 'local' healthy → healthy first, degraded last
        policy = _seed_ledger(tmp_path, [_row("nim", "general", 9000, False)] * 8
                              + [_row("local", "general", 200, True)] * 8)
        healthy, degraded = AdaptiveRouter(policy=policy).rank(("nim", "local"), "general")
        assert healthy == ["local"]
        assert degraded == ["nim"]

    def test_rank_all_healthy_reorders_nothing(self, tmp_path):
        policy = _seed_ledger(tmp_path, [_row("local", "general", 200, True)] * 4)
        healthy, degraded = AdaptiveRouter(policy=policy).rank(("local", "nim"), "general")
        assert degraded == []
        assert healthy == ["local", "nim"]        # unknown 'nim' assumed healthy

    def test_rank_preserves_order_within_group(self, tmp_path):
        policy = _seed_ledger(tmp_path, [_row("a", "general", 9000, False)] * 8
                              + [_row("c", "general", 9000, False)] * 8)
        healthy, degraded = AdaptiveRouter(policy=policy).rank(("a", "b", "c"), "general")
        assert healthy == ["b"]                    # only healthy member
        assert degraded == ["a", "c"]              # configured order kept


class TestDefaults:

    def test_empty_ledger_is_healthy_standard(self):
        d = AdaptiveRouter().decide("local", "general")  # default policy, no history
        assert d.backend_healthy is True
        assert d.tier == STANDARD

    def test_directive_to_dict_roundtrips(self):
        d = AdaptiveRouter(policy=None).decide("local", "general")
        keys = {"route", "tier", "max_output_tokens", "backend_healthy",
                "prefer_fallback", "reason"}
        assert set(d.to_dict()) == keys

    def test_directive_is_frozen(self):
        d = AdaptiveRouter(policy=None).decide("local", "general")
        with pytest.raises(Exception):
            d.route = "changed"


def test_cli_smoke(capsys):
    from axiom_os_router import _main
    assert _main(["local", "--action", "REASON_CHEAPLY"]) == 0
    assert "tier=economy" in capsys.readouterr().out
