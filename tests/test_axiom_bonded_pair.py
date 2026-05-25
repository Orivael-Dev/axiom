"""Tests for axiom_event_token.bonded_pair.

Three use-case demonstrations + the hard edges (tamper detection,
chain integrity, concurrent writers, illegal transitions).
"""
from __future__ import annotations

import json
import multiprocessing
import os
import sys
import time
from pathlib import Path

import pytest


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """Fresh master key + per-test workdir + reimport so the signing
    module rebinds to the new key."""
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    for mod in list(sys.modules):
        if mod.startswith(("axiom_signing", "axiom_event_token.bonded_pair")):
            sys.modules.pop(mod, None)
    return tmp_path


# ── Mint + verify ────────────────────────────────────────────────────────


def test_mint_pair_returns_two_linked_tokens(isolated):
    from axiom_event_token.bonded_pair import mint_pair, verify_pair
    primary, mirror = mint_pair(
        {"execution_command": "run_query"},
        {"monitor_target": "primary"},
    )
    assert primary.pair_id == mirror.pair_id
    assert primary.role == "primary" and mirror.role == "mirror"
    assert primary.partner_token_id == mirror.token_id
    assert mirror.partner_token_id == primary.token_id
    assert primary.timestamp_ns == mirror.timestamp_ns
    assert primary.verify() and mirror.verify()
    assert verify_pair(primary, mirror) is True


def test_pair_id_is_unique_across_mints(isolated):
    """Two independent mints must NOT collide on pair_id — otherwise
    a forger could craft a partner that looks legitimate."""
    from axiom_event_token.bonded_pair import mint_pair
    ids = {mint_pair({"a": i}, {"b": i})[0].pair_id for i in range(50)}
    assert len(ids) == 50


def test_token_signature_changes_with_payload(isolated):
    """The outer signature must cover the payload — tampering with
    payload bytes must invalidate verify()."""
    from axiom_event_token.bonded_pair import BondedToken, mint_pair
    primary, _ = mint_pair({"cmd": "good"}, {"mon": "primary"})
    tampered = BondedToken(**{**primary.to_dict(), "payload": {"cmd": "bad"}})
    assert tampered.verify() is False


def test_verify_pair_rejects_role_collision(isolated):
    """Two 'primary' halves must not validate as a pair."""
    from axiom_event_token.bonded_pair import (
        BondedToken, mint_pair, verify_pair, _resign,
    )
    primary, mirror = mint_pair({"a": 1}, {"b": 1})
    # Force a second 'primary' role on the mirror half + re-sign.
    fake_mirror = _resign(BondedToken(**{**mirror.to_dict(), "role": "primary",
                                         "signature": ""}))
    assert verify_pair(primary, fake_mirror) is False


def test_verify_pair_rejects_cross_pair_partners(isolated):
    """A primary from pair P1 + a mirror from pair P2 must not validate."""
    from axiom_event_token.bonded_pair import mint_pair, verify_pair
    p1, _ = mint_pair({"a": 1}, {"b": 1})
    _, m2 = mint_pair({"a": 2}, {"b": 2})
    assert verify_pair(p1, m2) is False


def test_partner_pointer_is_set_before_signing(isolated):
    """Regression: the partner_token_id must be inside the signed bytes
    so a swapped partner doesn't validate (catches the chicken-and-egg
    bug where ids would be added after signing)."""
    from axiom_event_token.bonded_pair import BondedToken, mint_pair
    primary, mirror = mint_pair({"a": 1}, {"b": 1})
    # Construct a token with the SAME id/payload but a different partner.
    swapped = BondedToken(
        **{**primary.to_dict(),
           "partner_token_id": "AXIOM-BP-otherpair-B"}
    )
    assert swapped.verify() is False


# ── Ledger: init + transitions ───────────────────────────────────────────


def test_ledger_init_pair_starts_active(isolated):
    from axiom_event_token.bonded_pair import (
        BondedPairLedger, mint_pair, is_authorized,
    )
    led = BondedPairLedger(isolated / "ledger.jsonl")
    primary, _ = mint_pair({"cmd": "x"}, {"mon": "p"})
    led.init_pair(primary.pair_id, actor="test")
    assert led.current_state(primary.pair_id) == "ACTIVE_VALIDATED"
    assert is_authorized(led, primary.pair_id) is True


def test_ledger_double_init_raises(isolated):
    from axiom_event_token.bonded_pair import (
        BondedPairLedger, BondedPairLedgerError, mint_pair,
    )
    led = BondedPairLedger(isolated / "ledger.jsonl")
    pid = mint_pair({"a": 1}, {"b": 1})[0].pair_id
    led.init_pair(pid)
    with pytest.raises(BondedPairLedgerError, match="already initialised"):
        led.init_pair(pid)


