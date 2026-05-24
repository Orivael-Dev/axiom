"""Tests for axiom_autonomous.ledger — per-step signed token chain.

Verifies structural + cryptographic integrity of a chain (parent
linkage, run_id continuity, chain_sig HMAC, full EventToken.verify()).
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("HOME", str(tmp_path))
    for mod in list(sys.modules):
        if mod.startswith(("axiom_autonomous", "axiom_signing",
                           "axiom_event_token", "axiom_exoskeleton")):
            sys.modules.pop(mod, None)
    yield


def test_single_step_token_verifies(isolated):
    from axiom_autonomous.ledger import build_step_token
    t = build_step_token(
        run_id="auto_test_1",
        step_idx=0,
        step_kind="plan",
        parent_token_id=None,
        payload={"task": "x", "summary": "planned 1 subgoal"},
    )
    assert t.verify(), "step token failed full verification"


def test_chain_of_three_verifies(isolated):
    from axiom_autonomous.ledger import TokenChain, verify_chain
    chain = TokenChain(run_id="auto_test_2")
    chain.append(step_kind="plan",    payload={"summary": "p"})
    chain.append(step_kind="execute", payload={"summary": "e"})
    chain.append(step_kind="verify",  payload={"summary": "v"})
    r = verify_chain(chain.tokens, run_id="auto_test_2")
    assert r["ok"], f"chain verify failed: {r['reason']}"


def test_chain_records_parent_token_ids(isolated):
    from axiom_autonomous.ledger import TokenChain
    chain = TokenChain(run_id="auto_test_3")
    t0 = chain.append(step_kind="plan",    payload={"s": 1})
    t1 = chain.append(step_kind="execute", payload={"s": 2})
    t2 = chain.append(step_kind="verify",  payload={"s": 3})
    assert t0.text.payload["parent_token_id"] == ""
    assert t1.text.payload["parent_token_id"] == t0.id
    assert t2.text.payload["parent_token_id"] == t1.id


def test_tampered_payload_breaks_verification(isolated):
    """Swap a step's payload bytes — token.verify() must fail."""
    from axiom_autonomous.ledger import TokenChain
    from axiom_event_token.models import EventToken, LayerReport
    chain = TokenChain(run_id="auto_test_4")
    chain.append(step_kind="plan", payload={"summary": "honest"})
    token = chain.tokens[0]
    new_payload = dict(token.text.payload)
    new_payload["summary"] = "tampered"
    bad_layer = LayerReport(
        agent=token.text.agent,
        payload=new_payload,
        confidence=token.text.confidence,
        signature=token.text.signature,        # keep OLD sig → mismatch
    )
    bad_token = EventToken(
        id=token.id,
        format_version=token.format_version,
        created_at=token.created_at,
        activated_agents=token.activated_agents,
        text=bad_layer,
        coordinator_sig=token.coordinator_sig,
        signature=token.signature,
    )
    assert not bad_token.verify()


def test_chain_sig_independent_of_outer_sig(isolated):
    """Forging an outer EventToken with a fresh id but no recompute
    of chain_sig must be detectable.
    """
    from axiom_autonomous.ledger import (
        TokenChain, _compute_chain_sig, _verify_chain_sig,
    )
    chain = TokenChain(run_id="auto_test_5")
    chain.append(step_kind="plan", payload={"summary": "p"})
    chain.append(step_kind="execute", payload={"summary": "e"})
    head = chain.tokens[1]
    payload = head.text.payload
    # The chain_sig embedded in the token must match the recomputed
    # HMAC over (run_id, step_idx, parent, token_id).
    assert _verify_chain_sig(
        run_id="auto_test_5",
        step_idx=int(payload["step_idx"]),
        parent_token_id=payload["parent_token_id"] or None,
        token_id=head.id,
        sig=payload["chain_sig"],
    )
    # Lying about the parent breaks the check.
    assert not _verify_chain_sig(
        run_id="auto_test_5",
        step_idx=int(payload["step_idx"]),
        parent_token_id="auto_step_FORGED",
        token_id=head.id,
        sig=payload["chain_sig"],
    )


def test_ledger_append_records_use_case_prefix(isolated, tmp_path):
    from axiom_autonomous.ledger import TokenChain
    from axiom_exoskeleton_ledger import LedgerWriter, read_ledger
    ledger_path = tmp_path / "ledger.jsonl"
    writer = LedgerWriter(ledger_path)
    chain = TokenChain(run_id="auto_test_6", ledger=writer)
    chain.append(step_kind="plan",    payload={"summary": "p"})
    chain.append(step_kind="execute", payload={"summary": "e"})
    entries = read_ledger(ledger_path)
    assert len(entries) == 2
    assert all(e.use_case.startswith("autonomous:auto_test_6:")
               for e in entries)
    assert {e.use_case.rsplit(":", 1)[-1] for e in entries} == {"plan", "execute"}
    assert all(e.verified for e in entries)


def test_reannotate_head_preserves_token_id(isolated):
    from axiom_autonomous.ledger import TokenChain
    chain = TokenChain(run_id="auto_test_7")
    chain.append(step_kind="verify", payload={"summary": "v"})
    original_id = chain.tokens[0].id
    chain.reannotate_head(extra={"honesty_findings": [{"category": "x"}]})
    assert chain.tokens[0].id == original_id
    assert chain.tokens[0].verify()
    assert "honesty_findings" in chain.tokens[0].text.payload
