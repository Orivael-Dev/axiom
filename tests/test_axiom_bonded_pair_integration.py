"""Bonded-pair integration: IntentGate wiring + CLI surface.

Tests the full path: mint -> ledger -> gate denies on revocation, and
exercises the operator CLI end-to-end (mint, transition, revoke,
state, history, verify).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("AXIOM_BONDED_PAIR_LEDGER", str(tmp_path / "bp.jsonl"))
    for mod in list(sys.modules):
        if mod.startswith(("axiom_signing",
                           "axiom_event_token.bonded_pair",
                           "axiom_intent_classifier",
                           "axiom_intent_gate",
                           "axiom_bonded_pair_cli")):
            sys.modules.pop(mod, None)
    return tmp_path


# ─── IntentGate integration ─────────────────────────────────────────────


def test_gate_denies_when_pair_revoked(isolated):
    """Headline path: a packet with a benign payload but a REVOKED
    pair_id must be denied by the gate (HARM verdict)."""
    from axiom_event_token.bonded_pair import (
        BondedPairLedger, mint_pair,
    )
    from axiom_intent_classifier import IntentClassifier
    from axiom_intent_gate import IntentGate
    from axiom_signing import derive_key

    led = BondedPairLedger()
    primary, _ = mint_pair({"cmd": "run_query"}, {"mon": "primary"})
    led.init_pair(primary.pair_id, actor="provisioner")

    classifier = IntentClassifier(derive_key(b"axiom-firewall-v1"))
    gate = IntentGate(classifier,
                      log_path=str(isolated / "gate.log"),
                      bonded_pair_ledger=led)

    benign_packet = {
        "packet_id":  "test-1",
        "source":     "kid_app",
        "destination": "model_runtime",
        "payload":    {"text": "Tell me a story about a bunny.",
                       "pair_id": primary.pair_id},
    }

    # Initial: pair ACTIVE_VALIDATED — gate should pass the benign text
    r1 = gate.check(benign_packet)
    assert r1.intent_class not in ("HARM", "DECEIVE")

    # Revoke the pair via the mirror's authority
    led.revoke(primary.pair_id, actor="security_monitor")

    # Same packet again — must now be HARM with the revocation signal
    r2 = gate.check(benign_packet)
    assert r2.intent_class == "HARM"
    assert "bonded_pair_revoked" in r2.signals
    assert any(f"pair_id={primary.pair_id}" in s for s in r2.signals)
    assert any("state=REVOKED" in s for s in r2.signals)
    assert classifier.verify(r2), \
        "revocation verdict must carry a valid HMAC signature"


def test_gate_without_ledger_unchanged_behaviour(isolated):
    """Backward compatibility: an IntentGate constructed without a
    bonded_pair_ledger must behave exactly as before — no lookups,
    no surprises."""
    from axiom_intent_classifier import IntentClassifier
    from axiom_intent_gate import IntentGate
    from axiom_signing import derive_key
    classifier = IntentClassifier(derive_key(b"axiom-firewall-v1"))
    gate = IntentGate(classifier, log_path=str(isolated / "gate.log"))
    packet = {"payload": {"text": "Tell me a story.",
                          "pair_id": "bp-nonexistent"}}
    # Without a wired-in ledger, the pair_id is just ignored.
    r = gate.check(packet)
    assert "bonded_pair_revoked" not in r.signals


def test_gate_ignores_packet_without_pair_id(isolated):
    """A packet with no pair_id must run the classifier normally even
    when the gate is configured with a ledger."""
    from axiom_event_token.bonded_pair import BondedPairLedger
    from axiom_intent_classifier import IntentClassifier
    from axiom_intent_gate import IntentGate
    from axiom_signing import derive_key
    led = BondedPairLedger()
    classifier = IntentClassifier(derive_key(b"axiom-firewall-v1"))
    gate = IntentGate(classifier, log_path=str(isolated / "gate.log"),
                      bonded_pair_ledger=led)
    r = gate.check({"payload": {"text": "hello"}})
    assert "bonded_pair_revoked" not in r.signals


def test_gate_denies_uninitialised_pair(isolated):
    """A pair_id the ledger has never seen is treated as not-authorised."""
    from axiom_event_token.bonded_pair import BondedPairLedger
    from axiom_intent_classifier import IntentClassifier
    from axiom_intent_gate import IntentGate
    from axiom_signing import derive_key
    led = BondedPairLedger()
    classifier = IntentClassifier(derive_key(b"axiom-firewall-v1"))
    gate = IntentGate(classifier, log_path=str(isolated / "gate.log"),
                      bonded_pair_ledger=led)
    r = gate.check({"payload": {"text": "hi",
                                "pair_id": "bp-never-minted"}})
    assert r.intent_class == "HARM"
    assert "bonded_pair_revoked" in r.signals
    assert any("state=uninitialised" in s for s in r.signals)


def test_gate_pair_id_in_metadata_field(isolated):
    """pair_id can also be supplied in packet['metadata'] — common when
    callers don't want to inject it into the user-visible payload."""
    from axiom_event_token.bonded_pair import BondedPairLedger, mint_pair
    from axiom_intent_classifier import IntentClassifier
    from axiom_intent_gate import IntentGate
    from axiom_signing import derive_key
    led = BondedPairLedger()
    primary, _ = mint_pair({"a": 1}, {"b": 1})
    led.init_pair(primary.pair_id)
    led.revoke(primary.pair_id, actor="t")
    classifier = IntentClassifier(derive_key(b"axiom-firewall-v1"))
    gate = IntentGate(classifier, log_path=str(isolated / "gate.log"),
                      bonded_pair_ledger=led)
    r = gate.check({"payload": {"text": "anything"},
                    "metadata": {"pair_id": primary.pair_id}})
    assert r.intent_class == "HARM"