def test_revoke_short_circuits_authority(isolated):
    """USE CASE 1: action + monitor. The mirror's revocation flips
    is_authorized() to False even though the primary token is
    untouched and still verifies cryptographically — that's the whole
    point of the primitive."""
    from axiom_event_token.bonded_pair import (
        BondedPairLedger, mint_pair, is_authorized,
    )
    led = BondedPairLedger(isolated / "ledger.jsonl")
    primary, mirror = mint_pair(
        {"execution_command": "run_local_model_optimization"},
        {"monitor_target": "primary"},
    )
    led.init_pair(primary.pair_id)
    assert is_authorized(led, primary.pair_id) is True

    led.revoke(primary.pair_id, actor="security_monitor")

    # Primary's bytes + signature are unchanged.
    assert primary.verify() is True
    # But authority is gone — the gate consults the ledger.
    assert is_authorized(led, primary.pair_id) is False
    assert led.current_state(primary.pair_id) == "REVOKED"


def test_terminal_state_blocks_further_transitions(isolated):
    from axiom_event_token.bonded_pair import (
        BondedPairLedger, BondedPairLedgerError, mint_pair,
    )
    led = BondedPairLedger(isolated / "ledger.jsonl")
    pid = mint_pair({"a": 1}, {"b": 1})[0].pair_id
    led.init_pair(pid)
    led.revoke(pid, actor="test")
    # Can't un-revoke
    with pytest.raises(BondedPairLedgerError, match="terminal"):
        led.transition(pid, "ACTIVE_VALIDATED", actor="oops")


def test_transition_rejects_unknown_state(isolated):
    from axiom_event_token.bonded_pair import (
        BondedPairLedger, BondedPairLedgerError, mint_pair,
    )
    led = BondedPairLedger(isolated / "ledger.jsonl")
    pid = mint_pair({"a": 1}, {"b": 1})[0].pair_id
    led.init_pair(pid)
    with pytest.raises(BondedPairLedgerError, match="not in allowed"):
        led.transition(pid, "MADE_UP_STATE", actor="test")


def test_transition_rejects_uninitialised_pair(isolated):
    from axiom_event_token.bonded_pair import (
        BondedPairLedger, BondedPairLedgerError, mint_pair,
    )
    led = BondedPairLedger(isolated / "ledger.jsonl")
    pid = mint_pair({"a": 1}, {"b": 1})[0].pair_id
    with pytest.raises(BondedPairLedgerError, match="not initialised"):
        led.transition(pid, "SUSPENDED", actor="test")


def test_history_returns_full_transition_record(isolated):
    from axiom_event_token.bonded_pair import BondedPairLedger, mint_pair
    led = BondedPairLedger(isolated / "ledger.jsonl")
    pid = mint_pair({"a": 1}, {"b": 1})[0].pair_id
    led.init_pair(pid, actor="alice")
    led.transition(pid, "SUSPENDED", actor="bob")
    led.transition(pid, "ACTIVE_VALIDATED", actor="alice")
    led.revoke(pid, actor="carol")
    h = led.history(pid)
    assert [e.to_state for e in h] == [
        "ACTIVE_VALIDATED", "SUSPENDED", "ACTIVE_VALIDATED", "REVOKED",
    ]
    assert [e.actor for e in h] == ["alice", "bob", "alice", "carol"]


# ── Use case 2: two-party commit ────────────────────────────────────────


def test_two_party_commit_pattern(isolated):
    """USE CASE 2: both halves must transition together. The ledger
    is the single coordination point — neither party can claim a
    half-transition; the ledger only knows the pair state.

    Sale + delivery: the pair is ACTIVE_VALIDATED while the contract
    is open. Either party initiates the commit; the ledger flips the
    pair to EXPIRED and from that point neither side can change it.
    """
    from axiom_event_token.bonded_pair import (
        BondedPairLedger, mint_pair, is_authorized, BondedPairLedgerError,
    )
    led = BondedPairLedger(isolated / "ledger.jsonl")
    pid = mint_pair({"side": "sale"}, {"side": "delivery"})[0].pair_id
    led.init_pair(pid)
    assert is_authorized(led, pid) is True

    led.transition(pid, "EXPIRED", actor="delivery_party")
    assert is_authorized(led, pid) is False

    # Sale party can't un-expire either — the commit is atomic.
    with pytest.raises(BondedPairLedgerError, match="terminal"):
        led.transition(pid, "ACTIVE_VALIDATED", actor="sale_party")


# ── Hash-chain integrity ────────────────────────────────────────────────


def test_verify_chain_passes_on_clean_ledger(isolated):
    from axiom_event_token.bonded_pair import BondedPairLedger, mint_pair
    led = BondedPairLedger(isolated / "ledger.jsonl")
    pid = mint_pair({"a": 1}, {"b": 1})[0].pair_id
    led.init_pair(pid)
    led.transition(pid, "SUSPENDED", actor="test")
    led.transition(pid, "ACTIVE_VALIDATED", actor="test")
    assert led.verify_chain() is True


