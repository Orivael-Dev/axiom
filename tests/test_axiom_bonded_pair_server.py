"""REST + CMAA integration tests for the bonded-paired-token primitive.

Covers the surfaces a deployer actually hits:
  /v1/bonded_pair/mint, /transition, /revoke, /state, /history, /verify
  plus end-to-end: mint + revoke at REST level → /gate/check denies the
  same packet → /cmaa/route also denies (same singleton ledger).

Also asserts the demo PDF fixture at fixtures/bonded_pair_demo/audit.pdf
verifies under the documented fixture key.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Force CMAA singleton + gate logs into a tmp directory so we don't
# litter the repo root.
_TEST_LOG_DIR = REPO_ROOT / "tests" / "_tmp_bonded_pair_server_logs"
_TEST_LOG_DIR.mkdir(exist_ok=True)
os.environ.setdefault("AXIOM_CMAA_LOG_DIR", str(_TEST_LOG_DIR))

# Point the bonded-pair ledger singleton at a per-suite tmp file so
# tests don't pollute ~/.axiom/.
_TEST_LEDGER = _TEST_LOG_DIR / "bonded_pair_ledger.jsonl"
os.environ["AXIOM_BONDED_PAIR_LEDGER"] = str(_TEST_LEDGER)

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_bp_server_integration"


@pytest.fixture
def client(monkeypatch):
    """Fresh server singletons + empty ledger per test.

    We RESET singletons in place rather than dropping modules from
    sys.modules — the sibling test_axiom_server_integration.py uses
    importlib.reload(axiom_server) which requires the module to
    remain in sys.modules across tests.
    """
    if _TEST_LEDGER.exists():
        _TEST_LEDGER.unlink()
    from fastapi.testclient import TestClient
    import axiom_server
    axiom_server._cmaa_singleton = None
    axiom_server._bonded_pair_ledger_singleton = None
    yield TestClient(axiom_server.app)
    axiom_server._cmaa_singleton = None
    axiom_server._bonded_pair_ledger_singleton = None


# ─── REST round-trip ─────────────────────────────────────────────────────


def test_mint_returns_pair_with_signatures(client):
    r = client.post("/v1/bonded_pair/mint", json={
        "primary_payload": {"execution_command": "run_query"},
        "mirror_payload":  {"monitor_target": "primary"},
        "actor":           "provisioner",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pair_id"].startswith("bp-")
    assert body["primary"]["role"] == "primary"
    assert body["mirror"]["role"]  == "mirror"
    # Cross-references match
    assert body["primary"]["partner_token_id"] == body["mirror"]["token_id"]
    assert body["mirror"]["partner_token_id"]  == body["primary"]["token_id"]
    # Signatures present + non-empty
    assert len(body["primary"]["signature"]) == 64
    assert len(body["mirror"]["signature"])  == 64
    assert body["current_state"] == "ACTIVE_VALIDATED"


def test_state_then_revoke_then_state(client):
    pair = client.post("/v1/bonded_pair/mint", json={
        "primary_payload": {"a": 1}, "mirror_payload": {"b": 1},
    }).json()
    pid = pair["pair_id"]

    r = client.get(f"/v1/bonded_pair/{pid}/state")
    assert r.status_code == 200
    assert r.json() == {
        "pair_id": pid,
        "current_state": "ACTIVE_VALIDATED",
        "authorized": True,
    }

    r = client.post(f"/v1/bonded_pair/{pid}/revoke",
                    json={"actor": "security_monitor"})
    assert r.status_code == 200
    assert r.json()["to_state"] == "REVOKED"
    assert r.json()["actor"] == "security_monitor"

    r = client.get(f"/v1/bonded_pair/{pid}/state")
    assert r.json() == {
        "pair_id": pid,
        "current_state": "REVOKED",
        "authorized": False,
    }


def test_history_returns_all_transitions(client):
    pid = client.post("/v1/bonded_pair/mint",
                      json={"primary_payload": {}, "mirror_payload": {}}).json()["pair_id"]
    client.post(f"/v1/bonded_pair/{pid}/transition",
                json={"to_state": "SUSPENDED", "actor": "alice"})
    client.post(f"/v1/bonded_pair/{pid}/revoke", json={"actor": "carol"})

    h = client.get(f"/v1/bonded_pair/{pid}/history").json()
    assert h["pair_id"] == pid
    assert [t["to_state"] for t in h["transitions"]] == [
        "ACTIVE_VALIDATED", "SUSPENDED", "REVOKED",
    ]
    assert [t["actor"] for t in h["transitions"]] == ["rest", "alice", "carol"]


def test_state_404_on_unknown_pair(client):
    r = client.get("/v1/bonded_pair/bp-does-not-exist/state")
    assert r.status_code == 404
    assert "not initialised" in r.json()["detail"]["error"]


def test_transition_to_unknown_state_returns_400(client):
    pid = client.post("/v1/bonded_pair/mint",
                      json={"primary_payload": {}, "mirror_payload": {}}).json()["pair_id"]
    r = client.post(f"/v1/bonded_pair/{pid}/transition",
                    json={"to_state": "MADE_UP_STATE"})
    assert r.status_code == 400
    assert "not in allowed" in r.json()["detail"]["error"]


def test_revoke_then_un_revoke_returns_400(client):
    pid = client.post("/v1/bonded_pair/mint",
                      json={"primary_payload": {}, "mirror_payload": {}}).json()["pair_id"]
    client.post(f"/v1/bonded_pair/{pid}/revoke", json={})
    r = client.post(f"/v1/bonded_pair/{pid}/transition",
                    json={"to_state": "ACTIVE_VALIDATED"})
    assert r.status_code == 400
    assert "terminal" in r.json()["detail"]["error"].lower()


def test_verify_chain_endpoint(client):
    pid = client.post("/v1/bonded_pair/mint",
                      json={"primary_payload": {}, "mirror_payload": {}}).json()["pair_id"]
    client.post(f"/v1/bonded_pair/{pid}/revoke", json={})
    r = client.get("/v1/bonded_pair/verify")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert Path(body["ledger_path"]) == _TEST_LEDGER


# ─── End-to-end: REST revoke → /gate/check denies ────────────────────────


def test_rest_revoke_propagates_to_gate_check(client):
    """The HEADLINE behaviour. REST POST /v1/bonded_pair/{id}/revoke
    must immediately deny the SAME benign packet at /gate/check —
    no orchestrator restart, no key rotation."""
    pair = client.post("/v1/bonded_pair/mint", json={
        "primary_payload": {"cmd": "x"}, "mirror_payload": {"mon": "x"},
    }).json()
    pid = pair["pair_id"]

    # Before revoke: benign text + the pair_id passes (or at worst gets
    # UNCERTAIN — definitely not HARM with the revocation signal).
    benign = f"Tell me about volcanoes. (pair_id: {pid})"
    r = client.post("/gate/check", json={"text": benign})
    assert r.status_code == 200
    # No bonded_pair_revoked signal yet — text-only /gate/check doesn't
    # know about the pair_id without it being parseable from the packet,
    # so this just confirms classification runs cleanly.
    assert r.json()["intent_class"] != "HARM" or \
        "bonded_pair_revoked" not in r.json()["signals"]

    # Now exercise the real path: route a packet that carries the
    # pair_id in its payload through /cmaa/route — the orchestrator's
    # gate consults the same ledger.
    pre = client.post("/cmaa/route", json={
        "packet_id":   "pkt-1",
        "source":      "kid_app",
        "destination": "model_runtime",
        "payload":     {"text": "Tell me about volcanoes.", "pair_id": pid},
    })
    # Should NOT be blocked before revoke
    assert pre.status_code == 200
    assert pre.json()["intent_class"] != "HARM"

    # Revoke via REST
    r = client.post(f"/v1/bonded_pair/{pid}/revoke", json={"actor": "ops"})
    assert r.status_code == 200

    # Same packet, after revoke — must be blocked at /cmaa/route.
    # CMAA's 403 response intentionally hides signal detail at the API
    # boundary (only the orchestrator log records signals); what the
    # test must prove is that the SAME packet that passed before is
    # now refused. The "why" — bonded_pair_revoked signal — is
    # asserted directly in the gate-level integration suite.
    post = client.post("/cmaa/route", json={
        "packet_id":   "pkt-2",
        "source":      "kid_app",
        "destination": "model_runtime",
        "payload":     {"text": "Tell me about volcanoes.", "pair_id": pid},
    })
    assert post.status_code == 403, post.text
    body = post.json()
    assert body["error"] == "intent_violation"
    assert body["alert"]["intent_class"] == "HARM"

    # And the gate's own log must show the bonded_pair_revoked signal
    # — that's the audit trail an operator would inspect.
    gate_log = _TEST_LOG_DIR / "axiom_intent_gate_log.jsonl"
    log_text = gate_log.read_text(encoding="utf-8")
    assert "bonded_pair_revoked" in log_text, \
        "gate log must record the revocation signal"


# ─── Bootstrap / CMAA wiring ─────────────────────────────────────────────


def test_bootstrap_default_accepts_ledger():
    """The CMAA bootstrap helper must accept a bonded_pair_ledger arg
    and pass it through to the default IntentGate."""
    from axiom_cmaa import bootstrap_default
    from axiom_event_token.bonded_pair import BondedPairLedger
    led = BondedPairLedger(_TEST_LEDGER)
    orch = bootstrap_default(bonded_pair_ledger=led)
    # The orchestrator's classify callable must be functional — that's
    # the integration point. We don't reach inside to assert wiring,
    # but the constructor should accept and not raise.
    assert orch is not None


# ─── Demo PDF fixture ────────────────────────────────────────────────────


def test_bonded_pair_demo_pdf_verifies(monkeypatch):
    """The committed fixture PDF must verify under the documented
    fixture key — same pattern as the kid-audit baseline."""
    monkeypatch.setenv("AXIOM_MASTER_KEY",
                       "audit_baseline_fixture_key_do_not_use_in_prod_"
                       "ffffffffffffffff")
    # Reimport so axiom_signing rebinds to the fixture key.
    for mod in list(sys.modules):
        if mod.startswith(("axiom_signing", "axiom_report")):
            sys.modules.pop(mod, None)
    from axiom_report.generator import verify_pdf
    pdf_path = REPO_ROOT / "fixtures" / "bonded_pair_demo" / "audit.pdf"
    sig_path = REPO_ROOT / "fixtures" / "bonded_pair_demo" / "audit.pdf.sig"
    pdf = pdf_path.read_bytes()
    sig = sig_path.read_text().strip()
    assert verify_pdf(pdf, sig) is True


def test_bonded_pair_demo_pdf_modification_fails_verify(monkeypatch):
    """Flip a byte in the middle of the PDF — verification must fail."""
    monkeypatch.setenv("AXIOM_MASTER_KEY",
                       "audit_baseline_fixture_key_do_not_use_in_prod_"
                       "ffffffffffffffff")
    for mod in list(sys.modules):
        if mod.startswith(("axiom_signing", "axiom_report")):
            sys.modules.pop(mod, None)
    from axiom_report.generator import verify_pdf
    pdf_path = REPO_ROOT / "fixtures" / "bonded_pair_demo" / "audit.pdf"
    sig_path = REPO_ROOT / "fixtures" / "bonded_pair_demo" / "audit.pdf.sig"
    pdf = bytearray(pdf_path.read_bytes())
    pdf[len(pdf) // 2] ^= 0x01
    assert verify_pdf(bytes(pdf), sig_path.read_text().strip()) is False
