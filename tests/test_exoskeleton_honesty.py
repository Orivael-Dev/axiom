"""Tests for axiom_exoskeleton_honesty + ExoskeletonAgent
post-scan integration. AXIOM is pre-revenue; the system must
catch + flag invented track-record / overclaim language in
delegate outputs."""
from __future__ import annotations

import json
import sys

import pytest


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("HOME", str(tmp_path))
    for mod in list(sys.modules):
        if mod.startswith((
            "axiom_event_token", "axiom_signing",
            "axiom_exoskeleton",
        )):
            sys.modules.pop(mod, None)
    yield


# ── Pure module tests ───────────────────────────────────────────────


def test_clean_output_has_no_findings(isolated):
    from axiom_exoskeleton_honesty import scan
    r = scan(
        "AXIOM is designed to help startups verify agent outputs. "
        "Once shipped, the Intent Firewall will enable per-tenant policy."
    )
    assert r.findings == ()
    assert r.block_count == 0


def test_axiom_has_helped_is_blocked(isolated):
    from axiom_exoskeleton_honesty import scan
    r = scan(
        "AXIOM has helped startups secure their AI agents and "
        "reduced costs by 40% across our customer base."
    )
    cats = {f.category for f in r.findings}
    assert "invented_track_record" in cats
    # The 40% counts as an "unearned_results" claim too.
    assert any(f.category == "unearned_results" for f in r.findings)
    assert r.block_count >= 1


def test_thousands_of_customers_blocked(isolated):
    from axiom_exoskeleton_honesty import scan
    r = scan("We have thousands of customers using AXIOM today.")
    cats = {f.category for f in r.findings}
    assert "fake_customer_count" in cats
    assert r.block_count >= 1


def test_regex_count_pattern_catches_numerics(isolated):
    from axiom_exoskeleton_honesty import scan
    r = scan("1,000+ developers ship to production with AXIOM weekly.")
    cats = {f.category for f in r.findings}
    assert "fake_customer_count" in cats


def test_dollar_amount_savings_flagged(isolated):
    from axiom_exoskeleton_honesty import scan
    r = scan("Our customers have saved $2M in compliance costs.")
    cats = {f.category for f in r.findings}
    # 'our customers' is a block; the dollar amount is a flag.
    assert "invented_testimonial" in cats or "fake_customer_count" in cats


def test_percent_improvement_flagged(isolated):
    from axiom_exoskeleton_honesty import scan
    r = scan("Reduced incidents by 73% improvement across the board.")
    cats = {f.category for f in r.findings}
    assert "unearned_results" in cats


def test_allowed_framing_defuses_match(isolated):
    """'AXIOM is designed to help startups' is FINE — forward-looking
    framing should NOT trip the 'helped startups' detector."""
    from axiom_exoskeleton_honesty import scan
    r = scan("AXIOM is designed to help startups ship safely.")
    # No invented-track-record finding (the 'help' is forward-looking).
    assert not any(f.category == "invented_track_record"
                   for f in r.findings)


def test_industry_position_claim_flagged(isolated):
    from axiom_exoskeleton_honesty import scan
    r = scan("AXIOM is the leading platform for AI governance.")
    cats = {f.category for f in r.findings}
    assert "invented_industry_adoption" in cats


def test_redact_replaces_matched_spans(isolated):
    from axiom_exoskeleton_honesty import scan
    r = scan(
        "AXIOM has helped 500+ startups and saved $1M in costs.",
        redact=True,
    )
    assert r.redacted is not None
    assert "OVERCLAIM_REDACTED" in r.redacted
    # Original spans gone from the redacted output.
    assert "AXIOM has helped" not in r.redacted


def test_empty_input_returns_empty_result(isolated):
    from axiom_exoskeleton_honesty import scan
    assert scan("").findings == ()
    assert scan(None).findings == ()
    assert scan("   ").findings == ()


def test_honesty_preamble_size_under_200_tokens(isolated):
    """Preamble must stay short — every delegate inherits it, so
    bloat eats the content budget."""
    from axiom_exoskeleton_honesty import HONESTY_PREAMBLE
    assert len(HONESTY_PREAMBLE) < 1000, \
        "preamble grew past 1000 chars — check budgets"



# Integration tests below (test_preamble_is_in_every_delegate_prompt +
# the "End-to-end ExoskeletonAgent integration" section) need
# examples.exoskeleton_pack + axiom_exoskeleton.py — neither is on
# main yet. They ship with the exoskeleton agent PR.