def test_tampering_one_entry_breaks_chain(isolated):
    """Adversarial: an attacker modifies a transition's to_state in the
    log file (e.g. flips REVOKED back to ACTIVE_VALIDATED). The chain
    must detect this on verify_chain()."""
    from axiom_event_token.bonded_pair import BondedPairLedger, mint_pair
    path = isolated / "ledger.jsonl"
    led = BondedPairLedger(path)
    pid = mint_pair({"a": 1}, {"b": 1})[0].pair_id
    led.init_pair(pid)
    led.revoke(pid, actor="security")
    assert led.verify_chain() is True

    # Tamper: flip the revoke back to ACTIVE_VALIDATED in the raw file.
    lines = path.read_text(encoding="utf-8").splitlines()
    last = json.loads(lines[-1])
    last["to_state"] = "ACTIVE_VALIDATED"
    lines[-1] = json.dumps(last, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert led.verify_chain() is False
    # And the gate still reports the current state from the tampered
    # line — verify_chain is the auditor's job, not the gate's.
    # (Operators are expected to verify_chain() periodically, not on
    # every is_authorized() call.)


def test_deleting_an_entry_breaks_chain(isolated):
    """Adversarial: delete a transition to retroactively un-do a state
    change. The prev_signature pointer in the next entry must catch it."""
    from axiom_event_token.bonded_pair import BondedPairLedger, mint_pair
    path = isolated / "ledger.jsonl"
    led = BondedPairLedger(path)
    pid = mint_pair({"a": 1}, {"b": 1})[0].pair_id
    led.init_pair(pid)
    led.transition(pid, "SUSPENDED", actor="t")
    led.revoke(pid, actor="t")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    # Drop the middle entry.
    path.write_text(lines[0] + "\n" + lines[2] + "\n", encoding="utf-8")
    assert led.verify_chain() is False


def test_reordering_entries_breaks_chain(isolated):
    """Swap two adjacent rows. The prev_signature link must catch it."""
    from axiom_event_token.bonded_pair import BondedPairLedger, mint_pair
    path = isolated / "ledger.jsonl"
    led = BondedPairLedger(path)
    pid = mint_pair({"a": 1}, {"b": 1})[0].pair_id
    led.init_pair(pid)
    led.transition(pid, "SUSPENDED", actor="t")
    led.transition(pid, "ACTIVE_VALIDATED", actor="t")
    lines = path.read_text(encoding="utf-8").splitlines()
    # Swap last two
    lines[-1], lines[-2] = lines[-2], lines[-1]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert led.verify_chain() is False


# ── Concurrent writers ──────────────────────────────────────────────────


def _writer_proc(ledger_path: str, pair_id: str, n: int, key: str) -> None:
    """Subprocess: hammer the ledger with n transitions for one pair."""
    os.environ["AXIOM_MASTER_KEY"] = key
    # Ensure fresh import in the subprocess.
    from axiom_event_token.bonded_pair import BondedPairLedger
    from pathlib import Path as _P
    led = BondedPairLedger(_P(ledger_path))
    for i in range(n):
        target = "SUSPENDED" if i % 2 == 0 else "ACTIVE_VALIDATED"
        led.transition(pair_id, target, actor=f"w-{os.getpid()}")


def test_concurrent_writers_dont_corrupt_chain(isolated):
    """Two processes append to the same ledger concurrently. With the
    fcntl lock, every appended entry must verify and chain correctly.

    This is the property the 'atomic state manager' must hold up: no
    matter how many writers race, verify_chain() stays True.
    """
    if sys.platform == "win32":
        pytest.skip("fcntl-based test; runs on POSIX only")
    from axiom_event_token.bonded_pair import BondedPairLedger, mint_pair
    path = isolated / "ledger.jsonl"
    led = BondedPairLedger(path)
    pid = mint_pair({"a": 1}, {"b": 1})[0].pair_id
    led.init_pair(pid)

    key = os.environ["AXIOM_MASTER_KEY"]
    ctx = multiprocessing.get_context("fork")
    p1 = ctx.Process(target=_writer_proc, args=(str(path), pid, 20, key))
    p2 = ctx.Process(target=_writer_proc, args=(str(path), pid, 20, key))
    p1.start(); p2.start(); p1.join(); p2.join()
    assert p1.exitcode == 0 and p2.exitcode == 0

    # 1 init + 40 transitions = 41 lines.
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 41
    # Hash chain still intact end-to-end.
    assert led.verify_chain() is True


# ── Replay / forgery ────────────────────────────────────────────────────


def test_unsigned_entry_fails_verify(isolated):
    from axiom_event_token.bonded_pair import StateTransition
    t = StateTransition(
        pair_id="bp-xyz", from_state="", to_state="ACTIVE_VALIDATED",
        timestamp_ns=1, actor="x", prev_signature="",
    )
    assert t.verify() is False