# ─── Default ledger path ────────────────────────────────────────────────


def test_default_ledger_path_uses_env_var(isolated):
    """AXIOM_BONDED_PAIR_LEDGER (set by the isolated fixture) wins
    over the home-dir default."""
    from axiom_event_token.bonded_pair import default_ledger_path
    assert default_ledger_path() == isolated / "bp.jsonl"


def test_default_ledger_falls_back_to_home(monkeypatch):
    """When AXIOM_BONDED_PAIR_LEDGER is unset, default to
    ~/.axiom/bonded_pair_ledger.jsonl."""
    monkeypatch.delenv("AXIOM_BONDED_PAIR_LEDGER", raising=False)
    for mod in list(sys.modules):
        if mod.startswith("axiom_event_token.bonded_pair"):
            sys.modules.pop(mod, None)
    from axiom_event_token.bonded_pair import default_ledger_path
    p = default_ledger_path()
    assert str(p).endswith("/.axiom/bonded_pair_ledger.jsonl")


# ─── CLI surface ────────────────────────────────────────────────────────


def _run_cli(*args, env_extra=None, master_key=True) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if master_key:
        env["AXIOM_MASTER_KEY"] = "test" + "0" * 60
    else:
        env.pop("AXIOM_MASTER_KEY", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "axiom_bonded_pair_cli.py"), *args],
        capture_output=True, text=True, env=env, timeout=30,
    )


def test_cli_mint_emits_pair_id_and_initialises(isolated):
    ledger = isolated / "cli.jsonl"
    r = _run_cli(
        "mint",
        "--primary", '{"cmd":"run_query"}',
        "--mirror",  '{"mon":"primary"}',
        "--ledger", str(ledger),
        "--json",
    )
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads(r.stdout)
    assert out["pair_id"].startswith("bp-")
    assert out["primary"]["role"] == "primary"
    assert out["mirror"]["role"] == "mirror"
    assert out["primary"]["partner_token_id"] == out["mirror"]["token_id"]
    assert out["initialised"] is True
    assert Path(out["ledger"]) == ledger
    # Ledger file actually written
    assert ledger.exists()
    assert "ACTIVE_VALIDATED" in ledger.read_text()


def test_cli_state_exit_codes(isolated):
    ledger = isolated / "cli.jsonl"
    r = _run_cli("mint", "--primary", "{}", "--mirror", "{}",
                 "--ledger", str(ledger), "--json")
    pair_id = json.loads(r.stdout)["pair_id"]

    # ACTIVE_VALIDATED → exit 0
    r = _run_cli("state", pair_id, "--ledger", str(ledger))
    assert r.returncode == 0
    assert r.stdout.strip() == "ACTIVE_VALIDATED"

    # Revoke
    r = _run_cli("revoke", pair_id, "--ledger", str(ledger), "--actor", "test")
    assert r.returncode == 0

    # REVOKED → exit 1 (terminal, not authorised)
    r = _run_cli("state", pair_id, "--ledger", str(ledger))
    assert r.returncode == 1
    assert r.stdout.strip() == "REVOKED"

    # Quiet form for shell pipelines
    r = _run_cli("state", pair_id, "--ledger", str(ledger), "-q")
    assert r.returncode == 1
    assert r.stdout == ""


def test_cli_state_on_unknown_pair_exits_1(isolated):
    ledger = isolated / "cli.jsonl"
    ledger.touch()
    r = _run_cli("state", "bp-doesnotexist", "--ledger", str(ledger))
    assert r.returncode == 1
    assert "not initialised" in r.stderr


def test_cli_history_lists_transitions(isolated):
    ledger = isolated / "cli.jsonl"
    pair_id = json.loads(_run_cli(
        "mint", "--primary", "{}", "--mirror", "{}",
        "--ledger", str(ledger), "--json").stdout)["pair_id"]
    _run_cli("transition", pair_id, "SUSPENDED",
             "--ledger", str(ledger), "--actor", "alice")
    _run_cli("revoke", pair_id, "--ledger", str(ledger), "--actor", "bob")

    r = _run_cli("history", pair_id, "--ledger", str(ledger), "--json")
    assert r.returncode == 0
    history = json.loads(r.stdout)
    assert [t["to_state"] for t in history] == [
        "ACTIVE_VALIDATED", "SUSPENDED", "REVOKED",
    ]
    assert [t["actor"] for t in history] == ["cli", "alice", "bob"]


def test_cli_verify_passes_on_clean_chain(isolated):
    ledger = isolated / "cli.jsonl"
    pair_id = json.loads(_run_cli(
        "mint", "--primary", "{}", "--mirror", "{}",
        "--ledger", str(ledger), "--json").stdout)["pair_id"]
    _run_cli("transition", pair_id, "SUSPENDED", "--ledger", str(ledger))

    r = _run_cli("verify", "--ledger", str(ledger))
    assert r.returncode == 0
    assert "PASS" in r.stdout


def test_cli_verify_detects_tampering(isolated):
    ledger = isolated / "cli.jsonl"
    pair_id = json.loads(_run_cli(
        "mint", "--primary", "{}", "--mirror", "{}",
        "--ledger", str(ledger), "--json").stdout)["pair_id"]
    _run_cli("revoke", pair_id, "--ledger", str(ledger))

    # Flip the last entry from REVOKED back to ACTIVE_VALIDATED.
    lines = ledger.read_text(encoding="utf-8").splitlines()
    d = json.loads(lines[-1])
    d["to_state"] = "ACTIVE_VALIDATED"
    lines[-1] = json.dumps(d, sort_keys=True, separators=(",", ":"))
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

    r = _run_cli("verify", "--ledger", str(ledger))
    assert r.returncode == 2
    assert "FAIL" in r.stderr


def test_cli_transition_to_terminal_state_blocks_further(isolated):
    ledger = isolated / "cli.jsonl"
    pair_id = json.loads(_run_cli(
        "mint", "--primary", "{}", "--mirror", "{}",
        "--ledger", str(ledger), "--json").stdout)["pair_id"]
    _run_cli("revoke", pair_id, "--ledger", str(ledger))

    # Attempting to un-revoke must fail cleanly (exit 2, friendly error).
    r = _run_cli("transition", pair_id, "ACTIVE_VALIDATED",
                 "--ledger", str(ledger))
    assert r.returncode == 2
    assert "Traceback" not in r.stderr
    assert "terminal" in r.stderr.lower()


def test_cli_payload_from_file(isolated):
    """--primary @file.json should load the payload from disk."""
    ledger = isolated / "cli.jsonl"
    payload_file = isolated / "p.json"
    payload_file.write_text(json.dumps({"execution_command": "from_file"}))
    r = _run_cli(
        "mint", "--primary", f"@{payload_file}",
        "--mirror", '{"mon":"x"}', "--ledger", str(ledger), "--json",
    )
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads(r.stdout)
    assert out["primary"]["payload"] == {"execution_command": "from_file"}


def test_cli_payload_must_be_json_object(isolated):
    """--primary with a JSON array (not object) should fail cleanly."""
    ledger = isolated / "cli.jsonl"
    r = _run_cli(
        "mint", "--primary", "[1,2,3]", "--mirror", "{}",
        "--ledger", str(ledger),
    )
    assert r.returncode != 0
    assert "must be a JSON object" in r.stderr


def test_cli_missing_master_key_exits_nonzero(isolated):
    """axiom_signing raises on import without AXIOM_MASTER_KEY — the CLI
    must propagate that as a clean failure, not silently mint a bad pair."""
    ledger = isolated / "cli.jsonl"
    r = _run_cli("mint", "--primary", "{}", "--mirror", "{}",
                 "--ledger", str(ledger), master_key=False)
    assert r.returncode != 0
    assert "AXIOM_MASTER_KEY" in (r.stderr + r.stdout)
